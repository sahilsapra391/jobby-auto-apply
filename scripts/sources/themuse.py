#!/usr/bin/env python3
"""The Muse — curated postings, mostly US and Western Europe.

Verified 2026-08-19 and corrected against the plan: Appendix A lists this as a
free-key channel, but the public endpoint answers with no key at all. A key
raises the rate limit rather than unlocking access, so this runs as a connected
Tier-1b channel whether or not one is installed, and says which of the two it
is doing.
"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "themuse"
SOURCE_CLASS = "aggregator"
TIER = "1b"
KEY_VAR = "JOBBY_THEMUSE_KEY"
SEARCH_URL = "https://www.themuse.com/api/public/jobs"
MAX_PAGES = 3


def parse_job(entry):
    locations = [loc.get("name") for loc in (entry.get("locations") or []) if loc.get("name")]
    raw = "; ".join(locations)
    location = base.parse_location(locations[0] if locations else "")
    location["raw"] = raw or location["raw"]
    company = (entry.get("company") or {}).get("name")
    url = (entry.get("refs") or {}).get("landing_page")
    tags = [level.get("name") for level in (entry.get("levels") or []) if level.get("name")]
    tags += [cat.get("name") for cat in (entry.get("categories") or []) if cat.get("name")]
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, company, entry.get("name"), url,
        location=location,
        workplace=base.detect_workplace(raw, entry.get("name")),
        posted_at=entry.get("publication_date"),
        description=entry.get("contents") or "", tags=tags)


def fetch(fetcher, config=None, settings=None, limit=None, pages=MAX_PAGES, **_):
    key = base.api_key(KEY_VAR, settings)
    postings, errors = [], []
    before = fetcher.requests
    for page in range(pages):
        params = {"page": page}
        if key:
            params["api_key"] = key
        data, response = fetcher.get_json(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
        if response.unchanged:
            continue
        if data is None:
            errors.append(base.describe(response, f"{SOURCE} page {page}"))
            break
        for entry in (data.get("results") or []):
            postings.append(parse_job(entry))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
        if page + 1 >= (data.get("page_count") or 1):
            break
    requests = fetcher.requests - before
    note = "public access, no key needed" if not key else "using the installed key"
    if errors and not postings:
        return base.failed(SOURCE, errors[0], requests, errors)
    return base.ok(SOURCE, postings, requests, errors, note)
