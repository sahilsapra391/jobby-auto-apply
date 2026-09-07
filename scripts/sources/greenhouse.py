#!/usr/bin/env python3
"""Greenhouse company boards.

Registry-driven: one request per company token, no key, no rate limit worth
worrying about. This is what makes hourly scanning affordable — the cost is a
function of how many companies are known, and every company that enters the
registry is covered for free from then on.

Bodies come with the list, and that decision was reversed once on evidence.

The first version fetched them per job, because the list is 59 KB for 83 jobs
and the same list with `content=true` is 845 KB — fourteen times the traffic for
descriptions of roles about to be rejected on title and country anyway.

Then a full sweep ran: 88 boards, 8,422 postings, 799 kept. Per-job fetching
needs one request each, at a second apiece, so the run took 6.5 minutes and hit
its own body cap at 300 — leaving seven of every ten kept jobs with no
description, no fingerprint, and no extracted experience. The cheaper request
was buying a worse board.

`content=true` costs one request per board instead of one per job: 88 requests
rather than 887, every kept job with a real body, and the sweep drops to about
a quarter of the time. The bandwidth is the price and it is worth paying.

Verified 2026-08-19: list at /v1/boards/{token}/jobs, one body at
/v1/boards/{token}/jobs/{id} (the `id` field, not `internal_job_id`, which
404s). A wrong token is a 404 with {"error":"Job not found"}. The per-job route
is kept because a caller may still want exactly one body.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402

SOURCE = "greenhouse"
SOURCE_CLASS = "ats"
#: This connector emits the §3 record key. It fetches one board per registry
#: token, so the token *is* the tenant slug, and the list response carries each
#: job's own id: both halves in hand, with nothing derived from a URL.
#:
#: **It is the one that moves the most records.** Measured against the live pool
#: on 2026-09-07: 7,465 greenhouse records, of which 4,206 parse a slug out of
#: their URL and 3,259 do not and are keyed on this device's own pool id, which
#: means nothing off this machine. The 3,259 are the postings on an employer's
#: own domain -- `stripe.com/jobs/search?gh_jid=...` carries the job id and not
#: the slug -- which `board_key.py` refuses to guess at, correctly, because a
#: wrong slug splits one board in two forever.
#:
#: And nothing already keyed correctly moves: of the 4,131 records where both a
#: derived slug and a registry token exist, the two agree on **all** of them,
#: and disagree on none. `id`, not `internal_job_id`, for the same reason the
#: module docstring gives: the other one 404s.
EMITS_BOARD_KEY = True
TIER = "1a"
LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
JOB_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job}"


def boards(companies):
    """Every company in the registry whose board is a Greenhouse one."""
    return [c for c in (companies or {}).values()
            if c.get("ats") == SOURCE and c.get("token")]


def parse_job(entry, company_name, token):
    location = base.parse_location((entry.get("location") or {}).get("name"))
    job_id = entry.get("id")
    job_id = str(job_id).strip() if job_id not in (None, "") else None
    return base.posting(
        SOURCE, SOURCE_CLASS, TIER,
        entry.get("company_name") or company_name,
        entry.get("title"),
        entry.get("absolute_url"),
        location=location,
        workplace=base.detect_workplace(location.get("raw"), entry.get("title")),
        # first_published is when the posting went up; updated_at moves whenever
        # anyone edits a typo, and using it would make every edited posting look
        # brand new.
        # An edit is not a new posting. Unknown is more honest than making an
        # old requisition look fresh because Greenhouse only supplied updated_at.
        posted_at=entry.get("first_published"),
        description=entry.get("content") or "",
        lang=entry.get("language"),
        # The token this board was fetched by, not a parse of the URL it
        # returned. Both or neither: a board with no token never reaches here
        # (`boards()` filters on it) and a job with no id falls back.
        board_slug=token if job_id else None,
        external_id=job_id,
    )


def fetch(fetcher, companies=None, limit=None, **_):
    postings, errors, dead = [], [], 0
    targets = boards(companies)
    if not targets:
        return base.skipped(SOURCE, "no Greenhouse boards in the company registry")
    before = fetcher.requests
    for company in targets:
        token = company["token"]
        data, response = fetcher.get_json(LIST_URL.format(token=token))
        if response.unchanged:
            continue                       # nothing has changed on this board
        if data is None:
            if response.status == 404:
                dead += 1
            errors.append(base.describe(response, f"{company.get('name')} ({token})"))
            continue
        for entry in (data.get("jobs") or []):
            postings.append(parse_job(entry, company.get("name"), token))
            if limit and len(postings) >= limit:
                break
        if limit and len(postings) >= limit:
            break
    note = f"{len(targets)} boards"
    if dead:
        note += f", {dead} token(s) 404 and need re-resolving"
    requests = fetcher.requests - before
    if errors and not postings:
        return base.failed(SOURCE, f"every board failed ({len(errors)})", requests, errors)
    # The board endpoint returns every open requisition for the tenant,
    # so a posting missing from it has been taken down.
    return base.ok(SOURCE, postings, requests, errors, note, complete=True)


def fetch_body(fetcher, entry_url, token, job_id):
    """One posting's description, for a job that survived the metadata gate."""
    data, response = fetcher.get_json(JOB_URL.format(token=token, job=job_id))
    if data is None:
        return "", base.describe(response, f"{token}/{job_id} body")
    return data.get("content") or "", ""
