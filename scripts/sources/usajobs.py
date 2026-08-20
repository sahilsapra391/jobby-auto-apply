#!/usr/bin/env python3
"""USAJobs — US federal postings. Needs both a key and the email it was issued
to, which the API expects in the User-Agent header.

Federal postings are worth having because they are invisible to every
commercial aggregator, but almost all of them carry citizenship or clearance
requirements, which the rubric's gate handles rather than this connector.

Endpoint shape not verified live: no key is installed. Written to the published
contract; the first real run confirms it.
"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "usajobs"
SOURCE_CLASS = "aggregator"
TIER = "1b"
KEY_VAR = "JOBBY_USAJOBS_KEY"
EMAIL_VAR = "JOBBY_USAJOBS_EMAIL"
SEARCH_URL = "https://data.usajobs.gov/api/search"
COUNTRY = "US"
PAGE_SIZE = 100


def parse_job(entry):
    descriptor = entry.get("MatchedObjectDescriptor") or {}
    locations = descriptor.get("PositionLocation") or []
    raw = (locations[0].get("LocationName") if locations else "") or ""
    location = base.parse_location(raw, fallback_country=COUNTRY)
    detail = (descriptor.get("UserArea") or {}).get("Details") or {}
    summary = descriptor.get("QualificationSummary") or ""
    duties = " ".join(detail.get("MajorDuties") or [])
    remuneration = (descriptor.get("PositionRemuneration") or [{}])[0]
    salary = None
    if remuneration.get("MinimumRange") and remuneration.get("MaximumRange"):
        salary = f"{remuneration['MinimumRange']}-{remuneration['MaximumRange']}"
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER,
        (descriptor.get("OrganizationName") or descriptor.get("DepartmentName")),
        descriptor.get("PositionTitle"), descriptor.get("PositionURI"),
        location=location,
        workplace=base.detect_workplace(raw, detail.get("TeleworkEligible") and "remote"),
        posted_at=descriptor.get("PublicationStartDate"),
        description=f"{summary}\n\n{duties}".strip(),
        tags=[t.get("Name") for t in (descriptor.get("JobCategory") or []) if t.get("Name")],
        salary=salary)


def fetch(fetcher, config=None, settings=None, limit=None, countries=None, **_):
    key = base.api_key(KEY_VAR, settings)
    email = base.api_key(EMAIL_VAR, settings)
    if not key or not email:
        return base.skipped(SOURCE, f"no key ({KEY_VAR} and {EMAIL_VAR} are both needed)")
    if countries and COUNTRY not in [c.upper() for c in countries]:
        return base.skipped(SOURCE, "the saved search does not include the US")

    config = config or {}
    terms = config.get("keywords") or [""]
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    postings, errors = [], []
    before = fetcher.requests
    for term in terms:
        params = {"ResultsPerPage": PAGE_SIZE}
        if term:
            params["Keyword"] = term
        data, response = fetcher.get_json(
            f"{SEARCH_URL}?{urllib.parse.urlencode(params)}", headers=headers)
        if data is None:
            errors.append(base.describe(response, f"{SOURCE} {term!r}"))
            continue
        result = (data.get("SearchResult") or {})
        for entry in (result.get("SearchResultItems") or []):
            postings.append(parse_job(entry))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, errors[0], requests, errors)
    return base.ok(SOURCE, postings, requests, errors, f"{len(terms)} terms")
