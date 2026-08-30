"""The seven-day clock, and the four ways a day count lies.

Written after the count was read by hand and read wrong: the start was reported
as the newest line in the log rather than the first green one, two hours and
forty minutes late, and nothing compared the two.
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import parity_clock  # noqa: E402


def green(at, **extra):
    return {"at": at, "ok": True, "divergences": 0, **extra}


def red(at, n=3):
    return {"at": at, "ok": False, "divergences": n}


def inconclusive(at):
    return {"at": at, "ok": False, "inconclusive": True, "reason": ""}


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


class TheClockCounts(unittest.TestCase):
    def test_the_required_span_is_three_days(self):
        """Named here rather than left implicit in a gate arithmetic test, so a
        change to the span fails as a decision rather than as a date."""
        self.assertEqual(parity_clock.REQUIRED_DAYS, 3)

    def test_it_starts_at_the_first_green_run_not_the_newest(self):
        """The bug this file exists for. Both readings produce a plausible
        timestamp and a plausible day count, and only one of them is the
        clock."""
        entries = [green("2026-08-30T01:34:42Z"),
                   green("2026-08-30T03:30:38Z"),
                   green("2026-09-01T04:14:20Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["startedAt"], "2026-08-30T01:34:42Z")
        self.assertEqual(r["gateAt"], "2026-09-02T01:34:42Z")
        self.assertEqual(r["day"], 3)

    def test_a_divergence_restarts_the_clock_at_zero(self):
        entries = [green("2026-08-25T01:00:00Z"),
                   green("2026-08-26T01:00:00Z"),
                   red("2026-08-27T01:00:00Z"),
                   green("2026-09-02T06:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["day"], 0)
        self.assertEqual(r["startedAt"], "2026-09-02T06:00:00Z")
        self.assertEqual(r["resetAt"], "2026-08-27T01:00:00Z")

    def test_green_runs_before_a_divergence_do_not_count(self):
        """Consecutive means consecutive. Six good days followed by a
        divergence is zero days, not six."""
        entries = [green(f"2026-08-2{d}T01:00:00Z") for d in range(2, 8)]
        entries.append(red("2026-08-28T01:00:00Z"))
        entries.append(green("2026-09-02T01:00:00Z"))
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["day"], 0)
        self.assertEqual(r["greenRuns"], 1)

    def test_an_inconclusive_run_is_missing_coverage_not_a_failure(self):
        """The real first line in the log: the LaunchAgent could not exec its
        own program, so the check never ran. Restarting a seven-day clock over
        a plist bug is wrong, and calling it green is worse."""
        entries = [inconclusive("2026-08-30T01:34:18Z"),
                   green("2026-08-30T01:34:42Z"),
                   green("2026-09-01T01:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["startedAt"], "2026-08-30T01:34:42Z",
                         "an inconclusive run reset the clock")
        self.assertEqual(r["day"], 3)

    def test_an_inconclusive_run_inside_the_streak_is_counted_and_shown(self):
        entries = [green("2026-08-30T01:00:00Z"),
                   inconclusive("2026-08-31T01:00:00Z"),
                   green("2026-09-01T01:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["day"], 3, "an inconclusive run broke the streak")
        self.assertEqual(r["inconclusive"], 1,
                         "the missing coverage is invisible in the report")

    def test_a_gap_is_measured_rather_than_judged(self):
        """This runs on a laptop that sleeps. A rule breaking the streak on a
        gap would never let the clock complete on the only machine it has."""
        entries = [green("2026-08-30T01:00:00Z"),
                   green("2026-08-30T14:00:00Z"),
                   green("2026-09-02T01:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["day"], 3, "a 13-hour gap broke the streak")
        self.assertGreater(r["widestGapHours"], 12,
                           "the gap is not reported, so nobody can judge it")

    def test_a_stale_log_is_named_rather_than_counted_silently(self):
        """The failure shape this repository keeps paying for. The day count
        rises with wall-clock time whether or not the timer ever fires again,
        so the count alone cannot distinguish a healthy clock from a dead one."""
        entries = [green("2026-08-30T01:00:00Z"), green("2026-08-30T02:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["day"], 3, "the day count stopped rising")
        self.assertTrue(r["stale"],
                        "three days without a run reads as healthy")
        self.assertGreater(r["sinceNewestHours"], 24)

    def test_a_fresh_log_is_not_called_stale(self):
        """The control. A staleness flag that is always on says nothing."""
        entries = [green("2026-09-02T11:50:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertFalse(r["stale"])

    def test_an_unparseable_line_is_surfaced_rather_than_dropped(self):
        raw = [{"at": None, "unparseable": "{truncated"},
               green("2026-09-01T01:00:00Z")]
        r = parity_clock.assess(raw, now=NOW)
        self.assertEqual(r["unparseable"], 1)

    def test_an_empty_log_is_no_runs_rather_than_day_zero_of_a_running_clock(self):
        r = parity_clock.assess([], now=NOW)
        self.assertEqual(r["state"], "no runs")
        self.assertEqual(r["day"], 0)


class TheClockReadsTheRealLog(unittest.TestCase):
    def test_it_parses_a_truncated_line_without_raising(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "parity.jsonl"
            p.write_text(json.dumps(green("2026-09-01T01:00:00Z")) + "\n"
                         + "{not json\n")
            entries = parity_clock.read(p)
        self.assertEqual(len(entries), 2)
        self.assertIn("unparseable", entries[1])

    def test_a_missing_log_reads_as_empty_rather_than_raising(self):
        self.assertEqual(parity_clock.read(Path("/nonexistent/parity.jsonl")), [])

    def test_the_interval_matches_the_timer_that_writes_the_log(self):
        """Two files asserting one number. The timer's interval is what makes
        four of them a sensible staleness threshold, so a change to one that
        leaves the other behind should fail here rather than quietly widen the
        window in which a dead timer reads as healthy."""
        sys.path.insert(0, str(REPO / "scripts"))
        import install_parity_timer
        self.assertEqual(
            parity_clock.INTERVAL,
            timedelta(seconds=install_parity_timer.INTERVAL_SECONDS))


if __name__ == "__main__":
    unittest.main()
