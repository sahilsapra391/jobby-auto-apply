#!/usr/bin/env python3
"""USAJobs — US federal postings. Needs both a key and the email it was issued
to, which the API expects in the User-Agent header.

Federal postings are worth having because they are invisible to every
commercial aggregator, but almost all of them carry citizenship or clearance
requirements, which the rubric's gate handles rather than this connector.

Verified live 2026-09-07: found 100, kept 0, status ok. Zero kept is the search
gate, not the connector. Written to the published
contract; the first real run confirms it.
"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "usajobs"
SOURCE_CLASS = "aggregator"
#: The hosts this connector's postings actually live on, which is not
#: the API host above and is what the board derives a logo from.
#: All 343 postings.
#:
#: **Declared so it can be checked.** An aggregator whose host is not in
#: `company_logos.INFRA_HOST_PARTS` has the board drawing the job
#: board's own mark on every employer card it supplies, and that is
#: invisible precisely because it is consistent.
#: `tests/test_aggregator_hosts.py` fails when the two part.
POSTING_HOSTS = ("www.usajobs.gov",)
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
    # **A missing key is a fault, not a choice.** `skipped` is for a channel
    # this run had no reason to ask -- reed on a search that excludes the UK --
    # and it is absent from the report's error list, so a channel configured to
    # run and unable to run looked identical to one deliberately left out. In a
    # container, where the key arrives as an environment variable, that is the
    # difference between a sweep that says it is missing five sources and one
    # that looks healthy while missing them.
    if not key or not email:
        return base.failed(SOURCE, f"no key: set {KEY_VAR} and {EMAIL_VAR}")
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
