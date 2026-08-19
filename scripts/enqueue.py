#!/usr/bin/env python3
"""Put a request into a board queue from this machine.

The board's buttons are the normal way in. This exists for the two cases they
cannot cover: restoring requests lost to a bad claim, and exercising the watcher
end to end without clicking anything.

Deduped by application id, the same as the board endpoint, so running it twice
does not queue the same role twice.

  python3 scripts/enqueue.py apply <applicationId>
  python3 scripts/enqueue.py scan
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
PREFIXES = {"apply": "apply/", "scan": "scan/", "marked": "marked/"}


def blob_env():
    return toolpath.queue_env()


def main():
    ap = argparse.ArgumentParser(description="Queue a board request from this machine.")
    ap.add_argument("queue", choices=sorted(PREFIXES))
    ap.add_argument("application_id", nargs="?",
                    help="required for apply and marked; scan generates its own id")
    args = ap.parse_args()

    aid = args.application_id
    if args.queue == "scan" and not aid:
        aid = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-000Z")
    if not aid:
        raise SystemExit(f"enqueue: {args.queue} needs an application id")

    r = subprocess.run([toolpath.node(), "queue.mjs", "enqueue", PREFIXES[args.queue], aid],
                       cwd=DEPLOY, capture_output=True, text=True, env=blob_env())
    if r.returncode != 0:
        raise SystemExit(f"enqueue failed: {(r.stderr or '').strip()[:300]}")
    print(json.dumps({"queue": args.queue, **json.loads(r.stdout or "{}")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
