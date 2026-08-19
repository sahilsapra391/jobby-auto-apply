#!/usr/bin/env python3
"""Background watcher: keeps the board in step with the store, unattended.

Runs as a launchd agent so nothing has to be typed. Every cycle it:

  1. Applies any "Mark submitted" clicks made on the board, deduplicated.
  2. Retires submitted applications out of Watching and republishes.
  3. Claims any Scan requests and records them for the next agent session.

The honest boundary: applying marks and refreshing the board are pure store
operations this can finish on its own. Running an actual job scan needs an
agent, so a Scan click is recorded in `meta.scanRequested` and left for a
Claude Code session to act on rather than being silently dropped.

  python3 scripts/watch.py --once        # one cycle, for testing
  python3 scripts/watch.py --interval 60 # loop (what launchd runs)
  python3 scripts/watch.py --install     # install the launchd agent
  python3 scripts/watch.py --uninstall
"""

import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LABEL = "ai.jobby.watch"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / ".job-application" / "logs"


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def store(args):
    return run([sys.executable, str(REPO / "scripts" / "store.py")] + args)


def cycle():
    """One pass. Returns what changed, so quiet cycles stay quiet."""
    changed = {}

    marks = run([sys.executable, str(REPO / "scripts" / "apply_marks.py")])
    if marks.returncode == 0 and marks.stdout:
        try:
            result = json.loads(marks.stdout)
            if result.get("applied"):
                changed["marksApplied"] = [a.get("company") for a in result["applied"]]
        except json.JSONDecodeError:
            pass

    scans = run([sys.executable, str(REPO / "scripts" / "scan_queue.py"), "--claim"])
    if scans.returncode == 0 and scans.stdout:
        try:
            result = json.loads(scans.stdout)
            if result.get("claimed"):
                # A scan needs an agent; record the request rather than dropping it.
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                store(["meta-set", "--key", "scanRequested", "--value", stamp])
                changed["scanRequested"] = stamp
        except json.JSONDecodeError:
            pass

    # One queued Apply request per cycle, never two at once. Spawns a Claude
    # session, which is the only thing that can build documents and drive a
    # browser; this daemon cannot.
    if run([sys.executable, str(REPO / "scripts" / "run_apply.py"), "--check"]).returncode == 0:
        applied = run([sys.executable, str(REPO / "scripts" / "run_apply.py")])
        try:
            changed["applyRun"] = json.loads(applied.stdout or "{}")
        except json.JSONDecodeError:
            changed["applyRun"] = {"error": (applied.stderr or "")[:200]}

    # Retire anything submitted out of Watching and refresh the published board.
    # Only publish when something actually moved, so this is not redeploying
    # every minute for no reason.
    pending = json.loads(store(["session-list"]).stdout or "[]")
    history = json.loads(store(["history-list"]).stdout or "[]")
    done = {e.get("applicationId") for e in history if e.get("event") == "completed"}
    stale = [s for s in pending if s["applicationId"] in done]
    if stale or changed:
        sync = run([sys.executable, str(REPO / "scripts" / "sync_board.py")])
        try:
            changed["board"] = json.loads(sync.stdout or "{}")
        except json.JSONDecodeError:
            changed["boardError"] = (sync.stderr or "")[:200]
    return changed


def install():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LOG_DIR, 0o700)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(REPO / "scripts" / "watch.py"),
                             "--interval", "30"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(REPO),
        "StandardOutPath": str(LOG_DIR / "watch.log"),
        "StandardErrorPath": str(LOG_DIR / "watch.err"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }
    PLIST.write_bytes(plistlib.dumps(plist))
    run(["launchctl", "unload", str(PLIST)])
    loaded = run(["launchctl", "load", str(PLIST)])
    ok = loaded.returncode == 0
    print(json.dumps({
        "installed": ok, "label": LABEL, "plist": str(PLIST),
        "log": str(LOG_DIR / "watch.log"),
        "error": (loaded.stderr or "").strip()[:200] or None,
    }, indent=2))
    return 0 if ok else 1


def uninstall():
    run(["launchctl", "unload", str(PLIST)])
    if PLIST.exists():
        PLIST.unlink()
    print(json.dumps({"uninstalled": True}, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Keep the board in step, unattended.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=0)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    if args.install:
        return install()
    if args.uninstall:
        return uninstall()

    if args.once or not args.interval:
        changed = cycle()
        print(json.dumps(changed or {"quiet": True}, indent=2, sort_keys=True))
        return 0

    while True:
        try:
            changed = cycle()
            if changed:
                stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                print(f"{stamp} {json.dumps(changed, sort_keys=True)}", flush=True)
        except Exception as exc:  # a watcher that dies on one bad cycle is useless
            print(f"cycle error: {exc}", file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
