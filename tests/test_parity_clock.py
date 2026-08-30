"""The seven-day clock, and the four ways a day count lies.

Written after the count was read by hand and read wrong: the start was reported
as the newest line in the log rather than the first green one, two hours and
forty minutes late, and nothing compared the two.
"""

import json
import subprocess
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
        # `day` counts covered time now, and these three runs are two days
        # apart, so the property under test is the start rather than the count.
        self.assertGreater(r["gateNotBefore"], "2026-09-02T01:34:42Z")

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
        self.assertIsNone(r["resetAt"])

    def test_an_inconclusive_run_inside_the_streak_is_counted_and_shown(self):
        entries = [green("2026-08-30T01:00:00Z"),
                   inconclusive("2026-08-31T01:00:00Z"),
                   green("2026-09-01T01:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertIsNone(r["resetAt"], "an inconclusive run reset the clock")
        self.assertEqual(r["inconclusiveRuns"], 1,
                         "the missing coverage is invisible in the report")

    def test_a_gap_does_not_break_the_streak_it_just_does_not_count(self):
        """A gap is inconclusive, not red. It must not reset the clock -- this
        runs on a laptop that sleeps and a resetting rule would never finish --
        and it must not accrue either, which is the half that was wrong."""
        entries = [green("2026-08-30T01:00:00Z"),
                   green("2026-08-30T14:00:00Z"),
                   green("2026-09-02T01:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertIsNone(r["resetAt"], "a gap reset the clock")
        self.assertLess(r["coveredDays"], 1.0, "unwatched hours accrued")
        self.assertGreater(r["widestGapHours"], 12,
                           "the gap is not reported, so nobody can judge it")

    def test_a_stale_log_is_named_rather_than_counted_silently(self):
        """The failure shape this repository keeps paying for. The day count
        rises with wall-clock time whether or not the timer ever fires again,
        so the count alone cannot distinguish a healthy clock from a dead one."""
        entries = [green("2026-08-30T01:00:00Z"), green("2026-08-30T02:00:00Z")]
        r = parity_clock.assess(entries, now=NOW)
        self.assertEqual(r["day"], 0,
                         "three days of silence accrued toward the gate")
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


class TimeNobodyWatchedIsNotTimeThatPassed(unittest.TestCase):
    """The third state. A run is green, red, or did-not-happen.

    Until 2026-08-30 the clock counted wall-clock between the first green run
    and now, and reported "2 green runs, no reset" across a window in which
    parity was red -- found by hand, because the scheduled check never fired
    inside it. Absence of red read as green.
    """

    def hourly(self, n, start="2026-08-30T00:00:00Z", step_hours=0.5):
        base = datetime.fromisoformat(start.replace("Z", "+00:00"))
        return [green((base + timedelta(hours=step_hours * i))
                      .strftime("%Y-%m-%dT%H:%M:%SZ")) for i in range(n)]

    def test_a_long_gap_does_not_count_as_covered_time(self):
        """Two green runs three days apart are not three days of agreement.
        They are two observations and a hole."""
        entries = [green("2026-08-30T00:00:00Z"), green("2026-09-02T00:00:00Z")]
        now = datetime(2026, 9, 2, 0, 30, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=now)
        self.assertLess(r["coveredDays"], 0.2)
        self.assertGreater(r["elapsedDays"], 2.9)
        self.assertFalse(r["complete"])
        self.assertGreater(r["inconclusiveHours"], 60)

    def test_control_continuous_runs_do_accrue(self):
        """Otherwise "a gap does not count" passes because nothing ever counts.
        Three days of runs every thirty minutes must open the gate."""
        entries = self.hourly(3 * 48 + 1)
        last = datetime.fromisoformat(entries[-1]["at"].replace("Z", "+00:00"))
        r = parity_clock.assess(entries, now=last + timedelta(minutes=5))
        self.assertGreaterEqual(r["coveredDays"], 3.0)
        self.assertTrue(r["complete"])
        self.assertEqual(r["inconclusiveHours"], 0.0)

    def test_one_missed_tick_is_tolerated(self):
        """The threshold is two intervals, so a single skipped run is covered
        rather than punished. A guard that fires on ordinary jitter is one
        somebody turns off."""
        entries = [green("2026-08-30T00:00:00Z"), green("2026-08-30T00:59:00Z")]
        now = datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=now)
        self.assertEqual(r["inconclusiveHours"], 0.0)

    def test_the_tail_since_the_newest_run_is_bounded_too(self):
        """Time since the last observation is exactly as unwatched as a gap in
        the middle, and counting it in full is how a dead timer would keep
        accruing days."""
        entries = [green("2026-08-30T00:00:00Z")]
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=now)
        self.assertLessEqual(r["coveredDays"], 0.05)
        self.assertGreater(r["inconclusiveHours"], 40)

    def test_the_gate_date_moves_out_by_the_time_nobody_watched(self):
        entries = [green("2026-08-30T00:00:00Z"), green("2026-09-01T00:00:00Z")]
        now = datetime(2026, 9, 1, 0, 30, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=now)
        self.assertGreater(r["gateNotBefore"], "2026-09-02T00:00:00Z")

    def test_green_red_and_did_not_run_are_three_different_counts(self):
        entries = [red("2026-08-29T00:00:00Z"),
                   green("2026-08-30T00:00:00Z"),
                   inconclusive("2026-08-30T00:30:00Z"),
                   green("2026-08-30T01:00:00Z")]
        now = datetime(2026, 8, 30, 1, 30, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=now)
        self.assertEqual(r["greenRuns"], 2)
        self.assertEqual(r["redRuns"], 0)          # the red is before the reset
        self.assertEqual(r["inconclusiveRuns"], 1)


class AGatePathChangeAlsoResetsIt(unittest.TestCase):
    """The rule has two resets and only one of them leaves a trace in the log.

    A divergence writes a red line. Editing class_b.py writes nothing at all,
    and without this the clock keeps counting green runs that were measuring
    the code as it was before the edit. Found by making exactly that change and
    watching the day count not move.
    """

    def test_a_gate_path_change_after_the_last_divergence_restarts_it(self):
        entries = [green("2026-08-30T01:00:00Z"), green("2026-08-31T01:00:00Z"),
                   green("2026-09-01T12:00:00Z")]
        touched = datetime(2026, 8, 31, 6, 0, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=NOW, gate_change=touched)
        self.assertEqual(r["startedAt"], "2026-09-01T12:00:00Z")
        self.assertEqual(r["resetBy"], "a gate path changing")

    def test_control_without_the_change_the_clock_runs_from_the_first_green(self):
        """The half that makes the test above mean something: same entries, no
        gate change, and the clock starts two days earlier."""
        entries = [green("2026-08-30T01:00:00Z"), green("2026-08-31T01:00:00Z"),
                   green("2026-09-01T12:00:00Z")]
        r = parity_clock.assess(entries, now=NOW, gate_change=None)
        self.assertEqual(r["startedAt"], "2026-08-30T01:00:00Z")

    def test_a_gate_path_change_older_than_the_last_divergence_is_ignored(self):
        """Whichever reset is later wins. An edit before a divergence is not a
        second reset, and treating it as one would move the clock backwards."""
        entries = [red("2026-08-31T00:00:00Z"), green("2026-09-01T00:00:00Z")]
        touched = datetime(2026, 8, 30, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=NOW, gate_change=touched)
        self.assertEqual(r["startedAt"], "2026-09-01T00:00:00Z")
        self.assertEqual(r["resetBy"], "a divergence")

    def test_a_gate_path_change_newer_than_every_run_is_a_reset_not_a_clock(self):
        entries = [green("2026-08-30T01:00:00Z")]
        touched = datetime(2026, 9, 1, tzinfo=timezone.utc)
        r = parity_clock.assess(entries, now=NOW, gate_change=touched)
        self.assertEqual(r["state"], "reset")
        self.assertEqual(r["day"], 0)
        self.assertEqual(r["resetBy"], "a gate path changing")

    def test_the_gate_paths_are_the_four_the_rule_names(self):
        self.assertEqual(set(parity_clock.GATE_PATHS), {
            "scripts/verify_parity.py", "scripts/class_b.py",
            "scripts/dual_write.py", "scripts/jobs_store.py"})

    def test_a_directory_that_is_not_a_repo_reads_as_no_change(self):
        self.assertIsNone(parity_clock.gate_path_touched(repo="/nonexistent"))

    def test_it_fails_open_when_git_itself_raises(self):
        """The `except` branch, reached deliberately.

        The first version of this test passed `/nonexistent` and called it
        done. `git -C /nonexistent log` exits non-zero rather than raising, so
        the handler never ran and replacing `return None` with `raise` passed
        the whole suite. Caught by the mutation, not by reading it."""
        from unittest import mock
        for boom in (OSError("no git"), subprocess.SubprocessError("boom"),
                     subprocess.TimeoutExpired("git", 10)):
            with self.subTest(error=type(boom).__name__):
                with mock.patch("subprocess.run", side_effect=boom):
                    self.assertIsNone(parity_clock.gate_path_touched())

    def test_it_finds_a_real_commit_in_this_checkout(self):
        """The control for both of the above, and it may not skip.

        Fails-open is only acceptable if the non-failing path works, and a
        version of `gate_path_touched` that always returns None satisfies every
        other test in this class. The first version skipped when it got None,
        which is exactly how a check reads as a pass while measuring nothing.
        So this asserts we are in a checkout first, then requires an answer."""
        repo = Path(parity_clock.__file__).resolve().parents[1]
        self.assertTrue((repo / ".git").exists() or (repo / ".git").is_file(),
                        "these tests must run from a git checkout")
        touched = parity_clock.gate_path_touched()
        self.assertIsNotNone(
            touched, "gate_path_touched found nothing in a real checkout that "
                     "contains all four gate paths, so the reset can never fire")
        self.assertLessEqual(touched, datetime.now(timezone.utc))


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
