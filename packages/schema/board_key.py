#!/usr/bin/env python3
"""(board_slug, external_id) for a posting, derived from its URL.

§3 keys `jobs` on `(source, board_slug, external_id)`. That is the connector
upsert target: a Phase 3 connector fetching `boards.greenhouse.io/<slug>` knows
all three by construction. This module exists for the two callers that only
have a URL, which are the Phase 1 backfill and the dual-write shim over the
current ingest path.

**It parses only shapes where the slug is unambiguous, and otherwise falls
back.** A wrong slug is worse than no slug: `board_slug` names an employer's
ATS tenant, so deriving `stripe` from one URL and `stripe-inc` from another
splits one board in two and makes the upsert insert duplicates forever, while
deriving the wrong employer's slug files a posting under a stranger. The same
argument `company_logos` makes about guessing a domain, with a worse failure at
the end of it: a wrong logo costs a monogram, a wrong key costs the identity of
the row.

The fallback is `(source, record_id)`, and for a feed that is not a compromise
but the truth. An Adzuna posting has no ATS tenant, because it did not come
from a board; Adzuna is the board it came from, and the record id is the
identity this repository already derived for it. Stating the convention is the
point, so that nobody later reads `board_slug = 'adzuna'` as a parse failure.

Measured over the live pool of 20,103 records on 2026-08-29. A real employer
slug is found for 14,127 (70.3%) of all records, and for 14,127 of the 17,832
(79.2%) whose source has a pattern at all. The rest fall back, and every group
of them is a case where no tenant exists in the URL to find:

  aggregators and feeds   careerjet, jooble, adzuna, reed, usajobs, remoteok,
                          weworkremotely, workingnomads. No ATS tenant exists.
  greenhouse on an        stripe.com/jobs/search?gh_jid=8113100 carries the
  employer's own domain   job id and not the board slug, 3,528 records
  workable /j/<id>        the short link has no slug in it at all, 144 records
  recruitee, teamtailor   custom careers domains, 29 records between them
  on custom domains

The one thing this must never do is turn the middle case into a guess. `stripe`
is a plausible slug for stripe.com and plausible is exactly the problem: the
real one could be `stripe`, `stripeinc` or anything else, and being wrong
splits one board into two that never reconcile.
"""

import re

# Each entry is (compiled pattern, slug group, id group). The patterns are
# anchored on the vendor's own hostname on purpose: matching a path shape alone
# would claim any URL that happened to look similar.
_PATTERNS = {
    "greenhouse": [
        # boards.greenhouse.io/<slug>/jobs/<id> and the job-boards. variant.
        # NOT the ?gh_jid= form on an employer's own domain: that carries the
        # id and not the slug, and the slug is not guessable from the host.
        # `job-boards.eu.greenhouse.io` is the same product in another
        # region, and 323 records use it. A pattern without the optional
        # region label reads as a parse failure for all of them.
        (re.compile(r"^(?:https?://)?(?:www\.)?(?:job-)?boards\."
                    r"(?:eu\.)?greenhouse\.io/"
                    r"(?:embed/job_app\?for=)?([^/?#]+)/jobs/(\d+)", re.I), 1, 2),
    ],
    "ashby": [
        (re.compile(r"^(?:https?://)?jobs\.ashbyhq\.com/([^/?#]+)/([^/?#]+)",
                    re.I), 1, 2),
    ],
    "lever": [
        (re.compile(r"^(?:https?://)?jobs\.lever\.co/([^/?#]+)/([^/?#]+)",
                    re.I), 1, 2),
    ],
    "smartrecruiters": [
        (re.compile(r"^(?:https?://)?(?:jobs|careers)\.smartrecruiters\.com/"
                    r"([^/?#]+)/(\d+)", re.I), 1, 2),
    ],
    "rippling": [
        (re.compile(r"^(?:https?://)?ats\.rippling\.com/([^/?#]+)/jobs/([^/?#]+)",
                    re.I), 1, 2),
    ],
    "pinpoint": [
        (re.compile(r"^(?:https?://)?([^./?#]+)\.pinpointhq\.com/[^?#]*?"
                    r"postings/([^/?#]+)", re.I), 1, 2),
    ],
    "workday": [
        # <tenant>.wd<N>.myworkdayjobs.com/<site>/job/<place>/<title>_<req>.
        # The requisition is the last underscore-separated segment carrying a
        # digit, and its shape varies: R0064682, R-427374, JR10. Matching a
        # fixed shape missed 67 records; taking the last such segment does not
        # have to know the shape at all.
        (re.compile(r"^(?:https?://)?([^./?#]+)\.wd\d+\.myworkdayjobs\.com/"
                    r"[^?#]*_([^/?#_]*\d[^/?#_]*)(?:[/?#]|$)", re.I), 1, 2),
    ],
    "oracle_orc": [
        # `<tenant>.fa.oraclecloud.com` and `<tenant>.fa.<region>.oraclecloud
        # .com` are both in the pool; the region label accounts for 1,030 of
        # the 4,149 oracle records.
        (re.compile(r"^(?:https?://)?([^./?#]+)\.fa\.(?:[a-z0-9-]+\.)?"
                    r"oraclecloud\.com/[^?#]*?/job/(\d+)", re.I), 1, 2),
    ],
    "teamtailor": [
        (re.compile(r"^(?:https?://)?([^./?#]+)\.teamtailor\.com/jobs/(\d+)",
                    re.I), 1, 2),
    ],
    "personio": [
        (re.compile(r"^(?:https?://)?([^./?#]+)\.jobs\.personio\.[a-z.]+/job/(\d+)",
                    re.I), 1, 2),
    ],
    "jazzhr": [
        (re.compile(r"^(?:https?://)?([^./?#]+)\.applytojob\.com/apply/jobs/"
                    r"details/([^/?#]+)", re.I), 1, 2),
    ],
    "breezy": [
        (re.compile(r"^(?:https?://)?([^./?#]+)\.breezy\.hr/p/([0-9a-f]+)", re.I),
         1, 2),
    ],
    "recruitee": [
        (re.compile(r"^(?:https?://)?([^./?#]+)\.recruitee\.com/o/([^/?#]+)",
                    re.I), 1, 2),
    ],
    "workable": [
        # apply.workable.com/<slug>/j/<id>. The bare /j/<id> form carries no
        # slug and falls through.
        (re.compile(r"^(?:https?://)?apply\.workable\.com/([^/?#]+)/j/([^/?#]+)",
                    re.I), 1, 2),
    ],
    "successfactors": [
        # jobs.<employer>.com/job/<title-and-place>/<id>. The employer is the
        # registrable label rather than a path segment, which is safe here
        # only because the host is the employer's own careers domain and the
        # id is the last path segment.
        (re.compile(r"^(?:https?://)?(?:jobs|careers|career)\.([^./?#]+)\."
                    r"[a-z.]+/job/[^?#]*/(\d+)(?:[/?#]|$)", re.I), 1, 2),
    ],
    "himalayas": [
        (re.compile(r"^(?:https?://)?himalayas\.app/companies/([^/?#]+)/jobs/"
                    r"([^/?#]+)", re.I), 1, 2),
    ],
}

# Hostname labels that are never an employer. Belt and braces: the patterns
# above already anchor on the vendor host, so a match cannot produce one of
# these, and a future pattern written less carefully would.
_NEVER_A_SLUG = {
    "www", "jobs", "job", "boards", "job-boards", "apply", "ats", "careers",
    "career", "api", "app", "web", "en", "us",
}


def parse(source, url):
    """`(board_slug, external_id)` if the URL names a tenant, else None.

    Separate from `derive` so "the URL carried a slug" and "the slug happens to
    equal the source" are different questions. They are not the same: Rippling
    posts its own jobs at `ats.rippling.com/rippling/jobs/...`, so its slug is
    literally `rippling`. A first version of `is_fallback` compared the derived
    slug against the source name and reported all 374 of those as parse
    failures, which put the measured coverage at 60.2% when it was 62.0%. The
    lesson is the ordinary one: a measurement that reads plausibly is still a
    measurement of the wrong thing.
    """
    source = (source or "").strip().lower()
    text = (url or "").strip()
    for pattern, slug_group, id_group in _PATTERNS.get(source, ()):
        found = pattern.search(text)
        if not found:
            continue
        slug = (found.group(slug_group) or "").strip().lower()
        external = (found.group(id_group) or "").strip()
        if slug and external and slug not in _NEVER_A_SLUG:
            return slug, external
    return None


#: Where a connector puts the key it already knows, on the record itself.
#: One field holding both halves rather than two, because half a key is not a
#: key and two fields make "one of them is missing" a state somebody has to
#: handle.
RECORD_FIELD = "boardKey"


def stored(record):
    """The key a connector emitted for this record, or None.

    **Both halves, both strings, both non-empty, or nothing.** A partial or
    malformed value falls through to the URL parse rather than being repaired,
    for the reason this module already gives about guessing a slug: a wrong key
    costs the identity of the row, and a key assembled from one real half and
    one invented one is wrong in the way that looks right.
    """
    value = (record or {}).get(RECORD_FIELD)
    if not isinstance(value, dict):
        return None
    slug = value.get("slug")
    external = value.get("externalId")
    if not isinstance(slug, str) or not isinstance(external, str):
        return None
    slug = slug.strip().lower()
    external = external.strip()
    if not slug or not external or slug in _NEVER_A_SLUG:
        return None
    return slug, external


def for_record(record):
    """`(board_slug, external_id)` for one pool record, preferring what it knows.

    **A connector knows its own key and a URL parser is guessing at it.** The
    §3 contract is that a connector fetching `boards.greenhouse.io/<slug>`
    emits all three fields by construction; this module exists for the callers
    that only have a URL, and it stops being the authority the moment a record
    carries the real thing. Measured over the live pool on 2026-08-29, the URL
    parse finds a real tenant for 70.3% of records and falls back to
    `(source, record_id)` for the rest -- and a large part of that rest is not
    a parse failure at all but an aggregator, which knows its own posting id
    perfectly well and was never asked.

    The stored value is checked rather than trusted (`stored`), because it
    arrives from a connector and a malformed one must degrade to the old
    behaviour rather than write a corrupt key.
    """
    found = stored(record)
    if found:
        return found
    return derive(_text(record.get("canonicalSource")),
                  _text(record.get("canonicalUrl")),
                  _text(record.get("id")))


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def derive(source, url, record_id):
    """Return `(board_slug, external_id)` for one posting, always.

    `record_id` is the identity the store already holds, used as the external
    id when the URL does not carry one. It is required: falling back to an
    empty id would collide every unparsed record onto a single key, and the
    upsert would fold them into one row.
    """
    record_id = (record_id or "").strip()
    if not record_id:
        raise ValueError(
            "a record id is required: without one every unparsed posting "
            "shares a key and the upsert folds them into one row")
    found = parse(source, url)
    if found:
        return found
    return (source or "").strip().lower() or "unknown", record_id


def is_fallback(source, url, record_id=None):
    """Whether the URL named no tenant, so `derive` used the record id."""
    return parse(source, url) is None


if __name__ == "__main__":                               # pragma: no cover
    import json
    import sys
    from pathlib import Path

    pool = Path("~/.job-application/jobs.json").expanduser()
    jobs = json.loads(pool.read_text())
    counts = {}
    for job in jobs.values():
        src = job["canonicalSource"]
        hit = parse(src, job["canonicalUrl"]) is not None
        got, total = counts.get(src, (0, 0))
        counts[src] = (got + (1 if hit else 0), total + 1)
    real = sum(g for g, _ in counts.values())
    print(f"{real}/{len(jobs)} ({real/len(jobs):.1%}) yield a real tenant slug")
    for src, (got, total) in sorted(counts.items(), key=lambda kv: -kv[1][1]):
        print(f"  {src:18} {got:>6}/{total:<6} {got/total:>6.1%}")
    sys.exit(0)
