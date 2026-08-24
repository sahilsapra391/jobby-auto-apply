#!/usr/bin/env python3
"""Six more company-board platforms: SmartRecruiters, Workable, Recruitee,
Personio, Breezy and Rippling.

Grouped for the same reason `feeds.py` is: each is one unauthenticated request
per company returning that company's whole board, and the only real difference
is which key holds the title. Six modules would be six copies of twenty lines.

**These are registry-driven, and that is the whole story of their value.** There
is no endpoint that returns every SmartRecruiters job; there is only
`/v1/companies/{token}/postings`. A connector for a platform the registry knows
ten tenants of adds ten companies of coverage. See
docs/plans/ats-coverage-triage.md — the number that moves coverage is how many
company tokens are known, not how many platforms are spoken.

Every endpoint below was confirmed against a live tenant on 2026-08-20 before
the connector was written, and the shapes are not what a reasonable person
would guess:

  SmartRecruiters  {content: [...]}, and the list carries NO description, so
                   these records score on their title alone until a detail
                   fetch is added. Said out loud rather than hidden.
  Workable         {jobs: [...]}, with country and city as separate fields.
  Recruitee        {offers: [...]}, description in HTML, country_code present.
  Personio         XML, not JSON — and it is the only platform of the six that
                   states years of experience as a field.
  Breezy           a bare array; location is an object with is_remote.
  Rippling         a bare array with five keys and no description or date at
                   all, so its records are deliberately thin.
"""

import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE_CLASS = "ats"
TIER = "1a"

SMARTRECRUITERS_URL = "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100"
WORKABLE_URL = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
RECRUITEE_URL = "https://{token}.recruitee.com/api/offers/"
PERSONIO_URL = "https://{token}.jobs.personio.de/xml"
BREEZY_URL = "https://{token}.breezy.hr/json"
RIPPLING_URL = "https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs"


def boards(companies, ats):
    return [c for c in (companies or {}).values()
            if c.get("ats") == ats and c.get("token")]


def _sweep(fetcher, ats, url_template, extract, parse, companies, limit, accept=None):
    """One request per registry company on this platform, and loud about each."""
    postings, errors, dead = [], [], 0
    targets = boards(companies, ats)
    if not targets:
        return base.skipped(ats, f"no {ats} boards in the company registry")
    before = fetcher.requests
    for company in targets:
        url = url_template.format(token=company["token"])
        if accept:
            response = fetcher.get(url, headers={"Accept": accept})
            data = response.body if response.ok else None
        else:
            data, response = fetcher.get_json(url)
        if response.unchanged:
            continue
        if data is None:
            if response.status == 404:
                dead += 1
            errors.append(base.describe(response, f"{company.get('name')} ({company['token']})"))
            continue
        try:
            entries = extract(data)
        except (AttributeError, TypeError, KeyError, ValueError,
                ElementTree.ParseError) as exc:
            errors.append(f"{company.get('name')}: unexpected response shape ({exc})")
            continue
        for entry in entries:
            record = parse(entry, company)
            if record is not None:
                postings.append(record)
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    note = f"{len(targets)} boards"
    if dead:
        note += f", {dead} token(s) 404 and need re-resolving"
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(ats, f"every board failed ({len(errors)})", requests, errors)
    return base.ok(ats, postings, requests, errors, note)


# --- SmartRecruiters --------------------------------------------------------

def _smartrecruiters_job(entry, company):
    loc = entry.get("location") or {}
    country = (loc.get("country") or "").upper() or None
    location = base.parse_location(loc.get("fullLocation") or
                                   ", ".join(x for x in (loc.get("city"), loc.get("region")) if x),
                                   fallback_country=country)
    workplace = ("remote" if loc.get("remote") else
                 "hybrid" if loc.get("hybrid") else
                 base.detect_workplace(location.get("raw"), entry.get("name")))
    token = company["token"]
    tags = [(entry.get(k) or {}).get("label") for k in ("department", "function", "industry")]
    return base.posting(
        "smartrecruiters", SOURCE_CLASS, TIER,
        (entry.get("company") or {}).get("name") or company.get("name"),
        entry.get("name"),
        f"https://jobs.smartrecruiters.com/{token}/{entry.get('id')}",
        location=location, workplace=workplace,
        posted_at=entry.get("releasedDate"),
        # The postings list carries no description. These score on title until
        # a detail fetch is added, and the card says experience is not stated
        # because it genuinely is not known.
        description="",
        tags=[t for t in tags if t],
        lang=(entry.get("language") or {}).get("code"))


def smartrecruiters(fetcher, companies=None, limit=None, **_):
    return _sweep(fetcher, "smartrecruiters", SMARTRECRUITERS_URL,
                  lambda d: d.get("content") or [], _smartrecruiters_job, companies, limit)


# --- Workable ---------------------------------------------------------------

def _workable_job(entry, company):
    raw = ", ".join(x for x in (entry.get("city"), entry.get("state"), entry.get("country")) if x)
    location = base.parse_location(raw)
    if entry.get("country") and len(str(entry["country"])) == 2:
        location["country"] = str(entry["country"]).upper()
    workplace = "remote" if entry.get("telecommuting") else base.detect_workplace(
        raw, entry.get("title"))
    return base.posting(
        "workable", SOURCE_CLASS, TIER, company.get("name"), entry.get("title"),
        entry.get("url") or entry.get("shortlink") or entry.get("application_url"),
        location=location, workplace=workplace,
        # created_at can predate publication while a requisition is prepared.
        posted_at=entry.get("published_on"),
        description=entry.get("description") or "",
        tags=[t for t in (entry.get("department"), entry.get("function"),
                          entry.get("employment_type")) if t])


def workable(fetcher, companies=None, limit=None, **_):
    return _sweep(fetcher, "workable", WORKABLE_URL,
                  lambda d: d.get("jobs") or [], _workable_job, companies, limit)


# --- Recruitee --------------------------------------------------------------

def _recruitee_job(entry, company):
    location = base.parse_location(entry.get("location"),
                                   fallback_country=(entry.get("country_code") or "").upper() or None)
    workplace = ("remote" if entry.get("remote") else
                 "hybrid" if entry.get("hybrid") else
                 "onsite" if entry.get("on_site") else
                 base.detect_workplace(entry.get("location"), entry.get("title")))
    return base.posting(
        "recruitee", SOURCE_CLASS, TIER,
        entry.get("company_name") or company.get("name"),
        entry.get("title") or entry.get("position_title"),
        entry.get("careers_url") or entry.get("careers_apply_url"),
        location=location, workplace=workplace,
        # updated_at moves when the requisition changes. It is not its posted date.
        posted_at=entry.get("published_at"),
        description=entry.get("description") or "",
        tags=[t for t in (entry.get("department"), entry.get("category_code")) if t])


def recruitee(fetcher, companies=None, limit=None, **_):
    return _sweep(fetcher, "recruitee", RECRUITEE_URL,
                  lambda d: d.get("offers") or [], _recruitee_job, companies, limit)


# --- Personio (XML) ---------------------------------------------------------

def _personio_positions(body):
    root = ElementTree.fromstring(body.decode("utf-8", "replace")
                                  if isinstance(body, bytes) else body)
    return list(root.iter("position"))


def _personio_job(entry, company):
    def text(tag):
        node = entry.find(tag)
        return (node.text or "").strip() if node is not None and node.text else ""

    offices = [text("office")] + [(o.text or "").strip()
                                  for o in entry.iter("office")][1:]
    location = base.parse_location(offices[0] if offices else "")
    # <jobDescriptions><jobDescription><name/><value>CDATA</value></…>
    descriptions = " ".join(
        (node.text or "") for node in entry.iter("value") if node.text)
    token = company["token"]
    return base.posting(
        "personio", SOURCE_CLASS, TIER,
        text("subcompany") or company.get("name"), text("name"),
        f"https://{token}.jobs.personio.de/job/{text('id')}",
        location=location,
        workplace=base.detect_workplace(text("office"), text("name")),
        posted_at=text("createdAt") or None,
        description=descriptions,
        tags=[t for t in (text("department"), text("recruitingCategory"),
                          text("seniority"), text("schedule")) if t])


def personio(fetcher, companies=None, limit=None, **_):
    return _sweep(fetcher, "personio", PERSONIO_URL, _personio_positions,
                  _personio_job, companies, limit, accept="application/xml")


# --- Breezy -----------------------------------------------------------------

def _breezy_job(entry, company):
    loc = entry.get("location") or {}
    country = ((loc.get("country") or {}).get("id")
               if isinstance(loc.get("country"), dict) else loc.get("country"))
    location = base.parse_location(loc.get("name"),
                                   fallback_country=str(country).upper()[:2] if country else None)
    workplace = "remote" if loc.get("is_remote") else base.detect_workplace(
        loc.get("name"), entry.get("name"))
    return base.posting(
        "breezy", SOURCE_CLASS, TIER,
        (entry.get("company") or {}).get("name") or company.get("name"),
        entry.get("name"), entry.get("url"),
        location=location, workplace=workplace,
        posted_at=entry.get("published_date"),
        description=entry.get("description") or "",
        tags=[t for t in (entry.get("department"),
                          (entry.get("type") or {}).get("name")) if t],
        salary=entry.get("salary") or None)


def breezy(fetcher, companies=None, limit=None, **_):
    return _sweep(fetcher, "breezy", BREEZY_URL, lambda d: d if isinstance(d, list) else [],
                  _breezy_job, companies, limit)


# --- Rippling ---------------------------------------------------------------

def _rippling_job(entry, company):
    where = (entry.get("workLocation") or {}).get("label") or ""
    return base.posting(
        "rippling", SOURCE_CLASS, TIER, company.get("name"), entry.get("name"),
        entry.get("url"), location=base.parse_location(where),
        workplace=base.detect_workplace(where, entry.get("name")),
        # No date and no description in this payload at all, so these records
        # are thin by construction and score their unknowns as unknown.
        posted_at=None, description="",
        tags=[t for t in [(entry.get("department") or {}).get("label")] if t])


def rippling(fetcher, companies=None, limit=None, **_):
    return _sweep(fetcher, "rippling", RIPPLING_URL,
                  lambda d: d if isinstance(d, list) else [], _rippling_job, companies, limit)


CONNECTORS = {
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "recruitee": recruitee,
    "personio": personio,
    "breezy": breezy,
    "rippling": rippling,
}
