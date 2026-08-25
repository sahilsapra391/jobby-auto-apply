#!/usr/bin/env python3
"""Ashby company boards.

One request per company returns the board with each posting's plain-text
description, an explicit workplace type and an `isRemote` flag, so like Lever
it needs no second round trip.

Verified 2026-08-19: /posting-api/job-board/{token}. `isListed` is the
board's own visibility flag and an unlisted posting is one the company has
chosen not to show, so it is not collected. A wrong token is a 404 with a
plain-text body rather than JSON, which is why the status is checked before
the parse.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "ashby"
SOURCE_CLASS = "ats"
TIER = "1a"
LIST_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"

WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite",
             "on-site": "onsite", "unspecified": None}


def boards(companies):
    return [c for c in (companies or {}).values()
            if c.get("ats") == SOURCE and c.get("token")]


def parse_job(entry, company_name):
    location = base.parse_location(entry.get("location"))
    workplace = WORKPLACE.get(str(entry.get("workplaceType") or "").lower())
    if workplace is None and entry.get("isRemote"):
        workplace = "remote"
    if workplace is None:
        workplace = base.detect_workplace(location.get("raw"), entry.get("title"))
    tags = [t for t in (entry.get("department"), entry.get("team")) if t]
    compensation = (entry.get("compensation") or {}).get("compensationTierSummary")
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, company_name, entry.get("title"),
        entry.get("jobUrl") or entry.get("applyUrl"),
        location=location, workplace=workplace,
        posted_at=entry.get("publishedAt"),
        description=entry.get("descriptionPlain") or entry.get("descriptionHtml") or "",
        tags=tags, salary=compensation,
    )


def fetch(fetcher, companies=None, limit=None, **_):
    postings, errors, dead = [], [], 0
    targets = boards(companies)
    if not targets:
        return base.skipped(SOURCE, "no Ashby boards in the company registry")
    before = fetcher.requests
    for company in targets:
        data, response = fetcher.get_json(LIST_URL.format(token=company["token"]))
        if response.unchanged:
            continue
        if data is None:
            if response.status == 404:
                dead += 1
            errors.append(base.describe(response, f"{company.get('name')} ({company['token']})"))
            continue
        for entry in (data.get("jobs") or []):
            # The company has chosen not to list this one publicly.
            if entry.get("isListed") is False:
                continue
            postings.append(parse_job(entry, company.get("name")))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    note = f"{len(targets)} boards"
    if dead:
        note += f", {dead} token(s) 404 and need re-resolving"
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, f"every board failed ({len(errors)})", requests, errors)
    # The board endpoint returns every open requisition for the tenant,
    # so a posting missing from it has been taken down.
    return base.ok(SOURCE, postings, requests, errors, note, complete=True)
