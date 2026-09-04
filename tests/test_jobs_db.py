"""The Postgres store, without needing Postgres to test most of it.

The row mapping and the URL reading are pure functions and are tested here.
The queries need a database and skip when there is none, so CI stays green on a
runner with no Neon credentials — the same shape as the tests that need a live
browser slot.
"""

import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import jobs_db  # noqa: E402
import pgtest  # noqa: E402
import jobs_store  # noqa: E402


def job(**over):
    base = dict(jobs_store.blank_job("abc123", "Northwind Analytics",
                                     "Senior Product Manager"),
                state="new",
                canonicalUrl="https://job-boards.greenhouse.io/northwind/jobs/1",
                canonicalSource="greenhouse",
                postedAt="2026-08-20T10:00:00Z",
                workplace="hybrid",
                location={"raw": "New York, NY", "country": "US"},
                score={"total": 72},
                sources=[{"source": "greenhouse", "sourceClass": "ats",
                          "url": "https://job-boards.greenhouse.io/northwind/jobs/1"}])
    base.update(over)
    return base


class RowMappingTests(unittest.TestCase):
    """What gets lifted out of the record into real columns."""

    def columns(self, record):
        row = jobs_db._row("abc123", record)
        names = ["id"] + [n for n, _ in jobs_db.LIFTED] + ["record"]
        return dict(zip(names, row))

    def test_the_filterable_fields_become_columns(self):
        got = self.columns(job())
        self.assertEqual(got["company"], "Northwind Analytics")
        self.assertEqual(got["state"], "new")
        self.assertEqual(got["canonical_source"], "greenhouse")
        self.assertEqual(got["country"], "US")
        self.assertEqual(got["workplace"], "hybrid")
        self.assertEqual(got["score"], 72)
        self.assertEqual(got["source_class"], "ats")
        self.assertIsNotNone(got["posted_at"])

    def test_the_whole_record_still_travels(self):
        """The columns are an index over the record, not a replacement for it.
        A field gaining meaning later must not need a migration to survive."""
        record = job(someFutureField={"nested": [1, 2, 3]})
        stored = json.loads(self.columns(record)["record"])
        self.assertEqual(stored["someFutureField"], {"nested": [1, 2, 3]})
        self.assertEqual(stored, record)

    def test_a_record_missing_everything_optional_still_maps(self):
        got = self.columns({"id": "x", "company": "Acme", "state": "new"})
        self.assertIsNone(got["posted_at"])
        self.assertIsNone(got["score"])
        self.assertIsNone(got["country"])
        self.assertEqual(got["state"], "new")

    def test_state_defaults_rather_than_writing_null(self):
        self.assertEqual(self.columns({"id": "x"})["state"], "new")

    def test_company_normalisation_is_stable_across_punctuation(self):
        a = self.columns(job(company="Northwind Analytics"))["norm_company"]
        b = self.columns(job(company="northwind  analytics!"))["norm_company"]
        self.assertEqual(a, b)

    def test_a_bad_score_does_not_become_a_column_value(self):
        self.assertIsNone(self.columns(job(score={"total": "high"}))["score"])
        self.assertIsNone(self.columns(job(score=None))["score"])

    def test_the_lifted_set_matches_the_jobs_table(self):
        """Two lists that must agree, compared against the `jobs` block only.

        Matching the column name anywhere in SCHEMA passed for `company`,
        `role`, `url` and `name`, which are also declared on sessions and
        roles: deleting `company` from the jobs DDL left this green while
        replace_jobs failed at runtime.
        """
        block = jobs_db.SCHEMA.split("create table if not exists jobs (", 1)[1]
        block = block.split(");", 1)[0]
        declared = {line.strip().split()[0]
                    for line in block.splitlines() if line.strip()}
        for name, _fn in jobs_db.LIFTED:
            with self.subTest(column=name):
                self.assertIn(name, declared)
        self.assertIn("record", declared)
        self.assertIn("id", declared)


class ConnectionConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = pathlib.Path(self.tmp.name) / "config.env"

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_reads_the_url_from_the_config_file(self):
        self.cfg.write_text("HEALTHCHECKS_URL=x\n"
                            "JOBBY_DATABASE_URL=postgresql://u:p@h/db\n")
        self.assertEqual(jobs_db.database_url(self.cfg), "postgresql://u:p@h/db")

    def test_a_store_with_no_url_reads_as_none_rather_than_raising(self):
        """The file-backed store is still a valid configuration, so absence is
        an answer and not a fault."""
        self.cfg.write_text("HEALTHCHECKS_URL=x\n")
        self.assertIsNone(jobs_db.database_url(self.cfg))
        self.assertIsNone(jobs_db.database_url(self.cfg.parent / "nothing.env"))

    def test_comments_and_blank_lines_are_ignored(self):
        self.cfg.write_text("# JOBBY_DATABASE_URL=postgresql://commented/out\n\n")
        self.assertIsNone(jobs_db.database_url(self.cfg))

    def test_connecting_without_a_url_says_how_to_set_one(self):
        """The error has to name the command, because the person who hits it
        is configuring the machine and has no reason to know it exists."""
        original = jobs_db.database_url
        jobs_db.database_url = lambda *a, **k: None
        try:
            with self.assertRaises(jobs_db.DatabaseError) as caught:
                jobs_db.connect()
        finally:
            jobs_db.database_url = original
        self.assertIn("set-database", str(caught.exception))
        self.assertIn(jobs_db.URL_NAME, str(caught.exception))


class LiveDatabaseTests(pgtest.SchemaCase):
    """Runs only on an explicit opt-in, and only inside its own schema.

    The first version skipped when JOBBY_DATABASE_URL was absent, which is
    backwards: it skipped exactly when the database was safe to touch and ran
    when it was not. `jobs_db.CONFIG` is bound at import from the real home, so
    JOB_APPLICATION_DIR cannot redirect it, and the repo's own verification
    command uses no HOME redirect. The result was CREATE TABLE against the
    user's production Neon on every full suite run, and CI runs this module.

    Requiring JOBBY_TEST_DATABASE_URL fixed the accident and left the hazard:
    it moved the guarantee into an instruction, "point this at a throwaway
    database", which is the kind of rule this repository has watched fail
    every time it has relied on one. It failed again on 2026-08-29. The agent
    adding the Phase 0 migration tooling set that variable to the Neon URL, ran
    the full suite, and executed this module's CREATE TABLE against the
    database holding 15,400 real jobs. Nothing was lost, because the DDL is
    idempotent, which is precisely what the note below it said was not the
    point.

    So the isolation is structural now. `pgtest.SchemaCase` gives each test its
    own schema on a direct (never pooled) session and drops it afterwards, so
    where the variable points stops deciding whether the run is safe. On a
    machine with one database, that is the only version of this guarantee that
    is worth anything.
    """

    prefix = "jobsdb_test"

    def test_the_scratch_schema_holds_the_work(self):
        """First, because every assertion below is conditional on it."""
        self.assert_isolated()

    def test_the_schema_applies_and_is_idempotent(self):
        jobs_db.ensure_schema(self.conn)
        jobs_db.ensure_schema(self.conn)
        self.assertIn("jobs", jobs_db.counts(self.conn))

    def test_the_board_slice_is_one_query(self):
        """The property the migration exists for: answering a question about
        part of the pool must not load the pool.

        The assertion used to be `>= 0`, which is true of every integer this
        query can return, and it ran against whatever `jobs` the connection
        happened to see. On this machine that was the real table with 15,400
        rows, so it read production to prove nothing. Now the schema is the
        test's own, and the count is checked against rows the test put there.
        """
        jobs_db.ensure_schema(self.conn)
        jobs_db.replace_jobs(self.conn, {
            "a": {"company": "Acme", "title": "PM", "state": "new"},
            "b": {"company": "Acme", "title": "PM 2", "state": "dropped"},
        })
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from jobs where state = 'new'")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("select count(*) from jobs")
            self.assertEqual(cur.fetchone()[0], 2)
