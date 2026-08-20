#!/usr/bin/env python3
"""The eye, and the pass: a Found job becoming a decision.

👁 moves a job out of Found and into Watching, where it gets the treatment
Watching already gives a role — Apply right there, View posting, ×. That means
a session, because Watching is rendered from sessions and always has been.

The session's applicationId **is** the job id. That is what makes the eye
idempotent for free: the board re-sends remembered clicks on every load, so the
second 👁 on one job has to find the session already there rather than create a
second one, and matching on a shared id needs no bookkeeping to get wrong.

Pass writes the tombstone. A passed job is never offered again, and the
tombstone outlives the job record so pruning cannot resurrect it.

Neither of these invents prose. Phase 1 promotes a job before anything has read
the posting, so the card carries the rubric breakdown, labelled as exactly that.

  python3 scripts/promote.py --id <jobId>            # the eye
  python3 scripts/promote.py --id <jobId> --pass     # pass on it
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store  # noqa: E402
import match_score  # noqa: E402
import toolpath  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"
# The Watching card shows a handful of skills, not a tag dump.
MAX_SKILLS = 6


def store_call(root, args, payload=None):
    cmd = [sys.executable, str(STORE)]
    if root:
        cmd += ["--root", str(root)]
    tmp = None
    if payload is not None:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="promote.")
        with open(fd, "w") as handle:
            json.dump(payload, handle)
        args = args + ["--input", tmp]
    try:
        return subprocess.run(cmd + args, capture_output=True, text=True,
                              env=toolpath.enriched_env())
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def session_from_job(job):
    """The Watching entry for a job the user pressed the eye on.

    Only facts the job record actually holds. `alignment` is the rubric spelled
    out rather than a sentence pretending somebody read the posting.
    """
    location = job.get("location") or {}
    posted = (job.get("postedAt") or "")[:10] or None
    return {
        "applicationId": job["id"],
        "company": job.get("company") or "",
        "role": job.get("title") or "",
        "status": "watch",
        "url": job.get("canonicalUrl") or "",
        "source": job.get("canonicalSource") or "",
        "location": location.get("raw") or "",
        "region": location.get("country") or "",
        "coreSkills": (job.get("tags") or [])[:MAX_SKILLS],
        "postedDate": posted,
        "foundAt": job.get("firstSeenAt"),
        "fitScore": (job.get("score") or {}).get("total"),
        "alignment": match_score.explain(job.get("score")),
        "seenOnBoards": len({s.get("source") for s in (job.get("sources") or [])
                            if s.get("source")}),
        "promotedFrom": "found",
    }


def watch(root, job_id):
    """👁. Idempotent per job id, because the board re-sends clicks it remembers."""
    existing = store_call(root, ["session-load", "--id", job_id])
    if existing.returncode == 0:
        jobs = jobs_store.load_jobs(root)
        if job_id in jobs and jobs[job_id].get("state") != "watched":
            jobs_store.mark_watched(root, job_id)
        return {"watched": job_id, "sessionCreated": False, "alreadyWatching": True}

    result = jobs_store.mark_watched(root, job_id)
    session = session_from_job(result["job"])
    saved = store_call(root, ["session-save", "--id", job_id], payload=session)
    if saved.returncode != 0:
        raise jobs_store.JobsStoreError(
            f"could not create the Watching session: {(saved.stderr or '').strip()[:200]}")
    return {"watched": job_id, "sessionCreated": True,
            "company": session["company"], "role": session["role"]}


def passed(root, job_id, reason=""):
    return jobs_store.mark_passed(root, job_id, reason)


def main():
    ap = argparse.ArgumentParser(description="Promote a found job, or pass on it.")
    ap.add_argument("--id", required=True, help="the job id from the Found lane")
    ap.add_argument("--store", help="store directory (default ~/.job-application)")
    ap.add_argument("--pass", dest="passing", action="store_true",
                    help="tombstone it instead of watching it")
    ap.add_argument("--reason", default="")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.store)
    try:
        result = passed(root, args.id, args.reason) if args.passing else watch(root, args.id)
    except jobs_store.JobsStoreError as exc:
        print(f"promote: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
