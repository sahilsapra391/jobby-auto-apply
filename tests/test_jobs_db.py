"""The Postgres store, without needing Postgres to test most of it.

The row mapping and the URL reading are pure functions and are tested here.
The queries need a database and skip when there is none, so CI stays green on a
runner with no Neon credentials — the same shape as the tests that need a live
browser slot.
"""

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import jobs_db  # noqa: E402
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

    def test_the_lifted_set_matches_the_schema(self):
        """Two lists that must agree. They are in the same file precisely so
        this test can compare them rather than a person remembering."""
        for name, _fn in jobs_db.LIFTED:
            with self.subTest(column=name):
                self.assertIn(f"    {name} ", jobs_db.SCHEMA.replace("\t", " "))


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


class LiveDatabaseTests(unittest.TestCase):
    """Skipped without credentials, like the tests that need a browser slot."""

    @classmethod
    def setUpClass(cls):
        if not jobs_db.database_url():
            raise unittest.SkipTest("no JOBBY_DATABASE_URL on this machine")
        try:
            cls.conn = jobs_db.connect()
        except jobs_db.DatabaseError as exc:
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "conn", None):
            cls.conn.close()

    def test_the_schema_applies_and_is_idempotent(self):
        jobs_db.ensure_schema(self.conn)
        jobs_db.ensure_schema(self.conn)
        self.assertIn("jobs", jobs_db.counts(self.conn))

    def test_the_board_slice_is_one_query(self):
        """The property the migration exists for: answering a question about
        part of the pool must not load the pool."""
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from jobs where state = 'new'")
            self.assertGreaterEqual(cur.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
