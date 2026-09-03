"""Quarantined: `test_take_release_round_trip`, flaky since 2026-09-03.

**Not collected by `unittest discover`**, whose default pattern is `test*.py`.
Run it deliberately:

    python3 -m unittest tests.quarantined_deploy_lock

**What is known, and it is not much.** It fails inside a full
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
code and `break`. What left with this test is the CLI's happy path end to end.
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


if __name__ == "__main__":
    unittest.main()
