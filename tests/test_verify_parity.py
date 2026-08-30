"""Parity, and the ways a parity job reports green without checking anything.

The exit code is the whole interface, so every test here is about the ways 0
can be returned for the wrong reason:

  dual-write off          nothing to compare, nothing concluded, must not be 0
  an unreadable pool      not an empty pool, must not be 0
  only the intersection   the 4,779 missing records hid there
  a divergence suppressed as a race

That last one is the one with history. The board once published an empty
Submitted tab over a good one, and "it was probably mid-write" is the reasoning
that gets you there. So a divergence that clears on re-read is still recorded,
with both readings, and only a divergence that persists turns the run red.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import backfill  # noqa: E402
import dual_write  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402
import verify_parity  # noqa: E402


def record(job_id, **kw):
    base = {"id": job_id, "canonicalSource": "greenhouse",
            "canonicalUrl": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
            "company": "Acme", "title": "PM", "state": "new",
            "firstSeenAt": "2026-08-01T00:00:00Z",
            "lastSeenAt": "2026-08-29T00:00:00Z", "postedAt": None}
    base.update(kw)
    return base


class TheComparisonIsFieldByField(unittest.TestCase):
    """`compare` needs no database: it is two dicts in and a list out."""

    def db_row(self, rec, **overrides):
        row = {name: read(rec) for name, read in verify_parity.FIELDS.items()}
        row["__state__"] = rec.get("state")
        row.update(overrides)
        return row

    def test_agreement_is_an_empty_list(self):
        rec = record("a")
        self.assertEqual(
            verify_parity.compare({"a": rec}, {"a": self.db_row(rec)}), [])

    def test_a_record_only_in_the_file_is_a_divergence(self):
        """Not a filter. The intersection is where 4,779 records hid."""
        found = verify_parity.compare({"a": record("a")}, {})
        self.assertEqual([f["kind"] for f in found], ["only_in_file"])

    def test_a_record_only_in_postgres_is_a_divergence(self):
        rec = record("a")
        found = verify_parity.compare({}, {"a": self.db_row(rec)})
        self.assertEqual([f["kind"] for f in found], ["only_in_postgres"])

    def test_a_changed_field_is_named_with_both_values(self):
        """A report saying only "they differ" sends whoever reads it back to
        the database to find out how."""
        rec = record("a")
        found = verify_parity.compare(
            {"a": rec}, {"a": self.db_row(rec, title="Something else")})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["field"], "title")
        self.assertEqual(found[0]["file"], "PM")
        self.assertEqual(found[0]["postgres"], "Something else")

    def test_state_is_compared_through_the_one_inverse(self):
        rec = record("a", state="watched")
        found = verify_parity.compare(
            {"a": rec}, {"a": self.db_row(rec, __state__="new")})
        self.assertEqual([f["field"] for f in found], ["state"])

    def test_every_compared_field_has_a_file_side_expression(self):
        """A field in the SQL and not in FIELDS would be read as None and
        compared against None, agreeing forever."""
        rec = record("a")
        for name, read in verify_parity.FIELDS.items():
            with self.subTest(field=name):
                self.assertTrue(callable(read))
                read(rec)


class TheRunCannotConcludeWithoutChecking(pgtest.SchemaCase):
    prefix = "parity"

    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('p@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def write_pool(self, records):
        (self.root / "jobs.json").write_text(json.dumps(records))

    def mirror(self, records):
        dual_write.mirror_pool(self.conn, self.account, {}, records)

    def test_a_matching_pair_is_clean(self):
        records = {"a": record("a"), "b": record("b", state="watched")}
        self.write_pool(records)
        self.mirror(records)
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertEqual(report["divergences"], [])
        self.assertTrue(report["checked"])

    def test_a_record_missing_from_postgres_is_reported(self):
        records = {"a": record("a"), "b": record("b")}
        self.mirror({"a": records["a"]})
        self.write_pool(records)
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertEqual([f["kind"] for f in report["divergences"]],
                         ["only_in_file"])

    def test_a_record_missing_from_the_file_is_reported(self):
        records = {"a": record("a"), "b": record("b")}
        self.mirror(records)
        self.write_pool({"a": records["a"]})
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertEqual([f["kind"] for f in report["divergences"]],
                         ["only_in_postgres"])

    def test_a_state_that_drifted_is_reported(self):
        records = {"a": record("a")}
        self.mirror(records)
        self.write_pool({"a": record("a", state="watched")})
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertEqual([f["field"] for f in report["divergences"]], ["state"])

    def test_an_unreadable_pool_is_not_an_empty_one(self):
        """The `json.loads(stdout or "[]")` shape at the parity layer: an
        outage would otherwise read as an empty file store and every record in
        Postgres would be reported as only_in_postgres."""
        self.mirror({"a": record("a")})
        (self.root / "jobs.json").write_text("{not json")
        with self.assertRaises(dual_write.DualWriteError):
            verify_parity.run(self.conn, self.account, str(self.root))

    def test_a_missing_pool_raises_rather_than_reporting_everything(self):
        self.mirror({"a": record("a")})
        with self.assertRaises(dual_write.DualWriteError):
            verify_parity.run(self.conn, self.account, str(self.root))


class ADivergenceIsNeverSuppressedAsARace(pgtest.SchemaCase):
    """Three apply slots write concurrently, so mid-write is real. It is also
    the reasoning that once published an empty Submitted tab over a good one,
    so the rule is narrow: re-read once, report what persists, record what
    cleared, drop nothing."""

    prefix = "parityrace"

    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('r@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = self.root / "jobs.json"

    def test_a_persistent_divergence_is_reported(self):
        dual_write.mirror_pool(self.conn, self.account, {}, {"a": record("a")})
        self.path.write_text(json.dumps({"a": record("a", state="watched")}))
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertTrue(report["reread"], "it did not re-read")
        self.assertEqual(len(report["divergences"]), 1)
        self.assertEqual(report["transient"], [])

    def test_a_divergence_that_clears_is_recorded_not_dropped(self):
        """The file is repaired between the two readings, which is what a
        concurrent write looks like from here. The run goes green, and the
        event is still in the report with both mtimes."""
        dual_write.mirror_pool(self.conn, self.account, {}, {"a": record("a")})
        self.path.write_text(json.dumps({"a": record("a", state="watched")}))

        original = verify_parity.run_once
        calls = []

        def repair_after_first(conn, account_id, root=None):
            result = original(conn, account_id, root)
            calls.append(1)
            if len(calls) == 1:
                self.path.write_text(json.dumps({"a": record("a")}))
            return result

        verify_parity.run_once = repair_after_first
        self.addCleanup(setattr, verify_parity, "run_once", original)

        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertEqual(report["divergences"], [])
        self.assertEqual(len(report["transient"]), 1)
        self.assertTrue(report["transient"][0]["cleared_on_reread"])
        self.assertIsNotNone(report["transient"][0]["first_mtime"])
        self.assertIsNotNone(report["transient"][0]["second_mtime"])

    def test_a_difference_only_the_second_reading_sees_is_still_reported(self):
        """The other direction. A write landing between the readings can
        create a divergence as easily as clear one, and one the first reading
        missed is exactly as real."""
        dual_write.mirror_pool(self.conn, self.account, {}, {"a": record("a")})
        self.path.write_text(json.dumps({"a": record("a"), "b": record("b")}))

        original = verify_parity.run_once
        calls = []

        def diverge_more(conn, account_id, root=None):
            result = original(conn, account_id, root)
            calls.append(1)
            if len(calls) == 1:
                self.path.write_text(json.dumps(
                    {"a": record("a"), "b": record("b"), "c": record("c")}))
            return result

        verify_parity.run_once = diverge_more
        self.addCleanup(setattr, verify_parity, "run_once", original)

        report = verify_parity.run(self.conn, self.account, str(self.root))
        ids = {f["id"] for f in report["divergences"]}
        self.assertIn("c", ids, "a divergence only the second reading saw was "
                                "dropped")

    def test_it_does_not_re_read_when_the_first_reading_is_clean(self):
        """The re-read is for divergences. Doing it always would double the
        cost of every green run for nothing."""
        records = {"a": record("a")}
        dual_write.mirror_pool(self.conn, self.account, {}, records)
        self.path.write_text(json.dumps(records))
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertFalse(report["reread"])


class AMergedRecordIsFoundRatherThanExempted(pgtest.SchemaCase):
    """Two legacy records sharing a board key become one posting, so the loser
    has no row of its own.

    That left parity with a permanent divergence: after the first full resync
    against live data it was the only difference remaining, 1 of 16,123. The
    fix is `postings.merged_legacy_ids`, which makes the loser findable, and
    not an exemption in the comparison, because a filter is where the 4,779
    missing records hid the first time.
    """

    prefix = "paritymerge"

    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('m@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def colliding(self):
        """Two records for one Greenhouse posting, reached two ways. This is
        the real pair's shape."""
        return {
            "aaa": record("aaa",
                          canonicalUrl="https://boards.greenhouse.io/braze/jobs/8139241"),
            "zzz": record("zzz",
                          canonicalUrl="https://job-boards.greenhouse.io/braze/jobs/8139241",
                          descFingerprint="simhash64:different"),
        }

    def test_the_folded_record_is_not_reported_as_missing(self):
        records = self.colliding()
        dual_write.mirror_pool(self.conn, self.account, {}, records)
        (self.root / "jobs.json").write_text(json.dumps(records))
        report = verify_parity.run(self.conn, self.account, str(self.root))
        kinds = {f["kind"] for f in report["divergences"]}
        self.assertNotIn("only_in_file", kinds)

    def test_its_discarded_fields_are_recorded_not_hidden(self):
        """Visible and counted, in their own bucket. The merge threw those
        values away deliberately and that has to stay legible."""
        records = self.colliding()
        dual_write.mirror_pool(self.conn, self.account, {}, records)
        (self.root / "jobs.json").write_text(json.dumps(records))
        report = verify_parity.run(self.conn, self.account, str(self.root))
        dropped = report["merged_field_dropped"]
        self.assertTrue(dropped, "the discarded fields vanished from the report")
        for finding in dropped:
            self.assertEqual(finding["id"], "zzz")
            self.assertEqual(finding["merged_into"], "aaa")
            self.assertIn("file", finding)
            self.assertIn("postgres", finding)

    def test_discarded_fields_do_not_make_the_run_red(self):
        records = self.colliding()
        dual_write.mirror_pool(self.conn, self.account, {}, records)
        (self.root / "jobs.json").write_text(json.dumps(records))
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertEqual(report["divergences"], [])

    def test_a_clean_run_with_merges_does_not_re_read(self):
        """Splitting the drops out before the re-read matters: otherwise a
        corpus with merges pays a second full comparison on every green run."""
        records = self.colliding()
        dual_write.mirror_pool(self.conn, self.account, {}, records)
        (self.root / "jobs.json").write_text(json.dumps(records))
        report = verify_parity.run(self.conn, self.account, str(self.root))
        self.assertFalse(report["reread"])

    def test_a_state_disagreement_between_merged_records_is_still_red(self):
        """The surviving row holds one state. If the two legacy records
        disagree about it, somebody has to be told: this is the one field a
        merge cannot quietly discard."""
        records = self.colliding()
        dual_write.mirror_pool(self.conn, self.account, {}, records)
        records["zzz"]["state"] = "watched"
        (self.root / "jobs.json").write_text(json.dumps(records))
        report = verify_parity.run(self.conn, self.account, str(self.root))
        fields = [f.get("field") for f in report["divergences"]]
        self.assertIn("state", fields)


class TheExitCodeCannotLie(unittest.TestCase):
    def test_dual_write_being_off_is_not_success(self):
        """A run that compared nothing must never exit 0. This is the way that
        happens with nothing looking broken."""
        original = dual_write.enabled
        dual_write.enabled = lambda *a, **k: False
        self.addCleanup(setattr, dual_write, "enabled", original)
        self.assertEqual(verify_parity.main([]), 1)

    def test_the_module_is_executable(self):
        """It runs from cron and from CI; an import-time error there is a
        silent no-op."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "verify_parity.py"), "--help"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TheScheduledRunLogsWhatHappened(unittest.TestCase):
    """The seven-day clock is counted from these lines, so a line that says
    `ok` when the run did not conclude is the clock measuring silence."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import parity_log
        self.summarise = parity_log.summarise

    def test_a_clean_run_is_ok(self):
        raw = json.dumps({"divergences": [], "transient": [],
                          "merged_field_dropped": []})
        line = self.summarise(raw, 0)
        self.assertTrue(line["ok"])
        self.assertEqual(line["divergences"], 0)

    def test_a_run_with_divergences_is_not_ok(self):
        raw = json.dumps({"divergences": [{"kind": "only_in_file", "id": "a"}],
                          "transient": [], "merged_field_dropped": []})
        self.assertFalse(self.summarise(raw, 1)["ok"])

    def test_divergences_with_a_zero_exit_are_still_not_ok(self):
        """Belt and braces on the one thing that would corrupt the clock: if
        the exit code ever disagreed with the report, believe the report."""
        raw = json.dumps({"divergences": [{"kind": "only_in_file", "id": "a"}],
                          "transient": [], "merged_field_dropped": []})
        self.assertFalse(self.summarise(raw, 0)["ok"])

    def test_output_that_is_not_json_is_inconclusive_with_its_reason(self):
        """The first version of the cron script fed the report into a heredoc,
        which discards it, so every run logged as inconclusive with an empty
        reason and looked like a real outage."""
        line = self.summarise("Traceback: something broke", 1)
        self.assertFalse(line["ok"])
        self.assertTrue(line["inconclusive"])
        self.assertIn("something broke", line["reason"])

    def test_no_output_at_all_is_inconclusive_and_says_so(self):
        line = self.summarise("", 1)
        self.assertFalse(line["ok"])
        self.assertEqual(line["reason"], "no output")

    def test_merged_field_drops_do_not_make_a_run_unclean(self):
        raw = json.dumps({"divergences": [], "transient": [],
                          "merged_field_dropped": [{"id": "z"}, {"id": "z"}]})
        line = self.summarise(raw, 0)
        self.assertTrue(line["ok"])
        self.assertEqual(line["mergedFieldDropped"], 2)


def _database_holds_the_device_store():
    """Is the configured database the one holding the store on this disk?

    `dual_write.enabled()` used to answer this by accident: it read config.env,
    so "a database is configured" and "the device's database" were the same
    sentence. Once `enabled()` and `connect()` were unified onto one resolver
    that prefers JOBBY_TEST_DATABASE_URL, they stopped being: with a local
    throwaway Postgres configured, `enabled()` is true and points somewhere
    that has never seen this store, and this test connected to it and failed on
    a missing `accounts` table.

    Two questions that were one. This asks the one that is actually meant.
    """
    import sys
    sys.path.insert(0, str(REPO / "packages" / "schema"))
    import migrate
    resolved = dual_write.resolved_url()
    device = migrate._device_url()
    if resolved is None and device:
        # The test-runner guard refuses the device URL to any fall-through, so
        # `resolved` is None here whenever nothing else is configured. That is
        # the guard working; this test is the documented exception to it, and
        # it takes the exception explicitly below rather than by being allowed
        # to fall through.
        return True
    return bool(resolved and device and migrate.same_database(resolved, device))


def _device_connection():
    """A connection to the device's own database, named explicitly.

    **The one sanctioned way past the test-runner guard**, and the guard's own
    docstring says so: an operator naming a database is not a fall-through.
    This test is a live-corpus check -- it asserts a property of the real
    store's 15,400 postings, which no fixture can stand in for -- and
    `verify_parity.run` only reads.

    Any test reaching for this owes the same two things: a reason a fixture
    cannot answer the question, and a guarantee that it only reads.
    """
    import sys
    sys.path.insert(0, str(REPO / "packages" / "schema"))
    import migrate
    return migrate.connect(url=migrate.direct_url(migrate._device_url()),
                           announce=False)


class TheMergedFieldDropsAreExactlyTwoAndNamed(unittest.TestCase):
    """A number in a green report with no explanation is a number people stop
    reading, and an exemption nobody counts is one that grows.

    Both drops belong to one record: `9c9cf0baa815c1eb`, folded into
    `5f10861ba0ce8980`, two stored records for one Braze posting reached
    through `boards.` and `job-boards.`. Its URL differs because the two
    hostnames are why there were ever two records, and its fingerprint differs
    because the pages were fetched separately and land 4 bits apart.

    A third drop means a new collision or a new differing field. Either is
    worth a look rather than a larger total, so this fails on any other number
    and `docs/parity.md` names the two it allows.
    """

    EXPECTED = {"apply_url", "desc_fingerprint"}

    def test_the_document_names_both_fields(self):
        doc = (REPO / "docs" / "parity.md").read_text(encoding="utf-8")
        for field in self.EXPECTED:
            with self.subTest(field=field):
                self.assertIn(field, doc)

    def test_the_document_names_the_record_and_its_winner(self):
        doc = (REPO / "docs" / "parity.md").read_text(encoding="utf-8")
        self.assertIn("9c9cf0baa815c1eb", doc)
        self.assertIn("5f10861ba0ce8980", doc)

    @unittest.skipUnless(
        Path("~/.job-application/jobs.json").expanduser().exists()
        and _database_holds_the_device_store(),
        "needs the real store and the database that holds it")
    def test_the_live_run_drops_exactly_these_two(self):
        conn = _device_connection()
        try:
            report = verify_parity.run(conn, dual_write.account_id(conn))
        finally:
            conn.close()
        dropped = report["merged_field_dropped"]
        self.assertEqual(
            len(dropped), 2,
            f"{len(dropped)} merged-field drops, expected 2. A third is a new "
            f"collision or a newly differing field, and both want looking at: "
            f"{[(d['id'], d['field']) for d in dropped]}")
        self.assertEqual({d["field"] for d in dropped}, self.EXPECTED)
        self.assertEqual({d["id"] for d in dropped}, {"9c9cf0baa815c1eb"})


class TheScheduledTimerCanActuallyRun(unittest.TestCase):
    """launchd would not execute the shell version, and said so where nobody
    was looking.

        /bin/sh: .../scripts/parity_cron.sh: Operation not permitted

    exit 126, twice, while `parity.jsonl` stayed empty. An empty parity log and
    a timer that has not fired yet are the same observation, so the failure
    looked like waiting. The cause is the macOS Privacy control CLAUDE.md
    already records for the launchd installer: a LaunchAgent may not exec a file
    under ~/Documents. The three existing services run the interpreter with the
    script as an argument, so the binary executed is one that holds the grant.
    """

    def setUp(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import install_parity_timer
        self.installer = install_parity_timer

    def test_the_plist_runs_an_interpreter_not_the_script(self):
        body = self.installer.plist_body("/opt/anaconda3/bin/python3")
        argv = body["ProgramArguments"]
        self.assertEqual(argv[0], "/opt/anaconda3/bin/python3")
        self.assertTrue(argv[1].endswith(".py"),
                        f"launchd is asked to exec {argv[1]}, which it may not "
                        f"do for a file under ~/Documents")

    def test_it_does_not_point_at_a_shell_script(self):
        body = self.installer.plist_body("/usr/bin/python3")
        for entry in body["ProgramArguments"]:
            with self.subTest(entry=entry):
                self.assertFalse(entry.endswith(".sh"))

    def test_the_shell_version_is_gone_rather_than_left_beside_it(self):
        """A script that cannot run on the only machine it targets is a trap
        for whoever finds it next."""
        self.assertFalse((REPO / "scripts" / "parity_cron.sh").exists())

    def test_the_entrypoint_it_names_exists(self):
        body = self.installer.plist_body(sys.executable)
        self.assertTrue(Path(body["ProgramArguments"][1]).exists())

    def test_a_crash_still_writes_a_line(self):
        """Silence is the failure mode here: an empty log and a timer that has
        not fired look identical."""
        sys.path.insert(0, str(REPO / "scripts"))
        import parity_log
        line = parity_log.summarise("RuntimeError: exploded", 1)
        self.assertFalse(line["ok"])
        self.assertIn("exploded", line["reason"])


if __name__ == "__main__":
    unittest.main()
