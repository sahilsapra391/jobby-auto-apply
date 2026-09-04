"""A slot lock must never outlive the process that took it.

Two of five apply slots sat locked for over a day with nothing running in them,
which is 40% of the apply capacity gone while the board looked healthy. The
lock files held pid 0.

pid 0 is not a process. In POSIX, kill(0, sig) signals the caller's own process
group, so os.kill(0, 0) always succeeds and the liveness check answered True
forever. lock_holder never saw the lock as stale, never cleared it, and nothing
reported it: the slot was simply missing.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watch = load("watch")


class PidLivenessTests(unittest.TestCase):
    def test_pid_zero_is_never_alive(self):
        """The exact value that made a lock immortal."""
        self.assertFalse(watch.pid_alive(0))

    def test_negative_pids_are_never_alive(self):
        # kill(-n) signals process group n, so these would also succeed.
        for pid in (-1, -7973):
            with self.subTest(pid=pid):
                self.assertFalse(watch.pid_alive(pid))

    def test_non_integers_are_never_alive(self):
        for pid in (None, "7973", 1.5, True):
            with self.subTest(pid=pid):
                self.assertFalse(watch.pid_alive(pid))

    def test_this_process_is_alive(self):
        self.assertTrue(watch.pid_alive(os.getpid()))

    def test_a_pid_that_cannot_exist_is_not_alive(self):
        self.assertFalse(watch.pid_alive(4_000_000))


class LockClearingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-locks-")
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.dir)

    def tearDown(self):
        watch.LOCK_DIR = self.original

    def write(self, name, pid):
        watch.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        watch.lock_path(name).write_text(json.dumps({"pid": pid, "at": "x", "note": ""}))

    def test_a_pid_zero_lock_is_cleared_and_the_slot_comes_back(self):
        self.write("apply-1", 0)
        self.assertIsNone(watch.lock_holder("apply-1"))
        self.assertFalse(watch.lock_path("apply-1").exists())
        self.assertTrue(watch.reserve("apply-1", note="starting"))

    def test_a_live_holder_is_left_alone(self):
        self.write("apply-2", os.getpid())
        held = watch.lock_holder("apply-2")
        self.assertIsNotNone(held)
        self.assertEqual(held["pid"], os.getpid())
        self.assertFalse(watch.reserve("apply-2", note="starting"))
