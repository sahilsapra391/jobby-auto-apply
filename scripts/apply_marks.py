#!/usr/bin/env python3
"""Apply "Mark submitted" clicks made on the board.

The board cannot write to the store on this machine, so the button queues the
mark and this claims it: logs a `completed` event, retires the watch-list
session, and republishes so Needs you and Submitted agree.

**This is the second half of a fill-only handoff and the only half a person
can see.** A fill-only run has no `pre_submit` and no `record_completed` on its
MCP server (`application_mcp.SUBMISSION_TOOLS`), so it fills the form, parks
with the handoff, and stops. The person presses the form's own Submit and then
Confirm submitted, and this is what turns that into a record. No nonce, and
that is right rather than a shortcut: the nonce proves the agent that clicked
Submit is the agent that recorded the confirmation, and on this path no agent
clicked anything. `finish.py --completed` stays nonce-gated for the agent path
and nothing here touches it.

Reads the queue directly with the Blob token in deploy/.env.local rather than
through the password-gated endpoint, so the poller never needs the board's
password and can run unattended.

  python3 scripts/apply_marks.py            # apply everything queued
  python3 scripts/apply_marks.py --check    # exit 0 if anything is waiting
  python3 scripts/apply_marks.py --no-publish
  python3 scripts/apply_marks.py --unsent   # apply "It did not go through" clicks

**`--unsent` is the other answer to an unresolved submit.** A run that clicked
Submit and saw no confirmation leaves a latest `submitting` event, and only a
person can say what happened. Confirm submitted is one answer and writes
`completed`. "It did not go through" is the other and writes `abandoned`, with
`confirmedBy: user` and a note, then hands the application back to Needs you
saying a fresh Retry is now possible. It is accepted only while the latest
event is `submitting`: an `abandoned` written over a `completed` retracts a
submission, which is a different claim from "this submit never went in", and a
click on a stale card must not make it.
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
PHASE = REPO / "scripts" / "phase.py"
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

    # **Who says it went in, and what was seen, are two claims and they no
    # longer share a field.**
    #
    # `confirmation` means "the employer's own words on the page the agent
    # read", and both boards render a green `confirmed` chip from it with that
    # text as the proof. This path put the sentence "marked submitted by the
    # user on the board" in it, so 11 of the 104 completed events on this
    # machine (measured 2026-09-07, against 69 with real page text and 24 with
    # none) rendered as confirmed with a claim about a button press standing in
    # for the employer's confirmation. That is the salvaged-answer join in
    # miniature: a record presented as evidence of a thing it is not evidence
    # of.
    #
    # So this writes `confirmedBy` and no `confirmation`. Nobody read a page.
    # The application really was submitted -- a person did it by hand, which is
    # the whole point of a fill-only handoff -- and a board that says so in its
    # own words is telling the truth, where one that borrows the employer's is
    # not. Third state, named, rather than the convenient empty answer or the
    # convenient full one.
    event = {
        "event": "completed",
        "applicationId": aid,
        "company": detail.get("company", ""),
        "role": detail.get("role", ""),
        "ats": detail.get("ats", ""),
        "source": detail.get("ats") or "board",
        "confirmedBy": "user",
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


def latest_events(history):
    """The latest event for each application, by its own timestamp."""
    latest = {}
    for event in sorted(history, key=lambda e: e.get("at") or ""):
        if isinstance(event, dict) and event.get("applicationId"):
            latest[event["applicationId"]] = event
    return latest


#: What the `abandoned` event says about itself. A person said so, and nobody
#: read a page, so it carries `confirmedBy` and a note and never a
#: `confirmation`, on the same rule `apply_one` follows for `completed`.
NOT_SENT_NOTE = "the user confirmed the submit did not go through; nothing was sent"


def apply_unsent(aid, latest):
    """Record the user's answer that an unresolved submit never went in.

    Refused unless `latest` is a `submitting` event. Returns a result with
    `applied` or `skipped` set, and `error` when the store refused a write.
    """
    if not latest or latest.get("event") != "submitting":
        was = (latest or {}).get("event") or "nothing"
        return {"applicationId": aid, "skipped": True,
                "why": f"the latest event is {was}, not an unresolved submit"}
    detail = {}
    loaded = store(["session-load", "--id", aid])
    if loaded.returncode == 0:
        try:
            detail = json.loads(loaded.stdout or "{}")
        except json.JSONDecodeError:
            detail = {}
    # A session still in flight may be a run between `pre_submit` and
    # `record_completed`, and an `abandoned` under it would fail the agent's
    # own confirmation. The board offers this only in Needs you and the route
    # refuses it in flight; this is the same rule on the machine that writes.
    if detail.get("status") == "inflight":
        return {"applicationId": aid, "skipped": True,
                "why": "it is still in flight, so a run may yet confirm the submit"}
    event = {
        "event": "abandoned",
        "applicationId": aid,
        "company": detail.get("company") or latest.get("company", ""),
        "role": detail.get("role") or latest.get("role", ""),
        "ats": detail.get("ats") or latest.get("ats", ""),
        "source": "board",
        "confirmedBy": "user",
        "note": NOT_SENT_NOTE,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(event, f)
        path = f.name
    r = store(["history-append", "--input", path])
    os.unlink(path)
    if r.returncode != 0:
        return {"applicationId": aid, "error": (r.stderr or "").strip()[:120]}
    # The event is the fact; the lane move is the board catching up with it.
    # A dropped session is left dropped (phase.py refuses), and that is fine:
    # the submit is resolved either way.
    moved = subprocess.run([sys.executable, str(PHASE), "--id", aid, "--not-sent"],
                           capture_output=True, text=True)
    result = {"applicationId": aid, "applied": True, "company": event["company"],
              "role": event["role"]}
    if moved.returncode != 0:
        result["laneNotMoved"] = (moved.stderr or moved.stdout or "").strip()[:160]
    return result


def apply_unsent_queue():
    """Claim every "It did not go through" click and apply each one."""
    # History before the claim, for the reason `main` gives: a failure after
    # claiming throws the user's clicks away.
    hist = store(["history-list"])
    if hist.returncode != 0:
        raise SystemExit(f"apply_marks: cannot read history: {(hist.stderr or '').strip()[:200]}")
    latest = latest_events(json.loads(hist.stdout or "[]"))
    claimed = queue("claim", "unsent/")
    seen, applied, skipped = set(), [], []
    for mark in claimed:
        aid = mark.get("applicationId")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        result = apply_unsent(aid, latest.get(aid))
        (skipped if result.get("skipped") else applied).append(result)
    return {"claimed": len(claimed), "applied": applied, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser(description="Apply board 'Mark submitted' clicks.")
    ap.add_argument("--check", action="store_true", help="exit 0 if marks are waiting")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--unsent", action="store_true",
                    help="apply 'It did not go through' clicks instead")
    args = ap.parse_args()

    if args.unsent:
        print(json.dumps(apply_unsent_queue(), indent=2, sort_keys=True))
        return 0

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
