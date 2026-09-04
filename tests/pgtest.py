"""A scratch Postgres schema per test, and the reasons it is built this way.

Not a test module: the name does not match `test_*.py`, so no runner collects
it. It is the shared harness for every module that needs a real database, the
way `tests/store_isolation.py` is the shared harness for the file store.

`JOBBY_TEST_DATABASE_URL` names the database. CI supplies a throwaway container.
On this machine the only Postgres available is the live Neon one holding 15,400
real jobs and 190 real answers, which is what makes the isolation here load
bearing rather than tidy.

**Nothing that needs a session may go through the pooler.** The configured URL
is Neon's pooled endpoint, and a pooler in transaction mode hands each
transaction whatever backend is free. Two consequences, both silent:

  · `set search_path` applies to one backend and the next statement may land on
    another with the default path. Measured: an earlier version of the
    migration tests passed all 17 while creating `a`, `b` and
    `schema_migrations` in `public` of the live database.
  · `pg_advisory_lock` is session-scoped, so a lock taken through a pooler
    excludes nobody while appearing to work.

`options=-c search_path=...` does not rescue it either; Neon's pooler refuses
that startup parameter and its error names the fix, which is the one used here:
connect to the direct endpoint. `migrate.direct_url` is the single place that
rewrite lives, because the runner needs it for its lock for the same reason.
"""

import os
import unittest
import uuid

URL = os.environ.get("JOBBY_TEST_DATABASE_URL", "").strip()


def driver():
    try:
        import psycopg
        return psycopg
    except ImportError:                                  # pragma: no cover
        return None


def available():
    return bool(URL) and driver() is not None


def _direct():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                          / "packages" / "schema"))
    import migrate
    return migrate.direct_url(URL)


def admin_conn():
    """A session on the direct endpoint, for creating and dropping schemas."""
    return driver().connect(_direct(), autocommit=True)


def connect_to(schema):
    """A session whose `search_path` is that schema, and stays that schema."""
    conn = driver().connect(_direct(), autocommit=True)
    conn.execute(f'set search_path to "{schema}"')
    return conn


@unittest.skipUnless(available(),
                     "needs JOBBY_TEST_DATABASE_URL and psycopg")
def apply_migrations(conn):
    """Build the real schema in whatever schema this connection is scoped to.

    **A test that hand-writes a table's DDL is a second declaration of it.**
    `test_answer_bank_deletion_is_bounded` spelled `answer_bank` out in a
    fixture, migration 0014 added `kind` and `category` to the real one, and
    eight tests then failed with "column kind does not exist" -- not because
    the mirror was wrong but because the fixture was a copy. That is the same
    failure `mirror_fields` exists to end, one layer down.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "schema"))
    import migrate
    migrate.run(conn, migrate.MIGRATIONS, False)
    conn.commit()


@unittest.skipUnless(available(),
                     "needs JOBBY_TEST_DATABASE_URL and psycopg")
class SchemaCase(unittest.TestCase):
    """A test case owning one disposable schema.

    `prefix` distinguishes one module's scratch schemas from another's when
    something goes wrong and they have to be found by hand.

    **The guard belongs here and it was on `apply_migrations`, which is a
    module-level function rather than a test.** That decorator is not useless:
    called from inside a test with no database configured it raises SkipTest
    and unittest records a skip. It is simply never reached, because `setUp`
    runs first and `admin_conn()` connects before any migration is applied.
    With `JOBBY_TEST_DATABASE_URL` unset, `driver().connect("")` does not
    refuse, it falls through to libpq's own defaults and dials localhost:5432.

    Measured on this machine 2026-09-03: 20 of `test_verify_parity`'s tests
    errored with "Is the server running locally and accepting connections on
    that socket?" where they should have skipped. Invisible in CI, which
    supplies the container, and it is one reason nobody runs this suite where
    the real corpus lives -- a clean checkout on the developer's own Mac
    reports twenty errors that mean nothing, which is how a real failure two
    classes further down went unread for a day.

    A class-level skip is inherited by subclasses and runs before `setUp`,
    which is the property this relies on and which was verified rather than
    assumed.
    """

    prefix = "pgtest"

    def setUp(self):
        super().setUp()
        self.schema = f"{self.prefix}_{uuid.uuid4().hex[:12]}"
        self.admin = admin_conn()
        self.addCleanup(self.admin.close)
        with self.admin.cursor() as cur:
            cur.execute(f'create schema "{self.schema}"')
        # Registered before the connection is opened, so a failure to connect
        # still drops the schema. An earlier version dropped it in tearDown
        # after assigning self.conn; when connecting raised, it left 18 scratch
        # schemas behind on the live database.
        self.addCleanup(self._drop_schema)
        self.conn = connect_to(self.schema)
        self.addCleanup(self.conn.close)

    def _drop_schema(self):
        with self.admin.cursor() as cur:
            cur.execute(f'drop schema if exists "{self.schema}" cascade')

    # ------------------------------------------------------------------

    def tables(self):
        with self.conn.cursor() as cur:
            cur.execute("select table_name from information_schema.tables "
                        "where table_schema = %s order by table_name",
                        (self.schema,))
            return [r[0] for r in cur.fetchall()]

    def assert_isolated(self, probe="isolation_probe"):
        """DDL on `self.conn` lands in the scratch schema and not in `public`.

        Read back from the admin connection, because the question is whether
        the pinning survives the pool rather than whether one session agrees
        with itself. Every module using this harness should call it once: the
        rest of its assertions are all conditional on it.
        """
        with self.conn.cursor() as cur:
            cur.execute("select current_schema()")
            self.assertEqual(cur.fetchone()[0], self.schema)
            cur.execute(f"create table {probe} (id int)")
        self.assertIn(probe, self.tables())
        with self.admin.cursor() as cur:
            cur.execute("select to_regclass(%s)", (f"public.{probe}",))
            self.assertIsNone(cur.fetchone()[0],
                              f"{probe} reached public")
