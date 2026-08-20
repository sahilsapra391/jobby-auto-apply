#!/usr/bin/env python3
"""Sweep public ATS job-board APIs and print PM/BA postings with real dates.

Verification tool for sourcing: every row printed came back from the employer's
own live board API this minute, so a 404/410 aggregator copy can never survive.
"""
import concurrent.futures as cf
import gzip
import io
import json
import re
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

TITLE_RE = re.compile(
    r"(product\s+manager|product\s+owner|business\s+analyst|product\s+analyst|"
    r"business\s+systems\s+analyst|program\s+manager,?\s+product|product\s+lead)",
    re.I)
REJECT_RE = re.compile(
    r"(principal|director|head\s+of|vp\b|vice\s+president|group\s+product|"
    r"gpm\b|intern\b|manager\s+of\s+product\s+management|chief)", re.I)


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return raw.decode("utf-8", "replace")


def greenhouse(token):
    d = json.loads(get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"))
    out = []
    for j in d.get("jobs", []):
        out.append({
            "company": token, "ats": "greenhouse", "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "updated": (j.get("updated_at") or "")[:10],
            "id": j.get("id"),
        })
    return out


def lever(token):
    d = json.loads(get(f"https://api.lever.co/v0/postings/{token}?mode=json"))
    out = []
    for j in d:
        ts = j.get("createdAt")
        import datetime as dt
        posted = (dt.datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                  if ts else "")
        out.append({
            "company": token, "ats": "lever", "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""), "updated": posted, "id": j.get("id"),
        })
    return out


def ashby(token):
    d = json.loads(get(
        f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"))
    out = []
    for j in d.get("jobs", []):
        out.append({
            "company": token, "ats": "ashby", "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("jobUrl", ""),
            "updated": (j.get("publishedAt") or "")[:10],
            "id": j.get("id"),
        })
    return out


def smartrecruiters(token):
    d = json.loads(get("https://api.smartrecruiters.com/v1/companies/%s/postings"
                       "?limit=100" % token))
    out = []
    for j in d.get("content", []):
        loc = j.get("location") or {}
        out.append({
            "company": token, "ats": "smartrecruiters", "title": j.get("name", ""),
            "location": ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                              loc.get("country")) if x),
            "url": "https://jobs.smartrecruiters.com/%s/%s" % (token, j.get("id")),
            "updated": (j.get("releasedDate") or "")[:10], "id": j.get("id"),
        })
    return out


def workable(token):
    d = json.loads(get("https://apply.workable.com/api/v1/widget/accounts/%s"
                       "?details=true" % token))
    out = []
    for j in d.get("jobs", []):
        out.append({
            "company": token, "ats": "workable", "title": j.get("title", ""),
            "location": ", ".join(x for x in (j.get("city"), j.get("state"),
                                              j.get("country")) if x),
            "url": j.get("url") or j.get("application_url", ""),
            "updated": (j.get("published_on") or "")[:10], "id": j.get("shortcode"),
        })
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "smartrecruiters": smartrecruiters, "workable": workable}


def sweep(pairs):
    rows, errs = [], []

    def one(p):
        plat, token = p
        try:
            return p, FETCHERS[plat](token), None
        except urllib.error.HTTPError as e:
            return p, [], f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            return p, [], type(e).__name__

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for p, got, err in ex.map(one, pairs):
            if err:
                errs.append(f"{p[0]}/{p[1]}: {err}")
            else:
                rows.extend(got)
    return rows, errs


def main():
    pairs = []
    src = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    for line in src.split():
        if "/" in line:
            plat, token = line.split("/", 1)
            if plat in FETCHERS:
                pairs.append((plat, token))
    rows, errs = sweep(pairs)
    keep = [r for r in rows
            if TITLE_RE.search(r["title"]) and not REJECT_RE.search(r["title"])]
    print(json.dumps({"boardsQueried": len(pairs), "boardsFailed": errs,
                      "totalPostings": len(rows), "titleMatches": len(keep),
                      "rows": keep}, indent=1))


if __name__ == "__main__":
    main()
