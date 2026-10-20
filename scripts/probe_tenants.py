#!/usr/bin/env python3
"""Find real ATS tenants by asking the ATS, one candidate at a time.

The registry's unit of coverage is the company, not the platform. Fifteen
connectors are wired and five of them had a single board, which is why they
returned nothing — the code was never the problem.

A candidate token is only kept when the platform's own public endpoint answers
200 with at least one live posting. That is the whole test: no scraping, no
guessing from a careers page, no third-party list to go stale. A name that is
not a tenant simply 404s and costs one request.

Politeness is per host and non-negotiable: one request a second to each vendor,
an honest User-Agent with a contact address, and a per-run ceiling so a long
candidate list cannot turn into a hammering. Platforms run concurrently because
they are different hosts; tokens within a platform run one at a time.

  python3 scripts/probe_tenants.py --candidates names.txt --out found.json
"""

import argparse
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "jobby/1.0 (+https://github.com/sahilsapra391/jobby-auto-apply)"
PER_HOST_DELAY = 1.0
# Big enough to parse an ordinary board, small enough that twelve threads
# holding one each is not a problem. Anything larger falls back to the marker.
READ_CAP = 4_000_000
TRUNCATED_MARKERS = (b'"jobs":', b'"items":', b'"offers":', b'"content":', b'"data":')
DEFAULT_CEILING = 1200


def _count(payload, *keys):
    """How many postings a response reports, across the shapes vendors use."""
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, int):
            return value
    return 0


PLATFORMS = {
    "greenhouse": (lambda t: f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
                   lambda d: _count(d, "jobs")),
    "ashby": (lambda t: f"https://api.ashbyhq.com/posting-api/job-board/{t}",
              lambda d: _count(d, "jobs")),
    "lever": (lambda t: f"https://api.lever.co/v0/postings/{t}?mode=json",
              lambda d: _count(d)),
    "smartrecruiters": (lambda t: f"https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=100",
                        lambda d: _count(d, "content", "totalFound")),
    "teamtailor": (lambda t: f"https://{t}.teamtailor.com/jobs.json",
                   lambda d: _count(d, "items")),
    "workable": (lambda t: f"https://apply.workable.com/api/v1/widget/accounts/{t}?details=true",
                 lambda d: _count(d, "jobs")),
    "breezy": (lambda t: f"https://{t}.breezy.hr/json",
               lambda d: _count(d)),
    "recruitee": (lambda t: f"https://{t}.recruitee.com/api/offers/",
                  lambda d: _count(d, "offers")),
    "pinpoint": (lambda t: f"https://{t}.pinpointhq.com/postings.json",
                 lambda d: _count(d, "data")),
    "jazzhr": (lambda t: f"https://{t}.applytojob.com/apply/jobs.json",
               lambda d: _count(d, "jobs")),
    "rippling": (lambda t: f"https://api.rippling.com/platform/api/ats/v1/board/{t}/jobs",
                 lambda d: _count(d, "items")),
    "personio": (lambda t: f"https://{t}.jobs.personio.de/xml",
                 lambda d: None),
}


def probe(platform, token, timeout=12):
    """(found, count) — found only when the board answers with live postings."""
    build, counter = PLATFORMS[platform]
    url = build(urllib.parse.quote(token, safe=""))
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False, 0
            body = response.read(READ_CAP)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
        return False, 0
    except ValueError:
        # A candidate the stdlib will not even turn into a request. The one
        # that happened was a 74-character agency strapline: half these
        # platforms address a tenant as a subdomain, `idna` refuses a DNS label
        # over 63 characters, and UnicodeEncodeError is a ValueError rather
        # than an OSError, so it went straight past the clause above and killed
        # six of the twelve platform threads. Every failure here means the same
        # thing anyway -- we could not ask, so this is not a tenant.
        return False, 0
    if counter is None:  # XML feeds: presence of a posting element is the signal
        return (b"<position>" in body or b"<job>" in body), 0
    try:
        count = counter(json.loads(body))
    except (json.JSONDecodeError, TypeError, AttributeError):
        # A board too big for the cap is a real tenant, not a miss. OpenAI's
        # Ashby board is 12 MB and a truncated read fails to parse, which would
        # have reported the largest employers on a platform as absent from it.
        # The marker says a posting object exists; the count is unknown, so it
        # is reported as -1 rather than invented.
        if len(body) >= READ_CAP and any(m in body for m in TRUNCATED_MARKERS):
            return True, -1
        return False, 0
    count = count or 0
    return count > 0, count


def run_platform(platform, tokens, ceiling, results, lock, progress, outcome):
    """Probe one platform's candidates, and record how far it actually got.

    `outcome` is the point. A thread that dies leaves its results empty, and a
    run that reports what it *planned* to probe then says 494 for a platform
    that managed nine. That is not a missing column, it is a false claim of
    coverage, and it is how six dead threads went unnoticed until the totals
    looked wrong hours later. So the count reported is the count attempted, and
    a worker that raises says so by name.
    """
    sent = 0
    try:
        for token in tokens:
            if sent >= ceiling:
                break
            found, count = probe(platform, token)
            sent += 1
            if found:
                with lock:
                    results.append({"platform": platform, "token": token, "jobs": count})
            with lock:
                progress[platform] = (sent, len(tokens))
            time.sleep(PER_HOST_DELAY)
    except BaseException as exc:                 # noqa: BLE001 - reported, not swallowed
        with lock:
            outcome[platform] = {"attempted": sent, "planned": len(tokens),
                                 "died": f"{type(exc).__name__}: {exc}"[:200]}
        raise
    with lock:
        outcome[platform] = {"attempted": sent, "planned": len(tokens), "died": None}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", required=True,
                    help="JSON: {platform: [token, ...]} or a newline token list")
    ap.add_argument("--platform", action="append",
                    help="only these platforms (repeatable)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ceiling", type=int, default=DEFAULT_CEILING,
                    help="max requests per host this run")
    args = ap.parse_args()

    raw = Path(args.candidates).read_text(encoding="utf-8")
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        tokens = [line.strip() for line in raw.splitlines() if line.strip()]
        plan = {name: tokens for name in PLATFORMS}
    if args.platform:
        plan = {k: v for k, v in plan.items() if k in set(args.platform)}
    plan = {k: v for k, v in plan.items() if k in PLATFORMS}

    results, lock, progress = [], threading.Lock(), {}
    outcome = {}
    threads = [threading.Thread(target=run_platform,
                                args=(name, tokens, args.ceiling, results, lock,
                                      progress, outcome),
                                daemon=True)
               for name, tokens in plan.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    results.sort(key=lambda r: (r["platform"], -r["jobs"]))
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    by_platform = {}
    for row in results:
        by_platform.setdefault(row["platform"], 0)
        by_platform[row["platform"]] += 1
    # A platform with no outcome record never reached its own final line, so
    # it died somewhere the except clause could not name.
    missing = {name: {"attempted": progress.get(name, (0, len(tokens)))[0],
                      "planned": len(tokens),
                      "died": "the worker vanished without reporting"}
               for name, tokens in plan.items() if name not in outcome}
    outcome.update(missing)
    dead = {k: v for k, v in outcome.items() if v["died"]}
    short = {k: v for k, v in outcome.items()
             if not v["died"] and v["attempted"] < min(v["planned"], args.ceiling)}

    print(json.dumps({"probed": {k: v["attempted"] for k, v in outcome.items()},
                      "found": by_platform,
                      "tenants": len(results),
                      "jobs": sum(r["jobs"] for r in results),
                      "workersDied": dead,
                      "workersStoppedEarly": short}, indent=2))
    if dead:
        print(f"probe_tenants: {len(dead)} of {len(plan)} platforms did not finish: "
              f"{', '.join(sorted(dead))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
