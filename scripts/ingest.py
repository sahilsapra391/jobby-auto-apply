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
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity  # noqa: E402
import jobs_store  # noqa: E402
import match_score  # noqa: E402
import search_config  # noqa: E402
import seed_companies  # noqa: E402
import sources  # noqa: E402
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
    text = posting.get("title") or ""
    if config.get("searchScope") != "title":
        text = f"{text}\n{posting.get('descriptionExcerpt') or ''}"
    return text


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
    lowered = text.lower()
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
    report = {
        "at": jobs_store.now_stamp(), "tier": "1", "found": 0, "kept": 0,
        "deduped": 0, "gatedOut": 0, "byChannel": {}, "channelsSkipped": [],
        "errors": [], "gatedOutBy": {}, "requests": 0, "pruned": 0,
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
    decided = applied_index(root)
    tombstones = tombstone_index(passed)
    index = identity.Index(jobs, companies)

    fetcher = fetcher or base.Fetcher(root=root)
    requests_before = fetcher.requests
    selected = select_channels(channels)
    bodies_fetched = 0
    discovered_companies = []

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

        found = kept = 0
        for posting in result["postings"]:
            # Learn direct ATS tenant evidence before the search gate. A role
            # outside today's search can still make its employer fetchable on
            # the next sweep. Aggregator URLs do not resolve in from_postings.
            discovered_companies.extend(seed_companies.from_postings([posting]))
            found += 1
            report["found"] += 1

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

    if not dry_run:
        jobs_store.save_jobs(root, jobs)
        if companies_after != companies:
            jobs_store.save_companies(root, companies_after)
        fetcher.save_validators()
        write_last_ingest(root, report)
        if publish:
            report["published"] = _publish()
    return report, jobs


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
