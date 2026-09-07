"""Quarantined deploy-lock tests: `test_take_release_round_trip` (2026-09-03)
and `test_extend_needs_the_token` (2026-09-06).

**Two now, and that makes it a family rather than two accidents.** Both shell
out to `scripts/deploy_lock.py` several times per assertion, both take a real
lease with a wall clock in it, and both fail only inside a full
`discover -s tests` run. `test_extend_needs_the_token` takes a **120-second**
lease and then makes two more CLI calls against it; the round-trip one is the
same shape.

**The shared state is the lock file itself, and neither test should be taking a
real lease.** `deploy_lock` is a lease rather than a pid lock precisely because
a deploy is a dozen short-lived commands with no process alive across them, so
its state lives entirely in a file whose meaning depends on the clock. Every
test in this family points `JOB_APPLICATION_DIR` at its own
`TemporaryDirectory`, which isolates the *path* and not the *clock*, and a
120-second lease outlives the test that took it by two orders of magnitude.

That is a hypothesis and it is written as one. What would settle it: give the
lease an injectable clock, or have these tests take the shortest lease the CLI
will accept and assert on refusal rather than on duration. Neither has been
done, and until one is, a fix for either test should be assumed to apply to
both.

**Not collected by `unittest discover`**, whose default pattern is `test*.py`.
Run it deliberately:

    python3 -m unittest tests.quarantined_deploy_lock

**What is known about the round trip, and it is not much.** It fails inside a full
`discover -s tests` run and passes in isolation, with and without the changes
that were in the tree when it first failed. It has failed once before and been
passed over twice. Nobody has diagnosed it.

**Why it is out rather than left in.** A suite that goes red for a reason
nobody believes trains everyone to read past the red, and this file's own
subject -- a check that measures nothing while looking like it measures
something -- is what that produces. One test whose failure is uninformative
costs more than the coverage it provides, because it devalues every other
failure in the same run.

**What it would take to bring it back.** A cause, or a fix. The likely places,
recorded so the next person does not start from nothing rather than as a claim
about what is wrong: it shells out to `scripts/deploy_lock.py` per assertion
with `JOB_APPLICATION_DIR` pointed at a `TemporaryDirectory`, so it is the only
test in its class doing four subprocess round trips against a lease with a wall
clock in it. None of that has been measured.

**It is not covering nothing.** `LeaseTests` still covers acquire, refusal,
expiry and release directly, and `CommandTests` still covers the refusal exit
code and `break`. What left with these two is the CLI's happy path end to end
and the `extend` token check. `extend` is the one command that can prolong
somebody else's window, so its absence from the suite is the more expensive of
the two and the reason to fix the clock rather than live here.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location(
    "deploy_lock_quarantined", ROOT / "scripts" / "deploy_lock.py")
deploy_lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_lock)


class QuarantinedCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, JOB_APPLICATION_DIR=self.temp.name)

    def run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "deploy_lock.py"), *args],
            capture_output=True, text=True, env=self.env)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
        return proc.returncode, payload, proc.stderr

    def test_take_release_round_trip(self):
        code, taken, _ = self.run_cli("take", "--owner", "me", "--note", "a -> b")
        self.assertEqual(code, 0)
        self.assertTrue(taken["taken"])
        code, status, _ = self.run_cli("status")
        self.assertTrue(status["held"])
        self.assertIn("me", status["message"])
        code, released, _ = self.run_cli("release", "--token", taken["token"])
        self.assertEqual(code, 0)
        self.assertTrue(released["released"])
        _, status, _ = self.run_cli("status")
        self.assertFalse(status["held"])

    def test_extend_needs_the_token(self):
        """Quarantined 2026-09-06. Takes a 120-second lease and then makes two
        more CLI calls against it; fails in a full run, passes 3 of 3 alone."""
        _, taken, _ = self.run_cli("take", "--owner", "me", "--lease", "120")
        code, refused, _ = self.run_cli("extend", "--token", "wrong")
        self.assertEqual(code, 1)
        self.assertFalse(refused["extended"])
        code, extended, _ = self.run_cli(
            "extend", "--token", taken["token"], "--lease", "600")
        self.assertEqual(code, 0)
        self.assertTrue(extended["extended"])
        self.assertEqual(extended["holder"]["owner"], "me")
