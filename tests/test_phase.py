"""Tests for scripts/phase.py: which lane a role sits in while it is being worked.

The bug these pin down: pressing Retry on a Needs you card moved it to In flight
in the browser, but the store still said `review`, so reloading the board put it
straight back under Needs you while the agent was queued. Applications run
strictly one at a time, so "queued" can last the length of another application.

The lane therefore has to be decided in the store, not in localStorage, and a
run that fails has to hand the role back to where it came from rather than
leaving it in flight claiming work is happening.

Run from the repo root: python3 -m unittest discover -s tests
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class LaneTransitionTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.store = [sys.executable, str(ROOT / "scripts" / "store.py"),
                      "--root", str(self.root)]
        subprocess.run(self.store + ["init"], capture_output=True)

    def save(self, status):
        session = {"applicationId": "acme-pm", "company": "Acme", "role": "PM",
                   "status": status}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(session, f)
            path = f.name
        subprocess.run(self.store + ["session-save", "--id", "acme-pm", "--input", path],
                       capture_output=True)
        os.unlink(path)

    def load(self):
        r = subprocess.run(self.store + ["session-load", "--id", "acme-pm"],
                           capture_output=True, text=True)
        return json.loads(r.stdout or "{}")

    def phase(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "phase.py"), "--id", "acme-pm",
             "--root", str(self.root), *args], capture_output=True, text=True)

    def test_queuing_moves_it_out_of_needs_you_immediately(self):
        # Not when a runner reaches it. A queued retry can wait behind a
        # forty-five minute application, and the board must not spend that time
        # claiming the click did nothing.
        self.save("review")
        self.phase("--phase", "Queued")
        self.assertEqual(self.load()["status"], "inflight")

    def test_the_lane_it_came_from_is_remembered(self):
        self.save("review")
        self.phase("--phase", "Queued")
        self.assertEqual(self.load()["previousStatus"], "review")

    def test_later_phase_updates_do_not_overwrite_where_it_came_from(self):
        # Recording it on every update would set it to "inflight" after the
        # first one, and the role could never be handed back.
        self.save("review")
        self.phase("--phase", "Queued")
        self.phase("--phase", "Building documents")
        self.phase("--phase", "Filling the form", "--detail", "14 of 18 fields")
        self.assertEqual(self.load()["previousStatus"], "review")

    def test_a_failed_retry_goes_back_to_needs_you(self):
        self.save("review")
        self.phase("--phase", "Queued")
        self.phase("--restore", "--phase", "Failed")
        after = self.load()
        self.assertEqual(after["status"], "review")
        self.assertEqual(after["phase"], "Failed")
        self.assertNotIn("previousStatus", after, "the marker must be cleared on the way out")

    def test_a_failed_apply_goes_back_to_watching(self):
        self.save("watch")
        self.phase("--phase", "Queued")
        self.phase("--restore", "--phase", "Failed")
        self.assertEqual(self.load()["status"], "watch")

    def test_restoring_without_a_marker_falls_back_to_watching(self):
        # A session that somehow reached in-flight without passing through the
        # queue still has to land somewhere sensible rather than staying stuck.
        self.save("inflight")
        self.phase("--restore", "--phase", "Stopped")
        self.assertEqual(self.load()["status"], "watch")

    def test_done_clears_the_marker_too(self):
        self.save("review")
        self.phase("--phase", "Queued")
        self.phase("--done")
        after = self.load()
        self.assertEqual(after["status"], "watch")
        self.assertNotIn("previousStatus", after)

    def test_a_missing_application_is_reported_not_crashed(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "phase.py"), "--id", "nope",
             "--root", str(self.root), "--phase", "Queued"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such application", r.stdout)


if __name__ == "__main__":
    unittest.main()
