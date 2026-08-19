#!/usr/bin/env python3
"""Apply "Mark submitted" clicks made on the board.

The board cannot write to the store on this machine, so the button queues the
mark and this claims it: logs a `completed` event, retires the watch-list
session, and republishes so Needs you and Submitted agree.

Reads the queue directly with the Blob token in deploy/.env.local rather than
through the password-gated endpoint, so the poller never needs the board's
password and can run unattended.

  python3 scripts/apply_marks.py            # apply everything queued
  python3 scripts/apply_marks.py --check    # exit 0 if anything is waiting
  python3 scripts/apply_marks.py --no-publish
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"
DEPLOY = REPO / "deploy"


def blob_env():
    """The blob token Vercel wrote when the store was created."""
    env = dict(os.environ)
    envfile = DEPLOY / ".env.local"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            if line.startswith("BLOB_READ_WRITE_TOKEN="):
                env["BLOB_READ_WRITE_TOKEN"] = line.split("=", 1)[1].strip().strip('"')
    if not env.get("BLOB_READ_WRITE_TOKEN"):
        raise SystemExit("apply_marks: no BLOB_READ_WRITE_TOKEN. Run: cd deploy && vercel env pull")
    return env


def queue(cmd, prefix="marked/"):
    r = subprocess.run(["node", "queue.mjs", cmd, prefix], cwd=DEPLOY,
                       capture_output=True, text=True, env=blob_env())
    if r.returncode != 0:
        raise SystemExit(f"apply_marks: queue read failed: {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "[]")


def store(args):
    return subprocess.run([sys.executable, str(STORE)] + args, capture_output=True, text=True)


def apply_one(aid):
    """Log the submission and let sync_board retire the session."""
    detail = {}
    loaded = store(["session-load", "--id", aid])
    if loaded.returncode == 0:
        detail = json.loads(loaded.stdout or "{}")

    event = {
        "event": "completed",
        "applicationId": aid,
        "company": detail.get("company", ""),
        "role": detail.get("role", ""),
        "ats": detail.get("ats", ""),
        "source": detail.get("ats") or "board",
        "confirmation": "marked submitted by the user on the board",
    }
    folder = REPO / "applications" / event["company"] if event["company"] else None
    if folder and folder.exists():
        for key, suffix in (("resumeVariant", "_Resume.pdf"), ("coverLetter", "_CoverLetter.pdf")):
            hits = sorted(folder.glob(f"*{suffix}"))
            if len(hits) == 1:
                event[key] = hits[0].name

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(event, f)
        path = f.name
    r = store(["history-append", "--input", path])
    os.unlink(path)
    if r.returncode != 0:
        return {"applicationId": aid, "error": (r.stderr or "").strip()[:120]}
    return {"applicationId": aid, "company": event["company"], "role": event["role"]}


def main():
    ap = argparse.ArgumentParser(description="Apply board 'Mark submitted' clicks.")
    ap.add_argument("--check", action="store_true", help="exit 0 if marks are waiting")
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    if args.check:
        pending = queue("list")
        print(json.dumps({"pending": len(pending)}, indent=2))
        return 0 if pending else 1

    claimed = queue("claim")
    # A user clicking twice must not log two submissions, and anything already
    # submitted is a no-op rather than a duplicate history entry.
    history = json.loads(store(["history-list"]).stdout or "[]")
    already = {e.get("applicationId") for e in history if e.get("event") == "completed"}
    seen, applied, skipped = set(), [], []
    for mark in claimed:
        aid = mark.get("applicationId")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        if aid in already:
            skipped.append({"applicationId": aid, "why": "already recorded as submitted"})
            continue
        applied.append(apply_one(aid))

    result = {"claimed": len(claimed), "applied": applied, "skipped": skipped}
    if applied and not args.no_publish:
        sync = subprocess.run([sys.executable, str(REPO / "scripts" / "sync_board.py")],
                              capture_output=True, text=True)
        try:
            result["board"] = json.loads(sync.stdout or "{}")
        except json.JSONDecodeError:
            result["board"] = {"error": (sync.stderr or "")[:160]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
