#!/usr/bin/env python3
"""Drop an application: the user has decided not to pursue it.

Distinct from `abandoned`, which records that a submission attempt failed or was
logged in error. Dropping is a decision, so it keeps the session rather than
deleting it, and it records where the application had got to at the moment the
decision was made. Knowing a role was dropped while it sat in Watching is a very
different fact from knowing it was dropped halfway through filling a form.

Dropping something in flight also stops the agent working on it. The runner is
spawned in its own session, so the whole process group goes, not just the
supervising python.

  python3 scripts/drop.py --id <applicationId>
  python3 scripts/drop.py --id <applicationId> --reason "8+ years required"
  python3 scripts/drop.py --id <applicationId> --restore    # put it back in Watching
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import watch  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"

# Where a card was when the user dropped it, in words the board can show.
LANE_NAMES = {
    "watch": "Watching",
    "inflight": "In flight",
    "review": "Needs you",
    "active": "Needs you",
}


def store(args, stdin=None):
    return subprocess.run([sys.executable, str(STORE)] + args,
                          capture_output=True, text=True, input=stdin)


def load(aid):
    r = store(["session-load", "--id", aid])
    if r.returncode != 0:
        return None
    return json.loads(r.stdout or "{}")


def save(session):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(session, f)
        path = f.name
    r = store(["session-save", "--id", session["applicationId"], "--input", path])
    os.unlink(path)
    if r.returncode != 0:
        raise SystemExit(f"drop: could not save session: {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "{}")


def describe_stage(session):
    """What the application was doing when it was dropped."""
    status = session.get("status")
    if status == "inflight":
        phase = session.get("phase") or "Queued"
        detail = session.get("phaseDetail") or ""
        return f"{phase}{f' ({detail})' if detail else ''}"
    if status in ("review", "active"):
        pending = session.get("pendingFields") or []
        if pending:
            first = pending[0].get("question", "")
            more = f" (+{len(pending) - 1} more)" if len(pending) > 1 else ""
            return f"waiting on you: {first[:120]}{more}"
        return "ready to submit, waiting on you"
    if status == "watch":
        return "not started"
    return status or "unknown"


def stop_agent(aid):
    """Kill the runner working on this application, if it is this one.

    Checks the note on the lock before signalling anything: the lock could be
    held by a different application by the time this runs, and killing the wrong
    run would be worse than not killing at all.
    """
    # Every apply slot, not just one. With three running concurrently the lock
    # holding this application could be any of them, and the note is what says
    # which; signalling by slot position would kill somebody else's run.
    for name in watch.APPLY_LOCKS + ["scan"]:
        holder = watch.lock_holder(name)
        if not holder or holder.get("note") != aid:
            continue
        pid = holder.get("pid")
        try:
            # The runner is started with start_new_session=True, so it leads its
            # own process group. Signal the group, or the spawned claude session
            # under it keeps going and keeps driving the browser.
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            watch.lock_path(name).unlink(missing_ok=True)
            return {"stopped": False, "why": "the runner had already exited"}
        # Give it a moment to go down cleanly, then insist.
        for _ in range(20):
            time.sleep(0.25)
            if not watch.pid_alive(pid):
                break
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        watch.lock_path(name).unlink(missing_ok=True)
        return {"stopped": True, "pid": pid}
    return {"stopped": False, "why": "nothing was running for this application"}


def drop(aid, reason=""):
    session = load(aid)
    if session is None:
        return {"applicationId": aid, "error": "no session with that id"}
    if session.get("status") == "dropped":
        return {"applicationId": aid, "alreadyDropped": True}

    was = session.get("status", "unknown")
    agent = stop_agent(aid) if was == "inflight" else {"stopped": False}

    session["droppedFrom"] = LANE_NAMES.get(was, was)
    session["droppedStage"] = describe_stage(session)
    session["droppedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if reason:
        session["droppedReason"] = reason
    session["status"] = "dropped"
    # Phase text would otherwise keep claiming the agent is mid-form.
    for key in ("phase", "phaseDetail", "phaseAt"):
        session.pop(key, None)
    save(session)
    return {
        "applicationId": aid,
        "company": session.get("company", ""),
        "role": session.get("role", ""),
        "droppedFrom": session["droppedFrom"],
        "droppedStage": session["droppedStage"],
        "agent": agent,
    }


def restore(aid):
    session = load(aid)
    if session is None:
        return {"applicationId": aid, "error": "no session with that id"}
    for key in ("droppedFrom", "droppedStage", "droppedAt", "droppedReason"):
        session.pop(key, None)
    session["status"] = "watch"
    save(session)
    return {"applicationId": aid, "restored": True}


def main():
    ap = argparse.ArgumentParser(description="Drop or restore an application.")
    ap.add_argument("--id", required=True)
    ap.add_argument("--reason", default="")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    result = restore(args.id) if args.restore else drop(args.id, args.reason)
    if not result.get("error") and not args.no_publish:
        sync = subprocess.run([sys.executable, str(REPO / "scripts" / "sync_board.py")],
                              capture_output=True, text=True)
        try:
            result["board"] = json.loads(sync.stdout or "{}")
        except json.JSONDecodeError:
            result["board"] = {"error": (sync.stderr or "")[:160]}
    print(json.dumps(result, indent=2))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
