"""CI's Postgres and Neon are the same major version, and cannot drift apart.

`postgres:18` in CI against a 16 or 17 endpoint is a bug waiting for the schema
to get interesting. Phase 1 already uses a generated stored column and partial
indexes; a container one major behind accepts DDL the endpoint rejects, and the
first anyone hears of it is a failed migrate step inside a deploy, on a branch
whose checks were green.

The obvious implementation is to connect to both and compare. CI cannot do
that, and should not be able to: handing a runner the production URL to read a
version number puts the database holding real applications one typo away from
every branch. So `migrate.POSTGRES_MAJOR` is the contract, and each side is
checked against it where it is reachable:

    the workflow's image tag   no database needed, runs everywhere
    the test database          runs in CI and on any machine with one
    Neon                       runs wherever the production URL resolves

Drift on either side fails somewhere, and the leg that needs no database at all
is the one that catches the most common mistake, which is editing the image tag.
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import migrate  # noqa: E402
import pgtest  # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "checks.yml"


class ThePinIsSane(unittest.TestCase):
    def test_the_pin_is_a_plausible_major(self):
        self.assertIsInstance(migrate.POSTGRES_MAJOR, int)
        self.assertGreaterEqual(migrate.POSTGRES_MAJOR, 13)

    def test_thirteen_is_the_floor_because_of_gen_random_uuid(self):
        """Migration 0001 dropped `pgcrypto` on the grounds that
        `gen_random_uuid()` is core. That is true from 13 onward and false
        before it, so the floor is not arbitrary."""
        self.assertGreaterEqual(migrate.POSTGRES_MAJOR, 13)


class _StubCursor:
    def __init__(self, answers):
        self.answers = answers
        self.row = None

    def execute(self, sql):
        for question, answer in self.answers.items():
            if question in sql:
                if isinstance(answer, Exception):
                    raise answer
                self.row = (answer,)
                return
        raise AssertionError(f"the stub server was not asked about {sql!r}")

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubConn:
    def __init__(self, **answers):
        self.answers = answers

    def cursor(self):
        return _StubCursor({"server_version_num": self.answers.get("num"),
                            "server_version": self.answers.get("display")})


class TheReaderUsesTheNumberNotTheDisplayString(unittest.TestCase):
    """`server_version` is for people. `server_version_num` is for programs.

    Neon's display string is "18.6 (c5250a2)", and splitting that on a dot
    happens to give "18", so a display-string implementation is right on every
    release and this distinction looks academic. It is not: a pre-release
    reports "19beta1" or "19devel", where the same code raises ValueError
    instead of answering, and the caller is a version gate.

    A mutation run swapping the implementation to the display string left the
    live-database tests passing, because the live database is a release. These
    use a stub server so the difference is observable without one, and they
    need no database at all, which is the other reason they are here.
    """

    def test_a_release_reads_correctly(self):
        conn = _StubConn(num="180006", display="18.6 (c5250a2)")
        self.assertEqual(migrate.server_major(conn), 18)

    def test_a_beta_reads_correctly(self):
        """The case that separates the two implementations. `int("19beta1")`
        raises; `190000 // 10000` is 19."""
        conn = _StubConn(num="190000", display="19beta1")
        self.assertEqual(migrate.server_major(conn), 19)

    def test_a_devel_build_reads_correctly(self):
        conn = _StubConn(num="200000", display="20devel")
        self.assertEqual(migrate.server_major(conn), 20)

    def test_it_never_consults_the_display_string(self):
        """Directly: a server whose display string raises must still answer."""
        conn = _StubConn(num="180006",
                         display=RuntimeError("display string unavailable"))
        self.assertEqual(migrate.server_major(conn), 18)

    def test_a_minor_version_does_not_move_the_major(self):
        for num, major in (("180000", 18), ("180014", 18), ("170009", 17)):
            with self.subTest(num=num):
                self.assertEqual(
                    migrate.server_major(_StubConn(num=num, display="x")),
                    major)


class TheWorkflowUsesThePinnedImage(unittest.TestCase):
    def setUp(self):
        if not WORKFLOW.exists():
            self.skipTest("no workflow file in this checkout")
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_service_image_is_the_pinned_major(self):
        found = re.findall(r"image:\s*postgres:(\d+)", self.text)
        self.assertEqual(
            found, [str(migrate.POSTGRES_MAJOR)],
            f"the workflow's postgres image is {found} and the schema is "
            f"pinned to {migrate.POSTGRES_MAJOR}")

    def test_the_image_is_not_floating(self):
        """`postgres:latest` passes a major check on the day it is written and
        silently becomes a different server later, which is the drift this
        whole module exists to prevent."""
        self.assertNotIn("postgres:latest", self.text)
        self.assertNotRegex(self.text, r"image:\s*postgres\s*$")
        self.assertNotRegex(self.text, r"image:\s*postgres\s*\n")


class TheTestDatabaseMatchesThePin(pgtest.SchemaCase):
    """Runs in CI against the service container, and locally against whatever
    JOBBY_TEST_DATABASE_URL names."""

    prefix = "pgver"

    def test_its_major_is_the_pinned_one(self):
        got = migrate.server_major(self.conn)
        self.assertEqual(
            got, migrate.POSTGRES_MAJOR,
            f"the test database is PostgreSQL {got} and the schema is pinned "
            f"to {migrate.POSTGRES_MAJOR}")

    def test_it_agrees_with_the_raw_number(self):
        with self.conn.cursor() as cur:
            cur.execute("show server_version_num")
            raw = cur.fetchone()[0]
        self.assertRegex(str(raw), r"^\d+$")
        self.assertEqual(int(raw) // 10000, migrate.server_major(self.conn))


def _production_url():
    """The Neon URL, or None. Read from config.env only.

    Deliberately not `migrate.database_url()`, which prefers
    JOBBY_TEST_DATABASE_URL: in CI that resolves to the container and this
    check would then assert the container against itself and pass while
    proving nothing about Neon.
    """
    try:
        import jobs_db
    except ImportError:                                  # pragma: no cover
        return None
    return jobs_db.database_url()


class NeonMatchesThePin(unittest.TestCase):
    """Skips where the production URL does not resolve, which is CI and any
    fresh clone. That skip is expected and is the one database skip
    `tests/test_ci_covers_everything.py` does not treat as a hole, because the
    alternative is putting the production URL on a runner."""

    def setUp(self):
        self.url = _production_url()
        if not self.url:
            self.skipTest("no production database URL on this machine")
        if pgtest.driver() is None:                      # pragma: no cover
            self.skipTest("psycopg is not installed")

    def test_its_major_is_the_pinned_one(self):
        conn = pgtest.driver().connect(migrate.direct_url(self.url))
        try:
            got = migrate.server_major(conn)
        finally:
            conn.close()
        self.assertEqual(
            got, migrate.POSTGRES_MAJOR,
            f"Neon is PostgreSQL {got} and the schema is pinned to "
            f"{migrate.POSTGRES_MAJOR}. Raising the pin is deliberate: change "
            f"migrate.POSTGRES_MAJOR and the workflow's image together.")

    def test_it_is_the_same_major_as_the_test_database(self):
        """The comparison the instruction actually asks for, on the one
        machine where both are reachable. The pin is what carries it into CI,
        but where both endpoints answer, compare them directly rather than
        each against a constant that could itself be stale."""
        if not pgtest.available():
            self.skipTest("no test database configured")
        prod = pgtest.driver().connect(migrate.direct_url(self.url))
        test = pgtest.driver().connect(migrate.direct_url(pgtest.URL))
        try:
            self.assertEqual(migrate.server_major(prod),
                             migrate.server_major(test))
        finally:
            prod.close()
            test.close()
