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

sys.path.insert(0, str(REPO / "scripts"))
import seen  # noqa: E402  (the ATS key patterns live in one place)


class StoreUnreadable(RuntimeError):
    """The history could not be read, so nothing can be ruled out."""


def store(args):
    """Read from the store, or raise.

    This used to return None on any failure, and submitted_index turned that
    into `or []`: an unreadable history became an empty history, and the guard
    then reported that nothing had been applied to. A duplicate guard that
    answers "not a duplicate" when it cannot see is worse than no guard, because
    the caller trusts it. One truncated line in applications.jsonl was enough.
    """
    r = subprocess.run([sys.executable, str(STORE)] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise StoreUnreadable(f"{' '.join(args)}: {(r.stderr or '').strip()[:200]}")
    try:
        return json.loads(r.stdout or "null")
    except json.JSONDecodeError as exc:
        raise StoreUnreadable(f"{' '.join(args)}: unparseable output ({exc})")


def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def norm_url(value):
    """Strip tracking noise so the same posting matches across channels.

    A URL whose identity rides its query strips to the employer's shared
    careers page: jobs.elastic.co/jobs names every Elastic posting at once,
    and one submitted Elastic application matched all of them here (measured
    2026-08-28, 10 of 102 submitted URLs keyed this way). Those answer "" and
    match by (company, ATS key) instead; a path-borne id survives the strip
    and keeps matching by URL. seen.url_identifies_posting is the one place
    that distinction lives.
    """
    if not seen.url_identifies_posting(value or ""):
        return ""
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
    # Query-keyed URLs left the urls set above, so their identity lives here:
    # (company, ATS key), scoped by company because a Workday requisition
    # number repeats across tenants.
    keys = set()
    for e in done:
        entry = roles.get(e.get("applicationId")) or {}
        k = seen.ats_key(entry.get("url") or "")
        company = e.get("company") or entry.get("company")
        if k and company:
            keys.add((norm(company), k))
    return done, ids, pairs, urls, keys, roles


def in_flight(exclude):
    """Applications being worked on right now, other than this one.

    Submitted history is not enough. Three runs for one posting under different
    ids all passed the guard at the same moment and all submitted twenty minutes
    later, because at the time none of them had logged anything yet.
    """
    out = []
    for x in (store(["session-list"]) or []):
        if x.get("applicationId") == exclude:
            continue
        if x.get("status") == "inflight":
            out.append(x)
    return out


def guard(aid):
    done, ids, pairs, urls, keys, roles = submitted_index()
    entry = store(["session-load", "--id", aid]) or roles.get(aid) or {}
    company, role = entry.get("company"), entry.get("role")
    url = entry.get("url")

    if aid in ids:
        return {"duplicate": True, "reason": "this application id is already submitted"}

    # Something else is applying to this same posting right now.
    for other in in_flight(aid):
        other_url = other.get("url") or (roles.get(other.get("applicationId")) or {}).get("url")
        same_role = (company and role
                     and norm(other.get("company")) == norm(company)
                     and norm(other.get("role")) == norm(role))
        # norm_url answers "" for query-keyed URLs, and "" == "" must not read
        # as the same posting; those compare by ATS key, within the company.
        nu, onu = norm_url(url), norm_url(other_url)
        uk = seen.ats_key(url or "")
        ok = seen.ats_key(other_url or "")
        same_url = bool((nu and nu == onu)
                        or (uk and uk == ok and company
                            and norm(other.get("company")) == norm(company)))
        if same_role or same_url:
            return {"duplicate": True,
                    "reason": f"another run is applying to this same posting right now "
                              f"({other.get('applicationId')})"}
    if company and role and (norm(company), norm(role)) in pairs:
        return {"duplicate": True,
                "reason": f"already applied to {company} — {role} under a different id"}
    k = seen.ats_key(url or "")
    if k and company and (norm(company), k) in keys:
        return {"duplicate": True, "reason": f"already applied to this exact posting: {url}"}
    if url and norm_url(url) in urls:
        return {"duplicate": True, "reason": f"already applied to this exact posting: {url}"}
    return {"duplicate": False, "company": company, "role": role}


def audit():
    done, ids, pairs, urls, keys, roles = submitted_index()
    by_id = collections.Counter(e.get("applicationId") for e in done)
    by_pair = collections.defaultdict(set)
    by_url = collections.defaultdict(set)
    by_key = collections.defaultdict(set)
    for e in done:
        by_pair[(norm(e.get("company")), norm(e.get("role")))].add(e.get("applicationId"))
        entry = roles.get(e.get("applicationId")) or {}
        u = norm_url(entry.get("url"))
        if u:
            by_url[u].add(e.get("applicationId"))
        k = seen.ats_key(entry.get("url") or "")
        company = e.get("company") or entry.get("company")
        if k and company:
            by_key[(norm(company), k)].add(e.get("applicationId"))

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
    for (c, k), aids in by_key.items():
        if len(aids) > 1:
            problems.append({"kind": "same posting key", "company": c, "key": k,
                             "ids": sorted(aids)})
    return {"submitted": len(done), "duplicates": problems, "clean": not problems}


def main():
    ap = argparse.ArgumentParser(description="Guard against duplicate applications.")
    ap.add_argument("--guard", metavar="APPLICATION_ID")
    args = ap.parse_args()

    if args.guard:
        try:
            result = guard(args.guard)
        except StoreUnreadable as exc:
            # Answer in the same shape, so a caller can tell "no" from "cannot
            # tell". duplicate is null rather than false: the caller must treat
            # this as blocking, not as clearance.
            print(json.dumps({"duplicate": None, "unavailable": str(exc)}, indent=2))
            return 2
        print(json.dumps(result, indent=2))
        return 1 if result["duplicate"] else 0

    try:
        result = audit()
    except StoreUnreadable as exc:
        print(json.dumps({"clean": False, "unavailable": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
