#!/usr/bin/env python3
"""Set the live phase on an in-flight application.

An Apply run takes minutes and passes through several stages. Without this the
board shows nothing between "clicked" and "submitted", which reads as broken.
Called by the agent doing the work, after each stage it finishes.

  python3 scripts/phase.py --id <app-id> --phase "Building resume and cover letter"
  python3 scripts/phase.py --id <app-id> --phase "Filling the form" --detail "12 of 18 fields"
  python3 scripts/phase.py --id <app-id> --done          # clear in-flight state
"""

import argparse
import json
import subprocess
import sys
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"


def main():
    ap = argparse.ArgumentParser(description="Set the phase of an in-flight application.")
    ap.add_argument("--id", required=True)
    ap.add_argument("--phase")
    ap.add_argument("--detail", default="")
    ap.add_argument("--done", action="store_true", help="return it to the watch list")
    ap.add_argument("--restore", action="store_true",
                    help="hand it back to the lane it came from, for a run that failed")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    args = ap.parse_args()

    store = [sys.executable, str(STORE)] + (["--root", args.root] if args.root else [])
    loaded = subprocess.run(store + ["session-load", "--id", args.id],
                            capture_output=True, text=True)
    if loaded.returncode != 0:
        print(json.dumps({"error": "no such application", "id": args.id}))
        return 2
    d = json.loads(loaded.stdout)

    if args.done:
        d["status"] = "watch"
        for key in ("phase", "phaseDetail", "phaseAt", "previousStatus"):
            d.pop(key, None)
    elif args.restore:
        # A run that failed should hand the role back to the lane it came from,
        # not strand it in flight. A retry that fails belongs in Needs you again;
        # an apply that fails belongs in Watching.
        d["status"] = d.pop("previousStatus", None) or "watch"
        d["phase"] = args.phase or "Stopped"
        d["phaseDetail"] = args.detail
        d["phaseAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        if not args.phase:
            print(json.dumps({"error": "--phase required unless --done"}))
            return 2
        # A dropped application is never promoted back into flight by a phase
        # update. This is what made Drop look like it did nothing: the tick
        # processes drops first and then walks the apply queue, and a stale
        # apply for the same id set the phase, which set the status, which put
        # the application straight back in flight ninety seconds after the user
        # dropped it. Observed on 2026-08-27, one tick, same two ids in both
        # `dropped` and `queued`.
        #
        # Refused here rather than by filtering the queue, because the queue is
        # one of several callers and the next one would find the same hole. It
        # also explains a session carrying droppedAt with a live status: the
        # drop wrote its record and this overwrote the one field that mattered.
        if (d.get("status") or "").lower() == "dropped":
            print(json.dumps({"applicationId": args.id, "refused": "dropped",
                              "why": "the user dropped this application"}))
            return 0
        # Remember the lane once, on the way in. Recording it on every phase
        # update would overwrite it with "inflight" after the first one and the
        # role could never be handed back.
        if d.get("status") != "inflight" and not d.get("previousStatus"):
            d["previousStatus"] = d.get("status") or "watch"
        d["status"] = "inflight"
        d["phase"] = args.phase
        d["phaseDetail"] = args.detail
        d["phaseAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(d, f)
        path = f.name
    r = subprocess.run(store + ["session-save", "--id", args.id, "--input", path],
                       capture_output=True, text=True)
    os.unlink(path)
    if r.returncode != 0:
        print(json.dumps({"error": (r.stderr or "").strip()[:150]}))
        return 2

    # Refresh the board so the phase is visible while the work is still running.
    if not args.root:
        subprocess.run([sys.executable, str(REPO / "scripts" / "publish.py")],
                       capture_output=True, text=True)
    print(json.dumps({"id": args.id, "status": d["status"],
                      "previousStatus": d.get("previousStatus"), "phase": d.get("phase")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
