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
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import jobs_store  # noqa: E402
from sources import base, greenhouse  # noqa: E402


class PostingBodyError(Exception):
    """The posting could not be read. Never fatal: callers fall back."""


def _greenhouse_parts(url):
    """Board token and job id out of either Greenhouse posting URL shape.

    Both `job-boards.greenhouse.io/<token>/jobs/<id>` and the older
    `boards.greenhouse.io/<token>/jobs/<id>` are in the pool, and the older one
    arrives carrying `?gh_jid=...`. Splitting the raw URL on "/" leaves that
    query string welded to the job id and the API answers 404, so the query and
    fragment come off first.
    """
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc.endswith("greenhouse.io"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 3 or parts[-2] != "jobs":
        return None
    token, job_id = parts[-3], parts[-1]
    if not token or not job_id.isdigit():
        return None
    return token, job_id


def _greenhouse_body(fetcher, url):
    parts = _greenhouse_parts(url)
    if parts is None:
        raise PostingBodyError("the posting URL is not a Greenhouse job")
    body, error = greenhouse.fetch_body(fetcher, url, parts[0], parts[1])
    if error:
        raise PostingBodyError(error)
    return base.strip_html(body)


# Sources whose full posting body this module knows how to read. A source that
# is absent here is not a failure, it just means the stored excerpt is the best
# text available for that job.
FETCHERS = {"greenhouse": _greenhouse_body}


def stored_excerpt(job):
    """The truncated body the sourcing sweep already saved with the record."""
    text = (job or {}).get("descriptionExcerpt")
    return text.strip() if isinstance(text, str) else ""


def full_body(job, fetcher=None):
    """Fetch the complete posting for one job record, or raise.

    Raises rather than returning empty, so `resolve` can tell "this ATS has no
    fetcher" apart from "the fetch went out and came back with nothing".
    """
    source = (job or {}).get("canonicalSource")
    url = (job or {}).get("canonicalUrl") or ""
    read = FETCHERS.get(source)
    if read is None or not url:
        raise PostingBodyError("no body fetcher for this posting")
    text = read(fetcher if fetcher is not None else base.Fetcher(), url).strip()
    if not text:
        raise PostingBodyError("the posting body came back empty")
    return text


def resolve(application_id, root=None, fetcher=None, allow_network=True):
    """The best posting text available for one application, or "".

    Never raises for a missing job, an unsupported ATS, or a failed fetch. The
    caller has a session in hand either way and an empty result is a fact about
    the posting, not an error in reading it.
    """
    try:
        jobs = jobs_store.load_jobs(jobs_store.root_dir(root))
    except (jobs_store.JobsStoreError, OSError):
        return ""
    job = jobs.get(application_id)
    if not isinstance(job, dict):
        return ""
    if allow_network:
        try:
            return full_body(job, fetcher)
        except (PostingBodyError, OSError, ValueError):
            pass
    return stored_excerpt(job)
