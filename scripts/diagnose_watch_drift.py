#!/usr/bin/env python3
"""Why the job pool's `watched` state and the Watching lane disagree.

Read-only, deliberately and completely. Two stores hold overlapping facts and
they have diverged; a repair written before the diagnosis would sync toward
whichever side the author assumed was right, which is the overwrite incident
again, by hand. So this writes nothing and answers one question: for each job
the pool calls `watched`, what does the session store say, and what does the
user actually see?

  python3 scripts/diagnose_watch_drift.py
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import jobs_store  # noqa: E402


def sessions():
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "store.py"),
                          "session-list"], capture_output=True, text=True)
    return {s["applicationId"]: s for s in json.loads(out.stdout or "[]")}


def history():
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "store.py"),
                          "history-list"], capture_output=True, text=True)
    try:
        return json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []


def main():
    jobs = jobs_store.load_jobs(jobs_store.root_dir())
    live = sessions()
    events = history()

    latest = {}
    for e in sorted(events, key=lambda e: e.get("at") or ""):
        if e.get("applicationId"):
            latest[e["applicationId"]] = e.get("event")

    watched = {k for k, v in jobs.items() if v.get("state") == "watched"}
    print(f"pool says `watched`: {len(watched)}")
    print(f"live sessions:       {len(live)} "
          f"({Counter(s.get('status') for s in live.values()).most_common()})\n")

    kinds = Counter()
    examples = {}
    for job_id in watched:
        session = live.get(job_id)
        if session is None:
            kind = (f"no session; last event {latest[job_id]}"
                    if job_id in latest else "no session, no history")
        else:
            kind = f"session status {session.get('status')}"
        kinds[kind] += 1
        examples.setdefault(kind, jobs[job_id])

    print("what the session store says about each of them:")
    for kind, n in kinds.most_common():
        job = examples[kind]
        print(f"  {n:>4}  {kind}")
        print(f"        e.g. {job.get('company')} - {(job.get('title') or '')[:44]}")

    # And the other direction: a watch session whose job is not `watched`.
    watching = {k for k, s in live.items() if s.get("status") == "watch"}
    other_way = Counter()
    for app_id in watching:
        job = jobs.get(app_id)
        other_way[job.get("state") if job else "no job record"] += 1
    print(f"\nwatch sessions ({len(watching)}), by the pool's state for them:")
    for state, n in other_way.most_common():
        print(f"  {n:>4}  {state}")

    print("\nwhat the user sees: the Watching lane is built from sessions, so a "
          f"`watched` job with no session is invisible ({kinds.total() - len(watching & watched)} "
          "of them), and the pool's state is what keeps it out of Found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
