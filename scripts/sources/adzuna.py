#!/usr/bin/env python3
"""Adzuna — the free-key API that carries most of Phase 1's international reach.

One request per country per search term, which is why the country list comes
from the saved search rather than from everywhere Adzuna operates: sweeping
twenty countries for a search that only wants three is seventeen requests an
hour spent on results that will be gated out anyway.

No key installed is a normal state, not a failure. It reports `skipped (no
key)` and the board says so, because a channel that is quietly absent looks
exactly like a channel that found nothing.

Endpoint shape not verified live: the key is not installed on this machine, so
this connector has never had a real response. It is written to the published
API contract and its fixtures come from that contract rather than from a
recorded response — recorded in references/sources-registry.md, and the first
real run is what confirms it.
"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "adzuna"
SOURCE_CLASS = "aggregator"
#: The hosts this connector's postings actually live on, which is not
#: the API host above and is what the board derives a logo from.
#: Adzuna serves a per-country host and the pool holds .com, .in, .fr and
#: .es, so the covering entry is the label rather than one domain.
#:
#: **Declared so it can be checked.** An aggregator whose host is not in
#: `company_logos.INFRA_HOST_PARTS` has the board drawing the job
#: board's own mark on every employer card it supplies, and that is
#: invisible precisely because it is consistent.
#: `tests/test_aggregator_hosts.py` fails when the two part.
POSTING_HOSTS = ("www.adzuna.com", "adzuna.")
TIER = "1b"
ID_VAR = "JOBBY_ADZUNA_ID"
KEY_VAR = "JOBBY_ADZUNA_KEY"
SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
RESULTS_PER_PAGE = 50

# The countries Adzuna actually operates in, lowercased as the path wants them.
# A country the user selected that is not here is not an error, it is a country
# this channel cannot answer for.
COUNTRIES = {"gb", "us", "at", "au", "be", "br", "ca", "ch", "de", "es", "fr",
             "in", "it", "mx", "nl", "nz", "pl", "sg", "za"}


def _query(config, country, term, days):
    params = {
        "app_id": config["app_id"], "app_key": config["app_key"],
        "results_per_page": RESULTS_PER_PAGE, "content-type": "application/json",
    }
    if term:
        params["what"] = term
    if days:
        params["max_days_old"] = int(max(1, days))
    return f"{SEARCH_URL.format(country=country)}?{urllib.parse.urlencode(params)}"


def parse_job(entry, country):
    location_raw = (entry.get("location") or {}).get("display_name") or ""
    location = base.parse_location(location_raw, fallback_country=country.upper())
    salary_min, salary_max = entry.get("salary_min"), entry.get("salary_max")
    salary = f"{salary_min:.0f}-{salary_max:.0f}" if salary_min and salary_max else None
    company = (entry.get("company") or {}).get("display_name")
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, company, entry.get("title"),
        entry.get("redirect_url"),
        location=location,
        workplace=base.detect_workplace(location_raw, entry.get("title"),
                                        entry.get("description")),
        posted_at=entry.get("created"),
        description=entry.get("description") or "",
        tags=[(entry.get("category") or {}).get("label")],
        salary=salary)


def fetch(fetcher, config=None, settings=None, limit=None, countries=None,
          cursor=0, **_):
    app_id = base.api_key(ID_VAR, settings)
    app_key = base.api_key(KEY_VAR, settings)
    if not app_id or not app_key:
        return base.skipped(SOURCE, f"no key ({ID_VAR} and {KEY_VAR} are not set)")

    config = config or {}
    wanted = [c.lower() for c in (countries or [])] or ["us"]
    reachable = [c for c in wanted if c in COUNTRIES]
    unreachable = sorted(set(wanted) - COUNTRIES)
    if not reachable:
        return base.skipped(SOURCE, f"none of the selected countries are covered "
                                    f"({', '.join(unreachable) or 'none selected'})")

    terms = config.get("keywords") or [""]
    days = config.get("_postedWithinDays")
    asked, start = base.query_slice(reachable, cursor)
    postings, errors = [], []
    before = fetcher.requests
    for country in asked:
        for term in terms:
            url = _query({"app_id": app_id, "app_key": app_key}, country, term, days)
            data, response = fetcher.get_json(url)
            if response.unchanged:
                continue
            if data is None:
                errors.append(base.describe(response, f"{SOURCE} {country} {term!r}"))
                continue
            for entry in (data.get("results") or []):
                postings.append(parse_job(entry, country))
                if limit and len(postings) >= limit:
                    break
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break

    note = f"{len(asked)} of {len(reachable)} countries x {len(terms)} terms"
    if len(asked) < len(reachable):
        note += f" (rotating window from {start})"
    if unreachable:
        note += f"; not covered here: {', '.join(unreachable)}"
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, errors[0], requests, errors)
    return base.ok(SOURCE, postings, requests, errors, note)
