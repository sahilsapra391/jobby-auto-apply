"""A drop and an Apply racing for one application resolve by click order.

The tick claims drops before applies, and it used to execute every claimed
drop unconditionally. On 2026-08-27 a board page replayed a stale drop memory
on every load, so each tick's drop killed the Apply or Retry the user had just
clicked for the same application, over and over for two hours, until all four
applications sat in the Dropped archive. The queue stamps every entry as it is
enqueued, which makes the race decidable: the newest request is the user's
decision.

These cases drive watch.split_superseded_drops, the function the tick calls on
the claimed drop list before acting on it.
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import watch  # noqa: E402


def drop_request(aid, at=None):
    entry = {"applicationId": aid}
    if at is not None:
        entry["at"] = at
    return entry


def apply_click(aid, at=None, **extra):
    entry = {"applicationId": aid}
    if at is not None:
        entry["at"] = at
    entry.update(extra)
    return entry


class LatestIntentWinsTests(unittest.TestCase):
    def test_a_drop_older_than_the_pending_apply_is_cancelled(self):
        execute, superseded = watch.split_superseded_drops(
            [drop_request("a", "2026-08-27T21:00:00.000Z")],
            [apply_click("a", "2026-08-27T21:05:00.000Z", retry=True)])
        self.assertEqual(execute, [])
        self.assertEqual(superseded, [{"applicationId": "a",
                                       "dropAt": "2026-08-27T21:00:00.000Z",
                                       "applyAt": "2026-08-27T21:05:00.000Z"}])

    def test_a_drop_newer_than_the_pending_apply_executes(self):
        """Stop on a queued application is the newer click and must still work."""
        execute, superseded = watch.split_superseded_drops(
            [drop_request("a", "2026-08-27T21:05:00.000Z")],
            [apply_click("a", "2026-08-27T21:00:00.000Z")])
        self.assertEqual([x["applicationId"] for x in execute], ["a"])
        self.assertEqual(superseded, [])

    def test_an_unstamped_drop_keeps_the_old_rule_and_executes(self):
        execute, superseded = watch.split_superseded_drops(
            [drop_request("a")],
            [apply_click("a", "2026-08-27T21:05:00.000Z")])
        self.assertEqual([x["applicationId"] for x in execute], ["a"])
        self.assertEqual(superseded, [])

    def test_an_unstamped_apply_does_not_cancel_a_stamped_drop(self):
        execute, superseded = watch.split_superseded_drops(
            [drop_request("a", "2026-08-27T21:00:00.000Z")],
            [apply_click("a")])
        self.assertEqual([x["applicationId"] for x in execute], ["a"])
        self.assertEqual(superseded, [])

    def test_only_the_matching_application_is_compared(self):
        execute, superseded = watch.split_superseded_drops(
            [drop_request("a", "2026-08-27T21:00:00.000Z")],
            [apply_click("b", "2026-08-27T23:00:00.000Z")])
        self.assertEqual([x["applicationId"] for x in execute], ["a"])
        self.assertEqual(superseded, [])

    def test_the_newest_of_several_apply_clicks_is_the_one_that_counts(self):
        """KV dedupes per id, but the decision must not depend on that."""
        execute, superseded = watch.split_superseded_drops(
            [drop_request("a", "2026-08-27T21:03:00.000Z")],
            [apply_click("a", "2026-08-27T21:00:00.000Z"),
             apply_click("a", "2026-08-27T21:05:00.000Z")])
        self.assertEqual(execute, [])
        self.assertEqual(superseded[0]["applyAt"], "2026-08-27T21:05:00.000Z")

    def test_a_mixed_batch_splits_per_application(self):
        execute, superseded = watch.split_superseded_drops(
            [drop_request("old", "2026-08-27T21:00:00.000Z"),
             drop_request("fresh", "2026-08-27T22:00:00.000Z"),
             drop_request("alone", "2026-08-27T21:30:00.000Z")],
            [apply_click("old", "2026-08-27T21:10:00.000Z"),
             apply_click("fresh", "2026-08-27T21:50:00.000Z")])
        self.assertEqual([x["applicationId"] for x in execute], ["fresh", "alone"])
        self.assertEqual([x["applicationId"] for x in superseded], ["old"])

    def test_junk_entries_pass_through_to_the_existing_guards(self):
        """The loop after this already skips entries with no application id."""
        execute, superseded = watch.split_superseded_drops(
            ["junk", {"noId": True}], [apply_click("a", "2026-08-27T21:00:00.000Z")])
        self.assertEqual(execute, ["junk", {"noId": True}])
        self.assertEqual(superseded, [])
