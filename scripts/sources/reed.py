#!/usr/bin/env python3
"""Reed — UK only, free key, HTTP Basic with the key as the username.

Only asked when the search actually selects the UK. A UK-only channel swept for
a US-only search is a request an hour spent on nothing.

Endpoint shape not verified live: no key is installed. Written to the published
contract; the first real run confirms it.
"""

import base64
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "reed"
SOURCE_CLASS = "aggregator"
TIER = "1b"
KEY_VAR = "JOBBY_REED_KEY"
SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
COUNTRY = "GB"
PAGE_SIZE = 100


def parse_job(entry):
    location = base.parse_location(entry.get("locationName"), fallback_country=COUNTRY)
    salary = None
    if entry.get("minimumSalary") and entry.get("maximumSalary"):
        salary = f"{entry['minimumSalary']:.0f}-{entry['maximumSalary']:.0f}"
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, entry.get("employerName"), entry.get("jobTitle"),
        entry.get("jobUrl"), location=location,
        workplace=base.detect_workplace(entry.get("locationName"), entry.get("jobTitle"),
                                        entry.get("jobDescription")),
        posted_at=_uk_date(entry.get("date")),
        description=entry.get("jobDescription") or "", salary=salary)


def _uk_date(value):
    """Reed writes dd/mm/yyyy, which fromisoformat reads as nothing."""
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%d/%m/%Y").replace(
            tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return base.iso_stamp(value)


def fetch(fetcher, config=None, settings=None, limit=None, countries=None, **_):
    key = base.api_key(KEY_VAR, settings)
    if not key:
        return base.skipped(SOURCE, f"no key ({KEY_VAR} is not set)")
    if countries and COUNTRY not in [c.upper() for c in countries]:
        return base.skipped(SOURCE, "the saved search does not include the UK")

    config = config or {}
    terms = config.get("keywords") or [""]
    auth = base64.b64encode(f"{key}:".encode()).decode()
    postings, errors = [], []
    before = fetcher.requests
    for term in terms:
        params = {"resultsToTake": PAGE_SIZE}
        if term:
            params["keywords"] = term
        data, response = fetcher.get_json(
            f"{SEARCH_URL}?{urllib.parse.urlencode(params)}",
            headers={"Authorization": f"Basic {auth}"})
        if data is None:
            errors.append(base.describe(response, f"{SOURCE} {term!r}"))
            continue
        for entry in (data.get("results") or []):
            postings.append(parse_job(entry))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, errors[0], requests, errors)
    return base.ok(SOURCE, postings, requests, errors, f"{len(terms)} terms")
