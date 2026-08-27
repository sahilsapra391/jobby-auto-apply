#!/usr/bin/env python3
"""Cut the published board state down to what the board actually draws.

The state used to carry each discovery record whole, on the reasoning that
filter and scoring logic should stay identical between a local render and a
deployed one. That reasoning is right and this keeps it: everything the deployed
renderer reads is still here. What is gone is everything it does not read.

Why it mattered. On 2026-08-27 the state reached 21.03 MB against the 20 MB
ceiling `publish.guard_state_size` enforces, the guard refused, and it refused
on every publish for about 22 hours. The board went on serving a real snapshot
from the evening before, so nothing looked wrong: healthy ticks, a full board,
a day out of date, and every Apply click in that window invisible on it.

Measured over the real slice, 4,366 records at 20.66 MB, about 4.8 KB each
against the 857 bytes the page embed gets to after its own hoisting:

    sections              9.39 MB   45.4%   read only by /api/posting
    descriptionExcerpt    4.92 MB   23.8%   320 of its 1,500 characters render
    score                 2.07 MB   10.0%   gate evidence is never drawn
    sources               0.86 MB    4.2%   three of its keys are read
    extracted             0.49 MB    2.4%   three of its keys are read
    thirteen more         1.00 MB    4.8%   nothing reads them at all

Slimming takes the jobs map to 13.31 MB and the whole state to 13.52 MB.

This is a size fix, not a ceiling fix. KV refuses a value over about 25 MiB, so
no amount of trimming survives a pool that keeps growing; the state belongs in
R2 behind a pointer, and that is the next step. This buys the room to do it
without the board being frozen while it happens.

The rule for changing anything here: a field comes out only when nothing on the
render path reads it, and `tests/test_state_slim.py` proves it by rendering the
real store both ways and comparing the HTML byte for byte.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import found_lane  # noqa: E402

# What /api/posting emits: twelve lines a side, four hundred characters a line.
# Trimming to the route's own limits cannot change a single response, because
# the route applies exactly this before answering.
SECTION_KEYS = ("responsibilities", "requirements")
SECTION_MAX_LINES = 12
SECTION_MAX_CHARS = 400

# found_lane.place needs all three: a city with no country stays a city,
# because inferring the country from a city name is how a London, Ontario role
# gets filtered as a British one.
LOCATION_FIELDS = ("country", "city", "raw")

# url and source draw the sighting chips; sourceClass is what direct_rank
# minimises over to decide how close a record is to the employer's own form.
SOURCE_FIELDS = ("url", "source", "sourceClass")

# The years figures the card shows. degree, employmentType and roleCategory
# measured 0 of 10,990 records populated on 2026-08-25 and render "not stated".
EXTRACTED_FIELDS = ("yearsMin", "yearsMax", "yearsText")

# Gates keep their label, which becomes a chip. Their evidence is a sentence of
# provenance that no card has ever drawn.
SCORE_FIELDS = ("total", "parts", "why", "unknowns")

# Read by the embed projection, by select(), by place(), by logo_plan, or by
# publishable_jobs. Anything not named here does not reach a pixel.
JOB_FIELDS = (
    "id", "company", "title", "canonicalUrl", "canonicalSource", "workplace",
    "postedAt", "salary", "tags", "state", "logoUrl", "companyWebsite",
    "keptByDecision",
)


def _sections(value):
    if not isinstance(value, dict):
        return None
    out = {}
    for key in SECTION_KEYS:
        lines = value.get(key)
        if not isinstance(lines, list):
            continue
        kept = [str(line or "")[:SECTION_MAX_CHARS]
                for line in lines[:SECTION_MAX_LINES]]
        kept = [line for line in kept if line]
        if kept:
            out[key] = kept
    return out or None


def _subset(value, fields):
    if not isinstance(value, dict):
        return None
    out = {field: value[field] for field in fields
           if value.get(field) not in (None, "", [], {})}
    return out or None


def _score(value):
    if not isinstance(value, dict):
        return None
    out = {field: value[field] for field in SCORE_FIELDS if value.get(field) is not None}
    labels = [{"label": gate.get("label")} for gate in (value.get("gates") or [])
              if isinstance(gate, dict) and gate.get("label")]
    if labels:
        out["gates"] = labels
    return out or None


def _sources(value):
    if not isinstance(value, list):
        return None
    out = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        kept = {field: entry[field] for field in SOURCE_FIELDS
                if entry.get(field) not in (None, "")}
        if kept:
            out.append(kept)
    return out or None


def slim_job(job):
    """One discovery record, reduced to the fields the board reads."""
    if not isinstance(job, dict):
        return job
    out = {field: job[field] for field in JOB_FIELDS
           if job.get(field) not in (None, "", [], {})}

    # The same transform the embed applies, one step earlier, which is what
    # turns a 1,500-character excerpt into the 320 that render. It has to be the
    # embed's own function: the transform must be a fixed point, or the embed
    # re-running it over a slimmed state produces a different string than it
    # would from a whole one.
    excerpt = found_lane.embed_excerpt(job.get("descriptionExcerpt"))
    if excerpt:
        out["descriptionExcerpt"] = excerpt

    for key, value in (("location", _subset(job.get("location"), LOCATION_FIELDS)),
                       ("extracted", _subset(job.get("extracted"), EXTRACTED_FIELDS)),
                       ("score", _score(job.get("score"))),
                       ("sources", _sources(job.get("sources"))),
                       ("sections", _sections(job.get("sections")))):
        if value:
            out[key] = value
    return out


def slim_jobs(jobs):
    """The jobs map, slimmed, in its original iteration order.

    Order is load-bearing and not incidental. dashboard.build reads company
    marks by walking jobs and taking the first record it sees for each
    employer, so reordering here silently moves a logo: it once moved Datadog's
    from careers.datadoghq.com to learn.datadoghq.com.
    """
    if not isinstance(jobs, dict):
        return jobs
    return {key: slim_job(job) for key, job in jobs.items()}
