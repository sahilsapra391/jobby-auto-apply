#!/usr/bin/env python3
"""Remote-job feeds: RemoteOK, We Work Remotely, Remotive, Himalayas, Working
Nomads.

Five channels, one file, because each is a single unauthenticated request
returning a flat list and the only thing that differs is which key holds the
title. Giving each of them a module of its own would be five copies of the same
twenty lines.

Every posting from these is remote by construction, so `workplace` is set
rather than detected. What they cannot tell you is the country: "Remote (US
only)" and "Remote — Europe" are restrictions rather than locations, so the
country stays None and the location bucket becomes `remote`, which is exactly
what it is.

Verified 2026-08-19. RemoteOK's first array element is a legal notice rather
than a job and would otherwise enter the pool as a posting with no title. We
Work Remotely is RSS rather than JSON. Remotive wraps its list in a `jobs` key
alongside two legal-notice keys. Himalayas dates in epoch seconds. Working
Nomads returns a bare array with comma-separated tags in a string.
"""

import re
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE_CLASS = "feed"
TIER = "1a"

REMOTEOK_URL = "https://remoteok.com/api"
WWR_URL = "https://weworkremotely.com/remote-jobs.rss"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
HIMALAYAS_URL = "https://himalayas.app/jobs/api?limit=200"
WORKING_NOMADS_URL = "https://www.workingnomads.com/api/exposed_jobs/"


def _collect(source, entries, parse, limit):
    postings = []
    for entry in entries:
        record = parse(entry)
        if record is None:
            continue
        postings.append(record)
        if limit and len(postings) >= limit:
            break
    return postings


def _simple(fetcher, source, url, extract, parse, limit):
    """The shape all five share: one request, one list, one parser."""
    before = fetcher.requests
    data, response = fetcher.get_json(url)
    requests = fetcher.requests - before
    if response.unchanged:
        return base.ok(source, [], requests, note="unchanged since the last run")
    if data is None:
        return base.failed(source, base.describe(response, source), requests,
                           [base.describe(response, source)])
    try:
        entries = extract(data)
    except (AttributeError, TypeError, KeyError) as exc:
        return base.failed(source, f"unexpected response shape: {exc}", requests)
    return base.ok(source, _collect(source, entries, parse, limit), requests,
                   note=f"{len(entries)} in the feed")


def remoteok(fetcher, limit=None, **_):
    def parse(entry):
        # The feed's first element is a legal notice, not a job.
        if not entry.get("position") or not entry.get("id"):
            return None
        salary = None
        if entry.get("salary_min") and entry.get("salary_max"):
            salary = f"{entry['salary_min']}-{entry['salary_max']}"
        return base.posting(
            "remoteok", SOURCE_CLASS, TIER, entry.get("company"), entry.get("position"),
            entry.get("url") or entry.get("apply_url"),
            location=base.parse_location(entry.get("location")),
            workplace="remote", posted_at=entry.get("date") or entry.get("epoch"),
            description=entry.get("description") or "", tags=entry.get("tags") or [],
            salary=salary, logo_url=entry.get("company_logo") or entry.get("logo"))
    return _simple(fetcher, "remoteok", REMOTEOK_URL, lambda d: d, parse, limit)


def remotive(fetcher, limit=None, **_):
    def parse(entry):
        return base.posting(
            "remotive", SOURCE_CLASS, TIER, entry.get("company_name"), entry.get("title"),
            entry.get("url"),
            location=base.parse_location(entry.get("candidate_required_location")),
            workplace="remote", posted_at=entry.get("publication_date"),
            description=entry.get("description") or "",
            tags=(entry.get("tags") or []) + ([entry["category"]] if entry.get("category") else []),
            salary=entry.get("salary") or None,
            logo_url=entry.get("company_logo") or entry.get("company_logo_url"))
    return _simple(fetcher, "remotive", REMOTIVE_URL, lambda d: d.get("jobs") or [], parse, limit)


def himalayas(fetcher, limit=None, **_):
    def parse(entry):
        restrictions = entry.get("locationRestrictions") or []
        return base.posting(
            "himalayas", SOURCE_CLASS, TIER, entry.get("companyName"), entry.get("title"),
            entry.get("applicationLink") or entry.get("guid"),
            location=base.parse_location(", ".join(str(r) for r in restrictions)),
            workplace="remote", posted_at=entry.get("pubDate"),
            description=entry.get("description") or entry.get("excerpt") or "",
            tags=(entry.get("categories") or []) + (entry.get("seniority") or []),
            logo_url=entry.get("companyLogo"))
    return _simple(fetcher, "himalayas", HIMALAYAS_URL, lambda d: d.get("jobs") or [],
                   parse, limit)


def working_nomads(fetcher, limit=None, **_):
    def parse(entry):
        tags = [t.strip() for t in str(entry.get("tags") or "").split(",") if t.strip()]
        return base.posting(
            "workingnomads", SOURCE_CLASS, TIER, entry.get("company_name"), entry.get("title"),
            entry.get("url"), location=base.parse_location(entry.get("location")),
            workplace="remote", posted_at=entry.get("pub_date"),
            description=entry.get("description") or "",
            tags=tags + ([entry["category_name"]] if entry.get("category_name") else []))
    return _simple(fetcher, "workingnomads", WORKING_NOMADS_URL, lambda d: d, parse, limit)


def parse_wwr_rss(xml_text):
    """We Work Remotely's RSS. Title is "Company: Role", which is the only place
    the company name appears at all."""
    entries = []
    root = ElementTree.fromstring(xml_text)
    for item in root.iter("item"):
        def text(tag):
            found = item.find(tag)
            return (found.text or "").strip() if found is not None else ""
        title = text("title")
        company, _, role = title.partition(":")
        description = text("description")
        logo = re.search(r'<img[^>]+src=["\']([^"\']+)', description, re.I)
        entries.append({
            "company": company.strip() if role else "",
            "title": (role or title).strip(),
            "url": text("link"),
            "description": description,
            "logoUrl": logo.group(1) if logo else "",
            "pubDate": text("pubDate"),
            "region": text("region"),
        })
    return entries


def weworkremotely(fetcher, limit=None, **_):
    before = fetcher.requests
    response = fetcher.get(WWR_URL, headers={"Accept": "application/rss+xml"})
    requests = fetcher.requests - before
    if response.unchanged:
        return base.ok("weworkremotely", [], requests, note="unchanged since the last run")
    if not response.ok:
        reason = base.describe(response, "weworkremotely")
        return base.failed("weworkremotely", reason, requests, [reason])
    try:
        entries = parse_wwr_rss(response.body.decode("utf-8", "replace"))
    except ElementTree.ParseError as exc:
        return base.failed("weworkremotely", f"the feed was not valid RSS: {exc}", requests)

    def parse(entry):
        if not entry.get("title"):
            return None
        return base.posting(
            "weworkremotely", SOURCE_CLASS, TIER, entry.get("company"), entry.get("title"),
            entry.get("url"), location=base.parse_location(entry.get("region")),
            workplace="remote", posted_at=base.rfc822_date(entry.get("pubDate")),
            description=entry.get("description") or "", logo_url=entry.get("logoUrl"))
    return base.ok("weworkremotely", _collect("weworkremotely", entries, parse, limit),
                   requests, note=f"{len(entries)} in the feed")


CONNECTORS = {
    "remoteok": remoteok,
    "weworkremotely": weworkremotely,
    "remotive": remotive,
    "himalayas": himalayas,
    "workingnomads": working_nomads,
}
