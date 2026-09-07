#!/usr/bin/env python3
"""Five public tenant boards: Pinpoint, Teamtailor, JazzHR, Oracle and Gem."""

import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

ORACLE_PAGE_SIZE = 200
ORACLE_MAX_PAGES = 50


def targets(companies, ats):
    return [c for c in (companies or {}).values()
            if c.get("ats") == ats and c.get("token")]


def finish(source, postings, errors, requests, count, complete=False,
           unchanged=0):
    """`complete` says the whole of every board asked for came back.

    It is not defaulted True here for the same reason base.ok is not: a
    platform added later that forgets to claim it stays harmless, and one that
    claims it wrongly closes live jobs.

    **`unchanged` is counted and reported because a 304 is an answer.** It is
    not a failure and it is not a shortfall: the board is telling us its
    listing has not moved since we last read it, so the postings we hold for it
    are still current and the connector legitimately returns nothing for it.
    Without the count the yield ratchet in `scripts/sweep_runs.py` would read a
    well-cached channel as a truncated one and refuse its closures every sweep.
    """
    if errors and not postings and not unchanged:
        return base.failed(source, f"every board failed ({len(errors)})", requests, errors)
    note = f"{count} boards"
    if unchanged:
        note += f", {unchanged} unchanged since the last run"
    return base.ok(source, postings, requests, errors, note,
                   complete=complete, unchanged=unchanged)


def workplace(value, fallback=None):
    value = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {"remote": "remote", "fully_remote": "remote", "telecommute": "remote",
            "hybrid": "hybrid", "on_site": "onsite", "onsite": "onsite",
            "in_office": "onsite", "office": "onsite"}.get(value, fallback)


def pinpoint(fetcher, companies=None, limit=None, **_):
    source, postings, errors, before = "pinpoint", [], [], fetcher.requests
    unchanged = 0
    boards = targets(companies, source)
    if not boards:
        return base.skipped(source, "no Pinpoint boards in the company registry")
    for company in boards:
        data, response = fetcher.get_json(
            f"https://{company['token']}.pinpointhq.com/postings.json")
        # **A 304 is an answer, not a failure.** `get_json` returns
        # (None, response) for anything that is not a 200, so an unchanged
        # board landed in the error branch below: 36 of pinpoint's 39 boards
        # and 18 of teamtailor's 23 on the 2026-09-07 sweep, 54 of the run's 67
        # errors. The boards went unread, the channel still said "39 boards"
        # and claimed completeness, and the error list became 80% noise, which
        # is how a real error stops being visible.
        if response.unchanged:
            unchanged += 1
            continue
        if data is None:
            errors.append(base.describe(response, company.get("name")))
            continue
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            errors.append(f"{company.get('name')}: unexpected response shape")
            continue
        for entry in entries:
            loc = entry.get("location") or {}
            raw = ", ".join(x for x in (loc.get("name"), loc.get("city"),
                                        loc.get("province")) if x)
            postings.append(base.posting(
                source, "ats", "1a", company.get("name"), entry.get("title"), entry.get("url"),
                location=base.parse_location(raw),
                workplace=workplace(entry.get("workplace_type"),
                                    base.detect_workplace(raw, entry.get("title"))),
                posted_at=None,
                description=" ".join(str(entry.get(k) or "") for k in
                                     ("description", "key_responsibilities",
                                      "skills_knowledge_expertise")),
                tags=[x for x in (entry.get("employment_type_text"),
                                  (entry.get("job") or {}).get("department", {}).get("name")) if x],
                salary=entry.get("compensation")))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    return finish(source, postings, errors, fetcher.requests - before,
                  len(boards), complete=True,
                  unchanged=unchanged)


def teamtailor(fetcher, companies=None, limit=None, **_):
    source, postings, errors, before = "teamtailor", [], [], fetcher.requests
    unchanged = 0
    boards = targets(companies, source)
    if not boards:
        return base.skipped(source, "no Teamtailor boards in the company registry")
    for company in boards:
        data, response = fetcher.get_json(f"https://{company['token']}.teamtailor.com/jobs.json")
        if response.unchanged:
            unchanged += 1
            continue
        if data is None:
            errors.append(base.describe(response, company.get("name")))
            continue
        entries = data.get("items") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            errors.append(f"{company.get('name')}: unexpected response shape")
            continue
        for entry in entries:
            schema = entry.get("_jobposting") or {}
            locations = schema.get("jobLocation") or []
            address = (locations[0].get("address") or {}) if locations else {}
            raw = ", ".join(x for x in (address.get("addressLocality"),
                                        address.get("addressRegion"),
                                        address.get("addressCountry")) if x)
            postings.append(base.posting(
                source, "ats", "1a", company.get("name"), entry.get("title"), entry.get("url"),
                location=base.parse_location(raw,
                    fallback_country=address.get("addressCountry")),
                workplace=workplace(schema.get("jobLocationType"),
                                    base.detect_workplace(raw, entry.get("title"))),
                posted_at=entry.get("date_published"),
                description=entry.get("content_html") or schema.get("description") or ""))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    return finish(source, postings, errors, fetcher.requests - before,
                  len(boards), complete=True,
                  unchanged=unchanged)


class JazzListParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.link = [], None, None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and str(attrs.get("id", "")).startswith("row_job_"):
            self.row = {"title": "", "url": "", "location": ""}
        elif self.row is not None and tag == "td":
            self.cell = "title" if self.cell is None else "location"
        elif self.row is not None and tag == "a" and "job_title_link" in attrs.get("class", ""):
            self.link = True
            self.row["url"] = attrs.get("href", "")

    def handle_data(self, data):
        if self.row is not None and self.cell in ("title", "location"):
            if self.cell == "title" and not self.link:
                return
            self.row[self.cell] += data.strip() + " "

    def handle_endtag(self, tag):
        if tag == "a":
            self.link = None
        elif tag == "td":
            self.cell = "location" if self.cell == "title" else None
        elif tag == "tr" and self.row is not None:
            self.rows.append({k: v.strip() for k, v in self.row.items()})
            self.row, self.cell = None, None


def jazzhr(fetcher, companies=None, limit=None, **_):
    source, postings, errors, before = "jazzhr", [], [], fetcher.requests
    boards = targets(companies, source)
    if not boards:
        return base.skipped(source, "no JazzHR boards in the company registry")
    for company in boards:
        origin = f"https://{company['token']}.applytojob.com"
        response = fetcher.get(origin + "/apply/jobs", headers={"Accept": "text/html"})
        if not response.ok:
            errors.append(base.describe(response, company.get("name")))
            continue
        parser = JazzListParser()
        parser.feed(response.body.decode("utf-8", "replace"))
        if not parser.rows and b"jobs_table" not in response.body:
            errors.append(f"{company.get('name')}: unexpected response shape")
            continue
        for entry in parser.rows:
            raw = html.unescape(entry["location"])
            postings.append(base.posting(
                source, "ats", "1a", company.get("name"),
                html.unescape(entry["title"]), origin + entry["url"].split("?", 1)[0],
                location=base.parse_location(raw),
                workplace=base.detect_workplace(raw, entry["title"]),
                posted_at=None, description=""))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    return finish(source, postings, errors, fetcher.requests - before,
                  len(boards), complete=True)


def oracle(fetcher, companies=None, limit=None, **_):
    source, postings, errors, before = "oracle_orc", [], [], fetcher.requests
    # Cleared by any board the page ceiling cut short: past that point every
    # posting would read as taken down, which is not something we know.
    whole = True
    boards = targets(companies, source)
    if not boards:
        return base.skipped(source, "no Oracle Recruiting boards in the company registry")
    for company in boards:
        parsed = urlparse(company.get("careersUrl") or "")
        site = company.get("token")
        if not parsed.netloc:
            errors.append(f"{company.get('name')}: missing verified Oracle host")
            continue
        offset, complete = 0, False
        for _page in range(ORACLE_MAX_PAGES):
            page_size = min(ORACLE_PAGE_SIZE, limit - len(postings)) if limit else ORACLE_PAGE_SIZE
            finder = (f"findReqs;siteNumber={site},limit={page_size},offset={offset},"
                      "sortBy=POSTING_DATES_DESC")
            url = (f"https://{parsed.netloc}/hcmRestApi/resources/latest/"
                   f"recruitingCEJobRequisitions?onlyData=true&expand=requisitionList&finder={finder}")
            data, response = fetcher.get_json(url, headers={"ora-irc-language": "en"})
            if data is None:
                errors.append(base.describe(response, company.get("name")))
                complete = True
                break
            roots = data.get("items") if isinstance(data, dict) else None
            root = roots[0] if roots else None
            entries = root.get("requisitionList") if root else None
            if not isinstance(entries, list):
                errors.append(f"{company.get('name')}: unexpected response shape")
                complete = True
                break
            for entry in entries:
                raw = entry.get("PrimaryLocation") or ""
                postings.append(base.posting(
                    source, "ats", "1a", company.get("name"), entry.get("Title"),
                    f"https://{parsed.netloc}/hcmUI/CandidateExperience/en/sites/{site}/job/{entry.get('Id')}",
                    location=base.parse_location(raw,
                        fallback_country=entry.get("PrimaryLocationCountry")),
                    workplace=workplace(entry.get("WorkplaceType"),
                                        base.detect_workplace(raw, entry.get("ShortDescriptionStr"))),
                    posted_at=entry.get("PostedDate"),
                    description=entry.get("ShortDescriptionStr") or "",
                    tags=[x for x in (entry.get("JobFamily"), entry.get("JobFunction"),
                                      entry.get("JobType")) if x]))
                if limit and len(postings) >= limit:
                    break
            offset += len(entries)
            total = int(root.get("TotalJobsCount") or offset)
            if (limit and len(postings) >= limit) or not entries or offset >= total:
                complete = True
                break
        if not complete:
            whole = False
            errors.append(f"{company.get('name')}: board exceeded the "
                          f"{ORACLE_MAX_PAGES * ORACLE_PAGE_SIZE}-job safety ceiling")
        if limit and len(postings) >= limit:
            break
    return finish(source, postings, errors, fetcher.requests - before,
                  len(boards), complete=whole)


def gem(fetcher, companies=None, limit=None, **_):
    source, postings, errors, before = "gem", [], [], fetcher.requests
    boards = targets(companies, source)
    if not boards:
        return base.skipped(source, "no Gem boards in the company registry")
    for company in boards:
        data, response = fetcher.get_json(
            f"https://api.gem.com/job_board/v0/{quote(company['token'])}/job_posts/")
        if data is None:
            errors.append(base.describe(response, company.get("name")))
            continue
        if not isinstance(data, list):
            errors.append(f"{company.get('name')}: unexpected response shape")
            continue
        for entry in data:
            raw = (entry.get("location") or {}).get("name") or ""
            arrangement = workplace(entry.get("location_type"))
            postings.append(base.posting(
                source, "ats", "1a", company.get("name"), entry.get("title"),
                entry.get("absolute_url"), location=base.parse_location(raw),
                workplace=arrangement,
                posted_at=entry.get("first_published_at"),
                description=entry.get("content_plain") or entry.get("content") or "",
                tags=[x.get("name") for x in (entry.get("departments") or []) if x.get("name")]))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    return finish(source, postings, errors, fetcher.requests - before,
                  len(boards), complete=True)


CONNECTORS = {"pinpoint": pinpoint, "teamtailor": teamtailor, "jazzhr": jazzhr,
              "oracle_orc": oracle, "gem": gem}
