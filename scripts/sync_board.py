#!/usr/bin/env python3
"""Move submitted applications out of Watching, and reconcile the pool.

An application that has gone out still has a watch-list session, so without
this it appears in both Watching and Submitted at once. The store's own rule is
that the session is deleted on confirmed submission and history is what
persists; this enforces it. It also flows each session's decision into the job
pool and archives watched roles whose posting has closed.

  python3 scripts/sync_board.py

Nothing is published from here. The board reads Postgres, which the class A and
class B mirrors write as the store changes. `--no-publish` is still accepted
and does nothing: a watcher that has not been restarted since this changed
still passes it, and refusing the flag would fail its reconcile.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"

sys.path.insert(0, str(REPO / "scripts"))
import drop  # noqa: E402  (needs the path above)
import jobs_store  # noqa: E402


def run(args):
    return subprocess.run([sys.executable, str(STORE)] + args,
                          capture_output=True, text=True)


def read(args):
    """Read from the store, or fail loudly.

    store.py exits 2 with a message on stderr and nothing on stdout, so
    `json.loads(run(args).stdout or "[]")` turns any store failure into a
    convincing empty result. One corrupt session file is enough to trigger it,
    and reasoning about the board from empty data is how a real state gets
    quietly overwritten by a wrong one.
    """
    r = run(args)
    if r.returncode != 0:
        raise SystemExit(f"sync_board: store {' '.join(args)} failed: "
                         f"{(r.stderr or '').strip()[:300]}")
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        raise SystemExit(f"sync_board: store {' '.join(args)} returned junk")


# What a session's status means about the posting, in the pool's vocabulary.
# The same map ingest already uses to decide whether an incoming posting is
# spoken for, kept identical on purpose: two vocabularies for one fact is how
# the halves drift apart in the first place.
STATUS_STATE = {"inflight": "applied", "review": "watched", "active": "watched",
                "watch": "watched", "dropped": "passed"}


def reconcile_pool(sessions, latest):
    """Move pool records to match the decisions. Returns what changed.

    A submitted application outranks its session status: `completed` is the
    last thing that happened to it whatever lane it was sitting in.

    `applied` and `passed` are terminal in the pool's own state machine, so a
    record already in one is left alone and an illegal move is skipped rather
    than forced. Nothing here creates a job record; a decision about a posting
    the corpus has never seen is the separate, larger problem.
    """
    want = {}
    for session in sessions:
        state = STATUS_STATE.get(session.get("status"))
        if state:
            want[session["applicationId"]] = state
    for aid, event in latest.items():
        if event.get("event") == "completed":
            want[aid] = "applied"

    changed = []
    try:
        root = jobs_store.root_dir()
        with jobs_store.edit(root, note="sync_board reconcile") as (jobs, edit_state):
            for aid, state in sorted(want.items()):
                job = jobs.get(aid)
                if job is None or job.get("state") == state:
                    continue
                if state not in jobs_store.TRANSITIONS.get(job.get("state"), set()):
                    continue
                changed.append({"id": aid, "from": job.get("state"), "to": state})
                jobs[aid] = jobs_store.transition(job, state)
            if not changed:
                edit_state["write"] = False
    except Exception as exc:                      # noqa: BLE001 - reported, not fatal
        # A busy lock is the ordinary case rather than a fault, and the
        # retirement below still has to run.
        return {"changed": [], "why": f"{type(exc).__name__}: {exc}"[:160]}
    return {"changed": changed}


def main():
    ap = argparse.ArgumentParser(description="Sync the board with submitted applications.")
    ap.add_argument("--no-publish", action="store_true",
                    help="accepted for compatibility; nothing publishes")
    ap.parse_args()

    sessions = read(["session-list"])
    history = read(["history-list"])
    # Latest event wins. A `completed` retracted by a later `abandoned` is not a
    # submission, and retiring it here would drop the role off the board
    # entirely: gone from Watching, and correctly absent from Submitted.
    latest = {}
    for e in sorted(history, key=lambda e: e.get("at") or ""):
        if e.get("applicationId"):
            latest[e["applicationId"]] = e
    submitted = {a for a, e in latest.items() if e.get("event") == "completed"}

    # Jobs the employer has taken down. A session's applicationId is the job
    # id, so the pool is the authority on whether the posting still exists.
    #
    # Watching only, and deliberately. Submitted, In flight and Needs you are
    # things the user did; a closed posting does not undo them and they keep
    # their cards. Watching is a shelf of roles still open to apply to, and a
    # role that cannot be applied to any more does not belong on it.
    closed = {}
    try:
        pool = jobs_store.load_jobs(jobs_store.root_dir())
        closed = {job_id: job.get("closedAt") for job_id, job in pool.items()
                  if job.get("state") == "expired"}
    except (OSError, ValueError, jobs_store.JobsStoreError):
        # An unreadable pool means we do not know what is closed. Retiring
        # nothing is the safe answer; retiring everything is not.
        closed = {}

    # Flow the user's decisions into the pool, before anything is retired.
    #
    # Sessions are authoritative for lane membership: they are what the person
    # did, and the pool is a corpus whose `state` is derived from them.
    # Reconciliation therefore runs one way only, decisions to pool, and never
    # back. That direction is the whole ruling; reversed, a stale pool would
    # overwrite a real decision.
    #
    # This exists because nothing did it. `finish.py` writes the `completed`
    # event and never touches the pool, and the loop below then deletes the
    # session, so a submitted role's pool record stayed `watched` for good.
    # Every submission added one, which is why fourteen of them had piled up.
    # Doing it here rather than in finish.py makes it continuous: any drift
    # from any cause is corrected on the next reconcile, rather than only the
    # drift created from now on.
    #
    # Before the retirement loop, deliberately: after it, the session that
    # carries the decision is already gone.
    reconciled = reconcile_pool(sessions, latest)

    retired, dropped_closed = [], []
    for s in sessions:
        aid = s["applicationId"]
        if aid in submitted:
            # The role index keeps company, role and URL, so the Submitted tab
            # can still link to the posting after the session is gone.
            if run(["session-delete", "--id", aid]).returncode == 0:
                retired.append(aid)
        elif s.get("status") == "watch" and aid in closed:
            # Archived rather than deleted. Nobody misses a Found card they
            # never looked at, but a Watching entry was chosen by hand, and a
            # starred role silently disappearing reads as the board losing
            # things. Dropped already records the lane, the stage it reached
            # and a reason, which is exactly the trace this needs, so this
            # reuses it instead of inventing a second archive.
            when = (closed[aid] or "")[:10]
            reason = ("posting closed by the employer"
                      + (f", noticed on {when}" if when else ""))
            if not drop.drop(aid, reason).get("error"):
                dropped_closed.append(aid)

    result = {"reconciledToPool": reconciled.get("changed", []),
              "retiredFromWatching": retired,
              "closedAndArchived": dropped_closed,
              "watchingNow": len(sessions) - len(retired) - len(dropped_closed),
              "submitted": len(submitted)}
    if reconciled.get("why"):
        result["reconcileSkipped"] = reconciled["why"]

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
