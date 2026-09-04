"""Tests for scripts/apply_marks.py, the Confirm-submitted button's other half.

The button says an application went out. What it must never do is write a second
submission for something already recorded, and what it must never refuse is an
application that was marked in error and retracted: that one is genuinely not
submitted, and the user pressing Confirm is right.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "apply_marks", ROOT / "scripts" / "apply_marks.py")
apply_marks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_marks)


def event(aid, kind, at):
    return {"applicationId": aid, "event": kind, "at": at, "company": "Acme",
            "role": "Product Manager"}


class CurrentlySubmittedTests(unittest.TestCase):
    def test_a_completed_application_counts(self):
        self.assertEqual(
            apply_marks.currently_submitted([event("a", "completed", "2026-08-01T00:00:00Z")]),
            {"a"})

    def test_a_retracted_completion_does_not_count(self):
        # The exact case: a mark logged in error, retracted with `abandoned`.
        # Counting it would leave the card stuck in Needs you with a Confirm
        # button that silently did nothing.
        history = [event("a", "completed", "2026-08-01T00:00:00Z"),
                   event("a", "abandoned", "2026-08-02T00:00:00Z")]
        self.assertEqual(apply_marks.currently_submitted(history), set())

    def test_a_re_completion_after_a_retraction_counts_again(self):
        history = [event("a", "completed", "2026-08-01T00:00:00Z"),
                   event("a", "abandoned", "2026-08-02T00:00:00Z"),
                   event("a", "completed", "2026-08-03T00:00:00Z")]
        self.assertEqual(apply_marks.currently_submitted(history), {"a"})

    def test_order_in_the_file_does_not_decide_it(self):
        # applications.jsonl is appended to by several runners at once, so the
        # line order is not necessarily the time order.
        history = [event("a", "abandoned", "2026-08-02T00:00:00Z"),
                   event("a", "completed", "2026-08-01T00:00:00Z")]
        self.assertEqual(apply_marks.currently_submitted(history), set())

    def test_other_events_do_not_make_it_submitted(self):
        history = [event("a", "submitting", "2026-08-01T00:00:00Z"),
                   event("a", "reviewed", "2026-08-02T00:00:00Z")]
        self.assertEqual(apply_marks.currently_submitted(history), set())

    def test_an_unrelated_application_is_unaffected(self):
        history = [event("a", "completed", "2026-08-01T00:00:00Z"),
                   event("b", "abandoned", "2026-08-02T00:00:00Z")]
        self.assertEqual(apply_marks.currently_submitted(history), {"a"})

    def test_an_empty_history_submits_nothing(self):
        self.assertEqual(apply_marks.currently_submitted([]), set())


class ClaimOrderTests(unittest.TestCase):
    def test_the_history_is_read_before_the_queue_is_claimed(self):
        # Claiming removes marks from the queue. Reading history afterwards and
        # failing threw the user's clicks away: the card had already left Needs
        # you and nothing was ever written.
        source = (ROOT / "scripts" / "apply_marks.py").read_text()
        body = source[source.index("def main("):]
        self.assertLess(body.index('store(["history-list"])'),
                        body.index('queue("claim")'),
                        "history must be read before the queue is claimed")
