#!/usr/bin/env python3
"""Run the tests that only this machine can run, and fail the ping when they don't.

**CI skips 24 tests and they are not the incidental ones.** They are the tests
that check against the real store, the real rendered documents and the real
database: `LiveCorpusTests`, the resume round-trip over every resume ever
written, the tracked-file PII scan, the published state's fit under its
ceiling, and the two parity tests that read the device's own pool. That is the
class that exists precisely because a synthetic fixture could not have caught
the thing.

The cost came due on 2026-09-03.
`test_every_dropped_field_is_one_a_merge_may_discard` was failing on `main`
and had been seen by nobody, because the only machine that can run it is the
one where nobody runs the suite. Four merged records had discarded fields
nobody had classified, and one of them was `sources`, which is the column the
multi-board sightings figure reads.

Two smaller things made the local run unusable, and both are fixed rather than
worked around here, because a check people cannot bear to run is a check that
does not exist:

  · `pgtest.SchemaCase` carried no skip guard, so twenty tests errored on a
    machine with no test database instead of skipping. See that class.
  · A full unredirected suite writes the device's own store. This runs the
    corpus modules only, and that set was measured read-only: a complete
    stat of all 69,671 files in `~/.job-application` before and after
    differed by one line, `locks/watcher.lock`, written by the running
    daemon rather than by any test.

**Why it hangs off the existing heartbeat rather than getting a service.**
`ai.jobby.parity` is the argument. It ran on its own timer while gating
nothing, and what it needed in the end was relabelling rather than anyone
noticing it. A fourth plist is a fourth thing to believe in. The watcher
already pings a dead-man every 55 minutes and already composes that ping from
four warnings; this is a fifth reason on the same alarm, which is the shape
`parked_slot_warning` established and the shape that gets read.

**It fails in more than one direction, which is the property most of the
guards written up in CLAUDE.md turned out to lack.** A failing test fails the
ping. So does a result that has stopped arriving, because a run that dies is
observed by nothing otherwise -- `spawn` never waits on it. And so does a run
that passes while measuring less than it used to: `MAX_SKIPPED` is a ceiling,
so tests that quietly stop running are a fault rather than a smaller green
number.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("JOB_APPLICATION_DIR",
                                Path.home() / ".job-application"))
RESULT = STATE_DIR / "corpus-check.json"

#: The modules holding tests that cannot run in CI. Named rather than
#: discovered, because the runner has to be able to name what it ran; and
#: cross-checked against the source in `tests/test_corpus_check.py`, which
#: derives the set of modules carrying a store-absence skip and fails when one
#: is missing here. That derivation earned its keep on the first run: it found
#: `test_resume_editor_mapping` and `test_postgres_version_is_pinned`, which
#: this list had been written without. That split is the `GATE_PATHS` lesson: a list nobody
#: derives anything from is a list that drifts, and the drift is always the
#: same direction, a writer gaining a case the reader never hears about.
MODULES = (
    "tests.test_verify_parity",
    "tests.test_resume_build",
    "tests.test_resume_pdf",
    "tests.test_cover_letter",
    "tests.test_state_slim",
    "tests.test_no_pii",
    "tests.test_board_key",
    "tests.test_backfill",
    "tests.test_alert_on_a_dead_sweep",
    "tests.test_resume_editor_mapping",
    "tests.test_postgres_version_is_pinned",
)

#: A module that carries a corpus skip and still must not run on this clock,
#: with the reason attached. One entry, and the reason is not tidiness.
#:
#: `test_apply_end_to_end` drives a real Chrome on a real apply slot: that is
#: the behaviour under test, not a leak, and it is the only invariant covering
#: the firewall against a live browser. Running it hourly would take a slot
#: from the fleet sixteen times a working day, open fixture tabs in a browser
#: that may be holding somebody's part-filled application, and leave an
#: `activity.json` under an id that no longer attributes to anything. It is
#: run deliberately, by a person, against a free slot.
EXCLUDED = {
    "tests.test_apply_end_to_end":
        "drives a real browser slot; running it on a timer takes capacity "
        "from the apply fleet and writes artifacts nothing can attribute",
}

#: Measured 2026-09-03 on this Mac over a real run of all eleven modules: 242
#: tests, 46 seconds, 53 skips. 51 of those are `pgtest.SchemaCase` subclasses
#: with no `JOBBY_TEST_DATABASE_URL` -- CI supplies the container and runs them
#: there, so they are covered, just not here -- one is `test_resume_build` with
#: no rendered resume for that case, and one is the Postgres version pin. Five
#: of headroom and no more: this number's job is to notice a corpus that has
#: gone away, and a ceiling generous enough never to fire notices nothing. When
#: a legitimate skip is added, move this and say in the commit what it was.
MAX_SKIPPED = 58

#: A run that has not reported in this long is itself the fault. Generous
#: against the 55-minute heartbeat because a run is deferred while any apply
#: slot is busy, and a genuinely busy afternoon should not ring an alarm.
STALE_SECONDS = 6 * 3600

#: Roughly two minutes observed. Ten is a hang, and a hang has to end in a
#: written result or the staleness branch is the only thing left to catch it.
TIMEOUT_SECONDS = 10 * 60


def _load():
    try:
        return json.loads(RESULT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def warning(now=None):
    """The message for the dead-man ping, or an empty string.

    Three ways to be unhappy and they are deliberately distinguishable in the
    text, because "the corpus tests failed" and "the corpus tests have not run
    since Tuesday" are different problems with different first moves.
    """
    now = time.time() if now is None else now
    payload = _load()
    if payload is None:
        # Not a fault on a machine that has never run it. The watcher writes
        # a result within its first hour; until then there is nothing to say,
        # and inventing an alarm for a cold start is how an alarm gets muted.
        return ""
    age = now - payload.get("ranAt", 0)
    if age > STALE_SECONDS:
        return (f"the corpus tests have not reported for {age / 3600:.1f} "
                f"hours, against a {STALE_SECONDS / 3600:.0f} hour limit. "
                f"They are the only tests that read the real store, so this "
                f"is silence rather than health. Read "
                f"~/.job-application/logs/corpus-check.log.")
    if payload.get("ok"):
        return ""
    bad = list(payload.get("failures") or []) + list(payload.get("errors") or [])
    if bad:
        shown = ", ".join(bad[:3]) + (f" and {len(bad) - 3} more" if len(bad) > 3 else "")
        return (f"{len(bad)} of the {payload.get('ran', 0)} tests that only run "
                f"where the real corpus lives are failing: {shown}. CI cannot "
                f"see these.")
    return (f"the corpus tests passed but {payload.get('skipped')} skipped "
            f"against a ceiling of {payload.get('skipCeiling')}. Tests that "
            f"stop running report the same green as tests that pass. Read "
            f"~/.job-application/corpus-check.json for the reasons.")


def due(now=None):
    """Whether a fresh run is wanted. Paced by the heartbeat, not by the tick."""
    now = time.time() if now is None else now
    payload = _load()
    if payload is None:
        return True
    return now - payload.get("ranAt", 0) >= 55 * 60


#: The runner, which lives in tests/ because it imports unittest and nothing
#: under scripts/ may. `watch.spawn` launches this path; see the module
#: docstring there for why the split is the fix rather than a dodge.
RUNNER = Path(__file__).resolve().parents[1] / "tests" / "corpus_runner.py"


def main(argv):
    if "--run" in argv:
        # Deliberately not run in this process: this module is imported by the
        # watcher, and a `run()` here would be one careless call away from
        # giving a production daemon a test runner's signature.
        print(f"run it directly, so unittest stays out of this process:\n"
              f"  python3 {RUNNER} --run", file=sys.stderr)
        return 2
    payload = _load()
    if payload is None:
        print("no corpus check has run on this machine")
        return 0
    print(json.dumps(payload, indent=2, sort_keys=True))
    message = warning()
    print(f"\nwarning: {message}" if message else "\nwarning: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
