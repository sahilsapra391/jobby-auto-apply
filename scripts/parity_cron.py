#!/usr/bin/env python3
"""The scheduled parity check, as a Python entrypoint launchd can actually run.

    python3 scripts/parity_cron.py

Appends one line per run to `~/.job-application/logs/parity.jsonl`, which is
what the seven-day clock is counted from.

**It is Python rather than the shell script it replaces, and that is not a
style preference.** launchd refused to execute `parity_cron.sh` with

    /bin/sh: .../scripts/parity_cron.sh: Operation not permitted

exit 126, twice, silently as far as the parity log was concerned: the runs
never reached `verify_parity`, so no line was written and the absence looked
like a timer that had simply not fired yet. The cause is the macOS Privacy
control CLAUDE.md already records for the launchd installer, in a new place. A
LaunchAgent may not exec a script living under `~/Documents`; the three existing
services avoid it by running `/opt/anaconda3/bin/python3` with the script as an
argument, so the binary being executed is the interpreter and it already holds
the grant. This does the same.

The shell version is deleted rather than left beside this one. A script that
cannot run on the only machine it targets is a trap for whoever finds it.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import parity_log  # noqa: E402


def main(argv=None):
    root = Path("~/.job-application").expanduser()
    log = root / "logs" / "parity.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)

    import io
    import contextlib
    import verify_parity

    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            code = verify_parity.main(["--json"])
        raw = captured.getvalue()
    except Exception as exc:
        # Never let a crash produce silence. An inconclusive line with the
        # reason on it is the difference between "the check is broken" and
        # "nothing has run", and those look identical in an empty log.
        code, raw = 1, f"{type(exc).__name__}: {exc}"

    line = parity_log.summarise(raw, code)

    # **What this run looked at, recorded beside what it found.** A run that
    # compares less does not fail, it succeeds faster, so the count is the only
    # thing that can tell a thorough green from a shallow one. Best-effort and
    # never fatal: a coverage read that cannot reach the database must not turn
    # a working parity check into a missing one.
    try:
        import parity_coverage
        import migrate
        counts, _ = parity_coverage.summary(migrate.connect(announce=False))
        line["coverage"] = counts
        line = parity_log.with_coverage(line)
    except Exception as exc:                        # noqa: BLE001
        # Kept rather than swallowed: "coverage unknown" and "coverage fine"
        # are different, and only one of them is good news.
        line["coverageError"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        sys.stderr.write(f"parity_cron: coverage unreadable: "
                         f"{line['coverageError']}\n")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
    print(json.dumps(line))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
