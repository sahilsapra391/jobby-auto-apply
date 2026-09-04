"""The deploy lease: what a second session finds instead of being told.

Two sessions deployed the same production checkout while believing they owned it
alone, twice in ninety minutes on 2026-08-28. Both announced. Both gated
correctly. The second pair produced a real split, daemons on one commit and the
disk on another, caught only by checking that no reflog entry fell between the
pull and the restarts. These pin the thing that would have refused.
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

import runlock  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "deploy_lock_under_test", ROOT / "scripts" / "deploy_lock.py")
deploy_lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy_lock)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def test_a_second_session_finds_it_held_rather_than_being_told(self):
        token = runlock.acquire(self.root, "deploy", note="cc860a3 -> 1a69452",
                                lease_seconds=600, owner="session-one")
        self.assertTrue(token)
        with self.assertRaises(runlock.LockBusy) as caught:
            runlock.acquire(self.root, "deploy", lease_seconds=600,
                            owner="session-two")
        # The refusal names the holder, because the next question is always who.
        self.assertIn("session-one", str(caught.exception) + json.dumps(
            runlock.holder(self.root, "deploy")))

    def test_a_lease_outlives_the_process_that_took_it(self):
        """The whole reason this is not a pid lock.

        A deploy is a pull, three restarts and a verification. No single process
        is alive across it, so a pid-alive test would report the lock free while
        the pull was still running.
        """
        runlock.acquire(self.root, "deploy", lease_seconds=600, owner="gone")
        record = json.loads(runlock.lock_path(self.root, "deploy").read_text())
        # A pid that is definitely not running. The lease must survive it.
        record["pid"] = 2 ** 31 - 1
        runlock.lock_path(self.root, "deploy").write_text(json.dumps(record))
        self.assertFalse(runlock.pid_alive(record["pid"]))
        self.assertIsNotNone(runlock.holder(self.root, "deploy"))

    def test_an_expired_lease_is_free_so_a_dead_session_cannot_wedge_production(self):
        runlock.acquire(self.root, "deploy", lease_seconds=1, owner="died")
        path = runlock.lock_path(self.root, "deploy")
        record = json.loads(path.read_text())
        record["expiresAt"] = "2020-01-01T00:00:00Z"
        path.write_text(json.dumps(record))
        self.assertIsNone(runlock.holder(self.root, "deploy"))
        self.assertTrue(
            runlock.acquire(self.root, "deploy", lease_seconds=600, owner="next"))

    def test_an_unreadable_deadline_reads_as_held_not_as_free(self):
        """One corrupt byte must not become permission to restart production."""
        runlock.acquire(self.root, "deploy", lease_seconds=600, owner="holder")
        path = runlock.lock_path(self.root, "deploy")
        record = json.loads(path.read_text())
        record["expiresAt"] = "not a timestamp"
        path.write_text(json.dumps(record))
        self.assertIsNotNone(runlock.holder(self.root, "deploy"))
        with self.assertRaises(runlock.LockBusy):
            runlock.acquire(self.root, "deploy", lease_seconds=600, owner="other")

    def test_release_needs_the_token_and_not_merely_the_same_process(self):
        token = runlock.acquire(self.root, "deploy", lease_seconds=600,
                                owner="holder")
        # Same process, wrong token, and same process is not the proof here.
        self.assertFalse(runlock.release(self.root, "deploy", token="wrong"))
        self.assertFalse(runlock.release(self.root, "deploy"))
        self.assertIsNotNone(runlock.holder(self.root, "deploy"))
        self.assertTrue(runlock.release(self.root, "deploy", token=token))
        self.assertIsNone(runlock.holder(self.root, "deploy"))

    def test_a_pid_lock_still_behaves_exactly_as_it_did(self):
        """The scan lock shares this directory and must not have changed."""
        self.assertTrue(runlock.acquire(self.root, "scan", note="tier1"))
        held = runlock.holder(self.root, "scan")
        self.assertEqual(held["pid"], os.getpid())
        self.assertNotIn("expiresAt", held)
        self.assertTrue(runlock.owned_by_this_process(self.root, "scan"))
        self.assertTrue(runlock.release(self.root, "scan"))

    def test_seconds_left_is_none_for_a_pid_lock(self):
        runlock.acquire(self.root, "scan")
        self.assertIsNone(runlock.seconds_left(runlock.holder(self.root, "scan")))
        runlock.release(self.root, "scan")


class CommandTests(unittest.TestCase):
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

    # `test_take_release_round_trip` lives in `tests/quarantined_deploy_lock.py`.
    # It fails in a full run and passes alone, and nobody has diagnosed it; see
    # that file and `docs/quarantine.md` for what is known and what it would
    # take to bring it back.

    def test_a_refused_take_exits_non_zero(self):
        """A caller that checks only the exit code must not read
        "somebody else is deploying" as permission to deploy."""
        self.run_cli("take", "--owner", "first")
        code, refused, _ = self.run_cli("take", "--owner", "second")
        self.assertEqual(code, 1)
        self.assertFalse(refused["taken"])
        self.assertIn("first", refused["reason"])

    def test_break_is_loud_and_says_who_it_took_it_from(self):
        self.run_cli("take", "--owner", "stuck", "--note", "a -> b")
        code, broken, stderr = self.run_cli(
            "break", "--reason", "holder died mid-deploy")
        self.assertEqual(code, 0)
        self.assertTrue(broken["broken"])
        self.assertEqual(broken["was"]["owner"], "stuck")
        self.assertIn("stuck", stderr)
        self.assertIn("holder died mid-deploy", stderr)

    def test_extend_needs_the_token(self):
        _, taken, _ = self.run_cli("take", "--owner", "me", "--lease", "120")
        code, refused, _ = self.run_cli("extend", "--token", "wrong")
        self.assertEqual(code, 1)
        self.assertFalse(refused["extended"])
        code, extended, _ = self.run_cli(
            "extend", "--token", taken["token"], "--lease", "600")
        self.assertEqual(code, 0)
        self.assertTrue(extended["extended"])
        self.assertEqual(extended["holder"]["owner"], "me")

    def test_the_lease_is_capped(self):
        _, taken, _ = self.run_cli("take", "--owner", "me", "--lease", "999999")
        _, status, _ = self.run_cli("status")
        self.assertLessEqual(
            runlock.seconds_left(status["holder"]), deploy_lock.MAX_LEASE_SECONDS)


class StatusSurfaceTests(unittest.TestCase):
    """`watch.py --status` is the consumer, and that is the point.

    A lock nobody reads is the artefact that exists and does nothing, and asking
    sessions to remember a second command is a rule that depends on the next
    person remembering.
    """

    def test_watch_status_reports_the_deploy_lease(self):
        import watch

        self.assertIn("deploy_lock", dir(watch))
        source = (ROOT / "scripts" / "watch.py").read_text()
        self.assertIn('out["deploy"]', source)
