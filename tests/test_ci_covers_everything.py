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

    def command_lines(self):
        """The workflow with its comments stripped.

        A YAML comment is text in the same file as the command it describes,
        and an assertion looking for a command finds the sentence about it just
        as happily. `test_no_module_exclusion_filter_has_come_back` already
        drops comment lines for exactly this reason."""
        return "\n".join(line for line in self.text.splitlines()
                          if not line.lstrip().startswith("#"))

    def test_the_python_step_discovers_rather_than_listing(self):
        self.assertIn("unittest discover -s tests", self.text)

    def test_no_module_exclusion_filter_has_come_back(self):
        """The exact shape that hid two files: a pipeline selecting module
        names and filtering some out. Matched against the command lines rather
        than the whole file so the explanation above can name it."""
        joined = self.command_lines()
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

    def test_every_node_test_on_disk_is_reached_by_a_glob(self):
        """The Python half of this file compares what unittest collects against
        what is in the directory. The node half has no discovery: it is a shell
        glob, and a glob covers exactly the directory somebody typed.

        That is not hypothetical here. The step read `node --test
        tests/*.test.mjs` for as long as node tests have existed, because that
        was the only directory holding them. The moment a second one appeared
        the old glob still passed, still ran real tests, and silently covered
        none of the new ones -- which is the JOBBY_ADD failure with a different
        mechanism, and it announces itself exactly as loudly."""
        step = next((line for line in self.text.splitlines()
                     if "node --test" in line and not line.lstrip().startswith("#")),
                    None)
        self.assertIsNotNone(step, "no `node --test` step in the workflow")

        globs = [g for g in step.split() if g.endswith(".test.mjs")]
        self.assertTrue(globs, f"the node step names no test glob: {step!r}")

        covered = set()
        for glob in globs:
            covered.update(REPO.glob(glob))

        on_disk = {p for p in REPO.rglob("*.test.mjs")
                   if "node_modules" not in p.parts and ".next" not in p.parts}
        missing = sorted(str(p.relative_to(REPO)) for p in on_disk - covered)
        self.assertEqual(missing, [],
                         "these node tests are on disk and no workflow glob "
                         "reaches them, so no green check has ever run them")

    def test_the_v2_build_is_checked_against_its_deployed_shape(self):
        """A build step that only builds proves the code compiles. It was a
        compiling, passing, locally-correct renderer that read a favicon out of
        deploy/public/ and 502'd every deployed request for two days.

        Matched against the command lines rather than the file, and that is not
        tidiness. The first version searched the whole text for "npm ci", and
        the workflow step's own comment says "`npm ci` rather than `npm
        install`" -- so swapping the real command for `npm install` left the
        assertion passing on the prose explaining why it must not be. Caught by
        running the mutation, not by reading the test. It is the marker-in-text
        bug CLAUDE.md has now paid for four times, and a comment is just
        another corpus that decides what a substring means."""
        commands = self.command_lines()
        self.assertIn("check_deployed_shape.mjs", commands,
                      "CI builds /v2 without checking what the deployment "
                      "actually carries")
        self.assertRegex(commands, r"(?m)^\s*npm ci\b",
                         "CI installs /v2 without the lockfile, so it tests a "
                         "tree that no deployment will reproduce")

    def test_the_database_container_is_declared(self):
        self.assertIn("postgres:18", self.text)
        self.assertIn("JOBBY_TEST_DATABASE_URL", self.text)


class TheStalenessCheckNamesEveryGeneratedFile(unittest.TestCase):
    """CI's list and the generator's list are one list, compared.

    They were three: `sync_v2_styles --check` named three of the four files it
    wrote, and the workflow's `git status --porcelain` named three of the five.
    `rubric.generated.ts` could be stale and both said current, which is this
    repository's oldest shape in a new place. `--check` now walks
    `sync_v2_styles.outputs()`, and this compares the workflow against the
    same function rather than against a list written here.
    """

    def setUp(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import sync_v2_styles
        self.generator = sync_v2_styles
        self.text = WORKFLOW.read_text()
        # There are two `git status --porcelain` gates: the renderer bundle's
        # and this one. Anchored on the step name, because taking the first
        # match found the wrong gate and the assertion then measured a list
        # that has nothing to do with v2.
        step = self.text.index("The v2 stylesheet matches the board")
        block = self.text[step:]
        start = block.index("--porcelain")
        self.block = block[start:start + block[start:].index(')"')]

    def test_the_workflow_watches_every_file_the_generator_writes(self):
        written = {str(path.relative_to(REPO))
                   for path, _ in self.generator.outputs()}
        self.assertGreater(len(written), 3, "the generator writes nothing?")
        missing = sorted(p for p in written if p not in self.block)
        self.assertEqual(
            missing, [],
            "these are regenerated on every CI run and not diffed, so a stale "
            "one merges silently and v2 stops matching the board it replaces")

    def test_the_reader_would_notice_an_empty_block(self):
        """The control. A slice that matched nothing would pass the above."""
        self.assertIn("board.css", self.block,
                      "the slice above stopped containing the file list")
        self.assertNotIn("deploy/renderer", self.block,
                         "that is the other gate; taking the first match "
                         "found it and measured the wrong list")

    def test_the_check_flag_walks_the_same_list(self):
        import inspect
        source = inspect.getsource(self.generator.main)
        self.assertIn("written = outputs()", source,
                      "--check has to walk the generator's own list, or it "
                      "goes back to naming a subset of what it writes")


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
