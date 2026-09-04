"""Deriving (board_slug, external_id) from a posting URL, and refusing to guess.

§3 keys `jobs` on `(source, board_slug, external_id)`, which is the connector
upsert target: a Phase 3 connector fetching `boards.greenhouse.io/<slug>` knows
all three by construction and never parses a URL. Backfill has only the URL, so
this exists for backfill and for dual-write from the current ingest path.

Two failure directions, and they are not symmetric.

**A wrong slug is worse than no slug.** `board_slug` names an employer's ATS
tenant. Deriving `stripe` from one URL and `stripe-inc` from another splits one
employer's board in two and makes the upsert insert duplicates forever. Worse,
deriving the wrong employer's slug files a posting under a stranger. The same
argument the logo derivation makes: a favicon endpoint answers 404 for a domain
it does not know, so a wrong guess costs a monogram, and here a wrong guess
costs a corrupted key.

**No slug is recoverable.** An aggregator posting has no ATS tenant at all,
because it came from a feed. For those the feed is the board, and the external
id is the identity this repository already derived for the record. That is a
convention rather than a discovery, and it is written down rather than inferred.

So the rule is: parse only shapes that are unambiguous, fall back explicitly,
and never invent a slug from a hostname that might belong to anyone.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))

import board_key  # noqa: E402

POOL = Path("~/.job-application/jobs.json").expanduser()


class RealUrlsParseCorrectly(unittest.TestCase):
    """Every case is a URL taken from the live pool on 2026-08-29."""

    CASES = [
        ("greenhouse", "https://boards.greenhouse.io/spacex/jobs/8696779002?gh_jid=8696779002",
         ("spacex", "8696779002")),
        ("greenhouse", "https://job-boards.greenhouse.io/anthropic/jobs/4020159008",
         ("anthropic", "4020159008")),
        ("ashby", "https://jobs.ashbyhq.com/airwallex/7035eefa-73b8-4406-a96f-811a10121f10",
         ("airwallex", "7035eefa-73b8-4406-a96f-811a10121f10")),
        ("lever", "https://jobs.lever.co/veeva/126dc4e0-1ddb-4b8f-a849-a466bc3dcfa8",
         ("veeva", "126dc4e0-1ddb-4b8f-a849-a466bc3dcfa8")),
        ("smartrecruiters", "https://jobs.smartrecruiters.com/deliveryhero/744000143469600",
         ("deliveryhero", "744000143469600")),
        ("rippling", "https://ats.rippling.com/rippling/jobs/890fc24b-fc04-4546-a879-4b3f49ad9318",
         ("rippling", "890fc24b-fc04-4546-a879-4b3f49ad9318")),
        ("pinpoint", "https://davies.pinpointhq.com/en/postings/71b69457-c30f-4a71-abf6-307a73f2c86b",
         ("davies", "71b69457-c30f-4a71-abf6-307a73f2c86b")),
        ("workday", "https://geico.wd1.myworkdayjobs.com/External/job/Poway-CA/Claims-Service-Specialist_R0064682",
         ("geico", "R0064682")),
        ("oracle_orc", "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210764384",
         ("jpmc", "210764384")),
        ("teamtailor", "https://mobile.teamtailor.com/jobs/8250934-responsabile-commerciale",
         ("mobile", "8250934")),
        ("personio", "https://tandem.jobs.personio.de/job/2768964",
         ("tandem", "2768964")),
        ("jazzhr", "https://focuskpi.applytojob.com/apply/jobs/details/ITTf516vvQ",
         ("focuskpi", "ITTf516vvQ")),
    ]

    def test_each_known_shape(self):
        for source, url, expected in self.CASES:
            with self.subTest(source=source, url=url):
                self.assertEqual(board_key.derive(source, url, "deadbeef"),
                                 expected)

    def test_the_slug_is_never_the_ats_vendor(self):
        """`jobs.lever.co/veeva/...` is Veeva's board, not Lever's. A parser
        taking the first hostname label gets `jobs` or `ats`, and one taking
        the registrable domain gets the vendor. Both file every employer on
        that ATS under one slug."""
        for source, url, (slug, _id) in self.CASES:
            with self.subTest(url=url):
                self.assertNotIn(slug, ("jobs", "ats", "boards", "apply",
                                        "www", "careers", "job-boards"))
                self.assertNotIn(slug, ("lever", "ashbyhq", "greenhouse",
                                        "smartrecruiters", "myworkdayjobs",
                                        "oraclecloud", "pinpointhq",
                                        "teamtailor", "personio",
                                        "applytojob"))


class AggregatorsFallBackRatherThanGuess(unittest.TestCase):
    """A feed posting has no ATS tenant. The feed is the board and the record's
    own identity is the external id, which is a convention, stated here."""

    FEEDS = [
        ("careerjet", "https://jobviewtrack.com/v2/lNTVgDr_82h1IBhP4tIB4ET"),
        ("jooble", "https://jooble.org/away/-7756061413376887192?aq=-58811"),
        ("adzuna", "https://www.adzuna.com/land/ad/5826550274?se=mNViH5qg"),
        ("reed", "https://www.reed.co.uk/jobs/business-analyst/57166852"),
        ("usajobs", "https://www.usajobs.gov/job/881174700"),
        ("remoteok", "https://remoteOK.com/remote-jobs/remote-teacher-1137182"),
        ("weworkremotely", "https://weworkremotely.com/remote-jobs/x-y-z"),
    ]

    def test_the_feed_is_the_board_and_the_record_id_is_the_external_id(self):
        for source, url in self.FEEDS:
            with self.subTest(source=source):
                slug, ext = board_key.derive(source, url, "abc123def456")
                self.assertEqual(slug, source)
                self.assertEqual(ext, "abc123def456")

    def test_no_hostname_label_leaks_into_the_slug(self):
        """The slug for a feed is the feed's name, never something scraped off
        its host.

        `careerjet` postings are served from `jobviewtrack.com`, so a parser
        reaching for a host label would key them under `jobviewtrack`, and
        `www` belongs to nobody at all. The check is that the slug came from
        the source name rather than the URL, which for `remoteok` is the same
        string by coincidence: the first version of this test listed
        `remoteok` as forbidden and failed on its own convention."""
        for source, url in self.FEEDS:
            with self.subTest(source=source):
                slug, _ = board_key.derive(source, url, "x")
                self.assertEqual(slug, source)
                host = url.split("//", 1)[-1].split("/", 1)[0]
                labels = {p for p in host.lower().split(".") if p != source}
                self.assertNotIn(slug, labels)


class ItRefusesRatherThanInventing(unittest.TestCase):
    def test_a_greenhouse_url_on_an_employers_own_domain_falls_back(self):
        """`stripe.com/jobs/search?gh_jid=8113100` carries the job id and not
        the board slug. `stripe` is a plausible guess and plausible is exactly
        what must not happen here: the real slug could be `stripe`, `stripeinc`
        or anything else, and being wrong splits the board."""
        slug, ext = board_key.derive(
            "greenhouse", "https://stripe.com/jobs/search?gh_jid=8113100", "id0")
        self.assertEqual(slug, "greenhouse")
        self.assertEqual(ext, "id0")

    def test_an_unknown_source_falls_back(self):
        self.assertEqual(
            board_key.derive("some_new_ats", "https://example.com/x", "id1"),
            ("some_new_ats", "id1"))

    def test_a_missing_url_falls_back(self):
        for url in (None, "", "   "):
            with self.subTest(url=url):
                self.assertEqual(board_key.derive("greenhouse", url, "id2"),
                                 ("greenhouse", "id2"))

    def test_a_malformed_url_falls_back_rather_than_raising(self):
        """Backfill runs over 20,103 records. One that raises stops the run."""
        for url in ("not a url", "http://", "https://boards.greenhouse.io/",
                    "https://jobs.lever.co/onlyslug"):
            with self.subTest(url=url):
                slug, ext = board_key.derive("greenhouse", url, "id3")
                self.assertTrue(slug and ext)

    def test_the_fallback_needs_a_record_id(self):
        """Falling back to an empty external id would make every unparsed
        record collide on one key, and the upsert would overwrite them into a
        single row. Refuse instead."""
        with self.assertRaises(ValueError):
            board_key.derive("adzuna", "https://x/y", "")


class TheNeverASlugFilterBinds(unittest.TestCase):
    """`_NEVER_A_SLUG` guards a pattern nobody has written yet.

    Every pattern shipped today anchors on the vendor's own hostname, so none
    of them can yield `www` or `jobs`, and emptying the set breaks no test that
    uses the real patterns. That makes it exactly the kind of defensive code
    the Phase 0 work deleted when nothing could tell it apart from its absence.

    It is kept rather than deleted because the hazard is real and arrives with
    the next pattern: `([^./?#]+)\\.example\\.com` written against
    `www.example.com/jobs/1` files every employer under `www`. So the guard is
    exercised against a deliberately careless pattern rather than left to be
    assumed.
    """

    def setUp(self):
        import re
        self.original = board_key._PATTERNS
        board_key._PATTERNS = dict(self.original)
        board_key._PATTERNS["careless"] = [
            (re.compile(r"^(?:https?://)?([^./?#]+)\.example\.com/jobs/(\d+)"),
             1, 2)]
        self.addCleanup(setattr, board_key, "_PATTERNS", self.original)

    def test_a_pattern_that_would_yield_a_host_word_is_refused(self):
        for host in ("www", "jobs", "careers", "apply"):
            with self.subTest(host=host):
                url = f"https://{host}.example.com/jobs/12345"
                self.assertIsNone(board_key.parse("careless", url))
                self.assertEqual(board_key.derive("careless", url, "rec1"),
                                 ("careless", "rec1"))

    def test_a_real_employer_label_still_parses(self):
        """The filter must not swallow the working case, or the next person
        widens it until it stops filtering."""
        self.assertEqual(
            board_key.parse("careless", "https://acme.example.com/jobs/12345"),
            ("acme", "12345"))


class TheResultIsAlwaysUsableAsAKey(unittest.TestCase):
    def test_neither_half_is_ever_empty(self):
        for source, url, _ in RealUrlsParseCorrectly.CASES:
            slug, ext = board_key.derive(source, url, "fallback")
            self.assertTrue(slug.strip())
            self.assertTrue(ext.strip())

    def test_slugs_are_lowercased_so_one_board_is_one_slug(self):
        """`RemoteOK.com` and `remoteok.com` appear in the pool for the same
        source. Case-sensitive slugs would key them apart."""
        a = board_key.derive("pinpoint", "https://Davies.pinpointhq.com/en/postings/aaa", "i")
        b = board_key.derive("pinpoint", "https://davies.pinpointhq.com/en/postings/aaa", "i")
        self.assertEqual(a, b)

    def test_it_is_deterministic(self):
        url = "https://jobs.ashbyhq.com/airwallex/7035eefa-73b8-4406-a96f-811a10121f10"
        self.assertEqual(board_key.derive("ashby", url, "x"),
                         board_key.derive("ashby", url, "x"))


class MeasuredAgainstTheRealPool(unittest.TestCase):
    """A parser is not verified by reading it. These numbers are the evidence,
    and they are asserted so a regression moves them."""

    @classmethod
    def setUpClass(cls):
        if not POOL.exists():
            raise unittest.SkipTest("no real job store on this machine")
        cls.jobs = json.loads(POOL.read_text())

    def test_every_record_yields_a_key(self):
        missing = [j["id"] for j in self.jobs.values()
                   if not all(board_key.derive(j["canonicalSource"],
                                               j["canonicalUrl"], j["id"]))]
        self.assertEqual(missing, [])

    def collisions(self):
        groups = {}
        for j in self.jobs.values():
            key = (j["canonicalSource"],) + board_key.derive(
                j["canonicalSource"], j["canonicalUrl"], j["id"])
            groups.setdefault(key, []).append(j)
        return {k: v for k, v in groups.items() if len(v) > 1}

    def test_every_colliding_key_is_a_genuine_duplicate_posting(self):
        """The unique index is the point, so a collision is information rather
        than a defect: it is two stored records for one posting.

        Measured 2026-08-29: exactly one, two Braze records for Greenhouse job
        8139241, reached once through `boards.greenhouse.io` and once through
        `job-boards.greenhouse.io`. The legacy dedupe keys on the URL and those
        two URLs differ, so it kept both. The new key sees one posting, which
        is correct, and the backfill has to merge rather than fail.

        The assertion is that every collision looks like that, because a
        collision between two *different* postings would mean the key is wrong
        and the backfill would silently drop a real job.

        **Title is part of identity here and title is the thing that changes.**
        Measured 2026-09-03, three collisions and all three the same shape: an
        employer edits a posting, Breezy keeps the id and rewrites the slug,
        the URL-keyed sighting misses it, the sweep expires the old record and
        ingests the new slug as a new one five or six minutes later.

            breezy/bitdeer/eeab1edbc9db    closed 07-47, next seen 19-47
            breezy/surge/f8b44d25ba27      closed 15-47, next seen 15-52
            breezy/nationsbenefits/8afada1bbe99  closed 19-43, next seen 19-48

        The key is right about those: one posting. So the discriminator is not
        the title, it is whether the records were ever live at the same time.
        A retitle is a handoff and the lifetimes are disjoint. Two genuinely
        different postings sharing one key would be open together, and that is
        what the key being wrong would look like.

        **This test passing is not the fold being right.** The mirror keeps the
        *expired* record of each pair and files the live one under it, so
        Postgres holds "Operations Manager, $65,000-$85,000, closed" where the
        live posting is "3rd Shift Electronics Operations Supervisor,
        $50,000-$55,000". That is a defect in winner selection, recorded in
        `docs/plans/phase3-inputs.md` and deliberately not fixed here: this
        test's subject is the key, and turning a red test green over a bug that
        still stands is worse than either."""
        for key, group in self.collisions().items():
            with self.subTest(key=key):
                identities = {(j["company"].strip().lower(),
                               j["title"].strip().lower()) for j in group}
                if len(identities) == 1:
                    continue
                order = sorted(group, key=lambda j: j.get("firstSeenAt") or "")
                overlapping = [
                    (a["id"], b["id"]) for a, b in zip(order, order[1:])
                    if not (a.get("lastSeenAt") and b.get("firstSeenAt")
                            and a["lastSeenAt"] < b["firstSeenAt"])]
                self.assertEqual(
                    overlapping, [],
                    f"{key} collides across postings that were live at the "
                    f"same time, which a retitle cannot explain: {identities}")

    def test_collisions_are_rare_enough_to_be_worth_looking_at(self):
        """A floor under the previous test. If this ever climbs, the key is
        merging things it should not and the per-collision check above becomes
        too weak to notice."""
        found = len(self.collisions())
        self.assertLessEqual(
            found, 25, f"{found} colliding keys, up from 1 on 2026-08-29")

    def test_a_real_slug_is_found_where_a_pattern_exists(self):
        """Scoped to sources this module claims to parse. A share of the whole
        pool moves whenever the aggregator mix moves, which makes it useless as
        a regression signal. Measured 2026-08-29: 14,127 of 17,832, 79.2%."""
        known = set(board_key._PATTERNS)
        rows = [j for j in self.jobs.values()
                if j["canonicalSource"] in known]
        parsed = sum(1 for j in rows
                     if board_key.parse(j["canonicalSource"], j["canonicalUrl"]))
        share = parsed / len(rows)
        self.assertGreater(
            share, 0.75,
            f"only {share:.1%} of postings from a source with a pattern "
            f"yields a slug, against 79.2% on 2026-08-29")

    def test_each_fully_covered_source_stays_fully_covered(self):
        """Eight sources parse every record they have. Those are the ones a
        broken pattern would show up in first, and a whole-pool percentage
        would hide a single source dropping to zero."""
        complete = ("ashby", "lever", "smartrecruiters", "workday",
                    "oracle_orc", "pinpoint", "himalayas", "successfactors")
        for source in complete:
            rows = [j for j in self.jobs.values()
                    if j["canonicalSource"] == source]
            if not rows:
                continue
            with self.subTest(source=source):
                parsed = sum(1 for j in rows
                             if board_key.parse(source, j["canonicalUrl"]))
                self.assertEqual(parsed, len(rows),
                                 f"{source} parsed {parsed}/{len(rows)}")
