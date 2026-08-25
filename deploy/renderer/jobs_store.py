#!/usr/bin/env python3
"""The pool of discovered jobs, and the tombstones for the ones passed on.

Sourcing writes here; the board's Found lane reads from here. This is separate
from store.py because the shapes have nothing in common: a session is one role
being worked on, a job record is one posting seen across several boards. What
they do share is the write discipline, so the atomic writer is imported rather
than copied.

Three rules this file exists to enforce.

A passed job is never offered again. The tombstone in passed.json outlives the
job record itself, so no amount of pruning can resurrect something the user has
already said no to.

A read never turns a failure into an empty pool. Reading the store as
`json.loads(stdout or "[]")` once turned any failure into a convincing empty
result; a corrupt jobs.json read as "nothing found yet" would silently wipe the
Found lane and look like a quiet day.

Only `new` jobs are pruned. Watched, applied and passed records are decisions
the user made, and a cap is not allowed to eat those.

  python3 scripts/jobs_store.py stats [--store DIR]
  python3 scripts/jobs_store.py list --state new --limit 20
  python3 scripts/jobs_store.py pass --id <jobId> [--reason "..."]
  python3 scripts/jobs_store.py prune
"""

import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runlock
import store  # noqa: E402  (write_private, read_json: same atomicity, one copy)

# Volume knobs, confirmed with the user before build. Stored pool first: the
# aggregator APIs raise volume by an order of magnitude over agent sweeps, and
# an unbounded jobs.json eventually makes every ingest read slower than the
# fetch that fed it.
#
# Raised from 3000 on 2026-08-25, on Sahil's call, after the store was found
# pinned exactly at the old cap: 3000 held, 316 companies represented against
# 546 known. A ring buffer at its ceiling is not a pool, it is a window, and
# every sweep was evicting to make room. The ingest cost is real and was
# accepted: jobs.json is read and rewritten whole, so at 2.8 KB a record this
# is a ~140 MB file at the ceiling rather than ~8 MB.
#
# The board does not carry this. dashboard.publishable_jobs publishes the
# EMBED_CAP slice plus the true totals, because KV tops out long before this
# number does.
STORED_CAP = 50000
# A `new` job nobody has acted on is stale well before it is dead, and the
# posting behind it usually is too.
NEW_MAX_AGE_DAYS = 30

# new      found and offered, nothing decided
# watched  the user pressed the eye; a session exists and Watching owns it now
# passed   the user said no; tombstoned, never offered again
# applied  matched against submitted history or an in-flight session
# expired  no longer live, or aged out while still unacted-on
STATES = ("new", "watched", "passed", "applied", "expired")

# Decisions are one-way. Passing and applying are the user acting, and an
# ingest re-seeing the posting must not undo either: that is exactly how a
# dropped role used to reappear on the next sweep.
TRANSITIONS = {
    "new": {"watched", "passed", "applied", "expired"},
    "watched": {"passed", "applied", "expired"},
    "expired": {"new", "watched", "passed", "applied"},
    "passed": set(),
    "applied": set(),
}

# Which sighting gets to name the job. The company's own ATS is the posting of
# record: it is current, it is what the application will be filed through, and
# an aggregator's copy of it is a summary written by someone else.
CANONICAL_RANK = {"ats": 0, "careers": 1, "aggregator": 2, "feed": 2, "other": 3}

# These connectors formerly wrote a non-publication field into postedAt. A
# one-time production repair clears records for which that connector is still
# canonical, then a fresh connector pass restores genuine publication dates.
UNTRUSTED_LEGACY_DATE_SOURCES = frozenset(
    ("greenhouse", "workable", "recruitee", "jooble"))


class JobsStoreError(Exception):
    pass


def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value):
    """A stored timestamp as a datetime, or None. Never raises on junk."""
    if not value:
        return None
    text = str(value).strip()
    # Offsets first, and without truncating. The old path sliced to twenty
    # characters and tried "...Z"; an offset stamp failed that, fell through to
    # the ten-character date form, and was read as midnight UTC — silently up
    # to a day out. Latent while every connector emitted Z, and a trap for the
    # next one that does not.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt, width in (("%Y-%m-%dT%H:%M:%SZ", 20), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:width], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def root_dir(override=None):
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()


def paths(root):
    root = Path(root)
    return {
        "jobs": root / "jobs.json",
        "passed": root / "passed.json",
        "companies": root / "companies.json",
    }


def _read(path, default):
    """Read a store file, or fail loudly.

    store.read_json raises on invalid JSON rather than returning the default,
    which is the behaviour that matters: an unreadable pool must never look
    like an empty one.
    """
    try:
        data = store.read_json(path, default)
    except store.StoreError as exc:
        raise JobsStoreError(str(exc))
    if not isinstance(data, type(default)):
        raise JobsStoreError(f"{Path(path).name} is not a JSON {type(default).__name__}")
    return data


# --- jobs -------------------------------------------------------------------

def load_jobs(root):
    return _read(paths(root)["jobs"], {})


# Read-modify-write on the whole pool is the shape that has now lost work
# twice. The lock is the fix, but a lock that each new script has to remember
# to take is a lock the next script will not take: on 2026-08-25 a sections
# backfill, written by somebody who had never heard of the sweep, discarded 222
# closures and 113 new jobs. So the lock lives here, on the write itself, and
# `edit` below is the only shape in which a caller can read-modify-write.
POOL_LOCK = "scan"


def save_jobs(root, jobs, note="save_jobs"):
    """Write the pool, under the lock, always.

    `runlock.held` is reentrant for the process that already owns the lock, so
    a sweep holding it for eight minutes writes through here without
    self-deadlocking, and everything else is excluded while it does.
    """
    with runlock.held(root, POOL_LOCK, note=note):
        store.write_private(paths(root)["jobs"], jobs)
    return jobs


@contextlib.contextmanager
def edit(root, note="edit"):
    """The pool, held open for a read-modify-write.

        with jobs_store.edit(root, note="mark_watched") as (jobs, edit):
            jobs[job_id]["state"] = "watched"

    `edit["write"] = False` skips the write for a call that turned out to
    change nothing. The pool is 36MB, and the eye being pressed twice should
    not rewrite it.

    Locking only the write would still leave the read stale: load, wait while a
    sweep finishes, then save a pool built before it started. The whole span
    has to be inside the lock, which is what this is for. Raises
    `runlock.LockBusy` if somebody else is mid-write; callers refuse rather
    than queue, because a queued writer is holding a copy that is already old.
    """
    with runlock.held(root, POOL_LOCK, note=note):
        jobs = load_jobs(root)
        state = {"write": True}
        yield jobs, state
        if state["write"]:
            store.write_private(paths(root)["jobs"], jobs)


def clear_untrusted_legacy_dates(jobs):
    """Clear dates written by former edit, draft, or refresh fallbacks.

    The canonical-source guard preserves a valid ATS date on a record that also
    has an old Jooble sighting. The caller owns persistence so dry runs and the
    atomic store write stay explicit.
    """
    changed = []
    for job_id, job in jobs.items():
        if (job.get("postedAt") and
                job.get("canonicalSource") in UNTRUSTED_LEGACY_DATE_SOURCES):
            job["postedAt"] = None
            changed.append(job_id)
    return changed


def refresh_posted_date(existing, incoming):
    """Copy genuine source date evidence onto an already stored identity.

    This is intentionally narrower than an upsert. Search gating decides which
    new jobs enter Found, but it must not stop a known record from learning its
    publication date on a later source sighting.
    """
    incoming_at = parse_stamp(incoming.get("postedAt"))
    if incoming_at is None:
        return False
    held_at = parse_stamp(existing.get("postedAt"))
    # Compared as instants, not as strings. "2026-08-19T00:00:00Z" and
    # "2026-08-19" are the same day, but the string compare put the bare date
    # first, so every run rewrote the record to the less precise form and
    # counted it as a date refresh. Offsets were worse: an IST stamp sorts
    # after a UTC one that is genuinely later.
    if held_at is not None and held_at <= incoming_at:
        return False
    existing["postedAt"] = incoming["postedAt"]
    return True


def load_passed(root):
    return _read(paths(root)["passed"], {})


def save_passed(root, tombstones):
    store.write_private(paths(root)["passed"], tombstones)
    return tombstones


def load_companies(root):
    return _read(paths(root)["companies"], {})


def save_companies(root, companies):
    store.write_private(paths(root)["companies"], companies)
    return companies


def blank_job(job_id, company, title, **fields):
    """A job record with every §3.1 field present.

    Absent is different from unknown, and the board draws an explicit `unknown`
    chip. A record that simply lacks the key cannot be told apart from one the
    renderer forgot, so every field exists from the moment the record does.
    """
    stamp = fields.pop("firstSeenAt", None) or now_stamp()
    job = {
        "id": job_id,
        "company": company,
        "title": title,
        "normTitle": fields.pop("normTitle", "") or norm_title(title),
        "canonicalUrl": "",
        "canonicalSource": "",
        "logoUrl": None,
        "companyWebsite": None,
        "sources": [],
        "location": {"raw": "", "city": None, "region": None, "country": None},
        "workplace": None,
        "postedAt": None,
        "firstSeenAt": stamp,
        "lastSeenAt": stamp,
        "seenCount": 0,
        "lang": None,
        "descriptionExcerpt": "",
        "descFingerprint": None,
        # What the posting listed under its own headings, for the expanded card.
        "sections": {"responsibilities": [], "requirements": []},
        "tags": [],
        "salary": None,
        "extracted": {"yearsMax": None, "degree": None,
                      "employmentType": None, "roleCategory": None},
        "industry": None,
        "score": None,
        "configVersion": None,
        "state": "new",
    }
    job.update(fields)
    return job


def norm_title(title):
    """Lowercase alphanumerics, for the title bucket in identity matching."""
    out = "".join(c.lower() if c.isalnum() else " " for c in str(title or ""))
    return " ".join(out.split())


def canonical_rank(source_class):
    return CANONICAL_RANK.get(str(source_class or "other"), CANONICAL_RANK["other"])


def _earliest(a, b):
    """The earlier of two stamps, tolerating either being absent or junk."""
    da, db = parse_stamp(a), parse_stamp(b)
    if da and db:
        return a if da <= db else b
    return a or b


def _latest(a, b):
    da, db = parse_stamp(a), parse_stamp(b)
    if da and db:
        return a if da >= db else b
    return a or b


def merge_sources(existing, incoming):
    """Union of sightings, keyed by (source, url), keeping the earliest sighting.

    Re-seeing the same posting on the same board must not grow the list, or the
    "seen on N boards" chip inflates every hour and stops meaning anything.

    Some channels make that harder than it sounds. Careerjet answers with a
    per-request tracking redirect — `jobviewtrack.com/v2/<opaque>` — and the
    same posting gets a different token every time it is asked for, verified by
    running one query twice a second apart and getting three different URLs for
    three unchanged jobs. Keyed on the URL, that appends a fresh sighting every
    hour, and by tomorrow the job claims to have been seen on twenty-four
    boards.

    So a connector may declare its URLs unstable, and those collapse to one
    sighting per source: newest URL, because that is the one that still
    resolves, and earliest firstSeenAt, because that is when the job was
    actually first seen.
    """
    merged = {}
    for entry in list(existing or []) + list(incoming or []):
        if not isinstance(entry, dict):
            continue
        key = ((entry.get("source"),) if entry.get("urlStable") is False
               else (entry.get("source"), entry.get("url")))
        if key in merged:
            merged[key] = dict(entry,
                               firstSeenAt=_earliest(merged[key].get("firstSeenAt"),
                                                     entry.get("firstSeenAt")))
        else:
            merged[key] = dict(entry)
    return sorted(merged.values(),
                  key=lambda e: (canonical_rank(e.get("sourceClass")),
                                 str(e.get("firstSeenAt") or ""), str(e.get("source") or "")))


def merge_records(existing, incoming):
    """Fold a new sighting into the record already held.

    The user's decision (`state`) and the record's identity (`id`) are never
    touched. Everything descriptive prefers whichever sighting outranks the
    other, because the ATS copy of a posting is the posting and the aggregator's
    copy is a summary of it.
    """
    merged = dict(existing)
    merged["sources"] = merge_sources(existing.get("sources"), incoming.get("sources"))

    best = merged["sources"][0] if merged["sources"] else {}
    merged["canonicalUrl"] = best.get("url") or existing.get("canonicalUrl") or ""
    merged["canonicalSource"] = best.get("source") or existing.get("canonicalSource") or ""

    incoming_wins = (canonical_rank(_class_of(incoming))
                     < canonical_rank(_class_of(existing)))
    for field in ("company", "title", "workplace", "lang", "salary", "industry",
                  "logoUrl", "companyWebsite"):
        new = incoming.get(field)
        if new not in (None, "", []) and (incoming_wins or merged.get(field) in (None, "", [])):
            merged[field] = new
    if merged.get("title"):
        merged["normTitle"] = norm_title(merged["title"])

    loc = dict(existing.get("location") or {})
    for key, value in (incoming.get("location") or {}).items():
        if value not in (None, "") and (incoming_wins or loc.get(key) in (None, "")):
            loc[key] = value
    merged["location"] = loc

    # The longest body seen is the most complete one; an aggregator excerpt
    # truncated at 200 characters must not overwrite the full ATS description.
    if len(incoming.get("descriptionExcerpt") or "") > len(merged.get("descriptionExcerpt") or ""):
        merged["descriptionExcerpt"] = incoming["descriptionExcerpt"]
    # The fuller pair wins, the same way the longer excerpt does: one board may
    # carry the headings this can read while another posts the same job as prose.
    def _weight(value):
        value = value or {}
        return len(value.get("responsibilities") or []) + len(value.get("requirements") or [])
    if _weight(incoming.get("sections")) > _weight(merged.get("sections")):
        merged["sections"] = incoming["sections"]
        merged["descFingerprint"] = incoming.get("descFingerprint")
    elif not merged.get("descFingerprint"):
        merged["descFingerprint"] = incoming.get("descFingerprint")

    merged["tags"] = sorted({*(existing.get("tags") or []), *(incoming.get("tags") or [])})
    extracted = dict(existing.get("extracted") or {})
    for key, value in (incoming.get("extracted") or {}).items():
        if value is not None and extracted.get(key) is None:
            extracted[key] = value
    merged["extracted"] = extracted

    # Earliest posted date wins: a board that lists a syndicated copy the day it
    # picked it up would otherwise make a three-week-old posting look fresh.
    merged["postedAt"] = _earliest(existing.get("postedAt"), incoming.get("postedAt"))
    merged["firstSeenAt"] = _earliest(existing.get("firstSeenAt"), incoming.get("firstSeenAt"))
    merged["lastSeenAt"] = _latest(existing.get("lastSeenAt"), incoming.get("lastSeenAt"))
    merged["seenCount"] = int(existing.get("seenCount") or 0) + max(
        1, int(incoming.get("seenCount") or 1))
    return merged


def _class_of(job):
    sources = job.get("sources") or []
    if not sources:
        return "other"
    return min((s.get("sourceClass") for s in sources if isinstance(s, dict)),
               key=canonical_rank, default="other")


def upsert(jobs, incoming, match_id=None, matched=None):
    """Add a job, or fold it into the record it matched. Returns (jobs, action).

    `match_id` comes from identity resolution; without one the record's own id
    is the only match, which is the degenerate "same canonical URL twice" case.

    `matched` says whether the matcher actually agreed these are the same job.
    When it is explicitly False and the id is already taken, that is a
    contract violation: the matcher looked at this job, declined to merge it,
    and something handed it an occupied id anyway. identity.resolve() makes
    that unreachable, so this refuses loudly rather than folding two
    requisitions together where nobody would ever see it happen.
    """
    job_id = match_id or incoming.get("id")
    if not job_id:
        raise JobsStoreError("a job needs an id before it can be stored")
    if matched is False and job_id in jobs:
        raise JobsStoreError(
            f"refusing to merge {job_id}: the matcher declined to match this job, "
            "so folding it onto an existing record would lose one of them")
    if job_id in jobs:
        jobs[job_id] = merge_records(jobs[job_id], incoming)
        return jobs, "merged"
    record = dict(incoming)
    record["id"] = job_id
    record.setdefault("state", "new")
    record["seenCount"] = max(1, int(record.get("seenCount") or 1))
    record["sources"] = merge_sources([], record.get("sources"))
    if record["sources"]:
        record.setdefault("canonicalUrl", record["sources"][0].get("url") or "")
        record.setdefault("canonicalSource", record["sources"][0].get("source") or "")
    jobs[job_id] = record
    return jobs, "created"


def transition(job, new_state):
    """Move a job to a new state, or refuse and say why."""
    if new_state not in STATES:
        raise JobsStoreError(f"state must be one of {list(STATES)}")
    current = job.get("state") or "new"
    if current == new_state:
        return job
    if new_state not in TRANSITIONS.get(current, set()):
        raise JobsStoreError(
            f"cannot move a job from {current} to {new_state}; "
            f"{current} is the user's decision and ingest does not overrule it")
    job["state"] = new_state
    return job


def tombstone(job, reason=""):
    """The durable record of a pass: enough to recognise the job again."""
    return {
        "id": job.get("id"),
        "company": job.get("company"),
        "title": job.get("title"),
        "normTitle": job.get("normTitle") or norm_title(job.get("title")),
        "canonicalUrl": job.get("canonicalUrl"),
        "descFingerprint": job.get("descFingerprint"),
        "passedAt": now_stamp(),
        "reason": reason or "",
    }


def mark_passed(root, job_id, reason=""):
    """Pass on a job: state, then tombstone. Idempotent per job id."""
    with edit(root, note="mark_passed") as (jobs, state):
        passed = load_passed(root)
        job = jobs.get(job_id)
        if job is None:
            if job_id in passed:
                state["write"] = False
                return {"passed": job_id, "alreadyPassed": True}
            state["write"] = False
            raise JobsStoreError(f"no job with id {job_id}")
        if job.get("state") != "passed":
            transition(job, "passed")
        passed.setdefault(job_id, tombstone(job, reason))
        save_passed(root, passed)
        return {"passed": job_id, "company": job.get("company"),
                "title": job.get("title")}


def mark_watched(root, job_id):
    """Promote a job to Watching. Idempotent: the eye may be pressed twice."""
    with edit(root, note="mark_watched") as (jobs, state):
        job = jobs.get(job_id)
        if job is None:
            state["write"] = False
            raise JobsStoreError(f"no job with id {job_id}")
        if job.get("state") == "watched":
            state["write"] = False
            return {"watched": job_id, "alreadyWatched": True, "job": job}
        transition(job, "watched")
        return {"watched": job_id, "job": job}


def keep_priority(job):
    """Sort key for what survives the cap: direct listings, then score, then age.

    Reversed, this is the drop order the plan asks for — oldest and
    lowest-scoring first — with one addition the plan does not state and a real
    run made unarguable.

    Retention used to sort on score alone. A full sweep brought in 1,482
    postings from company ATS boards and 5,300 aggregator copies; the cap kept
    3,000 and only 214 of the survivors were direct. Aggregator snippets are
    short and keyword-dense, so they score at least as well as a real job
    description, and they crowded out exactly the records worth keeping: the
    ones that link to the employer's own application form.

    The plan already ranks ATS above careers page above aggregator when
    deciding which sighting is canonical. This is the same ordering applied to
    the question of what to throw away, which is the same question wearing a
    different hat.
    """
    score = (job.get("score") or {}).get("total")
    score = score if isinstance(score, (int, float)) else -1
    classes = [s.get("sourceClass") for s in (job.get("sources") or [])
               if isinstance(s, dict)]
    best = min(classes, key=canonical_rank, default="other")
    direct = 1 if canonical_rank(best) <= 1 else 0
    # Discovery time is internal provenance, not job age. An undated record
    # must never outrank a dated one merely because Jobby saw it recently.
    when = job.get("postedAt") or ""
    return (direct, score, str(when))


def refresh_locations(jobs):
    """Re-read the country from each stored record's own location string.

    A parser improvement only reaches new sightings, and conditional requests
    mean an unchanged board is never re-fetched, so its jobs are never re-parsed.
    When `parse_location` learned to read "Chicago" and "Remote - US", 938
    stored records kept the empty country they were saved with and went on
    answering a search for every country.

    Only ever fills a blank. A country already on the record was read from a
    string this function is looking at again, and second-guessing it would let a
    parser regression quietly rewrite the store.

    Cheap enough to run every ingest: a hundred milliseconds over fourteen
    thousand records, and it means the next parser fix needs no migration.
    """
    from sources.base import parse_location

    filled = 0
    for job in jobs.values():
        location = job.get("location") or {}
        if location.get("country"):
            continue
        raw = location.get("raw")
        if not raw:
            continue
        country = parse_location(raw).get("country")
        if country:
            location["country"] = country
            job["location"] = location
            filled += 1
    return jobs, filled


# A board that legitimately empties would look identical to a board that
# failed, so a single employer is never allowed to close more than this share
# of what we hold for it in one sweep. Above it, the sweep says so and changes
# nothing. Chosen high because a real hiring freeze does happen; the point is
# to catch the fetch that returned two jobs instead of two hundred.
MAX_CLOSED_SHARE = 0.7

# How long a closed job stays in the pool as a tombstone. It is not kept to be
# shown; Found is `new` only. It is kept so a board that drops a posting for one
# sweep and relists it the next brings the same record back rather than minting
# a fresh one with today's firstSeenAt. A week is longer than any flicker and
# far shorter than the cap horizon.
CLOSED_TOMBSTONE_DAYS = 7

# Only these listings are complete enough for absence to mean anything. An
# employer's own board is the full set of roles it has open, so a posting
# missing from it is a posting that was taken down. An aggregator page is a
# search result: partial by design, paginated, and ranked. A job missing from
# one has most likely just fallen off the page we asked for.
CLOSEABLE_CLASSES = frozenset(("ats", "careers"))
MIN_BOARD_FOR_SHARE_CHECK = 5


def close_missing(jobs, seen, at=None, norm=None):
    """Expire jobs their own board has stopped listing. Returns (jobs, report).

    `seen` maps (source, normalised company) to the set of job ids that board
    returned this sweep, keyed with the same `norm` passed here. A stored job under a key that WAS fetched, and whose id is not
    in what came back, is closed: the employer took it down.

    Absence from the listing is the signal, not the posting URL's status code.
    Greenhouse answers a closed posting with HTTP 200 and the board index —
    title "Jobs at Vercel" — so a liveness check on the link calls a dead job
    alive. The board listing is the only thing that actually knows.

    Three brakes, because the failure mode here is mass deletion:

    A key absent from `seen` is untouched. A board that 304'd, errored, or was
    not fetched this run contributes nothing, and silence must never read as
    "everything closed".

    `applied` and `passed` are terminal and are never reopened by this. What
    the user did with a job outlives the employer taking it down; it stays in
    Submitted, In flight and Needs you exactly as it was.

    Only jobs whose best sighting is an employer board are eligible at all. An
    aggregator result page is partial by construction and absence from it is
    not evidence of anything.

    And no single board may close more than MAX_CLOSED_SHARE of what we hold
    for it. A truncated fetch looks exactly like a mass closure, and the
    difference matters more than the handful of stale cards it might leave.
    """
    at = at or datetime.now(timezone.utc)
    stamp = at.strftime("%Y-%m-%dT%H:%M:%SZ")
    closed, refused = [], []

    # The company half of the key has to be normalised the same way the sweep
    # normalised it, or nothing matches and this closes nothing forever. The
    # sweep's normaliser carries its alias map and lives in `identity`, which
    # imports this module, so it is passed in rather than imported.
    norm = norm or (lambda name: (name or "").strip().casefold())

    held = {}
    for job_id, job in jobs.items():
        if job.get("state") not in ("new", "watched"):
            continue
        if _class_of(job) not in CLOSEABLE_CLASSES:
            continue
        key = (job.get("canonicalSource"), norm(job.get("company")))
        if key in seen:
            held.setdefault(key, []).append(job_id)

    for key, ids in held.items():
        missing = [job_id for job_id in ids if job_id not in seen[key]]
        if not missing:
            continue
        share = len(missing) / len(ids)
        if len(ids) >= MIN_BOARD_FOR_SHARE_CHECK and share > MAX_CLOSED_SHARE:
            refused.append({"source": key[0], "company": key[1],
                            "held": len(ids), "wouldClose": len(missing),
                            "why": "more than %d%% of one board at once; treating "
                                   "this as a bad fetch rather than a mass closure"
                                   % int(MAX_CLOSED_SHARE * 100)})
            continue
        for job_id in missing:
            job = jobs[job_id]
            was = job.get("state")
            jobs[job_id] = transition(job, "expired")
            jobs[job_id]["closedAt"] = stamp
            closed.append({"id": job_id, "company": job.get("company"),
                           "title": job.get("title"), "wasState": was})

    return jobs, {"closed": closed, "refused": refused}


def reopen(job):
    """A closed job the board is listing again. Returns (job, reopened).

    Only from `expired`, and only ever back to `new`. A job that closed while
    watched had its watch session deleted when it closed, so restoring
    `watched` would leave a record the Watching tab cannot show. Found is where
    a live posting with no session belongs, and the user can watch it again.
    """
    if job.get("state") != "expired":
        return job, False
    job = transition(job, "new")
    job.pop("closedAt", None)
    return job, True


def prune(jobs, cap=STORED_CAP, max_age_days=NEW_MAX_AGE_DAYS, at=None,
          posted_ceiling_days=None):
    """Drop unacted-on jobs that are stale or over the cap. Returns (jobs, dropped).

    Only `new` records are eligible. A watched, applied or passed record is a
    decision the user made and the cap does not get to eat it.

    Two clocks, deliberately. `max_age_days` is how long we keep a job nobody
    has acted on since we last saw it. `posted_ceiling_days` is how old the
    posting itself is allowed to be. They are not the same question, and
    conflating them is how a February posting first seen yesterday lived
    forever: the gate refused to bring it in, and retention never looked at the
    field the gate had judged it on.

    An undated posting is kept. Most ATS boards publish no date at all, and
    dropping those would drop the direct listings this lane exists to surface.
    """
    at = at or datetime.now(timezone.utc)
    cutoff = at - timedelta(days=max_age_days)
    posted_cutoff = (at - timedelta(days=posted_ceiling_days)
                     if posted_ceiling_days is not None else None)
    dropped = []

    for job_id, job in list(jobs.items()):
        if job.get("state") != "new":
            continue
        seen = parse_stamp(job.get("lastSeenAt")) or parse_stamp(job.get("firstSeenAt"))
        if seen and seen < cutoff:
            del jobs[job_id]
            dropped.append({"id": job_id, "why": f"unacted-on for over {max_age_days} days"})
            continue
        if posted_cutoff is not None:
            posted = parse_stamp(job.get("postedAt"))
            if posted is not None and posted < posted_cutoff:
                del jobs[job_id]
                dropped.append({"id": job_id,
                                "why": f"posted over {posted_ceiling_days:g} days ago"})

    # Closed jobs are reaped on their own clock. They have to be: the cap below
    # measures the whole pool but may only evict `new` records, so tombstones
    # left to accumulate would push live postings out of the board instead.
    closed_cutoff = at - timedelta(days=CLOSED_TOMBSTONE_DAYS)
    for job_id, job in list(jobs.items()):
        if job.get("state") != "expired":
            continue
        closed_at = parse_stamp(job.get("closedAt")) or parse_stamp(job.get("lastSeenAt"))
        if closed_at is None or closed_at < closed_cutoff:
            del jobs[job_id]
            dropped.append({"id": job_id,
                            "why": f"closed over {CLOSED_TOMBSTONE_DAYS} days ago"})

    if cap is not None:
        newish = [(job_id, job) for job_id, job in jobs.items() if job.get("state") == "new"]
        over = len(jobs) - cap
        if over > 0 and newish:
            newish.sort(key=lambda pair: keep_priority(pair[1]))
            for job_id, _ in newish[:over]:
                del jobs[job_id]
                dropped.append({"id": job_id, "why": f"over the {cap}-job cap"})
    return jobs, dropped


def stats(root):
    jobs = load_jobs(root)
    passed = load_passed(root)
    by_state = {state: 0 for state in STATES}
    for job in jobs.values():
        by_state[job.get("state") or "new"] = by_state.get(job.get("state") or "new", 0) + 1
    merged = sum(1 for job in jobs.values() if len(job.get("sources") or []) > 1)
    return {
        "jobs": len(jobs),
        "byState": by_state,
        "tombstones": len(passed),
        "seenOnMoreThanOneBoard": merged,
        "companies": len(load_companies(root)),
        "storedCap": STORED_CAP,
    }


# --- CLI --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="The discovered-jobs pool.")
    ap.add_argument("--store", help="store directory (default ~/.job-application)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("list")
    p.add_argument("--state", choices=STATES)
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("get")
    p.add_argument("--id", required=True)
    p = sub.add_parser("pass")
    p.add_argument("--id", required=True)
    p.add_argument("--reason", default="")
    p = sub.add_parser("watch")
    p.add_argument("--id", required=True)
    sub.add_parser("prune")
    args = ap.parse_args()

    root = root_dir(args.store)
    try:
        if args.command == "stats":
            result = stats(root)
        elif args.command == "list":
            jobs = load_jobs(root)
            rows = [j for j in jobs.values() if not args.state or j.get("state") == args.state]
            rows.sort(key=keep_priority, reverse=True)
            result = [{"id": j["id"], "company": j.get("company"), "title": j.get("title"),
                       "state": j.get("state"),
                       "score": (j.get("score") or {}).get("total"),
                       "boards": len(j.get("sources") or [])}
                      for j in rows[:args.limit]]
        elif args.command == "get":
            job = load_jobs(root).get(args.id)
            if job is None:
                raise JobsStoreError(f"no job with id {args.id}")
            result = job
        elif args.command == "pass":
            result = mark_passed(root, args.id, args.reason)
        elif args.command == "watch":
            result = mark_watched(root, args.id)
        else:
            jobs, dropped = prune(load_jobs(root))
            save_jobs(root, jobs)
            result = {"remaining": len(jobs), "dropped": len(dropped), "why": dropped[:20]}
    except JobsStoreError as exc:
        print(f"jobs store: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
