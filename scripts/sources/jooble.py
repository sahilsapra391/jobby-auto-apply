#!/usr/bin/env python3
"""Jooble — 60+ countries, including the Gulf, Japan and Korea.

The only connector here that POSTs: the key is part of the path and the query
is a JSON body. Its country coverage is the widest of any free-key channel, so
it is what reaches the regions Adzuna does not.

Endpoint shape not verified live: no key is installed on this machine. Written
to the published contract; the first real run confirms it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "jooble"
SOURCE_CLASS = "aggregator"
#: The hosts this connector's postings actually live on, which is not
#: the API host above and is what the board derives a logo from.
#: One host, all 308 postings.
#:
#: **Declared so it can be checked.** An aggregator whose host is not in
#: `company_logos.INFRA_HOST_PARTS` has the board drawing the job
#: board's own mark on every employer card it supplies, and that is
#: invisible precisely because it is consistent.
#: `tests/test_aggregator_hosts.py` fails when the two part.
POSTING_HOSTS = ("jooble.org",)
TIER = "1b"
KEY_VAR = "JOBBY_JOOBLE_KEY"
SEARCH_URL = "https://jooble.org/api/{key}"


def parse_job(entry, country=None):
    location = base.parse_location(entry.get("location"), fallback_country=country)
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, entry.get("company"), entry.get("title"),
        entry.get("link"),
        location=location,
        workplace=base.detect_workplace(entry.get("location"), entry.get("title"),
                                        entry.get("snippet")),
        # Jooble's public payload calls this value "updated". Do not relabel an
        # aggregator refresh as the employer's original posting date.
        posted_at=None,
        description=entry.get("snippet") or "",
        tags=[entry.get("type")] if entry.get("type") else [],
        salary=entry.get("salary") or None)


def fetch(fetcher, config=None, settings=None, limit=None, countries=None,
          cursor=0, **_):
    key = base.api_key(KEY_VAR, settings)
    if not key:
        return base.skipped(SOURCE, f"no key ({KEY_VAR} is not set)")

    config = config or {}
    terms = config.get("keywords") or [""]
    places = list(countries or []) or [""]
    asked, start = base.query_slice(places, cursor)
    postings, errors = [], []
    before = fetcher.requests
    for place in asked:
        for term in terms:
            body = json.dumps({"keywords": term, "location": place}).encode("utf-8")
            data, response = _post_json(fetcher, SEARCH_URL.format(key=key), body)
            if data is None:
                # The key is in the path, so it must never reach an error line.
                errors.append(base.describe(response, f"{SOURCE} {place or 'any'} {term!r}"))
                continue
            for entry in (data.get("jobs") or []):
                postings.append(parse_job(entry, place or None))
                if limit and len(postings) >= limit:
                    break
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, errors[0], requests, errors)
    note = f"{len(asked)} of {len(places)} locations x {len(terms)} terms"
    if len(asked) < len(places):
        note += f" (rotating window from {start})"
    return base.ok(SOURCE, postings, requests, errors, note)


def _post_json(fetcher, url, body):
    """Jooble is the one channel that needs a POST, so the fetcher's throttle
    and retry are reused by handing it a request that already carries a body."""
    import urllib.parse
    import urllib.request
    host = urllib.parse.urlparse(url).netloc
    fetcher._throttle(host)
    fetcher.requests += 1
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"User-Agent": base.USER_AGENT, "Content-Type": "application/json"})
    try:
        with fetcher.opener.open(request, timeout=base.TIMEOUT_SECONDS) as raw:
            payload = raw.read(base.MAX_RESPONSE_BYTES)
            response = base.Response(raw.status, payload, dict(raw.headers))
    except Exception as exc:                       # noqa: BLE001 - reported, never raised
        return None, base.Response(0, error=f"{type(exc).__name__}: {exc}"[:200])
    if not response.ok:
        return None, response
    try:
        return response.json(), response
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        response.error = f"response was not JSON: {exc}"[:200]
        return None, response
