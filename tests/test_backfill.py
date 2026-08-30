"""The backfill, and the promise that nothing was quietly left behind.

The first class is the one that matters most and needs no database. It reads
`packages/schema/BACKFILL.md`, reads the real records, and fails when a key
exists in the store that the document mentions in neither its mapped table nor
its dropped table. "No silent field loss" cannot be a rule anybody holds in
their head across 20,103 records and 30 keys, and a code review cannot catch a
field that is simply never mentioned. A list compared against reality can.

It fails in both directions on purpose. A key in the store and not in the
document is a field about to be lost. A key in the document and not in the
store is a document describing a shape that no longer exists, which is how the
first kind of failure gets missed later.
"""

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import backfill  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402

DOC = REPO / "packages" / "schema" / "BACKFILL.md"
POOL = Path("~/.job-application/jobs.json").expanduser()

# Nested containers whose sub-keys the document accounts for individually.
NESTED = ("extracted", "location", "score")


def documented_keys():
    """Every legacy key named in the first column of the two key tables.

    Scoped by heading rather than taking every table in the file. The `state`
    sub-table's first column holds legacy state *values* (`new`, `expired`,
    `watched`, `passed`, `applied`), and a parser that swept every table read
    those five as field names and then reported them as keys the store had
    lost. The document was right and the reader was wrong, which is the
    ordinary way a check like this goes bad.
    """
    named, section = set(), None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            if heading == "mapped" or heading.startswith("dropped"):
                section = heading
            else:
                section = None            # `### state`, and everything after
            continue
        if section is None or not stripped.startswith("|"):
            continue
        first = stripped.strip("|").split("|")[0].strip()
        named.update(re.findall(r"`([^`]+)`", first))
    return named


def store_keys(records):
    """Every key the records actually carry, nested ones qualified."""
    found = set()
    for record in records:
        for key, value in record.items():
            found.add(key)
            if key in NESTED and isinstance(value, dict):
                for sub in value:
                    found.add(f"{key}.{sub}")
            if key == "sources" and isinstance(value, list):
                for element in value:
                    if isinstance(element, dict):
                        for sub in element:
                            found.add(f"sources[].{sub}")
    return found


class EveryFieldIsAccountedFor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not POOL.exists():
            raise unittest.SkipTest("no real job store on this machine")
        cls.records = list(json.loads(POOL.read_text()).values())
        cls.present = store_keys(cls.records)
        cls.documented = documented_keys()

    def covered(self, key):
        """A key is accounted for by its own row, or by a container's.

        `score` is dropped whole so `score.total` goes with it; `sources` is
        carried whole as jsonb so `sources[].url` goes with it. `extracted` and
        `location` are decomposed instead, and each has a row saying so, which
        is why the containers themselves must appear: without that row a
        sub-key silently left out of the document would look covered.
        """
        if key in self.documented:
            return True
        container = key.split(".")[0].split("[")[0]
        return container != key and container in self.documented

    def test_no_key_in_the_store_is_missing_from_the_document(self):
        missing = sorted(k for k in self.present if not self.covered(k))
        self.assertEqual(
            missing, [],
            f"{len(missing)} legacy keys are in the records and in neither "
            f"table of BACKFILL.md, so backfill would drop them silently")

    def test_no_key_in_the_document_has_vanished_from_the_store(self):
        """A document describing a shape that no longer exists stops being
        read, and then the check above stops being believed."""
        derived = {"sources[].tier"}          # carried inside the jsonb blob
        stale = sorted(self.documented - self.present - derived)
        self.assertEqual(
            stale, [],
            f"BACKFILL.md names keys the records no longer have: {stale}")

    def test_a_decomposed_container_names_every_sub_key_it_has(self):
        """The container rule must not become a way to cover a sub-key nobody
        mapped. `extracted` and `location` are decomposed, so each of their
        sub-keys needs its own row even though the container has one."""
        for container in ("extracted", "location"):
            subs = {k for k in self.present if k.startswith(container + ".")}
            missing = sorted(subs - self.documented)
            with self.subTest(container=container):
                self.assertEqual(
                    missing, [],
                    f"{container} is decomposed, so these need their own row")

    def test_the_document_lists_the_score_sub_keys_it_drops(self):
        """`score` is dropped wholesale, so its sub-keys are named in the
        reason rather than as rows. If that sentence disappears, the drop stops
        being explicit."""
        text = DOC.read_text(encoding="utf-8")
        for sub in ("total", "parts", "weights", "raw", "why", "unknowns",
                    "gates"):
            with self.subTest(sub=sub):
                self.assertIn(sub, text)


class TheStateSplitRoundTrips(unittest.TestCase):
    """One legacy field becomes two facts, and the inverse has exactly one
    implementation so the directions cannot drift."""

    def test_every_legacy_state_survives_the_round_trip(self):
        for state, (closed, decision) in backfill.STATE_MAP.items():
            with self.subTest(state=state):
                closed_at = "2026-08-29T00:00:00Z" if closed else None
                self.assertEqual(backfill.legacy_state(closed_at, decision),
                                 state)

    def test_a_decision_state_with_no_legacy_meaning_raises(self):
        """`queued`, `applying` and `needs_you` are new in §3. A record in one
        of those has no file-store equivalent, and parity must say so rather
        than pick the nearest."""
        for state in ("queued", "applying", "needs_you"):
            with self.subTest(state=state):
                with self.assertRaises(backfill.BackfillError):
                    backfill.legacy_state(None, state)

    def test_closed_and_a_decision_prefers_the_decision(self):
        """The file store cannot represent it, so this only arises after
        Phase 1 starts writing. The decision is the newer fact."""
        self.assertEqual(backfill.legacy_state("2026-01-01", "watching"),
                         "watched")


class RowsAreBuiltWithoutGuessing(unittest.TestCase):
    def record(self, **kw):
        base = {"id": "abc123", "canonicalSource": "greenhouse",
                "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/7",
                "title": "PM", "state": "new"}
        base.update(kw)
        return base

    def test_a_record_without_an_id_is_refused(self):
        with self.assertRaises(backfill.BackfillError):
            backfill.posting_row(self.record(id=""))

    def test_a_record_without_a_url_is_refused(self):
        """Inventing one produces a row nothing can apply to."""
        with self.assertRaises(backfill.BackfillError):
            backfill.posting_row(self.record(canonicalUrl=None))

    def test_a_missing_title_becomes_a_marker_rather_than_an_empty_string(self):
        row = backfill.posting_row(self.record(title=""))
        self.assertEqual(row["title"], "(untitled)")

    def test_an_empty_country_becomes_null_not_a_blank(self):
        row = backfill.posting_row(self.record(location={"country": ""}))
        self.assertIsNone(row["country"])

    def test_a_malformed_country_becomes_null(self):
        for value in ("USA", "u", "1S", None):
            with self.subTest(value=value):
                row = backfill.posting_row(
                    self.record(location={"country": value}))
                self.assertIsNone(row["country"])

    def test_remote_is_derived_from_workplace(self):
        for workplace, expected in (("remote", True), ("hybrid", False),
                                    ("onsite", False), (None, False)):
            with self.subTest(workplace=workplace):
                row = backfill.posting_row(self.record(workplace=workplace))
                self.assertIs(row["remote"], expected)
                self.assertEqual(row["workplace"], workplace)

    def test_workplace_survives_so_unknown_is_not_the_same_as_not_remote(self):
        unknown = backfill.posting_row(self.record(workplace=None))
        onsite = backfill.posting_row(self.record(workplace="onsite"))
        self.assertEqual(unknown["remote"], onsite["remote"])
        self.assertNotEqual(unknown["workplace"], onsite["workplace"])

    def test_an_oversized_summary_is_truncated_rather_than_refused(self):
        """The column caps at 2,048 and today's excerpts are 1,500. If ingest
        ever writes a longer one, losing the tail of a summary is better than
        losing the posting."""
        row = backfill.posting_row(
            self.record(descriptionExcerpt="x" * 4000))
        self.assertEqual(len(row["summary"]), 2048)

    def test_seen_count_is_at_least_one(self):
        for value in (None, 0, -3, "nonsense"):
            with self.subTest(value=value):
                row = backfill.posting_row(self.record(seenCount=value))
                self.assertGreaterEqual(row["seen_count"], 1)


class WebsitesBecomeHostnames(unittest.TestCase):
    """The store keeps a URL; §3's column is a domain."""

    def test_a_url_is_reduced_to_its_host(self):
        for value, expected in (
                ("https://www.spacex.com", "spacex.com"),
                ("https://stripe.com", "stripe.com"),
                ("http://Monzo.com/careers?x=1", "monzo.com"),
                ("oracle.com", "oracle.com"),
                ("https://jobs.example.co.uk/a/b", "jobs.example.co.uk")):
            with self.subTest(value=value):
                self.assertEqual(backfill.hostname(value), expected)

    def test_www_is_stripped_so_one_employer_is_one_domain(self):
        """`www.spacex.com` and `spacex.com` are the same employer. Keeping
        both would make the unique constraint miss the duplicates it exists
        to catch."""
        self.assertEqual(backfill.hostname("https://www.spacex.com"),
                         backfill.hostname("https://spacex.com"))

    def test_nothing_becomes_none_rather_than_an_empty_string(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(backfill.hostname(value))

    def test_a_malformed_website_does_not_stop_the_run(self):
        for value in ("://", "http://", "not a url at all", "https://"):
            with self.subTest(value=value):
                backfill.hostname(value)


class TheStoreIsReadOrNamed(unittest.TestCase):
    """The `json.loads(stdout or "[]")` shape, which the plan names as the
    exact failure to design against: a read that fails must never look like a
    read that returned nothing."""

    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        import shutil
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_missing_file_is_named_not_empty(self):
        with self.assertRaises(backfill.BackfillError) as caught:
            backfill.load_pool(self.dir / "nope.json")
        self.assertIn("could not be read", str(caught.exception))

    def test_a_truncated_file_is_named_not_empty(self):
        path = self.dir / "jobs.json"
        path.write_text('{"a": {"id": "a"}, "b": {"id"')
        with self.assertRaises(backfill.BackfillError) as caught:
            backfill.load_pool(path)
        self.assertIn("not valid JSON", str(caught.exception))

    def test_a_list_where_a_mapping_belongs_is_named(self):
        path = self.dir / "jobs.json"
        path.write_text("[]")
        with self.assertRaises(backfill.BackfillError):
            backfill.load_pool(path)

    def test_a_genuinely_empty_store_is_not_an_error(self):
        """Empty and unreadable must be distinguishable in both directions."""
        path = self.dir / "jobs.json"
        path.write_text("{}")
        self.assertEqual(backfill.load_pool(path), {})


class AgainstARealDatabase(pgtest.SchemaCase):
    prefix = "backfill"

    def setUp(self):
        super().setUp()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('b@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]

    def records(self):
        return {
            "id1": {"id": "id1", "canonicalSource": "greenhouse",
                    "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/1",
                    "company": "Acme Inc", "companyWebsite": "acme.com",
                    "title": "Product Manager", "state": "new",
                    "lastSeenAt": "2026-08-29T00:00:00Z",
                    "firstSeenAt": "2026-08-20T00:00:00Z",
                    "postedAt": "2026-08-19T00:00:00Z"},
            "id2": {"id": "id2", "canonicalSource": "greenhouse",
                    "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/2",
                    "company": "Acme Inc", "title": "Designer",
                    "state": "watched",
                    "lastSeenAt": "2026-08-29T00:00:00Z",
                    "firstSeenAt": "2026-08-20T00:00:00Z", "postedAt": None},
            "id3": {"id": "id3", "canonicalSource": "adzuna",
                    "canonicalUrl": "https://www.adzuna.com/land/ad/9",
                    "company": "Other Co", "title": "Analyst",
                    "state": "expired", "closedAt": "2026-08-28T00:00:00Z",
                    "lastSeenAt": "2026-08-28T00:00:00Z",
                    "firstSeenAt": "2026-08-21T00:00:00Z", "postedAt": None},
        }

    def counts(self):
        with self.conn.cursor() as cur:
            out = {}
            for table in ("employers", "postings", "job_decisions"):
                cur.execute(f"select count(*) from {table}")
                out[table] = cur.fetchone()[0]
            return out

    def test_it_lands_every_record(self):
        report = backfill.run(self.conn, self.account, self.records())
        self.assertEqual(report["records"], 3)
        self.assertEqual(self.counts(),
                         {"employers": 2, "postings": 3, "job_decisions": 1})

    def test_running_it_twice_changes_nothing(self):
        """The property that lets it close the gap between the backfill and
        the start of dual-write. Without it that window is permanent."""
        backfill.run(self.conn, self.account, self.records())
        first = self.counts()
        report = backfill.run(self.conn, self.account, self.records())
        self.assertEqual(self.counts(), first)
        self.assertEqual(report["postings"], 3)

    def test_a_second_run_picks_up_a_new_record(self):
        records = self.records()
        backfill.run(self.conn, self.account, records)
        records["id4"] = {"id": "id4", "canonicalSource": "lever",
                          "canonicalUrl": "https://jobs.lever.co/x/abc",
                          "company": "Acme Inc", "title": "SRE",
                          "state": "new",
                          "lastSeenAt": "2026-08-29T00:00:00Z",
                          "firstSeenAt": "2026-08-29T00:00:00Z",
                          "postedAt": None}
        backfill.run(self.conn, self.account, records)
        self.assertEqual(self.counts()["postings"], 4)

    def test_the_expired_record_is_closed_and_has_no_decision(self):
        backfill.run(self.conn, self.account, self.records())
        with self.conn.cursor() as cur:
            cur.execute("select closed_at from postings where legacy_id='id3'")
            self.assertIsNotNone(cur.fetchone()[0])
            cur.execute("""select count(*) from job_decisions d
                           join postings p on p.id = d.posting_id
                           where p.legacy_id = 'id3'""")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_the_watched_record_gets_a_watching_decision(self):
        backfill.run(self.conn, self.account, self.records())
        with self.conn.cursor() as cur:
            cur.execute("""select d.state from job_decisions d
                           join postings p on p.id = d.posting_id
                           where p.legacy_id = 'id2'""")
            self.assertEqual(cur.fetchone()[0], "watching")

    def test_a_blank_website_within_one_run_does_not_beat_a_real_one(self):
        """The records disagree with each other: 6,481 carry a website and the
        rest carry an empty string for the same employers. Whichever is seen
        last must not win."""
        records = self.records()
        records["id1"]["companyWebsite"] = ""
        records["id2"]["companyWebsite"] = "acme.com"
        backfill.run(self.conn, self.account, records)
        self.assertEqual(self.domain_of(backfill.norm_company("Acme Inc")), "acme.com")

    def test_a_later_run_with_no_website_at_all_keeps_the_stored_one(self):
        """The case the in-Python `or` cannot reach, and the one the SQL
        `coalesce` exists for: the record that carried the website has aged out
        of the pool, so the second run offers NULL for that employer.

        A mutation replacing the coalesce with `excluded.domain` survived until
        this existed, because the first version of this test changed one
        record's website to a blank and the other record still supplied it.
        """
        records = self.records()
        backfill.run(self.conn, self.account, records)
        self.assertEqual(self.domain_of(backfill.norm_company("Acme Inc")), "acme.com")

        later = self.records()
        for record in later.values():
            record.pop("companyWebsite", None)
        backfill.run(self.conn, self.account, later)
        self.assertEqual(self.domain_of(backfill.norm_company("Acme Inc")), "acme.com",
                         "a run with no website erased the stored one")

    def domain_of(self, norm_name):
        with self.conn.cursor() as cur:
            cur.execute("select domain from employers where norm_name = %s",
                        (norm_name,))
            row = cur.fetchone()
        self.assertIsNotNone(row, f"no employer {norm_name!r}")
        return row[0]

    def test_a_posting_with_no_employer_is_filed_not_dropped(self):
        """Three records on the real pool name no employer, all careerjet,
        each with a real title and a real apply URL. `employer_id` is not
        null, so it is a placeholder or losing three postings."""
        records = self.records()
        records["id1"]["company"] = ""
        report = backfill.run(self.conn, self.account, records)
        self.assertEqual(report["unnamed_employer"], 1)
        with self.conn.cursor() as cur:
            cur.execute("""select e.norm_name from postings p
                           join employers e on e.id = p.employer_id
                           where p.legacy_id = 'id1'""")
            self.assertEqual(cur.fetchone()[0], backfill.UNKNOWN_EMPLOYER)
            cur.execute("select count(*) from postings")
            self.assertEqual(cur.fetchone()[0], 3, "a posting was dropped")

    def test_the_placeholder_never_carries_a_domain_or_a_logo(self):
        """An unknown employer with a mark is the stranger's-logo failure in
        another hat, and a feed record can supply one."""
        records = self.records()
        records["id1"]["company"] = ""
        records["id1"]["companyWebsite"] = "https://someone-else.com"
        records["id1"]["logoUrl"] = "https://someone-else.com/logo.png"
        backfill.run(self.conn, self.account, records)
        with self.conn.cursor() as cur:
            cur.execute("select domain, logo_url from employers "
                        "where norm_name = %s", (backfill.UNKNOWN_EMPLOYER,))
            self.assertEqual(cur.fetchone(), (None, None))

    def test_no_real_employer_name_can_normalise_onto_the_placeholder(self):
        """identity.norm_company emits only lowercase alphanumerics and
        spaces, so the parentheses make the sentinel unreachable."""
        for name in ("Unknown employer", "(unknown employer)", "unknown",
                     "UNKNOWN EMPLOYER", "Unknown Employer Inc"):
            with self.subTest(name=name):
                self.assertNotEqual(backfill.norm_company(name),
                                    backfill.UNKNOWN_EMPLOYER)

    def test_the_placeholder_is_not_created_when_nothing_needs_it(self):
        """Every fresh database would otherwise get a stray employer row."""
        backfill.run(self.conn, self.account, self.records())
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from employers where norm_name = %s",
                        (backfill.UNKNOWN_EMPLOYER,))
            self.assertEqual(cur.fetchone()[0], 0)

    def test_two_employers_cannot_claim_one_domain(self):
        """§3 makes `employers.domain` unique, and on the real corpus that
        catches something true: `sharegate.com` is claimed by `sharegate` and
        by `sharegate migrate chezworkleap`, one employer the name normaliser
        did not merge. One collision in 1,934 employers.

        Failing the whole run over it would be wrong and merging two employer
        identities silently would be worse. The domain goes to one of them and
        the loser is reported."""
        records = self.records()
        records["id1"]["company"] = "Sharegate"
        records["id1"]["companyWebsite"] = "https://sharegate.com"
        records["id3"]["company"] = "Sharegate Migrate At Workleap"
        records["id3"]["companyWebsite"] = "https://www.sharegate.com"
        report = backfill.run(self.conn, self.account, records)
        self.assertEqual(len(report["contested_domains"]), 1)
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from employers where domain = %s",
                        ("sharegate.com",))
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("select count(*) from employers "
                        "where norm_name like 'sharegate%'")
            self.assertEqual(cur.fetchone()[0], 2,
                             "both contesting employers keep their row")

    def test_the_contested_winner_does_not_move_between_runs(self):
        """Non-deterministic ownership would make parity flap: the domain
        would belong to a different employer on alternate runs."""
        records = self.records()
        records["id1"]["company"] = "Sharegate"
        records["id1"]["companyWebsite"] = "https://sharegate.com"
        records["id3"]["company"] = "Sharegate Migrate At Workleap"
        records["id3"]["companyWebsite"] = "https://www.sharegate.com"
        backfill.run(self.conn, self.account, records)
        with self.conn.cursor() as cur:
            cur.execute("select norm_name from employers where domain = %s",
                        ("sharegate.com",))
            first = cur.fetchone()[0]
        backfill.run(self.conn, self.account, records)
        with self.conn.cursor() as cur:
            cur.execute("select norm_name from employers where domain = %s",
                        ("sharegate.com",))
            self.assertEqual(cur.fetchone()[0], first)

    def test_the_website_is_stored_as_a_host_not_a_url(self):
        backfill.run(self.conn, self.account, self.records())
        self.assertEqual(self.domain_of(backfill.norm_company("Acme Inc")),
                         "acme.com")

    def test_two_records_sharing_a_board_key_are_merged_and_reported(self):
        """Measured on the real pool: one pair, two Braze rows for one
        Greenhouse posting. The batch must not abort, and the loser must be
        named rather than silently dropped."""
        records = self.records()
        records["id5"] = dict(records["id1"], id="id5",
                              canonicalUrl="https://job-boards.greenhouse.io/acme/jobs/1")
        report = backfill.run(self.conn, self.account, records)
        self.assertEqual(report["merged"], ["id5"])
        self.assertEqual(self.counts()["postings"], 3)

    def test_an_unknown_legacy_state_stops_the_run(self):
        records = self.records()
        records["id1"]["state"] = "brand_new_state"
        with self.assertRaises(backfill.BackfillError):
            backfill.run(self.conn, self.account, records)

    def test_a_null_posted_at_sorts_last_after_a_real_backfill(self):
        """The plan's negative test, end to end rather than against a fixture:
        two of these three records have no posting date."""
        backfill.run(self.conn, self.account, self.records())
        with self.conn.cursor() as cur:
            cur.execute("""select legacy_id from postings
                           order by posted_at desc nulls last, id desc""")
            got = [r[0] for r in cur.fetchall()]
        self.assertEqual(got[0], "id1")
        self.assertEqual(sorted(got[1:]), ["id2", "id3"])


if __name__ == "__main__":
    unittest.main()
