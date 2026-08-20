#!/usr/bin/env python3
"""Lever company boards.

Cheaper than Greenhouse in every way that matters here: one request returns the
whole board *including* each posting's plain-text description, its workplace
type and a two-letter country code, so no second round trip is needed and the
country does not have to be inferred from a location string.

Verified 2026-08-19: /v0/postings/{token}?mode=json, and Lever is the one
platform of the three that reliably returns an ETag and answers a conditional
request with a 304. An unchanged board therefore costs a request and no
payload at all.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "lever"
SOURCE_CLASS = "ats"
TIER = "1a"
LIST_URL = "https://api.lever.co/v0/postings/{token}?mode=json"

# Lever's own vocabulary, which is already exactly the three values the schema
# uses. Anything else it invents later falls through to detection.
WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite",
             "onsite": "onsite", "unspecified": None}


def boards(companies):
    return [c for c in (companies or {}).values()
            if c.get("ats") == SOURCE and c.get("token")]


def parse_job(entry, company_name):
    categories = entry.get("categories") or {}
    location = base.parse_location(categories.get("location"),
                                   fallback_country=(entry.get("country") or "").upper() or None)
    workplace = WORKPLACE.get(str(entry.get("workplaceType") or "").lower())
    if workplace is None:
        workplace = base.detect_workplace(location.get("raw"), entry.get("text"))
    body = entry.get("descriptionPlain") or entry.get("description") or ""
    extra = entry.get("additionalPlain") or ""
    tags = [t for t in (categories.get("team"), categories.get("department"),
                        categories.get("commitment")) if t]
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, company_name, entry.get("text"),
        entry.get("hostedUrl") or entry.get("applyUrl"),
        location=location, workplace=workplace,
        posted_at=entry.get("createdAt"),
        description=f"{body}\n\n{extra}".strip(),
        tags=tags,
    )


def fetch(fetcher, companies=None, limit=None, **_):
    postings, errors, dead = [], [], 0
    targets = boards(companies)
    if not targets:
        return base.skipped(SOURCE, "no Lever boards in the company registry")
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
        if not isinstance(data, list):
            errors.append(f"{company.get('name')}: expected a list of postings")
            continue
        for entry in data:
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
    return base.ok(SOURCE, postings, requests, errors, note)
