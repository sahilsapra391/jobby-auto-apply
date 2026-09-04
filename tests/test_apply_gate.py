"""An HTTP sourcing pass must not silently hold up an Apply.

Apply was gated on `lock_holder("scan")`, and two different jobs share that
lock. An agent sweep drives a provider, so it genuinely competes with a form
runner. A Tier-1 ingest is an HTTP pass over the job boards: no provider, no
browser, and the apply path never calls jobs_store.save_jobs, so there is
nothing for it to contend with.

The cost was about thirteen minutes of dead Apply, six times a day, and it was
invisible: the tick evaluated the branch, found the lock held, spawned nothing
and logged nothing. Pressing Apply during a sweep did nothing and then worked
again, with no way to tell that apart from a dropped click.

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


class ScanBlockingApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.tmp.name) / "locks"
        watch.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(setattr, watch, "LOCK_DIR", self.original)

    def hold(self, note, pid=None):
        watch.take_lock("scan", os.getpid() if pid is None else pid, note=note)

    def test_the_daemons_hourly_ingest_does_not_hold_up_apply(self):
        # The regression this exists to prevent. watch.spawn tags the Tier-1
        # sourcing pass with this note, six times a day, unattended.
        self.hold("tier1-ingest")
        self.assertIsNone(watch.scan_blocking_apply())

    def test_a_hand_run_ingest_does_not_hold_up_apply_either(self):
        # scripts/ingest.py takes the same lock under its own note so that a
        # hand-run sweep cannot race the daemon's. It is the same HTTP pass and
        # has no more claim on Apply than the scheduled one.
        self.hold("ingest")
        self.assertIsNone(watch.scan_blocking_apply())

    def test_an_agent_sweep_still_holds_up_apply(self):
        # run_scan.py drives a provider, and select_provider_for_work hands out
        # one connection. That contention is real and is left alone.
        self.hold("scan")
        held = watch.scan_blocking_apply()
        self.assertIsNotNone(held)
        self.assertEqual("scan", held["note"])

    def test_an_unrecognised_holder_is_treated_as_blocking(self):
        # Fail closed. A future writer of this lock has to opt into being
        # ignored by naming itself, rather than being ignored by default.
        self.hold("something-nobody-has-written-yet")
        self.assertIsNotNone(watch.scan_blocking_apply())

    def test_a_note_less_holder_is_treated_as_blocking(self):
        watch.take_lock("scan", os.getpid())
        self.assertIsNotNone(watch.scan_blocking_apply())

    def test_no_scan_at_all_does_not_hold_up_apply(self):
        self.assertIsNone(watch.scan_blocking_apply())

    def test_a_dead_ingest_holder_does_not_hold_up_apply(self):
        watch.take_lock("scan", 0, note="tier1-ingest")
        self.assertIsNone(watch.scan_blocking_apply())

    def test_the_blocking_record_says_which_sweep_and_since_when(self):
        # The caller reports this to the board, so a waiting Apply can name
        # what it is waiting on instead of looking like a dropped click.
        self.hold("scan")
        held = watch.scan_blocking_apply()
        self.assertIn("at", held)
        self.assertEqual(os.getpid(), held["pid"])


class OneScanAtATimeStillHoldsTests(unittest.TestCase):
    """The rule that lock actually encodes is left exactly as it was.

    "An agent sweep and an API ingest both rewrite the same pool, and one at a
    time is the rule." That is a statement about scans. Narrowing the Apply gate
    must not narrow this.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.tmp.name) / "locks"
        watch.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(setattr, watch, "LOCK_DIR", self.original)

    def test_an_ingest_still_blocks_a_second_scan(self):
        watch.take_lock("scan", os.getpid(), note="tier1-ingest")
        self.assertIsNotNone(
            watch.lock_holder("scan"),
            "the scan-versus-scan guard reads lock_holder, not the Apply gate")

    def test_an_agent_sweep_still_blocks_a_second_scan(self):
        watch.take_lock("scan", os.getpid(), note="scan")
        self.assertIsNotNone(watch.lock_holder("scan"))

    def test_both_ingest_notes_are_still_visible_to_lock_holder(self):
        # scan_blocking_apply filters; lock_holder must not. If these ever
        # diverge, two sweeps could run at once.
        for note in sorted(watch.INGEST_LOCK_NOTES):
            with self.subTest(note=note):
                watch.take_lock("scan", os.getpid(), note=note)
                self.assertIsNotNone(watch.lock_holder("scan"))
                self.assertIsNone(watch.scan_blocking_apply())


class ReportedRunningTests(unittest.TestCase):
    """A sweep still shows as running even when it no longer gates Apply."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.tmp.name) / "locks"
        watch.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(setattr, watch, "LOCK_DIR", self.original)

    def test_an_ingest_is_still_reported_as_busy(self):
        watch.take_lock("scan", os.getpid(), note="tier1-ingest")
        self.assertIsNotNone(watch.lock_holder("scan"),
                             "the board's running list reads lock_holder")
