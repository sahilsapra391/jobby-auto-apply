#!/usr/bin/env python3
"""The two ends of a submission, each in one call.

Submitting used to cost seven separate round trips: re-read the session to see
if the user cancelled, run the duplicate guard, write the `submitting`
write-ahead event, set the phase, write the `completed` event, set the phase
again, and sync the board. Each one is the agent stopping to wait.

Two commands instead, and neither weakens a check:

    python3 scripts/finish.py --id <id> --pre-submit
        Re-reads the session and runs the duplicate guard. If either says stop,
        it writes NOTHING and reports proceed:false. Only when both pass does it
        write the `submitting` write-ahead event and set the phase. So the
        write-ahead still happens strictly before the click, which is the point
        of it: a run that dies mid-submit leaves evidence it was trying.

    python3 scripts/finish.py --id <id> --completed --confirmation '<quoted>' \\
        --resume <path> --cover <path>
        Writes the completed event and syncs the board.

    python3 scripts/finish.py --id <id> --park --pending 'account' --detail '<what>'
        Sends it to Needs you with the blocker named, and syncs the board.

Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"

# --root is exported as JOB_APPLICATION_DIR rather than passed to each child.
# Every script here resolves the store through store.root_dir(), which reads
# that variable, and children inherit it. Passing a flag instead would need each
# child to accept one, and dupe_check.py does not.


def call(args, capture=True):
    return subprocess.run([sys.executable] + [str(a) for a in args],
                          capture_output=capture, text=True)


def read_json(args, default=None):
    r = call(args)
    text = (r.stdout or "").strip()
    if r.returncode != 0 or not text:
        if default is not None:
            return default
        raise SystemExit(f"finish: {' '.join(str(a) for a in args[:2])} failed: "
                         f"{(r.stderr or '').strip()[:200]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if default is not None:
            return default
        raise SystemExit(f"finish: unparseable output: {text[:200]}")


def append_event(payload):
    r = subprocess.run([sys.executable, str(STORE), "history-append", "--input", "-"]
                      ,
                       input=json.dumps(payload), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"finish: history-append failed: {(r.stderr or '').strip()[:300]}")
    return json.loads(r.stdout or "{}")


def set_phase(aid, phase, detail=""):
    """Update the phase, carrying the store root through.

    phase.py republishes the board whenever --root is absent, so leaving it off
    meant a run against a test store rendered that store's fixtures over the
    real board. The sync_board guard below did not cover this second path out.
    """
    args = [REPO / "scripts" / "phase.py", "--id", aid, "--phase", phase,
            "--detail", detail]
    redirect = os.environ.get("JOB_APPLICATION_DIR")
    if redirect:
        args += ["--root", redirect]
    call(args)


def sync_board():
    """Tidy the store and republish, unless we are pointed at a test store.

    sync_board.py publishes by default. With JOB_APPLICATION_DIR redirected, the
    board built from that store describes a handful of fixture rows, and
    publishing it would overwrite the real one. That has happened once already
    and it looked exactly like every application vanishing, so the guard is
    structural rather than a note telling people to be careful.
    """
    args = [REPO / "scripts" / "sync_board.py"]
    if os.environ.get("JOB_APPLICATION_DIR"):
        args.append("--no-publish")
    r = call(args)
    if r.returncode != 0:
        return {"synced": False, "error": (r.stderr or "").strip()[:200] or "sync failed"}
    try:
        out = json.loads((r.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return {"synced": False, "error": "sync_board gave no readable result"}
    if "--no-publish" in [str(a) for a in args]:
        return {"synced": True, "published": False, "reason": "test store"}
    # sync_board.py exits 0 even when the publish fails and only says so in its
    # output. Reporting success off the exit code hid a dead board behind a
    # cheerful result.
    return {"synced": True, "published": bool(out.get("published")),
            **({"error": out.get("error")} if out.get("error") else {})}


def session(aid):
    return read_json([STORE, "session-load", "--id", aid], default={})


def pre_submit(aid):
    """Everything that must be true before the click, then the write-ahead."""
    entry = session(aid)
    if not entry:
        return {"proceed": False, "reason": "no session found for this application"}
    if (entry.get("status") or "").lower() == "dropped":
        return {"proceed": False,
                "reason": "the user cancelled this application while you were working. "
                          "Stop. Do not submit and do not change the status back."}

    # dupe_check exits 1 when it finds a duplicate, so its verdict lives in
    # stdout regardless of the exit code. Reading it with a default of
    # "not a duplicate" turned the refusal itself into a pass, which is how a
    # guard ends up worse than no guard at all. Parse the output; if there is
    # none to parse, refuse. A missed application costs a day. A second
    # application to the same job is sent under the user's name and cannot be
    # taken back.
    r = call([REPO / "scripts" / "dupe_check.py", "--guard", aid])
    try:
        guard = json.loads((r.stdout or "").strip())
    except json.JSONDecodeError:
        return {"proceed": False,
                "reason": "the duplicate guard did not answer, so this cannot be "
                          "cleared to submit: " + ((r.stderr or "").strip()[:200] or
                                                   "no output"),
                "guardExitCode": r.returncode}
    if guard.get("duplicate") is None:
        return {"proceed": False,
                "reason": "the duplicate guard could not read the history, so this "
                          "cannot be cleared to submit: "
                          + str(guard.get("unavailable", "unknown"))[:200]}
    if guard.get("duplicate"):
        return {"proceed": False, "reason": "this would be a second application to the "
                                            "same job", "guard": guard}

    append_event({"event": "submitting", "applicationId": aid,
                  "company": entry.get("company"), "role": entry.get("role"),
                  "url": entry.get("url")})
    set_phase(aid, "Submitting", "write-ahead logged")
    return {"proceed": True, "company": entry.get("company"), "role": entry.get("role"),
            "wroteWriteAhead": True}


def completed(aid, confirmation, resume, cover):
    entry = session(aid)
    if not entry.get("company"):
        # Writing the event anyway would put a row with no company and no role
        # on the board, which reads as a corrupted record of a real submission.
        raise SystemExit(f"finish: no session for {aid}, so the company and role "
                         f"behind this submission cannot be recorded. Do not "
                         f"re-submit; report it instead.")
    payload = {"event": "completed", "applicationId": aid,
               "company": entry.get("company"), "role": entry.get("role"),
               "url": entry.get("url"), "confirmation": confirmation}
    # Filenames, not paths. The publish bundle indexes documents by basename, and
    # storing a full path here silently dropped them from the bundle and produced
    # dashboard links to a document the route could not find.
    if resume:
        payload["resumeVariant"] = Path(resume).name
    if cover:
        payload["coverLetter"] = Path(cover).name
    event = append_event(payload)
    set_phase(aid, "Verifying", "confirmation recorded")
    return {"logged": True, "eventId": event.get("eventId"),
            "synced": sync_board(),
            "resumeVariant": payload.get("resumeVariant"),
            "coverLetter": payload.get("coverLetter")}


def park(aid, blockers, reason):
    """Send it to Needs you with the blocker named.

    A pendingField is an object carrying the question and why it is stuck, not a
    bare string; the store rejects anything else, and it must never carry a
    `value`, because answers belong in the answer store rather than in a session.
    """
    entry = session(aid)
    if not entry:
        raise SystemExit(f"finish: no session for {aid}, nothing to park")
    if (entry.get("status") or "").lower() == "dropped":
        # The same check pre-submit makes. Without it, parking overwrites the
        # cancellation with "review", the application reappears in Needs you,
        # and the next Retry sends something the user called off.
        return {"parked": False,
                "reason": "the user cancelled this application while you were "
                          "working. Leave it dropped."}
    entry["applicationId"] = aid
    entry["status"] = "review"
    entry["pendingFields"] = [
        {"field": b, "question": b, "reason": reason or "needs the user"}
        for b in blockers
    ]
    r = subprocess.run([sys.executable, str(STORE), "session-save", "--id", aid,
                        "--input", "-"],
                       input=json.dumps(entry), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"finish: session-save failed: {(r.stderr or '').strip()[:300]}")
    return {"parked": True, "pendingFields": entry["pendingFields"],
            "synced": sync_board()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pre-submit", action="store_true")
    mode.add_argument("--completed", action="store_true")
    mode.add_argument("--park", action="store_true")
    ap.add_argument("--confirmation")
    ap.add_argument("--resume")
    ap.add_argument("--cover")
    ap.add_argument("--pending", action="append", default=[],
                    help="the blocker, as the user will read it. Repeatable.")
    ap.add_argument("--detail", default="")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    args = ap.parse_args()
    if args.root:
        os.environ["JOB_APPLICATION_DIR"] = str(Path(args.root).expanduser())

    if args.pre_submit:
        out = pre_submit(args.id)
    elif args.completed:
        if not (args.confirmation or "").strip():
            raise SystemExit("finish: --completed needs --confirmation with the exact "
                             "wording you saw. Without it there is no evidence the "
                             "application went through, and an unverified completion is "
                             "what makes the board disagree with reality.")
        out = completed(args.id, args.confirmation, args.resume, args.cover)
    else:
        if not args.pending:
            raise SystemExit("finish: --park needs at least one --pending naming the "
                             "blocker, or the board shows a stuck row with no reason.")
        out = park(args.id, args.pending, args.detail)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
