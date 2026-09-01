#!/usr/bin/env python3
"""How far the seven-day parity clock has actually run.

    python3 scripts/parity_clock.py
    python3 scripts/parity_clock.py --json

**This exists because the number was being read by hand and was read wrong.**
On 2026-08-30 the clock's start was reported as 04:14:20Z, which was simply the
newest line in the log at the moment somebody looked; the first green line was
01:34:42Z, two hours and forty minutes earlier. Neither reading was checked
against the other, and a daily figure that a person derives freshly each day
from a growing file is a figure that will be wrong on the day it matters.

Three distinctions the log makes that a glance does not.

**A divergence resets the clock. Nothing else does.** That is the rule as set:
seven consecutive days of parity green. `ok: false` with divergences is a real
disagreement between the two stores and the streak restarts at zero.

**An inconclusive run is not a failure, it is an absence.** The first line in
the log is `{"ok": false, "inconclusive": true, "reason": ""}` from the launchd
agent that could not execute its own program: a LaunchAgent may not exec a file
under ~/Documents, so `/bin/sh` answered `Operation not permitted` and the
check never ran. Counting that as a parity failure would restart a clock over a
plist bug, and counting it as green would be worse. It is coverage that is
missing, and it is reported as coverage that is missing.

**A gap is not a failure either, and must not be made one.** This runs on a
laptop that sleeps. The largest gap in the log at the time of writing is one
hour fifty-six minutes across an overnight, against a thirty-minute interval. A
rule that broke the streak on a gap would never let the clock complete on the
only machine it runs on. So gaps are measured and printed rather than judged,
and the person reading decides whether an uncovered window matters.

What that leaves is the failure this repository keeps paying for: a check that
reports green while checking nothing. So the clock also refuses to keep
counting silently when the newest line is stale -- an empty log and a timer
that has not fired are the same observation, and so are a running timer and one
that stopped an hour ago if nobody looks at the timestamp.
"""

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Three, not seven, since 2026-08-30. Seven was buying coverage of write paths
# that fire rarely rather than duration for its own sake, so the coverage is
# bought directly instead: the gate is three consecutive green days AND every
# write path in docs/write-paths.md observed firing at least once during the
# clock. Duration alone never proved the rare paths ran; a counter does.
REQUIRED_DAYS = 3

# The four paths whose modification restarts the clock, per the rule the gate
# is written under. A change to any of them changes what parity is measuring,
# so the green runs before it were measuring something else.
#
# File granularity, and `jobs_store.py` is the loose one: the rule names
# `_persist_pool` rather than the whole module, so an unrelated edit there
# restarts the clock when it need not. That is the safe direction and the
# alternative is parsing a diff for one function, which would be wrong quietly.
#: Changing any of these makes every green run before it stop being evidence.
#:
#: **`backfill.py` was missing and it is where both of 2026-09-01's outages
#: lived.** `dual_write.persist` calls `backfill.upsert_employers`,
#: `upsert_postings` and `upsert_decisions`, so every class A write to Postgres
#: goes through it; two uncovered `on conflict` targets in that file froze the
#: board for seventeen hours. Neither fix reset the clock, because the list
#: named the caller and not the code that does the writing. A gate list that
#: does not include the module where the failures happen is the same shape as
#: everything else that went wrong that day: a check that passes while not
#: measuring its subject.
#:
#: `board_key.py` is here because it derives the key those upserts conflict on.
#: A change to it changes which records collide, which is a change to what the
#: mirror stores, without a line of `backfill.py` moving.
#:
#: Deliberately not the whole import closure. `parity_clock.py` itself,
#: `write_path_coverage.py`, `runlock.py`, `migrate.py` and `search_config.py`
#: are all reachable from the write path and none of them decides what is
#: written; including them would restart the clock for editing the clock.
#: `tests/test_gate_paths_cover_the_mirror.py` derives the upsert modules
#: rather than restating this list, so a new one fails until it is named here.
GATE_PATHS = (
    "scripts/verify_parity.py",
    "scripts/class_b.py",
    "scripts/dual_write.py",
    "scripts/jobs_store.py",
    "packages/schema/backfill.py",
    "packages/schema/board_key.py",
)
# The timer's own interval, from install_parity_timer.INTERVAL_SECONDS. A log
# whose newest line is older than a few of these is a timer to go and look at.
INTERVAL = timedelta(seconds=1800)
STALE_AFTER = INTERVAL * 4

#: How long a gap between two green runs can be and still count as covered.
#: Two intervals, so one missed tick is tolerated and a sleeping laptop is not.
#:
#: **Time the checker did not run is not time the stores agreed.** Until
#: 2026-08-30 this counted elapsed wall-clock between the first green run and
#: now, and reported "2 green runs, no reset" across a window in which parity
#: was actually red -- found by hand, because the scheduled check did not fire
#: inside it. Absence of red read as green, which is the seventh instance in
#: CLAUDE.md of a check reporting success while measuring nothing.
#:
#: So the clock counts *covered* time rather than elapsed time. A gap larger
#: than this is inconclusive: it does not reset the streak, because nothing
#: went wrong that anyone saw, and it does not count toward it either, because
#: nothing went right that anyone saw.
MAX_COVERED_GAP = INTERVAL * 2


def log_path():
    """Resolved on every call, never frozen into a default argument.

    Two rules this obeys, and both are in CLAUDE.md because both were paid for.
    `JOB_APPLICATION_DIR` is honoured the way `store.root_dir` honours it, so a
    redirected store redirects this too. And the path is not a module constant
    computed at import: a `Path.home()` evaluated when the module loads is a
    path no environment variable can reach afterwards, which is how half of the
    store-isolation leak survived a redirect that looked complete.
    """
    root = Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application"))
    return root.expanduser() / "logs" / "parity.jsonl"


def gate_path_touched(repo=None):
    """When a gate path last changed in this checkout, or None.

    **The clock resets on a divergence and on a change to what it measures, and
    only the first leaves a trace in the log.** Without this the counter reads
    green runs since the last divergence and silently over-reports whenever
    somebody edits the mirror, which is precisely when the previous runs stop
    being evidence.

    Read from git rather than from anyone's memory. It is the checkout's own
    history, so the reset arms when the change reaches the checkout the timer
    runs from -- the deploy, which is the same arming rule CLAUDE.md gives for
    everything else here.

    Fails open on any git error: a clock that stops reporting because git is
    unavailable is worse than one that reports the divergence half alone, and
    the divergence half is the one that catches real drift.
    """
    repo = Path(repo) if repo else Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%cI", "--"]
            + list(GATE_PATHS),
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return datetime.fromisoformat(result.stdout.strip()).astimezone(timezone.utc)
    except ValueError:
        return None


def read(path=None):
    """`path=None`, not `path=LOG`.

    Python evaluates a default once, when the `def` runs, so a store path in a
    signature keeps naming whatever the module held at import however the
    constant is set later. `NoStorePathIsAFrozenDefaultTests` walks scripts/
    for exactly this and caught the first version of this file.
    """
    path = path or log_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # Never discarded. A line the clock cannot parse is a line whose
            # verdict is unknown, which is exactly the case worth surfacing.
            out.append({"at": None, "unparseable": line[:120]})
            continue
        out.append(entry)
    return out


def _when(entry):
    at = entry.get("at")
    if not at:
        return None
    return datetime.fromisoformat(at.replace("Z", "+00:00"))


def diverged(entry):
    """A real parity failure, as opposed to a run that could not conclude."""
    if entry.get("unparseable"):
        return False
    if entry.get("inconclusive"):
        return False
    return bool(entry.get("divergences")) or entry.get("ok") is False


def assess(entries, now=None, gate_change=None):
    """`gate_change` is when a gate path last moved; the clock starts after
    whichever reset is later, a divergence or a change to the code under
    measurement. Passed in rather than read here so the tests can drive both
    halves without a git repository."""
    now = now or datetime.now(timezone.utc)
    stamped = [e for e in entries if _when(e)]
    if not stamped:
        return {"state": "no runs", "day": 0, "required": REQUIRED_DAYS}

    # The streak is everything after the last divergence.
    last_bad = None
    for entry in stamped:
        if diverged(entry):
            last_bad = entry

    cutoff = _when(last_bad) if last_bad else None
    if gate_change and (cutoff is None or gate_change > cutoff):
        cutoff = gate_change
    after = [e for e in stamped if cutoff is None or _when(e) > cutoff]
    green = [e for e in after if e.get("ok") is True]

    if not green:
        return {"state": "reset", "day": 0, "required": REQUIRED_DAYS,
                "resetAt": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ") if cutoff else None,
                "resetBy": ("a gate path changing"
                            if gate_change and gate_change == cutoff
                            else "a divergence"),
                "runs": len(stamped)}

    start = _when(green[0])
    newest = _when(stamped[-1])
    gate_seconds = REQUIRED_DAYS * 86400

    # Covered time: the sum of the gaps between consecutive green runs, each
    # capped at MAX_COVERED_GAP. A longer gap contributes its cap and the rest
    # is counted as inconclusive rather than as agreement.
    covered = 0.0
    inconclusive = 0.0
    gaps = []
    for a, b in zip(green, green[1:]):
        span = (_when(b) - _when(a)).total_seconds()
        gaps.append((span, _when(a), _when(b)))
        covered += min(span, MAX_COVERED_GAP.total_seconds())
        if span > MAX_COVERED_GAP.total_seconds():
            inconclusive += span - MAX_COVERED_GAP.total_seconds()
    # And the tail, from the newest green run to now, on the same terms.
    tail = (now - _when(green[-1])).total_seconds()
    covered += min(tail, MAX_COVERED_GAP.total_seconds())
    if tail > MAX_COVERED_GAP.total_seconds():
        inconclusive += tail - MAX_COVERED_GAP.total_seconds()

    widest = max(gaps, default=(0.0, start, start))
    elapsed = (now - start).total_seconds()
    projected = start + timedelta(seconds=gate_seconds + inconclusive)

    return {
        "state": "running" if covered < gate_seconds else "gate open",
        "day": int(covered // 86400),
        "required": REQUIRED_DAYS,
        "coveredDays": round(covered / 86400, 2),
        "elapsedDays": round(elapsed / 86400, 2),
        "inconclusiveHours": round(inconclusive / 3600, 2),
        "startedAt": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # No fixed gate date: it moves out by every hour nobody watched.
        "gateNotBefore": projected.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "greenRuns": len(green),
        "redRuns": sum(1 for e in after if diverged(e)),
        "inconclusiveRuns": sum(1 for e in after if e.get("inconclusive")),
        "unparseable": sum(1 for e in entries if e.get("unparseable")),
        "resetAt": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ") if cutoff else None,
        "resetBy": ("a gate path changing" if gate_change and gate_change == cutoff
                    else "a divergence" if cutoff else None),
        "gatePathTouched": (gate_change.strftime("%Y-%m-%dT%H:%M:%SZ")
                            if gate_change else None),
        "widestGapHours": round(widest[0] / 3600, 2),
        "widestGapFrom": widest[1].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "newestRunAt": newest.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale": (now - newest) > STALE_AFTER,
        "sinceNewestHours": round((now - newest).total_seconds() / 3600, 2),
        "complete": covered >= gate_seconds,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args(argv)

    result = assess(read(args.log), gate_change=gate_path_touched())
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    day = result.get("day", 0)
    print(f"day {day} of {result['required']}   ({result['state']})")
    if result.get("startedAt"):
        print(f"  started      {result['startedAt']}")
        print(f"  covered      {result['coveredDays']} days of "
              f"{result['elapsedDays']} elapsed")
        if result.get("inconclusiveHours"):
            print(f"  UNWATCHED    {result['inconclusiveHours']}h the checker "
                  f"did not run. Not red, not green: it does not count.")
        print(f"  not before   {result['gateNotBefore']}")
        print(f"  runs         {result['greenRuns']} green, "
              f"{result.get('redRuns', 0)} red, "
              f"{result.get('inconclusiveRuns', 0)} inconclusive")
        if result.get("resetAt"):
            print(f"  last reset   {result['resetAt']}"
                  f"  ({result.get('resetBy') or 'unknown'})")
        if result.get("unparseable"):
            print(f"  UNPARSEABLE  {result['unparseable']} line(s)")
        print(f"  widest gap   {result['widestGapHours']}h "
              f"from {result['widestGapFrom']}")
        print(f"  newest run   {result['newestRunAt']} "
              f"({result['sinceNewestHours']}h ago)")
        if result.get("stale"):
            print("  STALE: the newest run is older than four intervals. "
                  "The day count keeps rising whether or not the timer fires; "
                  "check `install_parity_timer.py --status` before trusting it.")
    return 1 if result.get("stale") else 0


if __name__ == "__main__":
    raise SystemExit(main())
