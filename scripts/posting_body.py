#!/usr/bin/env python3
"""The posting text for an application that arrived through the Found board.

A job promoted from Found carries no posting body. `promote.py` builds the
Watching session out of the job record, and its own docstring says why: phase 1
promotes a job before anything has read the posting. Nothing later filled the
gap, so `document_contract.build_bundle` reached "the application has no job
description", and `document_job._submit` flattened that to `invalid_request`.

Measured on 2026-08-25 across all 87 sessions: the 59 created by `run_scan`
carried an extraction, the 24 promoted from Found carried none, no exceptions
either way. Six had reached Apply and all six were parked one second after the
job was created, before anything reached the worker.

Two sources, in order.

The employer's own board, fetched in full. `descriptionExcerpt` on the job
record is capped at `base.EXCERPT_CHARS`, which is 1,500 characters, and it cuts
mid-word: enough to score a job, not enough to tailor a resume against it. The
six parked applications were all Greenhouse, where the full body runs 6.8k to
8.8k characters.

Then the stored excerpt, when the ATS has no body fetcher or the fetch fails.
Truncated posting text beats no posting text, because the alternative is an
application that parks.

The network is touched only for a job whose record names a source this module
can fetch, so a store that does not hold the record resolves offline or not at
all. Callers may inject a fetcher to keep it offline outright.

Standard library only.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import job_description  # noqa: E402
import jobs_store  # noqa: E402


class PostingBodyError(Exception):
    """The posting could not be read. Never fatal: callers fall back."""


def stored_excerpt(job):
    """The truncated body the sourcing sweep already saved with the record."""
    text = (job or {}).get("descriptionExcerpt")
    return text.strip() if isinstance(text, str) else ""


def full_body(job, fetcher=None):
    """Fetch the complete posting for one job record, or raise.

    Raises rather than returning empty, so `resolve` can tell a board it could
    not read apart from a fetch that went out and came back with nothing.

    The per-board routing lives in `job_description`, which reads Greenhouse,
    Lever, Ashby, SmartRecruiters and Workday through their own JSON endpoints
    and falls back to the posting page. Greenhouse alone was not enough: the
    pool spans every one of those, and Workday's page is an empty app shell
    that is readable no other way.
    """
    url = (job or {}).get("canonicalUrl") or ""
    if not url:
        raise PostingBodyError("the job record has no posting address")
    try:
        text = job_description.fetch(url, fetcher=fetcher).strip()
    except job_description.Unfetchable as exc:
        raise PostingBodyError(str(exc)) from exc
    if not text:
        raise PostingBodyError("the posting body came back empty")
    return text


def resolve_detail(application_id, root=None, fetcher=None, allow_network=True):
    """(text, source, why) for one application. Never raises.

    `source` is "posting" for the full body, "excerpt" for the 1,500-character
    copy the sweep stored, or "" for nothing. The caller records which one it
    got, because a resume tailored against an excerpt is a worse application
    that still goes out under the user's name, and that should be visible on
    the card rather than inferred later from the length of a file.
    """
    try:
        jobs = jobs_store.load_jobs(jobs_store.root_dir(root))
    except (jobs_store.JobsStoreError, OSError):
        return "", "", "the job pool could not be read"
    job = jobs.get(application_id)
    if not isinstance(job, dict):
        return "", "", "this application is no longer in the job pool"
    why = ""
    if allow_network:
        try:
            return full_body(job, fetcher), "posting", ""
        except (PostingBodyError, OSError, ValueError) as exc:
            why = str(exc)[:200]
    excerpt = stored_excerpt(job)
    if excerpt:
        return excerpt, "excerpt", why
    return "", "", why or "the job record holds no posting text"


def resolve(application_id, root=None, fetcher=None, allow_network=True):
    """The best posting text available for one application, or ""."""
    return resolve_detail(application_id, root, fetcher, allow_network)[0]
