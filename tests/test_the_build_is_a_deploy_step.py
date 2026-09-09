"""Step 0 of the deploy sequence is a command, and it has three answers.

**The build is the only check in the sequence that reads what the deployment
reads.** The suites read what they import, `tsc` reads the type graph, and every
AST guard reads a tree `ts.createSourceFile` handed back after recovering from
whatever was wrong with it. On 2026-09-09 a JSX comment in expression position
passed 3,558 Python tests, 238 node tests and a clean `tsc`, and `next build`
refused it.

So it became a step rather than a reminder. What that earns is this file: a step
nobody can run has not been added, and a step whose failure is indistinguishable
from its success is worse than no step at all.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import deploy_verify  # noqa: E402


class TheBuildStepTests(unittest.TestCase):
    def test_a_tree_with_no_web_app_is_unavailable_not_broken(self):
        """**"Could not look" is never "is not there", and this learned it the
        hard way.** Its first run was in the production checkout, which carries
        no installed dependencies, so `npm run build` exits 127 with
        `next: command not found`. That is not a compile failure. Reporting it
        as one sends a reader hunting a syntax error in a tree that is fine.
        """
        with tempfile.TemporaryDirectory() as tmp:
            verdict, detail = deploy_verify.web_build(Path(tmp))
        self.assertEqual(verdict, "unavailable")
        self.assertIn("package.json", detail)

    def test_uninstalled_dependencies_are_unavailable_and_name_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp) / "apps" / "web"
            web.mkdir(parents=True)
            (web / "package.json").write_text('{"name":"x"}')
            verdict, detail = deploy_verify.web_build(Path(tmp))
        self.assertEqual(verdict, "unavailable")
        self.assertIn("npm ci", detail,
                      "an unavailable verdict that names no fix is a dead end")

    def test_the_three_verdicts_have_three_exit_codes(self):
        """A deploy must not read `unavailable` as a pass, and a reader must
        not read it as a broken tree. Two states cannot carry three facts.
        """
        codes = {"ok": 0, "failed": 1, "unavailable": 2}
        self.assertEqual(len(set(codes.values())), 3)
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "deploy_verify.py"),
                 "--build-only", "--repo", tmp],
                capture_output=True, text=True)
        self.assertEqual(proc.returncode, codes["unavailable"],
                         "an unrunnable build exited as though it had an "
                         "opinion about the tree")

    def test_the_flag_exists_and_the_checklist_names_it(self):
        """A step documented and not implemented is a reminder with a number
        on it, which is what this replaced."""
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "deploy_verify.py"), "--help"],
            capture_output=True, text=True)
        self.assertIn("--build", proc.stdout)
        checklist = (REPO / "CLAUDE.md").read_text()
        self.assertIn("deploy_verify.py --build", checklist,
                      "the deploy sequence does not name the command, so step "
                      "0 is prose again")
        self.assertIn("0. run the build", checklist)

