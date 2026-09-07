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
#: Emits the §3 record key. One request per registry token, so the token is the
#: tenant slug, and each posting carries its own uuid `id` -- which is also the
#: last path segment of `jobUrl`, so the URL parser and this agree where the
#: parser can see a URL at all. Second source on the contract after greenhouse
#: and reed; ashby returned 2,241 postings on the 2026-09-07 sweep against
#: greenhouse's 14,254 and reed's 100.
EMITS_BOARD_KEY = True
TIER = "1a"
LIST_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"

WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite",
             "on-site": "onsite", "unspecified": None}


def boards(companies):
    return [c for c in (companies or {}).values()
            if c.get("ats") == SOURCE and c.get("token")]


def parse_job(entry, company_name, token=None):
    location = base.parse_location(entry.get("location"))
    job_id = entry.get("id")
    job_id = str(job_id).strip() if job_id not in (None, "") else None
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
        # The token this board was fetched by, not a parse of the URL it
        # returned. `token` is optional so a caller with one entry and no
        # board context still gets a posting rather than an error, and the key
        # is simply absent for it -- both halves or neither.
        board_slug=token if (token and job_id) else None,
        external_id=job_id if token else None,
    )


def fetch(fetcher, companies=None, limit=None, **_):
    postings, errors, dead = [], [], 0
    unchanged = []
    targets = boards(companies)
    if not targets:
        return base.skipped(SOURCE, "no Ashby boards in the company registry")
    before = fetcher.requests
    for company in targets:
        data, response = fetcher.get_json(LIST_URL.format(token=company["token"]))
        if response.unchanged:
            # Named rather than only counted, so the canary check can
            # tell an unchanged board from a broken one.
            unchanged.append(company["token"])
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
            postings.append(parse_job(entry, company.get("name"),
                                      company["token"]))
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
    if unchanged:
        note += f", {len(unchanged)} unchanged since the last run"
    return base.ok(SOURCE, postings, requests, errors, note, complete=True,
                   unchanged=len(unchanged), unchanged_slugs=unchanged)
