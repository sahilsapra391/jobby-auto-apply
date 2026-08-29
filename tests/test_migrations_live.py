"""The migration runner against a real Postgres.

These need a database, named by `JOBBY_TEST_DATABASE_URL`. CI supplies one as a
service container, so the skip below never fires there -- and
`tests/test_ci_workflow.py` asserts that, because a suite that reports "ok"
while every database test skipped is the failure this repo keeps rediscovering.

Every test runs inside its own `mig_test_<hex>` schema, created in setUp and
dropped in tearDown. Nothing here can reach `public`, which matters because the
one database available on the developer's machine is the live Neon one holding
15,400 real jobs.

The tests are ordered failure-first. A migration runner that applies things is
easy; one that is honest about what it did not apply is the whole point. Every
guard below has a corresponding way of being silently wrong:

  partial apply      -> half a schema and a row saying it succeeded
  re-apply           -> "already exists" on the second deploy of the same commit
  checksum drift     -> the database and the repo disagree and nothing says so
  missing history    -> a deleted migration file reads as never having existed
  out of order       -> a branch's 0004 lands before main's 0003
  concurrent runners -> two deploys, one crash, one half-applied schema
"""

import sys
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import migrate  # noqa: E402
import pgtest  # noqa: E402



class LiveMigrationTests(pgtest.SchemaCase):
    prefix = "mig_test"

    def setUp(self):
        import shutil, tempfile                          # noqa: E401
        super().setUp()
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_the_isolation_this_module_depends_on_actually_holds(self):
        """Everything else here asserts behaviour *given* that the scratch
        schema contains the work. This asserts the schema does.

        It is first because it is the test that was missing: without it the
        module passed completely while writing into `public` on the live
        database.
        """
        self.assert_isolated()

    # helpers -------------------------------------------------------------

    def write(self, name, sql):
        (self.dir / name).write_text(sql, encoding="utf-8")

    def recorded(self):
        return sorted(migrate.applied(self.conn).keys())

    def fresh_conn(self):
        c = pgtest.connect_to(self.schema)
        self.addCleanup(c.close)
        return c

    # failure first -------------------------------------------------------

    def test_a_migration_that_fails_leaves_nothing_behind(self):
        """The first statement is valid and the second is not. Neither the
        table nor the ledger row may survive.

        This one is nearly free, and it is worth being honest about why rather
        than claiming it as evidence for the runner's transaction. Postgres
        wraps a multi-statement simple query in an *implicit* transaction, so
        these two statements roll back together whatever the runner does. A
        mutation run replacing `conn.transaction()` with a null context left
        this test passing. What the explicit transaction actually protects is
        the test below.
        """
        self.write("0001_half.sql",
                   "create table half_applied (id int);\n"
                   "create table half_applied (id int);\n")
        with self.assertRaises(Exception):
            migrate.run(self.conn, self.dir)
        self.assertNotIn("half_applied", self.tables())
        self.assertEqual(self.recorded(), [])

    def test_the_ddl_and_its_ledger_row_commit_together_or_not_at_all(self):
        """The property the runner's own transaction exists for.

        The window is between a migration's SQL succeeding and its ledger row
        being written. A runner that commits the schema change and then records
        it leaves, on a crash or an error in between, a migration that is
        applied and unrecorded. The next deploy sees an unknown version, runs
        it again, and dies on "relation already exists" partway through
        somebody's release.

        Forcing that window open needs a migration whose own last act makes the
        runner's insert fail, which is contrived on purpose: the natural
        failures all happen inside the migration and are covered by Postgres's
        implicit transaction. Here the migration takes version 1 in the ledger
        for itself, so the runner's insert hits the primary key.

        With the transaction, `keep_out` never exists. Without it, the
        migration's own statements commit first and it does.
        """
        self.write("0001_steals_the_row.sql",
                   "create table keep_out (id int);\n"
                   "insert into schema_migrations (version, name, checksum)\n"
                   "  values (1, 'not_the_runners_row', 'deadbeef');\n")
        # The ledger has to exist before the migration can insert into it,
        # which is what `applied` does on any first call.
        self.assertEqual(migrate.applied(self.conn), {})

        with self.assertRaises(Exception):
            migrate.run(self.conn, self.dir)

        self.assertNotIn("keep_out", self.tables(),
                         "the DDL committed before its ledger row did")
        self.assertEqual(self.recorded(), [],
                         "a half-applied migration left a row behind")

    def test_a_failed_migration_does_not_block_a_fixed_one(self):
        self.write("0001_half.sql", "create table t1 (id int);\nbogus sql;\n")
        with self.assertRaises(Exception):
            migrate.run(self.conn, self.dir)
        self.write("0001_half.sql", "create table t1 (id int);\n")
        migrate.run(self.conn, self.dir)
        self.assertIn("t1", self.tables())
        self.assertEqual(self.recorded(), [1])

    def test_a_destructive_migration_applies_nothing_at_all(self):
        """The refusal has to happen before the first statement of the first
        migration, not when the runner reaches the bad one. Otherwise a run
        that is going to be refused still half-changes the schema."""
        self.write("0001_ok.sql", "create table keep_me (id int);")
        self.write("0002_bad.sql", "drop table keep_me;")
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.conn, self.dir)
        self.assertNotIn("keep_me", self.tables())
        self.assertEqual(self.recorded(), [])

    def test_checksum_drift_on_an_applied_migration_is_refused(self):
        self.write("0001_a.sql", "create table a (id int);")
        migrate.run(self.conn, self.dir)
        self.write("0001_a.sql", "create table a (id bigint);")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.conn, self.dir)
        self.assertIn("0001", str(caught.exception))
        self.assertIn("changed", str(caught.exception).lower())

    def test_drift_is_refused_before_a_later_migration_is_applied(self):
        """Editing history and adding a migration in the same commit must not
        result in the new one landing on a schema nobody can reproduce."""
        self.write("0001_a.sql", "create table a (id int);")
        migrate.run(self.conn, self.dir)
        self.write("0001_a.sql", "create table a (id bigint);")
        self.write("0002_b.sql", "create table b (id int);")
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.conn, self.dir)
        self.assertNotIn("b", self.tables())
        self.assertEqual(self.recorded(), [1])

    def test_a_recorded_migration_missing_from_disk_is_refused(self):
        """Deleting a migration file makes the database unreproducible from
        the repo. Silence here is how that is discovered a year later."""
        self.write("0001_a.sql", "create table a (id int);")
        self.write("0002_b.sql", "create table b (id int);")
        migrate.run(self.conn, self.dir)
        (self.dir / "0001_a.sql").unlink()
        self.write("0003_c.sql", "create table c (id int);")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.conn, self.dir)
        self.assertIn("0001", str(caught.exception))
        self.assertNotIn("c", self.tables())

    def test_a_migration_older_than_the_high_water_mark_is_refused(self):
        """Two branches, each adding "the next" migration. One merges, the
        other rebases badly, and 0002 arrives after 0003 has run. Applying it
        gives a schema no fresh database will ever reproduce."""
        self.write("0001_a.sql", "create table a (id int);")
        self.write("0003_c.sql", "create table c (id int);")
        migrate.run(self.conn, self.dir)
        self.write("0002_b.sql", "create table b (id int);")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.run(self.conn, self.dir)
        self.assertIn("0002", str(caught.exception))
        self.assertNotIn("b", self.tables())

    def test_two_runners_at_once_apply_each_migration_once(self):
        """Without the advisory lock both threads see an empty ledger, both
        run the DDL, and the loser dies on "relation already exists" partway
        through -- which is a half-applied deploy, not a clean failure."""
        self.write("0001_a.sql", "create table a (id int);")
        self.write("0002_b.sql", "create table b (id int);")
        errors = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                conn = pgtest.connect_to(self.schema)
                barrier.wait(timeout=30)
                migrate.run(conn, self.dir)
                conn.close()
            except Exception as exc:                     # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(errors, [], f"a concurrent runner failed: {errors}")
        self.assertEqual(self.recorded(), [1, 2])
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from schema_migrations")
            self.assertEqual(cur.fetchone()[0], 2)

    def test_the_lock_is_released_when_a_migration_fails(self):
        """A runner that dies holding the advisory lock wedges every later
        deploy until someone finds the session and kills it."""
        self.write("0001_bad.sql", "bogus sql;")
        with self.assertRaises(Exception):
            migrate.run(self.conn, self.dir)
        other = self.fresh_conn()
        self.write("0001_bad.sql", "create table fine (id int);")
        migrate.run(other, self.dir)                     # would block forever
        self.assertIn("fine", self.tables())

    # the happy path, last -------------------------------------------------

    def test_migrations_apply_in_order_and_are_recorded(self):
        self.write("0001_a.sql", "create table a (id int);")
        self.write("0002_b.sql", "create table b (id int);")
        done = migrate.run(self.conn, self.dir)
        self.assertEqual([m.version for m in done], [1, 2])
        self.assertEqual(self.recorded(), [1, 2])
        self.assertIn("a", self.tables())
        self.assertIn("b", self.tables())

    def test_a_second_run_applies_nothing(self):
        self.write("0001_a.sql", "create table a (id int);")
        migrate.run(self.conn, self.dir)
        self.assertEqual(migrate.run(self.conn, self.dir), [])

    def test_only_the_new_migration_runs_on_a_later_deploy(self):
        self.write("0001_a.sql", "create table a (id int);")
        migrate.run(self.conn, self.dir)
        self.write("0002_b.sql", "create table b (id int);")
        done = migrate.run(self.conn, self.dir)
        self.assertEqual([m.version for m in done], [2])

    def test_plan_reports_what_would_run_without_running_it(self):
        self.write("0001_a.sql", "create table a (id int);")
        pending = migrate.plan(self.conn, self.dir)
        self.assertEqual([m.version for m in pending], [1])
        self.assertNotIn("a", self.tables())
        self.assertEqual(self.recorded(), [])

    def test_a_declared_contract_migration_runs_only_with_the_flag(self):
        self.write("0001_a.sql", "create table gone (id int);")
        migrate.run(self.conn, self.dir)
        self.write("0002_contract_drop.sql",
                   "-- jobby:contract release N+2, gone is unread since 2.1.0\n"
                   "drop table gone;")
        with self.assertRaises(migrate.MigrationError):
            migrate.run(self.conn, self.dir)
        self.assertIn("gone", self.tables())
        migrate.run(self.conn, self.dir, allow_contract=True)
        self.assertNotIn("gone", self.tables())

    def test_the_ledger_records_who_and_when(self):
        """A schema_migrations row that says only "applied" cannot answer the
        question anybody actually asks, which is which deploy did this."""
        self.write("0001_a.sql", "create table a (id int);")
        migrate.run(self.conn, self.dir)
        row = migrate.applied(self.conn)[1]
        self.assertEqual(row["name"], "0001_a.sql")
        self.assertTrue(row["checksum"])
        self.assertIsNotNone(row["applied_at"])


class ShippedMigrationsApplyToAnEmptyDatabase(pgtest.SchemaCase):
    """The repo's own migrations, against nothing, in order.

    This is the check that a migration which happened to work against the
    developer's already-populated database also works on a fresh one. They are
    different questions and only one of them is what a new deployment does.
    """

    prefix = "mig_ship"

    def test_they_apply_and_are_idempotent(self):
        done = migrate.run(self.conn, migrate.MIGRATIONS)
        self.assertGreater(len(done), 0)
        self.assertEqual(migrate.run(self.conn, migrate.MIGRATIONS), [])

    def test_accounts_exists_with_a_flags_column(self):
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("select column_name, data_type, is_nullable "
                        "from information_schema.columns "
                        "where table_schema = %s and table_name = 'accounts' "
                        "order by column_name", (self.schema,))
            cols = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        self.assertIn("flags", cols)
        self.assertEqual(cols["flags"][0], "jsonb")
        self.assertEqual(cols["flags"][1], "NO", "flags must be not null")


if __name__ == "__main__":
    unittest.main()
