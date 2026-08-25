"""One sweep at a time, and a lock the watcher can still read.

The bug these cover: ingest.py read the whole store, swept for minutes, then
wrote it back, with nothing coordinating two runs. The second to finish wrote a
store built from a copy read before the first began, so the first run's
postings vanished with no error and a perfectly valid jobs.json.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import runlock  # noqa: E402


class RunLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_a_second_sweep_refuses_rather_than_queueing(self):
        runlock.acquire(self.root, "scan", note="first")
        with self.assertRaises(runlock.LockBusy):
            runlock.acquire(self.root, "scan", note="second")

    def test_the_refusal_names_who_holds_it(self):
        runlock.acquire(self.root, "scan", note="tier1-ingest")
        with self.assertRaises(runlock.LockBusy) as caught:
            runlock.acquire(self.root, "scan")
        message = str(caught.exception)
        self.assertIn(str(os.getpid()), message)
        self.assertIn("tier1-ingest", message)

    def test_a_dead_holder_does_not_block_forever(self):
        # A crash, a reboot, a kill: the record outlives the process.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        runlock._write(self.root, "scan", {"pid": dead.pid, "at": "x", "note": ""})
        self.assertIsNone(runlock.holder(self.root, "scan"))
        self.assertTrue(runlock.acquire(self.root, "scan"))

    def test_pid_zero_is_not_a_living_process(self):
        # os.kill(0, 0) signals the caller's own process group and succeeds, so
        # an unguarded check reported pid 0 as alive and the lock was immortal.
        self.assertFalse(runlock.pid_alive(0))
        self.assertFalse(runlock.pid_alive(-1))
        self.assertFalse(runlock.pid_alive(True))
        self.assertFalse(runlock.pid_alive("7"))
        self.assertTrue(runlock.pid_alive(os.getpid()))

    def test_a_parked_record_is_honoured_not_cleared(self):
        # watch.py parks browser slots with pid 0 on purpose. Nothing that
        # parks a slot takes this lock, but if the two readers ever disagreed
        # about a shared file one of them would delete the other's record.
        runlock._write(self.root, "apply-1",
                       {"pid": 0, "at": "x", "note": "abc", "parked": True})
        held = runlock.holder(self.root, "apply-1")
        self.assertIsNotNone(held)
        self.assertTrue(held["parked"])
        self.assertTrue(runlock.lock_path(self.root, "apply-1").exists())

    def test_an_unreadable_record_is_not_evidence_of_a_running_sweep(self):
        path = runlock.lock_path(self.root, "scan")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertIsNone(runlock.holder(self.root, "scan"))
        self.assertTrue(runlock.acquire(self.root, "scan"))

    def test_release_only_drops_a_lock_this_process_holds(self):
        runlock._write(self.root, "scan",
                       {"pid": os.getpid() + 1, "at": "x", "note": ""})
        self.assertFalse(runlock.release(self.root, "scan"))
        self.assertTrue(runlock.lock_path(self.root, "scan").exists())

    def test_the_lock_file_is_private_and_so_is_its_directory(self):
        runlock.acquire(self.root, "scan")
        self.assertEqual(
            0o600, runlock.lock_path(self.root, "scan").stat().st_mode & 0o777)
        self.assertEqual(0o700, runlock.lock_dir(self.root).stat().st_mode & 0o777)

    def test_a_lock_name_cannot_escape_the_directory(self):
        for bad in ("../escape", "a/b", "", "with space", "semi;colon"):
            with self.assertRaises(ValueError):
                runlock.lock_path(self.root, bad)

    def test_held_releases_on_the_way_out_even_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with runlock.held(self.root, "scan"):
                raise RuntimeError("sweep blew up")
        self.assertIsNone(runlock.holder(self.root, "scan"))

    def test_a_watcher_spawned_sweep_inherits_rather_than_deadlocking(self):
        # watch.spawn starts the child and THEN calls take_lock(name, proc.pid),
        # so a daemon-spawned sweep finds the scan lock already held by its own
        # pid. Acquiring again would deadlock against its own parent.
        runlock._write(self.root, "scan",
                       {"pid": os.getpid(), "at": "x", "note": "tier1-ingest"})
        with runlock.held(self.root, "scan") as guard:
            self.assertTrue(guard.inherited)
        # Not ours to release: the watcher's liveness check cleans it up when
        # the child exits, and dropping it here would free it mid-sweep.
        self.assertIsNotNone(runlock.holder(self.root, "scan"))


class WatcherCompatibilityTests(unittest.TestCase):
    """The whole point of reusing `scan` is that watch.py can read it.

    Two locks would each be internally consistent and would not exclude each
    other, which is the exact overlap being fixed. If these drift, a hand-run
    sweep stops being visible to the daemon and the race comes back silently.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_watch_lock_holder_reads_what_runlock_writes(self):
        import watch
        original = watch.LOCK_DIR
        watch.LOCK_DIR = runlock.lock_dir(self.root)
        try:
            runlock.acquire(self.root, "scan", note="ingest")
            held = watch.lock_holder("scan")
            self.assertIsNotNone(held, "the daemon cannot see a hand-run sweep")
            self.assertEqual(os.getpid(), held["pid"])
            self.assertEqual("ingest", held["note"])
        finally:
            watch.LOCK_DIR = original

    def test_runlock_reads_what_watch_take_lock_writes(self):
        import watch
        original = watch.LOCK_DIR
        watch.LOCK_DIR = runlock.lock_dir(self.root)
        try:
            runlock.lock_dir(self.root).mkdir(parents=True, exist_ok=True)
            watch.take_lock("scan", os.getpid(), note="tier1-ingest")
            held = runlock.holder(self.root, "scan")
            self.assertIsNotNone(held, "a hand-run sweep cannot see the daemon")
            self.assertEqual("tier1-ingest", held["note"])
        finally:
            watch.LOCK_DIR = original


class IngestRefusalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _ingest(self, *extra):
        return subprocess.run(
            [sys.executable, str(REPO / "scripts" / "ingest.py"),
             "--store", str(self.root), *extra],
            capture_output=True, text=True, timeout=120)

    def test_the_cli_refuses_and_says_so_when_a_sweep_is_running(self):
        # Held by this test process, which is alive, so the child must refuse
        # before it fetches anything.
        runlock.acquire(self.root, "scan", note="tier1-ingest")
        done = self._ingest()
        self.assertEqual(3, done.returncode, done.stderr)
        self.assertIn("already running", done.stderr)
        payload = json.loads(done.stdout)
        self.assertEqual("sweep already running", payload["skipped"])

    def test_a_dry_run_does_not_wait_on_a_running_sweep(self):
        # It writes nothing, so it cannot lose an update, and making it wait
        # would gate Apply for a diagnostic.
        runlock.acquire(self.root, "scan", note="tier1-ingest")
        done = self._ingest("--dry-run", "--channel", "nonexistent-channel")
        self.assertNotEqual(3, done.returncode,
                            "a dry run should not be refused: " + done.stderr)


if __name__ == "__main__":
    unittest.main()
