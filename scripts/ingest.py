#!/usr/bin/env python3
"""One sourcing pass: fetch, resolve, gate, score, store, account for it.

The accounting is not a side effect of this file, it is most of the point. A
scan that found nothing has to look different from a scan that did not run, and
a channel that was never asked has to look different from one that answered
"nothing today". So every run writes `meta.lastIngest` — including the runs that
fail — and the board's Found header renders it.

The order is fixed and each step is cheaper than the one after it:

  fetch      every selected channel, each reporting ok / skipped / error
  gate       metadata only: date window, country, workplace, keywords,
             excluded words, companies. This is where most of a sweep dies,
             on facts that cost nothing to check.
  enrich     fetch the body, but only for what survived. Greenhouse's list is
             fourteen times smaller without descriptions, and a description is
             worthless for a role already rejected on country.
  score      the rubric, plus the duplicate gate for anything already applied
  keep       minScoreToKeep
  resolve    identity: which stored job is this the same job as
  upsert     merge sightings, never overwrite a decision
  prune      caps and staleness
  account    meta.lastIngest, always

  python3 scripts/ingest.py --tier 1 --store /tmp/scratch --limit 50 --dry-run
  python3 scripts/ingest.py --tier 1 --channel greenhouse
"""

import argparse
import contextlib
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity  # noqa: E402
import jobs_store  # noqa: E402
import match_score  # noqa: E402
import runlock  # noqa: E402
import search_config  # noqa: E402
import seed_companies
import source_health
import sweep_runs  # noqa: E402
import sources  # noqa: E402

# The watcher's own lock, on purpose, not one of our own. `watch.spawn` takes
# `scan` for the sweeps it starts, and `watch.lock_holder("scan")` is what the
# rest of the daemon reads. A second lock would be internally consistent and
# would not exclude the daemon's sweep, which is the overlap that loses data.
SWEEP_LOCK = "scan"
import toolpath  # noqa: E402
from sources import base, greenhouse  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"

# Channels whose postings arrive without a body, so a description has to be
# asked for separately.
#
# Empty, and deliberately so. Greenhouse was the only member and it now returns
# bodies with the list: per-job fetching capped a full sweep at 300 bodies and
# left seven of every ten kept jobs with no description. The enrichment pass
# stays because the next connector that needs it will need exactly this.
NEEDS_BODY = set()
# Bodies cost a request each. Even after the metadata gate this is capped, so a
# badly-scoped saved search cannot turn one ingest into four thousand requests.
MAX_BODIES_PER_RUN = 300


class IngestError(Exception):
    pass


def store_call(root, args):
    cmd = [sys.executable, str(STORE)]
    if root:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd + args, capture_output=True, text=True,
                          env=toolpath.enriched_env())


def read_store(root, args, default):
    """Read through store.py, or fail loudly.

    `json.loads(stdout or "[]")` turned every store failure into a convincing
    empty result once (§13 item 8). Here that would mean an ingest that thinks
    nothing has ever been applied to, and offers back roles already submitted.
    """
    result = store_call(root, args)
    if result.returncode != 0:
        raise IngestError(f"store {' '.join(args)} failed: "
                          f"{(result.stderr or '').strip()[:200]}")
    try:
        return json.loads(result.stdout or "null") if result.stdout.strip() else default
    except json.JSONDecodeError:
        raise IngestError(f"store {' '.join(args)} returned junk")


# --- what is already spoken for ---------------------------------------------

def applied_index(root):
    """Everything already decided, so it is never offered as new.

    Three different facts, and they are not the same state:
      applied  submitted, or being worked on right now
      watched  already on the board in Watching
      passed   the user dropped it
    Collapsing these into one would either re-offer a dropped role or claim an
    application that never happened.
    """
    history = read_store(root, ["history-list"], [])
    sessions = read_store(root, ["session-list"], [])
    roles = read_store(root, ["roles-list"], {})

    latest = {}
    for event in sorted(history, key=lambda e: e.get("at") or ""):
        if event.get("applicationId"):
            latest[event["applicationId"]] = event

    index = {}

    def remember(company, title, url, state):
        key = (identity.norm_company(company), jobs_store.norm_title(title))
        if company and title:
            index.setdefault(key, state)
        link = identity.url_key(url)
        if link:
            index.setdefault(("url", link), state)

    for aid, event in latest.items():
        if event.get("event") == "completed":
            role = roles.get(aid) or {}
            remember(event.get("company"), event.get("role"),
                     role.get("url") or event.get("url"), "applied")

    status_state = {"inflight": "applied", "review": "watched", "active": "watched",
                    "watch": "watched", "dropped": "passed"}
    for session in sessions:
        state = status_state.get(session.get("status"))
        if not state:
            continue
        role = roles.get(session.get("applicationId")) or {}
        remember(session.get("company"), session.get("role"), role.get("url"), state)
    return index


def already_decided(index, posting):
    """('applied'|'watched'|'passed', why), or (None, "")."""
    for url in identity.sighting_urls(posting):
        state = index.get(("url", identity.url_key(url)))
        if state:
            return state, "the same posting URL is already on the board"
    key = (identity.norm_company(posting.get("company")),
           posting.get("normTitle") or jobs_store.norm_title(posting.get("title")))
    state = index.get(key)
    if state:
        return state, f"{posting.get('company')} — {posting.get('title')} is already on the board"
    return None, ""


# --- the configuration gate -------------------------------------------------

def _text_for_scope(posting, config):
    """What an *include* keyword is matched against."""
    text = posting.get("title") or ""
    if config.get("searchScope") != "title":
        text = f"{text}\n{posting.get('descriptionExcerpt') or ''}"
    return text


def _text_for_exclusions(posting):
    """What an *exclude* keyword is matched against: everything, always.

    An exclusion is a veto, and a veto should look everywhere. "staffing
    agency" almost never appears in a job title and almost always appears in
    the body, so scoping exclusions to the title would quietly stop rejecting
    exactly the postings the list exists to reject.

    This used to follow `searchScope` along with the include terms. That was
    invisible while the scope defaulted to title-and-description, and became a
    silent widening of what got stored the moment the default moved to titles.
    """
    return f"{posting.get('title') or ''}\n{posting.get('descriptionExcerpt') or ''}"


def gate(posting, config, now=None):
    """('' when the job survives, otherwise the reason it did not).

    Metadata only. Everything here is checkable without fetching a body, which
    is what makes the enrichment pass affordable.
    """
    now = now or datetime.now(timezone.utc)
    unknowns = config.get("includeUnknowns") or {}

    window = search_config.posted_within_days(config)
    if window is not None:
        posted = jobs_store.parse_stamp(posting.get("postedAt"))
        if posted is None:
            if not unknowns.get("postedAt", True):
                return "no posted date, and unknown dates are excluded"
        elif posted < now - timedelta(days=window):
            # Name the window that actually bound. On `all` the config's own
            # label reads "posted more than all ago", and the two-month ceiling
            # can bind under any label the user picked.
            return f"posted more than {window:g} days ago"

    wanted = set(search_config.expand_locations(config))
    if wanted:
        country = str((posting.get("location") or {}).get("country") or "").upper()
        remote_ok = (config.get("includeRemoteForSelection", True)
                     and str(posting.get("workplace") or "").lower() == "remote")
        if country and country not in wanted and not remote_ok:
            return f"{country} is not in the selected countries"

    allowed = config.get("workplace") or list(search_config.WORKPLACES)
    workplace = str(posting.get("workplace") or "").lower()
    if workplace and workplace not in allowed:
        return f"{workplace} is not an accepted arrangement"
    if not workplace and not unknowns.get("workplace", True):
        return "the posting does not say remote, hybrid or onsite"

    text = _text_for_scope(posting, config)
    excludes = config.get("excludeKeywords") or []
    lowered = _text_for_exclusions(posting).lower()
    for word in excludes:
        if word.lower() in lowered:
            return f"mentions an excluded word ({word})"

    keywords = config.get("keywords") or []
    if keywords:
        haystack = set(match_score._stems(text))
        if not any(match_score._phrase_hit(k, haystack) > 0 for k in keywords):
            return "matches none of the search terms"

    mode = config.get("companiesMode") or "all"
    if mode != "all":
        listed = {identity.norm_company(c) for c in (config.get("companies") or [])}
        company = identity.norm_company(posting.get("company"))
        if mode == "only" and company not in listed:
            return "not one of the only-these companies"
        if mode == "hide" and company in listed:
            return "one of the hidden companies"
    return ""


# --- the run ----------------------------------------------------------------

def select_channels(names=None, tier="1"):
    """Which connectors this run asks, and the ones it deliberately does not."""
    available = dict(sources.ALL)
    if not names:
        return available
    chosen, unknown = {}, []
    for name in names:
        if name in available:
            chosen[name] = available[name]
        else:
            unknown.append(name)
    if unknown:
        raise IngestError(f"unknown channel(s): {', '.join(unknown)}; "
                          f"known channels are {', '.join(sorted(available))}")
    return chosen


def run(root, channels=None, limit=None, dry_run=False, now=None, fetcher=None,
        settings=None, publish=False):
    started = time.time()
    now = now or datetime.now(timezone.utc)
    # (source, company) -> ids that board returned this sweep. Feeds
    # close_missing once every channel has run.
    seen_by_board = {}

    def _norm_company(name):
        return identity.norm_company(name, index.alias_map)

    report = {
        "at": jobs_store.now_stamp(), "tier": "1", "found": 0, "kept": 0,
        "reopened": 0,
        "deduped": 0, "gatedOut": 0, "byChannel": {}, "channelsSkipped": [],
        "errors": [], "gatedOutBy": {}, "requests": 0, "pruned": 0,
        "datesRefreshed": 0,
        "usingDefaults": False, "configVersion": 0, "dryRun": bool(dry_run),
    }

    config = search_config.load(root)
    if config is None:
        config = search_config.validate({})
        report["usingDefaults"] = True
    report["configVersion"] = config.get("version", 0)
    countries = search_config.expand_locations(config)
    config = dict(config, _postedWithinDays=search_config.posted_within_days(config))

    jobs = jobs_store.load_jobs(root)
    passed = jobs_store.load_passed(root)
    companies = jobs_store.load_companies(root)

    # Apply the verified-tenant pack before the channels run, not after.
    #
    # `references/probed-tenants.json` is where tenant discovery puts what it
    # confirmed, and until now nothing read it except `seed_companies.py` run
    # by hand. So a discovery block could add 34 boards, merge, deploy, sweep,
    # and move one job: the file was inert and the second command was a thing
    # to remember. This is the same shape as merging not being deploying.
    #
    # At the start rather than the end because a tenant seeded after the
    # channel loop is a tenant nobody fetches until the sweep after next.
    packed, pack_summary = seed_companies.seed(companies, seed_companies.from_pack())
    if packed != companies:
        companies = packed
        if not dry_run:
            jobs_store.save_companies(root, companies)
    report["tenantsSeeded"] = pack_summary["added"]

    decided = applied_index(root)
    tombstones = tombstone_index(passed)
    index = identity.Index(jobs, companies)

    fetcher = fetcher or base.Fetcher(root=root)
    requests_before = fetcher.requests
    selected = select_channels(channels)
    bodies_fetched = 0
    discovered_companies = []
    # What each channel returned the last time anybody believed it. Read once,
    # written once, after the postings are committed.
    ledger = read_store(root, ["meta-get", "--key", sweep_runs.STORE_KEY],
                        {}).get(sweep_runs.STORE_KEY) or {}
    believed_now, refused_now, low_runs = {}, {}, {}

    for name, connector in sorted(selected.items()):
        try:
            result = connector(fetcher, config=config, companies=companies,
                               settings=settings, countries=countries, limit=limit,
                               cursor=now.hour)
        except Exception as exc:                   # noqa: BLE001
            # A connector that raises must not take the other thirteen with it,
            # and must not vanish either.
            result = base.failed(name, f"{type(exc).__name__}: {exc}"[:200])
        if result["status"] == "skipped":
            report["channelsSkipped"].append({"channel": name, "why": result["reason"]})
            report["byChannel"][name] = {"status": "skipped", "found": 0, "kept": 0,
                                         "why": result["reason"]}
            continue
        if result["status"] == "error":
            report["errors"].append({"channel": name, "why": result["reason"]})
            report["byChannel"][name] = {"status": "error", "found": 0, "kept": 0,
                                         "why": result["reason"]}
            continue
        for problem in result.get("errors") or []:
            report["errors"].append({"channel": name, "why": problem})

        # Whether this channel's answer is the whole of the boards it asked
        # for. Only a complete listing can be evidence that a posting missing
        # from it was taken down. A connector that omits the flag is a board
        # sweep and complete by default; one reading a recent-items feed says
        # so, and its postings never feed the closure pass.
        listing_complete = result.get("listingComplete", True)

        # **`status: ok` is the connector's opinion of its own HTTP calls, and
        # coverage is a different claim.** On 2026-08-30 Greenhouse returned
        # 3,209 against 5,909, reported success, and 435 postings were closed
        # because they were absent from a fetch nobody could show was whole.
        #
        # A refused channel keeps its postings, which arrived and are real. It
        # loses only the right to have its *absences* read as closures, which
        # is brake 1 in `jobs_store.close_missing` applied deliberately instead
        # of by accident.
        total = len(result["postings"])
        anchor_before = (ledger.get(name) or {}).get("found")
        believed, why_not = sweep_runs.gate(
            name, total, ledger.get(name),
            unchanged=int(result.get("unchanged") or 0))
        # Against the anchor as it stood before this sweep, and moved whether
        # the sweep was believed or refused. `note_refusal` re-anchors after
        # two refusals, and a counter reading the new anchor would clear itself
        # at the moment it was meant to fire. A 304-heavy channel is exempt for
        # the same reason the gate exempts it: it was told nothing changed.
        if not result.get("unchanged"):
            low_runs[name] = source_health.observe(
                ledger.get(name), total, anchor_before)
        if believed:
            believed_now[name] = total
        else:
            refused_now[name] = total
            listing_complete = False
            report.setdefault("closureGated", []).append(
                {"channel": name, "found": total, "why": why_not})

        found = kept = 0
        for posting in result["postings"]:
            # Learn direct ATS tenant evidence before the search gate. A role
            # outside today's search can still make its employer fetchable on
            # the next sweep. Aggregator URLs do not resolve in from_postings.
            discovered_companies.extend(seed_companies.from_postings([posting]))
            found += 1
            report["found"] += 1

            # A saved job may predate a connector's real publish-date support.
            # Refresh that one trusted field before today's search gate. The
            # gate still prevents a rejected new job from entering the store.
            held_id, _held_how = identity.match(index, posting)
            if (held_id is not None and
                    jobs_store.refresh_posted_date(jobs[held_id], posting)):
                report["datesRefreshed"] += 1

            if _tombstoned(tombstones, posting,
                           identity.job_id(posting, index.alias_map)):
                _count_gate(report, "passed on before")
                continue

            reason = gate(posting, config, now)
            if reason:
                _count_gate(report, reason)
                continue

            if name in NEEDS_BODY and not posting.get("descriptionExcerpt"):
                if bodies_fetched < MAX_BODIES_PER_RUN:
                    bodies_fetched += 1
                    _enrich(fetcher, posting, companies, report)
                # Over the cap the job is still kept, just scored on its title.

            state, why = already_decided(decided, posting)
            flags = {}
            if state == "applied":
                flags = {"duplicateOfApplied": True, "duplicateReason": why}
            posting["score"] = match_score.score(posting, config, now=now, flags=flags)
            posting["configVersion"] = config.get("version", 0)

            if state == "passed":
                _count_gate(report, "dropped by the user before")
                continue
            if state in ("applied", "watched"):
                # The floor decides what is worth *offering*. These are not
                # being offered — they are being recorded so the next sighting
                # of the same job merges into them instead of arriving as new.
                # Applying the floor here would drop them, since the duplicate
                # gate has already capped them at 25.
                posting["state"] = state
            elif posting["score"]["total"] < config.get("minScoreToKeep", 0):
                _count_gate(report, f"scored below {config.get('minScoreToKeep')}")
                continue

            match_id, how = identity.resolve(index, posting)
            try:
                # `matched` carries the matcher's verdict through to the store,
                # so a job it declined can never be folded onto an existing
                # record by id alone.
                jobs, action = jobs_store.upsert(jobs, posting, match_id=match_id,
                                                 matched=how is not None)
            except jobs_store.JobsStoreError as exc:
                # identity.resolve() makes this unreachable. If it ever fires,
                # it is a real defect and it says so rather than eating a job.
                report["errors"].append({"channel": name, "why": str(exc)})
                continue
            if action == "merged":
                report["deduped"] += 1
                report.setdefault("dedupedBy", {})
                # `how` is None only on the conservative path, where a body was
                # too thin to fingerprint and there was nothing to tell two
                # records apart.
                label = how or "identity, no fingerprint to separate them"
                report["dedupedBy"][label] = report["dedupedBy"].get(label, 0) + 1
            else:
                kept += 1
                report["kept"] += 1
            index.add(match_id, jobs[match_id])
            # What this board still lists, so anything we hold for it that did
            # not come back can be recognised as taken down. Keyed on the
            # posting's own source rather than the channel, because a board
            # reached through two channels must not look half-empty in either.
            jobs[match_id], reopened = jobs_store.reopen(jobs[match_id])
            if reopened:
                report["reopened"] += 1
            if listing_complete:
                seen_by_board.setdefault(
                    (posting.get("canonicalSource"), _norm_company(posting.get("company"))),
                    set()).add(match_id)

        report["byChannel"][name] = {"status": "ok", "found": found, "kept": kept,
                                     "why": result.get("reason") or ""}

    # Every request this run made, not just the ones the connectors counted:
    # body enrichment happens outside them, and under-reporting our own traffic
    # is exactly the number nobody should be able to fool themselves with.
    report["requests"] = fetcher.requests - requests_before
    # The cap is the user's call; null keeps everything. Two separate clocks
    # here. `max_age_days` is how long a job nobody acted on survives since we
    # last saw it. The ceiling is how old the posting itself may be, and it has
    # to be enforced here as well as at the gate: the gate only sees postings
    # arriving, so without this a posting that predates the ceiling and was
    # stored before it existed stays on the board forever.
    # Before retention, because a record that just gained a country should be
    # counted under it. Cheap, and it means a parser fix reaches records whose
    # board has not changed since they were stored.
    jobs, backfilled = jobs_store.refresh_locations(jobs)
    report["locationsBackfilled"] = backfilled

    # Jobs whose own board has stopped listing them. Only on a complete sweep:
    # `limit` truncates every channel, so under it the postings that did not
    # arrive are the ones we chose not to ask for, and treating them as closed
    # would empty the board. Same for a dry run, which must change nothing.
    if limit is None and not dry_run:
        jobs, closure = jobs_store.close_missing(jobs, seen_by_board, at=now,
                                                 norm=_norm_company)
        report["closed"] = len(closure["closed"])
        report["closedFrom"] = _closed_by_state(closure["closed"])
        if closure["refused"]:
            report["closureRefused"] = closure["refused"]
    else:
        report["closed"] = 0

    max_age = search_config.posted_within_days(config) or jobs_store.NEW_MAX_AGE_DAYS
    jobs, dropped = jobs_store.prune(jobs, cap=config.get("storedCap"),
                                     max_age_days=max(max_age, jobs_store.NEW_MAX_AGE_DAYS),
                                     at=now,
                                     posted_ceiling_days=search_config.MAX_AGE_DAYS)
    report["pruned"] = len(dropped)
    report["storedTotal"] = len(jobs)
    report["bodiesFetched"] = bodies_fetched
    report["durationSeconds"] = round(time.time() - started, 1)

    companies_after, company_summary = seed_companies.seed(companies, discovered_companies)
    report["companiesDiscovered"] = company_summary["added"] + company_summary["updated"]

    # After the postings are committed, never before: a yield recorded first is
    # a lie the moment the write fails, and the next sweep would ratchet
    # against a run that did not happen. Same ordering as `writes.publish`.
    if not dry_run and limit is None:
        for channel, total in believed_now.items():
            ledger = sweep_runs.remember(ledger, channel, total, report["at"])
        for channel, total in refused_now.items():
            ledger, again = sweep_runs.note_refusal(ledger, channel, total,
                                                    report["at"])
            if again:
                report.setdefault("closureGateReanchored", []).append(channel)
        # After both, because `remember` and `note_refusal` rewrite the row and
        # would drop a counter written before them.
        for channel, observed in low_runs.items():
            if channel in ledger:
                ledger[channel]["lowRuns"] = observed.get("lowRuns", 0)
                if "lastRatio" in observed:
                    ledger[channel]["lastRatio"] = observed["lastRatio"]

    # **What is actually wrong, rather than every channel that carried a note.**
    # The board built this list itself with `status === "error" || c.why`, and
    # every channel has a `why`, so it read 27 problems against 1 real one.
    # Derived here because this is where the ledger and the report both are,
    # and because a rule in the renderer is a rule the device cannot alarm on.
    report["problems"] = source_health.problems(report.get("byChannel"), ledger)

    if not dry_run:
        jobs_store.save_jobs(root, jobs)
        if companies_after != companies:
            jobs_store.save_companies(root, companies_after)
        fetcher.save_validators()
        if limit is None:
            write_sweep_runs(root, ledger)
        write_last_ingest(root, report)
        if publish:
            report["published"] = _publish()
    return report, jobs


def add_postings(root, postings, now=None):
    """Put postings into the Found pool, the same way a connector's would go.

    The agent sweep used to write `status: "watch"` sessions directly, so
    anything it found landed in Watching without the user ever seeing it in
    Found. Sahil's rule is that a scan fills Found and only the eye moves a job
    to Watching, so the sweep needs a door into the pool — and it has to be this
    door, not a second one. A job added here is scored by the same rubric,
    resolved by the same identity rules and merged into the same records as one
    a connector found, which is what makes "seen on 2 boards" mean anything.

    Returns a report in the same shape a run does.
    """
    now = now or datetime.now(timezone.utc)
    config = search_config.load(root) or dict(search_config.DEFAULTS)
    jobs = jobs_store.load_jobs(root)
    passed = jobs_store.load_passed(root)
    companies = jobs_store.load_companies(root)
    index = identity.Index(jobs, companies)
    decided = applied_index(root)
    tombstones = tombstone_index(passed)

    report = {"found": 0, "kept": 0, "deduped": 0, "gatedOut": 0, "gatedOutBy": {},
              "errors": [], "added": [], "byChannel": {}}
    for posting in postings:
        report["found"] += 1
        reason = gate(posting, config, now=now)
        if reason:
            _count_gate(report, reason)
            continue
        if _tombstoned(tombstones, posting):
            _count_gate(report, "dropped by the user before")
            continue
        state, why = already_decided(decided, posting)
        flags = {"duplicateOfApplied": True, "duplicateReason": why} if state == "applied" else {}
        posting["score"] = match_score.score(posting, config, now=now, flags=flags)
        posting["configVersion"] = config.get("version", 0)
        if state in ("applied", "watched"):
            posting["state"] = state
        match_id, how = identity.resolve(index, posting)
        try:
            jobs, action = jobs_store.upsert(jobs, posting, match_id=match_id,
                                             matched=how is not None)
        except jobs_store.JobsStoreError as exc:
            report["errors"].append({"channel": "agent-sweep", "why": str(exc)})
            continue
        if action == "merged":
            report["deduped"] += 1
        else:
            report["kept"] += 1
            report["added"].append(match_id)
        index.add(match_id, jobs[match_id])
    jobs_store.save_jobs(root, jobs)
    report["storedTotal"] = len(jobs)
    return report


def _closed_by_state(closed):
    """How many left Found and how many left Watching, named separately.

    They are different losses. A card leaving Found is noise going away; a card
    leaving Watching is something the user chose that has been taken from them,
    and the board should be able to say which happened.
    """
    out = {}
    for row in closed:
        was = row.get("wasState") or "new"
        out[was] = out.get(was, 0) + 1
    return out


def tombstone_index(passed):
    """The tombstones, arranged for lookup rather than a scan.

    Built once per run: a linear pass per posting over every job ever passed on
    is quadratic, and both sides of it grow.
    """
    index = {"ids": set(passed), "identity": set(), "urls": set(), "prints": []}
    for job_id, tomb in passed.items():
        index["identity"].add((identity.norm_company(tomb.get("company")),
                               tomb.get("normTitle") or jobs_store.norm_title(tomb.get("title"))))
        link = identity.url_key(tomb.get("canonicalUrl"))
        if link:
            index["urls"].add(link)
        if tomb.get("descFingerprint"):
            index["prints"].append(tomb["descFingerprint"])
    return index


def _tombstoned(index, posting, job_key=None):
    """A passed job recognised again, however it comes back.

    The tombstone outlives the job record, so something pruned and re-found
    still has to be recognised — otherwise "never re-offered" only lasts until
    the next prune.
    """
    if job_key and job_key in index["ids"]:
        return True
    key = (identity.norm_company(posting.get("company")),
           posting.get("normTitle") or jobs_store.norm_title(posting.get("title")))
    if key in index["identity"]:
        return True
    if index["urls"] and any(identity.url_key(u) in index["urls"]
                             for u in identity.sighting_urls(posting)):
        return True
    mine = posting.get("descFingerprint")
    return bool(mine and any(identity.similar(mine, other) for other in index["prints"]))


def _enrich(fetcher, posting, companies, report):
    """Fetch one body for a posting that survived the metadata gate."""
    source = (posting.get("sources") or [{}])[0]
    url = source.get("url") or ""
    token = job_id = ""
    parts = [p for p in url.split("/") if p]
    if "greenhouse.io" in url and len(parts) >= 4:
        token, job_id = parts[-3], parts[-1]
    if not token or not job_id:
        return
    body, error = greenhouse.fetch_body(fetcher, url, token, job_id)
    if error:
        report["errors"].append({"channel": "greenhouse", "why": error})
        return
    text = base.strip_html(body)
    posting["descriptionExcerpt"] = text[:base.EXCERPT_CHARS]
    posting["descFingerprint"] = identity.fingerprint(text)


def _count_gate(report, reason):
    report["gatedOut"] += 1
    report["gatedOutBy"][reason] = report["gatedOutBy"].get(reason, 0) + 1


def write_sweep_runs(root, ledger):
    """Persist the per-channel yield ledger. See `scripts/sweep_runs.py`."""
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="sweepruns.")
    try:
        with open(fd, "w") as handle:
            json.dump(ledger, handle)
        result = store_call(root, ["meta-set-json", "--key",
                                   sweep_runs.STORE_KEY, "--input", tmp])
        return result.returncode == 0
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_last_ingest(root, report):
    """Record the run. Called on the failure paths too, deliberately.

    A run that died is the run you most need to see on the board; if only the
    successful ones wrote here, an outage would leave yesterday's healthy
    numbers on display.
    """
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="lastingest.")
    try:
        with open(fd, "w") as handle:
            json.dump(report, handle)
        result = store_call(root, ["meta-set-json", "--key", "lastIngest", "--input", tmp])
        return result.returncode == 0
    finally:
        Path(tmp).unlink(missing_ok=True)


def _publish():
    result = subprocess.run([sys.executable, str(REPO / "scripts" / "publish.py")],
                            capture_output=True, text=True, env=toolpath.enriched_env())
    return result.returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Run one Tier-1 sourcing pass.")
    ap.add_argument("--add", metavar="FILE",
                    help="a JSON posting, or array of them, to put into Found "
                         "(what the agent sweep uses instead of writing "
                         "watch entries)")
    ap.add_argument("--tier", default="1", choices=["1"],
                    help="Tier 2 sweeps are Phase 3")
    ap.add_argument("--store", help="store directory (default ~/.job-application)")
    ap.add_argument("--limit", type=int, help="stop each channel after N postings")
    ap.add_argument("--channel", action="append", help="only this channel (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--publish", action="store_true",
                    help="push the board afterwards; the watcher does this itself")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.store)

    # One sweep at a time. Both branches below read the whole store, work for
    # minutes, then write it back, so two overlapping runs do not interleave:
    # the second to finish silently discards everything the first stored. A
    # dry run writes nothing, so it is left free rather than made to wait, and
    # it does not gate Apply while it observes.
    guard = (contextlib.nullcontext() if args.dry_run
             else runlock.held(root, SWEEP_LOCK, note="ingest"))
    try:
        with guard:
            return _run_cli(args, root)
    except runlock.LockBusy as exc:
        # Refuse rather than queue: two sweeps find no more jobs than one, so
        # waiting buys nothing and a queued second run would only widen the
        # window in which the store is being rewritten.
        print(f"ingest: a sweep is already running, so this one will not start. {exc}",
              file=sys.stderr)
        print(json.dumps({"error": str(exc), "skipped": "sweep already running"},
                         indent=2, sort_keys=True))
        return 3


def _run_cli(args, root):
    if args.add:
        raw = json.loads(pathlib.Path(args.add).expanduser().read_text())
        postings = raw if isinstance(raw, list) else [raw]
        try:
            report = add_postings(root, postings)
        except (IngestError, jobs_store.JobsStoreError,
                search_config.ConfigError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    try:
        report, _ = run(root, channels=args.channel, limit=args.limit,
                        dry_run=args.dry_run, publish=args.publish)
    except (IngestError, jobs_store.JobsStoreError, search_config.ConfigError) as exc:
        # The run failed, so the board must say so rather than keep showing the
        # last good numbers.
        failure = {"at": jobs_store.now_stamp(), "tier": args.tier, "found": 0,
                   "kept": 0, "deduped": 0, "gatedOut": 0, "byChannel": {},
                   "channelsSkipped": [], "errors": [{"channel": "ingest", "why": str(exc)}],
                   "failed": True}
        if not args.dry_run:
            try:
                write_last_ingest(root, failure)
            except OSError:
                pass
        print(f"ingest: {exc}", file=sys.stderr)
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
