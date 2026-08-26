#!/usr/bin/env python3
"""Reconcile the job pool to the decisions, once, for drift already there.

`sync_board` now does this continuously, so nothing new accumulates. This is
for the backlog that built up while nothing did: 70 pool records saying
`watched` that the session store disagrees with, and 43 watch sessions the pool
has never heard of.

**The ruling this implements.** Sessions are authoritative for lane membership,
because they are what the person decided. The pool is a corpus and its `state`
is derived. Reconciliation flows decisions to pool, never the reverse. A repair
that synced the other way would overwrite real decisions with a stale corpus,
which is the August overwrite incident performed deliberately.

**The rule this refuses to break.** No repair may fabricate a user decision.
The eye is the only route onto the shelf. For records with no evidence of a
decision anywhere -- no session, no history, absent from roles.json, no
timestamp on the record -- the conservative repair is reverting them to `new`.
Worst case, a role once eyed reappears in Found and costs one more tap. The
other worst case is a shelf carrying decisions nobody made.

Defaults to a dry run and writes a full diff. `--apply` takes a timestamped
backup of the whole store first.

  python3 scripts/repair_watch_drift.py --out diff.json
  python3 scripts/repair_watch_drift.py --apply --out diff.json
"""

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import jobs_store  # noqa: E402
import runlock  # noqa: E402
from sync_board import STATUS_STATE  # noqa: E402


def store(*args):
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "store.py"), *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"repair: store {' '.join(args)} failed: {out.stderr[:200]}")
    return json.loads(out.stdout or "[]")


def plan(jobs, sessions, history, roles):
    """Every change this would make, with the category that justifies it."""
    latest = {}
    for event in sorted(history, key=lambda e: e.get("at") or ""):
        if event.get("applicationId"):
            latest[event["applicationId"]] = event.get("event")
    by_id = {s["applicationId"]: s for s in sessions}

    changes, untouched = [], []
    for job_id, job in sorted(jobs.items()):
        state = job.get("state")
        if state not in ("watched",):
            continue
        session = by_id.get(job_id)
        if session is not None:
            want = STATUS_STATE.get(session.get("status"))
            category = f"session says {session.get('status')}"
        elif latest.get(job_id) == "completed":
            want, category = "applied", "submitted; session retired on submission"
        elif job_id in latest:
            want, category = None, f"no session; last event {latest[job_id]}"
        elif job_id in roles:
            want, category = None, "no session, but roles.json remembers it"
        else:
            # Nothing anywhere says a person chose this. Not a session, not an
            # event, not roles.json, and the record carries no decision stamp.
            want, category = "new", "no evidence of any decision"
        if want is None or want == state:
            untouched.append({"id": job_id, "state": state, "why": category})
            continue
        if want == "new":
            # Not a transition and deliberately not one: see
            # jobs_store.clear_undecided_watch. Carried through the plan so the
            # diff shows it like any other change.
            changes.append({"id": job_id, "from": state, "to": want,
                            "category": category, "repair": "clear_undecided_watch",
                            "company": job.get("company"),
                            "title": (job.get("title") or "")[:60]})
            continue
        if want not in jobs_store.TRANSITIONS.get(state, set()):
            untouched.append({"id": job_id, "state": state,
                              "why": f"{category}; {state} -> {want} is not a legal move"})
            continue
        changes.append({"id": job_id, "from": state, "to": want,
                        "category": category,
                        "company": job.get("company"),
                        "title": (job.get("title") or "")[:60]})

    stranded = [{"applicationId": s["applicationId"], "status": s.get("status"),
                 "company": s.get("company"), "role": (s.get("role") or "")[:60]}
                for s in sessions
                if s.get("status") == "watch" and s["applicationId"] not in jobs]
    return changes, untouched, stranded


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes; takes a full backup first")
    ap.add_argument("--out", help="write the complete diff here")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.store)
    jobs = jobs_store.load_jobs(root)
    sessions = store("session-list")
    history = store("history-list")
    roles_path = root / "roles.json"
    roles = json.loads(roles_path.read_text()) if roles_path.exists() else {}

    changes, untouched, stranded = plan(jobs, sessions, history, roles)
    by_category = Counter(c["category"] for c in changes)

    diff = {"applied": False, "changes": changes, "untouched": untouched,
            "strandedSessions": stranded,
            "byCategory": dict(by_category.most_common())}

    if args.apply:
        backup = root.parent / f"{root.name}-backup-{jobs_store.now_stamp().replace(':', '')}"
        shutil.copytree(root, backup)
        diff["backup"] = str(backup)
        with runlock.held(root, jobs_store.POOL_LOCK, note="repair_watch_drift"):
            live = jobs_store.load_jobs(root)
            for change in changes:
                job = live.get(change["id"])
                if job is None or job.get("state") != change["from"]:
                    change["skipped"] = "the record moved since the diff was taken"
                    continue
                if change.get("repair") == "clear_undecided_watch":
                    live[change["id"]] = jobs_store.clear_undecided_watch(
                        job, change["category"])
                else:
                    live[change["id"]] = jobs_store.transition(job, change["to"])
            jobs_store.save_jobs(root, live, note="repair_watch_drift")
        diff["applied"] = True

    print(f"{'APPLIED' if args.apply else 'DRY RUN'}: "
          f"{len(changes)} pool records would change, {len(untouched)} left alone, "
          f"{len(stranded)} watch sessions have no pool record\n")
    for category, n in by_category.most_common():
        print(f"  {n:>4}  {category}")
        for change in [c for c in changes if c["category"] == category][:3]:
            print(f"        {change['from']} -> {change['to']}  "
                  f"{change['company']} - {change['title'][:40]}")
    if diff.get("backup"):
        print(f"\nbackup: {diff['backup']}")
    if args.out:
        Path(args.out).write_text(json.dumps(diff, indent=2, sort_keys=True) + "\n")
        print(f"full diff: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
