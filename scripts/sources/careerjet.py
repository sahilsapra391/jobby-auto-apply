#!/usr/bin/env python3
"""Careerjet — 60+ country locales behind one affiliate key.

Endpoint shape not verified live: no key is installed. Written to the published
contract; the first real run confirms it.
"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "careerjet"
SOURCE_CLASS = "aggregator"
TIER = "1b"
KEY_VAR = "JOBBY_CAREERJET_KEY"
SEARCH_URL = "https://public.api.careerjet.net/search"
PAGE_SIZE = 50

# Careerjet is addressed by locale rather than by country code.
LOCALES = {"US": "en_US", "GB": "en_GB", "IN": "en_IN", "CA": "en_CA", "AU": "en_AU",
           "NZ": "en_NZ", "IE": "en_IE", "SG": "en_SG", "DE": "de_DE", "FR": "fr_FR",
           "ES": "es_ES", "IT": "it_IT", "NL": "nl_NL", "PL": "pl_PL", "SE": "sv_SE",
           "AE": "en_AE", "SA": "en_SA", "QA": "en_QA", "JP": "ja_JP", "KR": "ko_KR",
           "MY": "en_MY", "PH": "en_PH", "ID": "id_ID", "TH": "th_TH", "VN": "vi_VN",
           "ZA": "en_ZA", "BR": "pt_BR", "MX": "es_MX"}


def parse_job(entry, country):
    location = base.parse_location(entry.get("locations"), fallback_country=country)
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER, entry.get("company"), entry.get("title"),
        entry.get("url"), location=location,
        workplace=base.detect_workplace(entry.get("locations"), entry.get("title"),
                                        entry.get("description")),
        posted_at=entry.get("date"), description=entry.get("description") or "",
        salary=entry.get("salary") or None)


def fetch(fetcher, config=None, settings=None, limit=None, countries=None, **_):
    key = base.api_key(KEY_VAR, settings)
    if not key:
        return base.skipped(SOURCE, f"no key ({KEY_VAR} is not set)")
    config = config or {}
    wanted = [c.upper() for c in (countries or [])] or ["US"]
    reachable = [c for c in wanted if c in LOCALES]
    unreachable = sorted(set(wanted) - set(LOCALES))
    if not reachable:
        return base.skipped(SOURCE, f"no locale for the selected countries "
                                    f"({', '.join(unreachable) or 'none selected'})")

    terms = config.get("keywords") or [""]
    postings, errors = [], []
    before = fetcher.requests
    for country in reachable:
        for term in terms:
            params = {"affid": key, "locale_code": LOCALES[country], "pagesize": PAGE_SIZE,
                      "user_ip": "127.0.0.1", "user_agent": base.USER_AGENT}
            if term:
                params["keywords"] = term
            data, response = fetcher.get_json(
                f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
            if data is None:
                errors.append(base.describe(response, f"{SOURCE} {country} {term!r}"))
                continue
            if data.get("type") == "ERROR":
                errors.append(f"{SOURCE} {country}: {str(data.get('error'))[:120]}")
                continue
            for entry in (data.get("jobs") or []):
                postings.append(parse_job(entry, country))
                if limit and len(postings) >= limit:
                    break
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    note = f"{len(reachable)} locales x {len(terms)} terms"
    if unreachable:
        note += f"; no locale for: {', '.join(unreachable)}"
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, errors[0], requests, errors)
    return base.ok(SOURCE, postings, requests, errors, note)
