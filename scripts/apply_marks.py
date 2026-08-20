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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"
DEPLOY = REPO / "deploy"


def blob_env():
    return toolpath.queue_env()


def queue(cmd, prefix="marked/"):
    r = subprocess.run([toolpath.node(), "queue.mjs", cmd, prefix], cwd=DEPLOY,
                       capture_output=True, text=True, env=blob_env())
    if r.returncode != 0:
        raise SystemExit(f"apply_marks: queue read failed: {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "[]")


def store(args):
    return subprocess.run([sys.executable, str(STORE)] + args, capture_output=True, text=True)


def currently_submitted(history):
    """What is submitted right now, by the latest event for each application.

    Latest event wins. A `completed` can be retracted by a later `abandoned`,
    which is how a mark logged in error is undone. Counting every `completed`
    ever written meant a retracted application could never be confirmed again:
    the click was skipped as "already submitted" while the board correctly
    showed it as not submitted, so it sat in Needs you with a button that
    appeared to do nothing.
    """
    latest = {}
    for event in sorted(history, key=lambda e: e.get("at") or ""):
        if event.get("applicationId"):
            latest[event["applicationId"]] = event
    return {aid for aid, event in latest.items() if event.get("event") == "completed"}


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

    # Read the history BEFORE claiming. Claiming removes the marks from the
    # queue, so failing the duplicate check afterwards threw away the user's
    # clicks: they had pressed Confirm, the card left Needs you, and nothing
    # was ever written. The old comment here claimed the queue was left
    # "claimed-but-unapplied", which is not a state that exists.
    hist = store(["history-list"])
    if hist.returncode != 0:
        raise SystemExit(f"apply_marks: cannot read history: {(hist.stderr or '').strip()[:200]}")
    history = json.loads(hist.stdout or "[]")

    already = currently_submitted(history)

    claimed = queue("claim")
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
