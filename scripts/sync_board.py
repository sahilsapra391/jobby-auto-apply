#!/usr/bin/env python3
"""Move submitted applications out of Watching, then republish the board.

An application that has gone out still has a watch-list session, so without
this it appears in both Watching and Submitted at once. The store's own rule is
that the session is deleted on confirmed submission and history is what
persists; this enforces it and refreshes the published board in one step.

  python3 scripts/sync_board.py            # sync and publish
  python3 scripts/sync_board.py --no-publish
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"


def run(args):
    return subprocess.run([sys.executable, str(STORE)] + args,
                          capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser(description="Sync the board with submitted applications.")
    ap.add_argument("--no-publish", action="store_true", help="tidy the store, do not deploy")
    args = ap.parse_args()

    sessions = json.loads(run(["session-list"]).stdout or "[]")
    history = json.loads(run(["history-list"]).stdout or "[]")
    submitted = {e.get("applicationId") for e in history if e.get("event") == "completed"}

    retired = []
    for s in sessions:
        aid = s["applicationId"]
        if aid in submitted:
            # The role index keeps company, role and URL, so the Submitted tab
            # can still link to the posting after the session is gone.
            if run(["session-delete", "--id", aid]).returncode == 0:
                retired.append(aid)

    result = {"retiredFromWatching": retired, "watchingNow": len(sessions) - len(retired),
              "submitted": len(submitted)}

    if not args.no_publish:
        pub = subprocess.run([sys.executable, str(REPO / "scripts" / "publish.py")],
                             capture_output=True, text=True)
        result["published"] = pub.returncode == 0
        for line in (pub.stdout or "").splitlines():
            if line.startswith("Board published:"):
                result["url"] = line.split(": ", 1)[1].strip()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
