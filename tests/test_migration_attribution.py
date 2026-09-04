"""Who applied a migration, and how a lost one is repaired.

**The advisory lock worked. Attribution is what failed.** On 2026-09-02 two
migrations landed on the production database three seconds apart from a session
nobody could name, and one of them was a file that reached no branch. The lock
had serialised the writes correctly and neither was lost; what could not be
answered afterwards was whose they were, because `applied_by` defaults to
`current_user` and every runner on this project connects as the same owner
role. The column recorded the same eight characters for all eight migrations.

So exclusion was never the gap. Two sessions writing a schema is fine as long
as the second can see the first, and `schema_migrations` is the place that
record already lives.
"""

import os
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import store_isolation  # noqa: E402

import migrate  # noqa: E402


def setUpModule():
    store_isolation.begin(migrate)


def tearDownModule():
    store_isolation.end()


class AnActorIsIdentifiable(unittest.TestCase):
    def test_the_env_var_wins_when_a_runner_knows_its_own_name(self):
        with mock.patch.dict(os.environ, {"JOBBY_ACTOR": "session-42"}):
            self.assertEqual(migrate.actor(), "session-42")

    def test_otherwise_it_separates_two_processes_on_one_machine(self):
        with mock.patch.dict(os.environ, {"JOBBY_ACTOR": ""}):
            got = migrate.actor()
        self.assertIn("@", got)
        self.assertIn(str(os.getpid()), got,
                      "two agents on this Mac connect as the same database "
                      "role, so the pid is what tells them apart")

    def test_it_is_bounded(self):
        with mock.patch.dict(os.environ, {"JOBBY_ACTOR": "x" * 500}):
            self.assertLessEqual(len(migrate.actor()), 200)

    def test_it_is_never_the_database_role(self):
        """The control. `current_user` is what this replaces."""
        with mock.patch.dict(os.environ, {"JOBBY_ACTOR": ""}):
            self.assertNotIn("current_user", migrate.actor())

    def test_the_insert_records_it(self):
        source = (REPO / "packages" / "schema" / "migrate.py").read_text()
        self.assertIn("(version, name, checksum, applied_by)", source,
                      "a migration that does not stamp an actor leaves the "
                      "column at its default, which is the owner role and "
                      "identical for every session")


class ReconcileRepairsALostFile(unittest.TestCase):
    """The one thing here that edits history, and what it costs to use."""

    class FakeCursor:
        def __init__(self, outer):
            self.outer = outer

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, args=()):
            self.outer.executed.append((" ".join(sql.split()), args))
            self.sql = sql

        def fetchone(self):
            return self.outer.row

    class FakeConn:
        def __init__(self, row):
            self.row = row
            self.executed = []

        def cursor(self):
            return ReconcileRepairsALostFile.FakeCursor(self)

    def migration(self, sql="reconstructed"):
        """The checksum is derived from the SQL, so the fixture supplies SQL."""
        return migrate.Migration(version=8, name="0008_x.sql",
                                 path=pathlib.Path("0008_x.sql"), sql=sql)

    def test_it_refuses_without_a_reason(self):
        conn = self.FakeConn(("0008_x.sql", "old-sum", "neondb_owner"))
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.reconcile(conn, 8, "  ", [self.migration()])
        self.assertIn("--reason", str(caught.exception))
        self.assertEqual(conn.executed, [],
                         "a refusal must not have written anything first")

    def test_it_refuses_when_nothing_is_recorded(self):
        conn = self.FakeConn(None)
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.reconcile(conn, 8, "because", [self.migration()])
        self.assertIn("not recorded as applied", str(caught.exception))

    def test_it_refuses_with_no_file_to_reconcile_against(self):
        conn = self.FakeConn(("0008_x.sql", "old-sum", "who"))
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.reconcile(conn, 8, "because", [])
        self.assertIn("nothing to reconcile", str(caught.exception).lower()
                      .replace("no migration 0008 on disk to reconcile against",
                               "nothing to reconcile"))

    def test_it_does_nothing_when_the_checksum_already_agrees(self):
        conn = self.FakeConn(None)
        agreeing = self.migration()
        conn.row = ("0008_x.sql", agreeing.checksum, "who")
        out = migrate.reconcile(conn, 8, "because", [agreeing])
        self.assertFalse(out["changed"])
        self.assertEqual([e for e in conn.executed if "update" in e[0]], [],
                         "an agreeing checksum must not be rewritten, or every "
                         "run restamps the record and the trail is noise")

    def test_it_records_the_reason_and_the_actor(self):
        conn = self.FakeConn(("0008_x.sql", "old-sum", "neondb_owner"))
        with mock.patch.dict(os.environ, {"JOBBY_ACTOR": "session-42"}):
            out = migrate.reconcile(conn, 8, "the original reached no branch",
                                    [self.migration()])
        self.assertTrue(out["changed"])
        self.assertEqual(out["was"], "old-sum")
        self.assertEqual(out["previously_by"], "neondb_owner")
        update = [e for e in conn.executed if e[0].startswith("update")]
        self.assertEqual(len(update), 1)
        stamped = update[0][1][2]
        self.assertIn("session-42", stamped)
        self.assertIn("reconciled", stamped)
        self.assertIn("the original reached no branch", stamped,
                      "the record has to say the checksum was reconciled, or "
                      "it reads as though it was always this way")

    def test_the_stamp_is_bounded(self):
        conn = self.FakeConn(("0008_x.sql", "old-sum", "who"))
        migrate.reconcile(conn, 8, "y" * 500, [self.migration()])
        update = [e for e in conn.executed if e[0].startswith("update")][0]
        self.assertLessEqual(len(update[1][2]), 200)


class TheReconstructedMigrationSaysSo(unittest.TestCase):
    """0008 is a dump of the live schema, not the text that was applied."""

    PATH = (REPO / "packages" / "schema" / "migrations"
            / "0008_application_records.sql")

    def test_it_exists_so_the_schema_is_reproducible(self):
        self.assertTrue(self.PATH.exists(),
                        "a recorded migration with no file refuses every "
                        "later one, which is how production was blocked")

    def test_it_declares_itself_reconstructed(self):
        text = self.PATH.read_text()
        self.assertIn("RECONSTRUCTED", text)
        for owed in ("checksum will not match", "comments are gone",
                     "diff it against this file"):
            self.assertIn(owed, text,
                          "the file has to state what it cost, or the next "
                          "reader treats a dump as the author's intent")

    def test_it_creates_the_table_production_actually_has(self):
        text = self.PATH.read_text()
        self.assertIn("create table if not exists board_meta", text)
        for column in ("last_ingest", "next_scan_at", "scan_requested_at",
                       "scan_started_at", "scan_finished_at", "blocked",
                       "slots_parked", "search_config"):
            self.assertIn(column, text)
