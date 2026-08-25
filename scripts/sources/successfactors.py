#!/usr/bin/env python3
"""SAP SuccessFactors Career Site Builder boards.

Registry-driven like the other company-board platforms: the token is the
employer's own career hostname, and every CSB site serves the same feed at
`/services/rss/job/`. That path is what identifies the platform. Probing 50
candidate career hostnames on 2026-08-25 found it on two of them; the other 48
run something else at the same address and simply do not have it, which is a
cleaner test than looking for vendor branding on the page.

Two things about this feed are worth knowing before reading the parser.

**It is not a complete board listing.** It returns the most recent items, not
every open requisition, so the connector reports `complete=False` and its
postings never reach the staleness sweep. SAP alone has thousands of open
roles; treating a twenty-item window as "everything SAP has open" would expire
almost all of them on the first sweep. The postings are still `ats` class,
because they are the employer's own and an application goes through this board.

**The location lives in the title**, as a trailing parenthetical ending in an
ISO country code: `Senior Specialist (Dublin 24, IE, D24WA02)`. The role name
is what precedes it. A title with no such parenthetical keeps its whole text
and gets no location, rather than being cut at some other bracket.

Links carry `feedId` and `utm_*` tracking that changes between requests. Those
are stripped, because the canonical URL is what identity resolution matches on
and a rotating query string would mint a new record for the same job every run.

Verified against jobs.sap.com and jobs.bayer.com on 2026-08-25.
"""

import re
import sys
import urllib.parse
import xml.etree.ElementTree as ElementTree
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE_CLASS = "ats"
TIER = "1a"
ATS = "successfactors"

# The path is the platform. Nothing else on a career hostname serves it.
FEED_URL = "https://{token}/services/rss/job/?locale=en_US"

# Tracking that changes per request and would otherwise break URL identity.
DROP_PARAMS = ("feedid", "utm_source", "utm_medium", "utm_campaign",
               "utm_term", "utm_content", "src", "srcid")

# "Dublin 24, IE, D24WA02" -> the parenthetical the title ends with.
TAIL = re.compile(r"\s*\(([^()]{3,120})\)\s*$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
# A postal code, which SuccessFactors puts in the same parenthetical as the
# city: D24WA02, 100-0004, 821 09, 66265. The test is a digit with no lowercase
# letter, which keeps "Dublin 24" and "Rio Grande do Sul 2" -- real place names
# carry lowercase -- and drops the codes, which never do.
POSTCODE = re.compile(r"^(?=.*\d)[^a-z]+$")


def clean_url(url):
    """Drop per-request tracking, keep everything the page actually needs."""
    if not url:
        return url
    parts = urllib.parse.urlsplit(url)
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in DROP_PARAMS]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path,
         urllib.parse.urlencode(kept), ""))


def split_title(raw):
    """Return (role, location text, country code).

    Only a trailing parenthesis counts, and only when its last comma-separated
    part or the one before it is an ISO country code. "Manager (EMEA)" and
    "Engineer (Contract)" are not locations, and guessing would put a
    department in the location field on every such posting.
    """
    raw = (raw or "").strip()
    match = TAIL.search(raw)
    if not match:
        return raw, "", None
    parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
    country = next((p for p in reversed(parts) if COUNTRY.match(p)), None)
    if not country:
        return raw, "", None
    where = ", ".join(p for p in parts
                      if p != country and not POSTCODE.match(p))
    return raw[:match.start()].strip() or raw, where, country


def _job(item, company):
    def text(tag):
        node = item.find(tag)
        return (node.text or "").strip() if node is not None else ""

    role, where, country = split_title(text("title"))
    url = clean_url(text("link") or text("guid"))
    if not role or not url:
        return None
    location = base.parse_location(where, fallback_country=country)
    description = text("description")
    return base.posting(
        ATS, SOURCE_CLASS, TIER, company.get("name") or company["token"], role, url,
        location=location,
        workplace=base.detect_workplace(location.get("raw"), f"{role} {description[:400]}"),
        posted_at=base.rfc822_date(text("pubDate")),
        description=description,
        logo_url=company.get("logoUrl"))


def successfactors(fetcher, companies=None, limit=None, **_):
    targets = [c for c in (companies or {}).values()
               if c.get("ats") == ATS and c.get("token")]
    if not targets:
        return base.skipped(ATS, "no successfactors boards in the company registry")

    postings, errors, dead = [], [], 0
    before = fetcher.requests
    for company in targets:
        response = fetcher.get(FEED_URL.format(token=company["token"]),
                               headers={"Accept": "application/rss+xml, application/xml"})
        if response.unchanged:
            continue
        if not response.ok:
            if response.status == 404:
                dead += 1
            errors.append(base.describe(response, f"{company.get('name')} ({company['token']})"))
            continue
        try:
            root = ElementTree.fromstring(response.body)
        except ElementTree.ParseError as exc:
            errors.append(f"{company.get('name')}: the feed did not parse ({exc})")
            continue
        for item in root.findall("./channel/item"):
            record = _job(item, company)
            if record is not None:
                postings.append(record)
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break

    note = f"{len(targets)} boards, most recent items only"
    if dead:
        note += f", {dead} token(s) 404 and need re-resolving"
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(ATS, f"every board failed ({len(errors)})", requests, errors)
    # Never a complete listing: see the module docstring.
    return base.ok(ATS, postings, requests, errors, note, complete=False)


CHANNELS = {ATS: successfactors}
