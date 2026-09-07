#!/usr/bin/env python3
"""Careerjet — 60+ country locales behind one affiliate key.

Verified live 2026-09-07: found 50, kept 0, status ok. Zero kept is the search
gate, not the connector. Written to the published
contract; the first real run confirms it.
"""

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "careerjet"
SOURCE_CLASS = "aggregator"
#: The hosts this connector's postings actually live on, which is not
#: the API host above and is what the board derives a logo from.
#: Careerjet's own API hands out `jobviewtrack.com` links rather than
#: careerjet.co.uk ones, which is why the covering entry does not look
#: like the source's name. Measured over 956 postings.
#:
#: **Declared so it can be checked.** An aggregator whose host is not in
#: `company_logos.INFRA_HOST_PARTS` has the board drawing the job
#: board's own mark on every employer card it supplies, and that is
#: invisible precisely because it is consistent.
#: `tests/test_aggregator_hosts.py` fails when the two part.
POSTING_HOSTS = ("jobviewtrack.com",)
TIER = "1b"
KEY_VAR = "JOBBY_CAREERJET_KEY"
# HTTP, not HTTPS. Verified 2026-08-20 during the supervised run: the public
# endpoint refuses a TLS connection outright (connection refused on 443) and
# answers on port 80. That means the affiliate key travels in plaintext, which
# is recorded in references/sources-registry.md rather than papered over — it is
# an affiliate identifier rather than an account credential, but it is still a
# key on the wire.
SEARCH_URL = "http://public.api.careerjet.net/search"
REFERER = "https://github.com/sahilsapra391/jobby-auto-apply"
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
        salary=entry.get("salary") or None,
        # Careerjet answers with a per-request tracking redirect, so the same
        # posting has a different URL every time it is asked for.
        url_stable=False)


def fetch(fetcher, config=None, settings=None, limit=None, countries=None,
          cursor=0, **_):
    key = base.api_key(KEY_VAR, settings)
    if not key:
    # **A missing key is a fault, not a choice.** `skipped` is for a channel
    # this run had no reason to ask -- reed on a search that excludes the UK --
    # and it is absent from the report's error list, so a channel configured to
    # run and unable to run looked identical to one deliberately left out. In a
    # container, where the key arrives as an environment variable, that is the
    # difference between a sweep that says it is missing five sources and one
    # that looks healthy while missing them.
        return base.failed(SOURCE, f"no key: set {KEY_VAR}")
    config = config or {}
    wanted = [c.upper() for c in (countries or [])] or ["US"]
    reachable = [c for c in wanted if c in LOCALES]
    unreachable = sorted(set(wanted) - set(LOCALES))
    if not reachable:
        return base.skipped(SOURCE, f"no locale for the selected countries "
                                    f"({', '.join(unreachable) or 'none selected'})")

    terms = config.get("keywords") or [""]
    asked, start = base.query_slice(reachable, cursor)
    postings, errors = [], []
    before = fetcher.requests
    for country in asked:
        for term in terms:
            params = {"affid": key, "locale_code": LOCALES[country], "pagesize": PAGE_SIZE,
                      "user_ip": "127.0.0.1", "user_agent": base.USER_AGENT}
            if term:
                params["keywords"] = term
            # Careerjet refuses an undeclared caller: "Please add a Referer
            # header so we know who is calling this API and from which page."
            # Verified 2026-08-20 — the body says so in as many words. This is
            # the same honest-identification rule as the User-Agent, so it
            # names the project rather than pretending to be a browser.
            data, response = fetcher.get_json(
                f"{SEARCH_URL}?{urllib.parse.urlencode(params)}",
                headers={"Referer": REFERER})
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
    note = f"{len(asked)} of {len(reachable)} locales x {len(terms)} terms"
    if len(asked) < len(reachable):
        note += f" (rotating window from {start})"
    if unreachable:
        note += f"; no locale for: {', '.join(unreachable)}"
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, errors[0], requests, errors)
    return base.ok(SOURCE, postings, requests, errors, note)
