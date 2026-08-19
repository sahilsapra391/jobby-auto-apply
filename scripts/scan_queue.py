#!/usr/bin/env python3
"""Poll the board's scan queue and claim requests.

The published board cannot start anything on this machine. The Scan button
parks a request in a queue; this claims it, so a Claude Code session running
`/loop` can notice and run a cycle.

Reads the queue directly with the Blob token in deploy/.env.local, so it never
needs the board's password and can run unattended.

  python3 scripts/scan_queue.py --check   # exit 0 if a scan is pending
  python3 scripts/scan_queue.py --claim   # claim everything pending
  python3 scripts/scan_queue.py --status  # queue depth, no side effects
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"


def blob_env():
    env = dict(os.environ)
    envfile = DEPLOY / ".env.local"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            if line.startswith("BLOB_READ_WRITE_TOKEN="):
                env["BLOB_READ_WRITE_TOKEN"] = line.split("=", 1)[1].strip().strip('"')
    if not env.get("BLOB_READ_WRITE_TOKEN"):
        raise SystemExit("scan_queue: no BLOB_READ_WRITE_TOKEN. Run: cd deploy && vercel env pull")
    return env


def queue(cmd):
    r = subprocess.run(["node", "queue.mjs", cmd, "scan/"], cwd=DEPLOY,
                       capture_output=True, text=True, env=blob_env())
    if r.returncode != 0:
        raise SystemExit(f"scan_queue: queue read failed: {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "[]")


def main():
    ap = argparse.ArgumentParser(description="Poll the board's scan queue.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="exit 0 if a scan is pending")
    g.add_argument("--claim", action="store_true", help="claim all pending requests")
    g.add_argument("--status", action="store_true", help="print queue depth only")
    args = ap.parse_args()

    if args.claim:
        claimed = queue("claim")
        print(json.dumps({"claimed": len(claimed), "requests": claimed}, indent=2))
        return 0 if claimed else 1

    pending = queue("list")
    print(json.dumps({"pending": len(pending), "requests": pending[:5]}, indent=2))
    if args.check:
        return 0 if pending else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
