#!/usr/bin/env python3
"""Reed — UK only, free key, HTTP Basic with the key as the username.

Only asked when the search actually selects the UK. A UK-only channel swept for
a US-only search is a request an hour spent on nothing.

Verified live 2026-09-07: found 100, kept 26, status ok. Written to the published
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
#: The hosts this connector's postings actually live on, which is not
#: the API host above and is what the board derives a logo from.
#: All 2,577 postings. This is the one that was missing, and it was the
#: project's largest single source: every card drew reed's own favicon
#: as the employer's mark.
#:
#: **Declared so it can be checked.** An aggregator whose host is not in
#: `company_logos.INFRA_HOST_PARTS` has the board drawing the job
#: board's own mark on every employer card it supplies, and that is
#: invisible precisely because it is consistent.
#: `tests/test_aggregator_hosts.py` fails when the two part.
POSTING_HOSTS = ("www.reed.co.uk",)
#: This connector emits the §3 record key rather than leaving it to be parsed
#: back out of a URL. See `scripts/sources/base.py` and
#: `tests/test_board_key_contract.py`, which enumerates every connector and
#: requires each one to either emit or say why it cannot.
#:
#: `board_slug` is the source itself, which is the convention
#: `packages/schema/board_key.py` already states for a feed: reed is not an ATS
#: tenant, reed is the board the posting came from. `external_id` is reed's own
#: `jobId`, which is the identity reed uses and is stable across sweeps, where
#: the fallback used this device's pool id and was therefore local to it.
EMITS_BOARD_KEY = True
TIER = "1b"
KEY_VAR = "JOBBY_REED_KEY"
SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
COUNTRY = "GB"
PAGE_SIZE = 100


def parse_job(entry):
    location = base.parse_location(entry.get("locationName"), fallback_country=COUNTRY)
    # `jobId` is an int in the payload and the key is text on both sides, so it
    # is stringified here rather than wherever it is next compared.
    job_id = entry.get("jobId")
    job_id = str(job_id).strip() if job_id not in (None, "") else None
    salary = None
    if entry.get("minimumSalary") and entry.get("maximumSalary"):
        salary = f"{entry['minimumSalary']:.0f}-{entry['maximumSalary']:.0f}"
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, entry.get("employerName"), entry.get("jobTitle"),
        entry.get("jobUrl"), location=location,
        workplace=base.detect_workplace(entry.get("locationName"), entry.get("jobTitle"),
                                        entry.get("jobDescription")),
        posted_at=_uk_date(entry.get("date")),
        description=entry.get("jobDescription") or "", salary=salary,
        board_slug=SOURCE if job_id else None, external_id=job_id)


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
    # **A missing key is a fault, not a choice.** `skipped` is for a channel
    # this run had no reason to ask -- reed on a search that excludes the UK --
    # and it is absent from the report's error list, so a channel configured to
    # run and unable to run looked identical to one deliberately left out. In a
    # container, where the key arrives as an environment variable, that is the
    # difference between a sweep that says it is missing five sources and one
    # that looks healthy while missing them.
    if not key:
        return base.failed(SOURCE, f"no key: set {KEY_VAR}")
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
