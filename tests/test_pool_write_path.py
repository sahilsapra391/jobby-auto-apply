"""There is one way to write the pool, and it holds the lock.

The rule this replaces was "remember to take the lock", and that rule has now
been broken twice by scripts whose authors had never heard of the sweep. On
2026-08-25 a sections backfill discarded 222 closures and 113 new jobs, having
loaded the pool before an eight-minute sweep finished and saved it after.

So the lock is on the write itself rather than on each caller, and `edit` is
the only shape in which a read-modify-write can be expressed. These are the
tests that keep it that way.
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import jobs_store  # noqa: E402
import runlock  # noqa: E402


class WriteTakesTheLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        jobs_store.save_jobs(self.root, {
            "a": dict(jobs_store.blank_job("a", "Northwind", "Product Manager"),
                      state="new")})

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_write_is_refused_while_another_process_holds_the_lock(self):
        """The whole point. A stale writer cannot land on top of a sweep."""
        helper = (
            "import sys; sys.path.insert(0, %r);\n"
            "import runlock, time\n"
            "with runlock.held(%r, 'scan', note='pretend-sweep'):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(6)\n" % (str(ROOT / "scripts"), str(self.root)))
        other = subprocess.Popen([sys.executable, "-c", helper],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(other.stdout.readline().strip(), "held")
            with self.assertRaises(runlock.LockBusy):
                jobs_store.save_jobs(self.root, {})
            with self.assertRaises(runlock.LockBusy):
                jobs_store.mark_watched(self.root, "a")
        finally:
            other.kill()
            other.wait()
            other.stdout.close()

    def test_the_lock_is_reentrant_for_the_process_that_holds_it(self):
        """A sweep holds it for minutes and writes through the same path.

        Without this the fix would deadlock the one caller that matters most.
        """
        with runlock.held(self.root, "scan", note="this-sweep"):
            jobs_store.save_jobs(self.root, {})
            self.assertEqual(jobs_store.load_jobs(self.root), {})

    def test_edit_holds_the_lock_across_the_read_as_well_as_the_write(self):
        """Locking only the write still loses updates: load, wait for a sweep
        to finish, then save a pool built before it started."""
        with jobs_store.edit(self.root, note="test") as (jobs, _state):
            self.assertIsNotNone(runlock.holder(self.root, "scan"))
            jobs["b"] = dict(jobs_store.blank_job("b", "Contoso", "Analyst"),
                             state="new")
        self.assertEqual(sorted(jobs_store.load_jobs(self.root)), ["a", "b"])

    def test_a_call_that_changes_nothing_does_not_rewrite_36mb(self):
        jobs_store.mark_watched(self.root, "a")
        before = (self.root / "jobs.json").stat().st_mtime_ns
        again = jobs_store.mark_watched(self.root, "a")
        self.assertTrue(again["alreadyWatched"])
        self.assertEqual((self.root / "jobs.json").stat().st_mtime_ns, before)

    def test_a_failed_edit_writes_nothing(self):
        with self.assertRaises(jobs_store.JobsStoreError):
            jobs_store.mark_watched(self.root, "nosuchjob")
        self.assertEqual(sorted(jobs_store.load_jobs(self.root)), ["a"])

    def test_the_button_writers_go_through_edit_rather_than_their_own_save(self):
        """A reviewer's check, in code. These two were the unlocked writers the
        incident exposed, and the fix is that they cannot write any other way."""
        source = (ROOT / "scripts" / "jobs_store.py").read_text()
        for name in ("def mark_passed", "def mark_watched"):
            body = source.split(name, 1)[1].split("\ndef ", 1)[0]
            self.assertIn("with edit(root", body, name)
            self.assertNotIn("save_jobs(root", body, name)


if __name__ == "__main__":
    unittest.main()
