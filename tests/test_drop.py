"""Tests for scripts/drop.py, the user deciding against an application.

Dropping is a decision, not a failure, so it keeps the session and records where
the application had got to. "Dropped while it sat in Watching" and "dropped
halfway through filling a form" are different facts and the board shows both.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drop = load("drop")


class StageDescriptionTests(unittest.TestCase):
    def test_watching_says_it_never_started(self):
        self.assertEqual(drop.describe_stage({"status": "watch"}), "not started")

    def test_in_flight_records_the_phase_it_reached(self):
        stage = drop.describe_stage({
            "status": "inflight", "phase": "Filling the form",
            "phaseDetail": "14 of 18 fields"})
        self.assertIn("Filling the form", stage)
        self.assertIn("14 of 18 fields", stage)

    def test_in_flight_without_a_detail_still_names_the_phase(self):
        self.assertEqual(drop.describe_stage({"status": "inflight", "phase": "Submitting"}),
                         "Submitting")

    def test_needs_you_records_what_it_was_waiting_on(self):
        stage = drop.describe_stage({
            "status": "review",
            "pendingFields": [{"question": "Create a Workday account"},
                              {"question": "Accept the arbitration agreement"}]})
        self.assertIn("Create a Workday account", stage)
        self.assertIn("+1 more", stage, "the count of remaining blockers is worth keeping")

    def test_needs_you_with_nothing_pending_reads_as_ready(self):
        self.assertIn("ready to submit", drop.describe_stage({"status": "review",
                                                              "pendingFields": []}))

    def test_a_long_blocker_is_truncated_rather_than_flooding_the_table(self):
        stage = drop.describe_stage({"status": "review",
                                     "pendingFields": [{"question": "x" * 500}]})
        self.assertLess(len(stage), 200)


class LaneNameTests(unittest.TestCase):
    def test_every_droppable_status_has_a_readable_lane_name(self):
        for status in ("watch", "inflight", "review", "active"):
            self.assertIn(status, drop.LANE_NAMES)

    def test_both_blocked_statuses_read_as_the_same_lane(self):
        # review and active are one lane on the board; the record should agree.
        self.assertEqual(drop.LANE_NAMES["review"], drop.LANE_NAMES["active"])


class StopAgentTests(unittest.TestCase):
    """Killing the runner is the risky half: signalling the wrong pid is worse
    than not signalling at all."""

    def setUp(self):
        self.calls = []
        self.original_holder = drop.watch.lock_holder

    def tearDown(self):
        drop.watch.lock_holder = self.original_holder

    def test_a_lock_held_for_another_application_is_left_alone(self):
        drop.watch.lock_holder = lambda name: {"pid": 999999, "note": "someone-else"}
        result = drop.stop_agent("the-one-being-dropped")
        self.assertFalse(result["stopped"])
        self.assertIn("nothing was running", result["why"])

    def test_no_lock_at_all_is_not_an_error(self):
        drop.watch.lock_holder = lambda name: None
        self.assertFalse(drop.stop_agent("anything")["stopped"])
