#!/usr/bin/env python3
"""Ask a candidate ATS platform whether it has an anonymous job endpoint.

`references/sources-registry.md` lists 28 platforms as `planned`, nearly all
annotated "shape unverified, verify at build". This is that verification. It
is deliberately not a connector: it makes one request per candidate URL shape
and reports exactly what came back, so a connector only ever gets written
against an endpoint somebody watched answer.

The failure this guards against is writing a connector from a remembered URL
shape. A plausible endpoint that 404s in production looks identical to a
platform with no tenants, and the registry then carries a confident row for a
channel that has never returned a posting.

Politeness matches probe_tenants.py: one request a second per host, an honest
User-Agent with a contact address, and a hard ceiling.

  python3 scripts/probe_platforms.py --out references/probed-platforms.json
"""

import argparse
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "jobby/1.0 (+https://github.com/sahilsapra391/jobby-auto-apply)"
PER_HOST_DELAY = 1.0
READ_CAP = 3_000_000
TIMEOUT = 25

# Words that appear in a job payload and almost nowhere else. Used only to
# describe what came back, never to decide it: the decision is a human reading
# the report.
JOB_WORDS = ("jobtitle", "job_title", "requisition", "jobreq", "postingid",
             "joblocation", "department", "job_posting", "jobposting",
             "positiontitle", "joburl", "applyurl", "job-id")

# (platform, tenant, url). Several shapes per platform on purpose: the point is
# to find which one answers, and a 404 costs one cheap request.
CANDIDATES = [
    # SAP SuccessFactors. The registry already calls this "public per-tenant
    # JSON", so it is the highest-value row on the planned list.
    ("successfactors", "sap", "https://jobs.sap.com/search/?q=&sortColumn=referencedate&createNewAlert=false&jobRecordsPerPage=20"),
    ("successfactors", "sap-rss", "https://jobs.sap.com/search/?q=&sortColumn=referencedate&createNewAlert=false&portal=&FeedRSS=true"),
    ("successfactors", "siemens", "https://jobs.siemens.com/search/?q=&sortColumn=referencedate&FeedRSS=true"),
    ("successfactors", "unilever", "https://careers.unilever.com/search/?q=&FeedRSS=true"),

    # Jobvite.
    ("jobvite", "atlassian", "https://jobs.jobvite.com/atlassian/search?c=&r=&l="),
    ("jobvite", "list-api", "https://api.jobvite.com/api/v2/job?companyId=atlassian"),
    ("jobvite", "careers-json", "https://jobs.jobvite.com/careers/atlassian/jobs.json"),
    ("jobvite", "feed", "https://jobs.jobvite.com/atlassian/jobs.rss"),

    # UKG Pro / UltiPro. The board id is a GUID, so a bare tenant path is the
    # thing to test; a redirect to the real board would still be useful.
    ("ukg", "board", "https://recruiting.ultipro.com/"),
    ("ukg", "opportunities", "https://recruiting.ultipro.com/api/v1/opportunities"),

    # Cornerstone OnDemand.
    ("csod", "search", "https://cornerstone.csod.com/services/x/career-site/v1/search?pageSize=20"),
    ("csod", "sites", "https://cornerstone.csod.com/ux/ats/careersite/4/home?c=cornerstone"),

    # Ceridian Dayforce.
    ("dayforce", "portal", "https://us241.dayforcehcm.com/CandidatePortal/en-US/ceridian"),
    ("dayforce", "api", "https://us241.dayforcehcm.com/CandidatePortal/api/en-US/ceridian/Search/GetJobs"),

    # Paylocity.
    ("paylocity", "jobs", "https://recruiting.paylocity.com/recruiting/jobs/All"),

    # Freshteam (Freshworks).
    ("freshteam", "freshworks", "https://freshworks.freshteam.com/jobs"),
    ("freshteam", "api", "https://freshworks.freshteam.com/api/job_postings"),

    # Factorial.
    ("factorial", "board", "https://factorialhr.factorialhr.com/job_posting"),
    ("factorial", "api", "https://api.factorialhr.com/api/public/v1/ats/job_postings"),

    # PageUp, dominant in Australia and APAC.
    ("pageup", "search", "https://cdn.pageuppeople.com/"),

    # National boards. Locations are unset, which means Worldwide, so these are
    # in scope, and a government feed is usually clean, high volume and stable.
    ("jobbank_ca", "rss", "https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=product+manager&sort=D&fmt=rss"),
    ("jobbank_ca", "json", "https://www.jobbank.gc.ca/api/jobsearch?searchstring=product%20manager"),
    ("france_travail", "api", "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search?motsCles=product"),
    ("eures", "api", "https://ec.europa.eu/eures/eures-searchengine/page/jv-search/search"),
    ("mycareersfuture", "api", "https://api.mycareersfuture.gov.sg/v2/search?limit=20&page=0"),
    ("arbeitsagentur", "api", "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs?was=product%20manager&size=20"),

    # India. The user's search is worldwide and India was an explicit ask.
    ("peoplestrong", "board", "https://recruit.peoplestrong.com/"),
    ("zimyo", "board", "https://careers.zimyo.com/"),
    ("darwinbox", "allJobs", "https://darwinbox.darwinbox.in/ms/candidate/careers"),
]


def looks_like(body, ctype):
    """Describe the response without deciding anything about it."""
    head = body[:400_000].lower()
    words = sorted({w for w in JOB_WORDS if w.encode() in head})
    kind = "other"
    stripped = body.lstrip()[:1]
    if "json" in ctype or stripped in (b"{", b"["):
        kind = "json"
    elif b"<rss" in head[:2000] or b"<feed" in head[:2000]:
        kind = "rss"
    elif b"<html" in head[:2000] or "html" in ctype:
        kind = "html"
    return kind, words


def probe(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, application/rss+xml, text/html"})
    try:
        r = urllib.request.urlopen(req, timeout=TIMEOUT)
        body, status, ctype, final = (r.read(READ_CAP), r.status,
                                      r.headers.get("Content-Type", ""), r.url)
    except urllib.error.HTTPError as e:
        body, status, ctype, final = e.read(READ_CAP), e.code, e.headers.get("Content-Type", ""), url
    except Exception as e:                       # noqa: BLE001 - reported, not raised
        return {"status": None, "error": f"{type(e).__name__}: {e}"[:160], "url": url}
    kind, words = looks_like(body, (ctype or "").lower())
    out = {"status": status, "bytes": len(body), "type": kind,
           "contentType": (ctype or "")[:60], "jobWords": words, "url": url}
    if final != url:
        out["redirectedTo"] = final[:160]
    if kind == "json":
        try:
            parsed = json.loads(body)
            out["topLevel"] = (sorted(parsed)[:12] if isinstance(parsed, dict)
                               else f"array of {len(parsed)}")
        except ValueError:
            out["topLevel"] = "unparseable"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="write the full report here as JSON")
    ap.add_argument("--platform", action="append", help="only these (repeatable)")
    args = ap.parse_args()

    wanted = set(args.platform or [])
    work = {}
    for platform, label, url in CANDIDATES:
        if wanted and platform not in wanted:
            continue
        work.setdefault(platform, []).append((label, url))

    results, lock = {}, threading.Lock()

    def worker(platform, items):
        rows = []
        for label, url in items:
            rows.append(dict(probe(url), label=label))
            time.sleep(PER_HOST_DELAY)
        with lock:
            results[platform] = rows

    threads = [threading.Thread(target=worker, args=(p, i), daemon=True)
               for p, i in work.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for platform in sorted(results):
        print(f"\n{platform}")
        for row in results[platform]:
            status = row.get("status")
            if status is None:
                print(f"  {row['label']:<14} ERROR  {row.get('error')}")
                continue
            extra = f" top={row['topLevel']}" if row.get("topLevel") else ""
            hit = ",".join(row["jobWords"][:4]) or "-"
            print(f"  {row['label']:<14} {status} {row['type']:<5} "
                  f"{row['bytes']:>8}b  words={hit}{extra}")
            if row.get("redirectedTo"):
                print(f"                 -> {row['redirectedTo']}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
