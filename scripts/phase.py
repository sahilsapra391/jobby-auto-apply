#!/usr/bin/env python3
"""Set the live phase on an in-flight application.

An Apply run takes minutes and passes through several stages. Without this the
board shows nothing between "clicked" and "submitted", which reads as broken.
Called by the agent doing the work, after each stage it finishes.

  python3 scripts/phase.py --id <app-id> --phase "Building resume and cover letter"
  python3 scripts/phase.py --id <app-id> --phase "Filling the form" --detail "12 of 18 fields"
  python3 scripts/phase.py --id <app-id> --done          # clear in-flight state
  python3 scripts/phase.py --id <app-id> --unconfirmed   # a submit with no confirmation
  python3 scripts/phase.py --id <app-id> --not-sent      # the user says it never went in

No path through this file changes the status of a dropped application.
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

#: Where an application lands when a run clicked Submit and saw no
#: confirmation. **Needs you, not In flight**, because nothing is running and
#: only a person can find out what happened: the employer's email or portal is
#: the only witness left. Retry is refused while the latest event is
#: `submitting` (`watch.do_not_start`), so the pending item names the two
#: controls that resolve it instead. Measured 2026-09-12: seven applications in
#: the real store sat at a latest `submitting` with nothing after it, and
#: one of them sat in In flight at phase "Submitting" offering a
#: Retry the device refused and a Stop that resolved nothing.
UNCONFIRMED_PHASE = "Submit unconfirmed"
UNCONFIRMED_DETAIL = ("a submit was attempted and no confirmation was seen; "
                      "check your email or the employer's portal")
UNCONFIRMED_ITEM = {
    "question": ("A submit was attempted and no confirmation was seen. Check "
                 "your email or the employer's portal."),
    "action": ("Press Confirm submitted if it went through, or It did not go "
               "through if it did not."),
}

#: Where it lands once the user says the submit never went in. The `abandoned`
#: event is already on the history by then, so `do_not_start` no longer
#: refuses, and the one pending item says Retry is now a fresh run.
NOT_SENT_PHASE = "Not sent"
NOT_SENT_DETAIL = "you confirmed nothing was sent; Retry starts a fresh run"
NOT_SENT_ITEM = {
    "question": "You confirmed the submit did not go through, so nothing was sent.",
    "action": "Press Retry to start a fresh run, or Drop it.",
}


def main():
    ap = argparse.ArgumentParser(description="Set the phase of an in-flight application.")
    ap.add_argument("--id", required=True)
    ap.add_argument("--phase")
    ap.add_argument("--detail", default="")
    ap.add_argument("--done", action="store_true", help="return it to the watch list")
    ap.add_argument("--restore", action="store_true",
                    help="hand it back to the lane it came from, for a run that failed")
    ap.add_argument("--unconfirmed", action="store_true",
                    help="a run clicked Submit and saw no confirmation: hand it to Needs you")
    ap.add_argument("--not-sent", action="store_true",
                    help="the user says the submit did not go through: hand it to Needs you")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    args = ap.parse_args()

    store = [sys.executable, str(STORE)] + (["--root", args.root] if args.root else [])
    loaded = subprocess.run(store + ["session-load", "--id", args.id],
                            capture_output=True, text=True)
    if loaded.returncode != 0:
        print(json.dumps({"error": "no such application", "id": args.id}))
        return 2
    d = json.loads(loaded.stdout)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # **A dropped application keeps its status, whatever asked.** Every branch
    # below sets a status and only the phase branch used to check. Observed
    # 2026-09-12 on a real application: drop.py ran at 05:52:51Z inside a
    # tick that had already passed `do_not_start`, the tick spawned
    # `run_apply.py`, the runner refused the start as dropped, and its refusal
    # handler called `--restore`. That branch set the status from
    # `previousStatus` with no dropped check, so the application came back as
    # `review`, phase "Stopped", `droppedAt` still set, and Retry worked on it
    # from Needs you. The refusal that existed to honour the drop undid it.
    #
    # Refused here rather than at each caller. The runner is one of several,
    # and the next would find the same hole; the phase branch learned that on
    # 2026-08-27 when a stale apply put a dropped application back in flight
    # ninety seconds after the user dropped it.
    if (d.get("status") or "").lower() == "dropped":
        print(json.dumps({"applicationId": args.id, "refused": "dropped",
                          "why": "the user dropped this application"}))
        return 0

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
        d["phaseAt"] = stamp
    elif args.unconfirmed or args.not_sent:
        # Needs you in both cases, with one pending item saying which. Nothing
        # here writes an event: the `submitting` that put it in this state and
        # the `abandoned` that takes it out are history, appended by the code
        # that witnessed them (`finish.py`, and `apply_marks.py` for the user's
        # own answer).
        d.pop("previousStatus", None)
        d["status"] = "review"
        if args.unconfirmed:
            d["phase"] = UNCONFIRMED_PHASE
            d["phaseDetail"] = args.detail or UNCONFIRMED_DETAIL
            d["pendingFields"] = [dict(UNCONFIRMED_ITEM)]
        else:
            d["phase"] = NOT_SENT_PHASE
            d["phaseDetail"] = args.detail or NOT_SENT_DETAIL
            d["pendingFields"] = [dict(NOT_SENT_ITEM)]
        d["phaseAt"] = stamp
    else:
        if not args.phase:
            print(json.dumps({"error": "--phase required unless --done"}))
            return 2
        # Remember the lane once, on the way in. Recording it on every phase
        # update would overwrite it with "inflight" after the first one and the
        # role could never be handed back.
        if d.get("status") != "inflight" and not d.get("previousStatus"):
            d["previousStatus"] = d.get("status") or "watch"
        d["status"] = "inflight"
        d["phase"] = args.phase
        d["phaseDetail"] = args.detail
        d["phaseAt"] = stamp

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(d, f)
        path = f.name
    r = subprocess.run(store + ["session-save", "--id", args.id, "--input", path],
                       capture_output=True, text=True)
    os.unlink(path)
    if r.returncode != 0:
        print(json.dumps({"error": (r.stderr or "").strip()[:150]}))
        return 2

    # Nothing is published from here. The board reads Postgres, and the
    # session-save above reaches it through the class B mirror.
    print(json.dumps({"id": args.id, "status": d["status"],
                      "previousStatus": d.get("previousStatus"),
                      "phase": d.get("phase")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
