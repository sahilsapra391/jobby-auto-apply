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


def read(args):
    """Read from the store, or fail loudly.

    store.py exits 2 with a message on stderr and nothing on stdout, so
    `json.loads(run(args).stdout or "[]")` turns any store failure into a
    convincing empty result. One corrupt session file is enough to trigger it,
    and reasoning about the board from empty data is how a real state gets
    quietly overwritten by a wrong one.
    """
    r = run(args)
    if r.returncode != 0:
        raise SystemExit(f"sync_board: store {' '.join(args)} failed: "
                         f"{(r.stderr or '').strip()[:300]}")
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        raise SystemExit(f"sync_board: store {' '.join(args)} returned junk")


def main():
    ap = argparse.ArgumentParser(description="Sync the board with submitted applications.")
    ap.add_argument("--no-publish", action="store_true", help="tidy the store, do not deploy")
    args = ap.parse_args()

    sessions = read(["session-list"])
    history = read(["history-list"])
    # Latest event wins. A `completed` retracted by a later `abandoned` is not a
    # submission, and retiring it here would drop the role off the board
    # entirely: gone from Watching, and correctly absent from Submitted.
    latest = {}
    for e in sorted(history, key=lambda e: e.get("at") or ""):
        if e.get("applicationId"):
            latest[e["applicationId"]] = e
    submitted = {a for a, e in latest.items() if e.get("event") == "completed"}

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
        if not result["published"]:
            # Carry the reason up. Reporting published:false and dropping the
            # message meant a spent deployment quota looked identical to a
            # broken build, and the watcher could not tell it should stop
            # trying rather than retry every tick.
            blob = ((pub.stderr or "") + (pub.stdout or "")).strip()
            result["error"] = blob[-500:] or f"publish.py exited {pub.returncode}"
        for line in (pub.stdout or "").splitlines():
            if line.startswith("Board published:"):
                result["url"] = line.split(": ", 1)[1].strip()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
