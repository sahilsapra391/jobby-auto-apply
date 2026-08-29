"""CI runs the whole suite, and a database test that skips there is a hole.

Two failure shapes, both of which this repo has paid for.

**A test file that CI never runs.** The workflow used to select modules with a
`grep -v` exclusion list naming test_resume_pdf and test_resume_build. Anything
added to either file after that was invisible to every green check, forever,
and nothing said so. The sibling of it is `JOBBY_ADD` permitting a file the
commit never staged: three merged PRs lost 40 tests that way, and CI passed
each time because the tests it would have run were not there.

**A skip that reads as a pass.** `tests/test_migrations_live.py` and
`tests/test_account_flags.py` skip themselves when no database is configured,
which is right on a laptop with no Postgres and wrong in CI, where a container
is supplied precisely so they run. A suite reporting "OK (skipped=41)" and one
reporting "OK" look the same in a green check. CLAUDE.md already records the
version of this that bit: a redirected-HOME run reporting 2,124 passed with
seven skips has less coverage than the number implies, "and if seven ever
becomes eight, notice."

So the rule is that CI is where skips are not allowed to be invisible, and the
assertion lives in a test rather than in the YAML, because a test travels with
the repository and runs on the laptop too.
"""

import os
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

import pgtest  # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "checks.yml"
IN_CI = os.environ.get("CI", "").lower() == "true"


class TheWorkflowRunsEveryModule(unittest.TestCase):
    def setUp(self):
        if not WORKFLOW.exists():
            self.skipTest("no workflow file in this checkout")
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_python_step_discovers_rather_than_listing(self):
        self.assertIn("unittest discover -s tests", self.text)

    def test_no_module_exclusion_filter_has_come_back(self):
        """The exact shape that hid two files: a pipeline selecting module
        names and filtering some out. Matched against the command lines rather
        than the whole file so the explanation above can name it."""
        commands = [line for line in self.text.splitlines()
                    if not line.lstrip().startswith("#")]
        joined = "\n".join(commands)
        for pattern in (r"grep\s+-[a-zA-Z]*v", r"--exclude", r"\bdeselect\b"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(
                    re.search(pattern, joined),
                    f"the workflow filters test modules again ({pattern}). "
                    f"A module dropped here is invisible to every green check.")

    def test_discovery_finds_every_test_module_on_disk(self):
        """The check that does not restate the rule: what the runner collects,
        against what is in the directory. A module that fails to import is
        collected as a _FailedTest and still appears, so this catches a file
        that is missing rather than one that is broken."""
        loader = unittest.TestLoader()
        suite = loader.discover(str(REPO / "tests"), top_level_dir=str(REPO / "tests"))

        collected = set()

        def walk(node):
            if isinstance(node, unittest.TestSuite):
                for child in node:
                    walk(child)
            else:
                collected.add(type(node).__module__)

        walk(suite)
        on_disk = {p.stem for p in (REPO / "tests").glob("test_*.py")}
        self.assertEqual(on_disk - collected, set(),
                         "these modules are on disk and not collected")

    def test_the_database_container_is_declared(self):
        self.assertIn("postgres:18", self.text)
        self.assertIn("JOBBY_TEST_DATABASE_URL", self.text)


class DatabaseTestsRunInCI(unittest.TestCase):
    """The skip guard. On a laptop this is a no-op; in CI it is the check."""

    @unittest.skipUnless(IN_CI, "only meaningful where a database is supplied")
    def test_a_database_is_reachable(self):
        self.assertTrue(
            pgtest.available(),
            "CI is set but JOBBY_TEST_DATABASE_URL does not resolve to a "
            "usable Postgres, so every database test skipped and the suite "
            "still reported OK. That is the hole this exists to close.")

    @unittest.skipUnless(IN_CI, "only meaningful where a database is supplied")
    def test_the_connection_actually_works(self):
        """Reachable is not the same as usable. A URL pointing at nothing
        still satisfies `available()`, which only checks the string and the
        driver."""
        conn = pgtest.admin_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("select 1")
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            conn.close()

    @unittest.skipUnless(IN_CI, "only meaningful where a database is supplied")
    def test_the_ci_database_is_not_pooled(self):
        """Migrations need a session. If someone points CI at a pooled URL the
        advisory-lock test passes while proving nothing."""
        sys.path.insert(0, str(REPO / "packages" / "schema"))
        import migrate
        self.assertFalse(migrate.is_pooled(pgtest.URL))


if __name__ == "__main__":
    unittest.main()
