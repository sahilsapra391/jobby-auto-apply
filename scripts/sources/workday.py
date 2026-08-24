#!/usr/bin/env python3
"""Workday CXS public candidate boards, one verified tenant site at a time."""

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "workday"
PAGE_SIZE = 20
# A corrupt total or an endpoint that repeats a page must still terminate. The
# ceiling is reported as an error if a genuine board exceeds it.
MAX_PAGES_PER_BOARD = 100


def board(company):
    parsed = urlparse(str(company.get("careersUrl") or ""))
    parts = [p for p in parsed.path.split("/") if p]
    if not parsed.netloc or not parts:
        return None
    return parsed.netloc, parsed.netloc.split(".", 1)[0], parts[0]


def fetch(fetcher, companies=None, limit=None, **_):
    targets = [c for c in (companies or {}).values()
               if c.get("ats") == SOURCE and board(c)]
    if not targets:
        return base.skipped(SOURCE, "no Workday boards with a verified career-site path")
    postings, errors, before = [], [], fetcher.requests
    for company in targets:
        host, tenant, site = board(company)
        endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        offset = 0
        complete = False
        for _page in range(MAX_PAGES_PER_BOARD):
            response = fetcher.post_json(endpoint, {
                "appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""})
            if not response.ok:
                errors.append(base.describe(response, f"{company.get('name')} ({site})"))
                complete = True
                break
            try:
                data = response.json()
                entries = data.get("jobPostings") or []
                total = int(data.get("total") or len(entries))
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"{company.get('name')}: unexpected response shape ({exc})")
                complete = True
                break
            for entry in entries:
                raw = entry.get("locationsText") or ""
                remote = str(entry.get("remoteType") or "").lower()
                postings.append(base.posting(
                    SOURCE, "ats", "1a", company.get("name"), entry.get("title"),
                    f"https://{host}/{site}{entry.get('externalPath') or ''}",
                    location=base.parse_location(raw),
                    workplace=(remote if remote in ("remote", "hybrid") else
                               base.detect_workplace(raw, entry.get("title"))),
                    posted_at=None, description="",
                    tags=[x for x in [entry.get("timeType"), *(entry.get("bulletFields") or [])]
                          if x]))
                if limit and len(postings) >= limit:
                    break
            offset += len(entries)
            if (limit and len(postings) >= limit) or not entries or offset >= total:
                complete = True
                break
        if not complete:
            errors.append(f"{company.get('name')}: board exceeded the "
                          f"{MAX_PAGES_PER_BOARD * PAGE_SIZE}-job safety ceiling")
        if limit and len(postings) >= limit:
            break
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, f"every board failed ({len(errors)})", requests, errors)
    return base.ok(SOURCE, postings, requests, errors,
                   f"{len(targets)} boards")
