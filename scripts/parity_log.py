#!/usr/bin/env python3
"""One JSONL line summarising a parity run. Called by parity_cron.sh.

Separate from the shell so the summarising is testable, and because a heredoc
inside a shell script is where the first version of this silently discarded the
report it was meant to be reading.
"""

import json
import sys
from datetime import datetime, timezone


def summarise(raw, code, at=None):
    at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        report = json.loads(raw)
    except ValueError:
        # Not JSON, so the run could not conclude. That is a failure and the
        # reason is kept: "inconclusive" with no cause is the shape that makes
        # an outage indistinguishable from a quiet success.
        return {"at": at, "ok": False, "inconclusive": True,
                "reason": (raw or "").strip()[:300] or "no output"}
    divergences = len(report.get("divergences", []))
    return {"at": at,
            "ok": code == 0 and divergences == 0,
            "divergences": divergences,
            "transient": len(report.get("transient", [])),
            "mergedFieldDropped": len(report.get("merged_field_dropped", [])),
            # Both asides are logged, or the log cannot show one accumulating.
            # `mergedFieldDropped` went 2, 5, 7 and every reading of it came
            # from this line; pruned losers grow the same way, one per folded
            # record that later leaves the pool.
            "prunedLosers": len(report.get("pruned_losers", [])),
            "reread": bool(report.get("reread")),
            "exit": code}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    raw = open(argv[0], encoding="utf-8").read() if argv else ""
    code = int(argv[1]) if len(argv) > 1 else 1
    print(json.dumps(summarise(raw, code)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
