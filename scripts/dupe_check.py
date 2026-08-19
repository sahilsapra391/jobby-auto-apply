#!/usr/bin/env python3
"""Guard against applying to the same job twice.

A duplicate application is worse than a missed one: it reaches a recruiter as
carelessness, and nothing in the pipeline notices on its own. This checks the
three ways a duplicate can arise, since matching on application id alone misses
the case where the same posting was found twice through different channels.

  python3 scripts/dupe_check.py                 # audit everything submitted
  python3 scripts/dupe_check.py --guard <id>    # exit 1 if this would duplicate
"""

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"


def store(args):
    r = subprocess.run([sys.executable, str(STORE)] + args, capture_output=True, text=True)
    return json.loads(r.stdout or "null") if r.returncode == 0 else None


def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def norm_url(value):
    """Strip tracking noise so the same posting matches across channels."""
    u = (value or "").split("?")[0].rstrip("/").lower()
    return re.sub(r"^https?://(www\.)?", "", u)


def submitted_index():
    """What is genuinely submitted, per the latest event for each application.

    A `completed` can be retracted by a later `abandoned` (a mark logged in
    error). Counting every `completed` ever written would then block a real
    application to that company forever, so only the last event counts.
    """
    history = store(["history-list"]) or []
    roles = store(["roles-list"]) or {}
    latest = {}
    for e in sorted(history, key=lambda e: e.get("at") or ""):
        if e.get("applicationId"):
            latest[e["applicationId"]] = e
    done = [e for e in latest.values() if e.get("event") == "completed"]
    ids = {e.get("applicationId") for e in done}
    pairs = {(norm(e.get("company")), norm(e.get("role"))) for e in done}
    urls = {norm_url((roles.get(e.get("applicationId")) or {}).get("url")) for e in done}
    urls.discard("")
    return done, ids, pairs, urls, roles


def guard(aid):
    done, ids, pairs, urls, roles = submitted_index()
    entry = store(["session-load", "--id", aid]) or roles.get(aid) or {}
    company, role = entry.get("company"), entry.get("role")
    url = entry.get("url")

    if aid in ids:
        return {"duplicate": True, "reason": "this application id is already submitted"}
    if company and role and (norm(company), norm(role)) in pairs:
        return {"duplicate": True,
                "reason": f"already applied to {company} — {role} under a different id"}
    if url and norm_url(url) in urls:
        return {"duplicate": True, "reason": f"already applied to this exact posting: {url}"}
    return {"duplicate": False, "company": company, "role": role}


def audit():
    done, ids, pairs, urls, roles = submitted_index()
    by_id = collections.Counter(e.get("applicationId") for e in done)
    by_pair = collections.defaultdict(set)
    by_url = collections.defaultdict(set)
    for e in done:
        by_pair[(norm(e.get("company")), norm(e.get("role")))].add(e.get("applicationId"))
        u = norm_url((roles.get(e.get("applicationId")) or {}).get("url"))
        if u:
            by_url[u].add(e.get("applicationId"))

    problems = []
    for aid, n in by_id.items():
        if n > 1:
            problems.append({"kind": "same id completed twice", "id": aid, "count": n})
    for (c, r), aids in by_pair.items():
        if len(aids) > 1:
            problems.append({"kind": "same company and role", "company": c, "role": r,
                             "ids": sorted(aids)})
    for u, aids in by_url.items():
        if len(aids) > 1:
            problems.append({"kind": "same posting url", "url": u, "ids": sorted(aids)})
    return {"submitted": len(done), "duplicates": problems, "clean": not problems}


def main():
    ap = argparse.ArgumentParser(description="Guard against duplicate applications.")
    ap.add_argument("--guard", metavar="APPLICATION_ID")
    args = ap.parse_args()

    if args.guard:
        result = guard(args.guard)
        print(json.dumps(result, indent=2))
        return 1 if result["duplicate"] else 0

    result = audit()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
