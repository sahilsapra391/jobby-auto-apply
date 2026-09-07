#!/usr/bin/env python3
"""Fetch every channel, write what came back, and claim nothing.

**The container fetches; the device merges.** This runs the same connectors the
laptop runs -- imported, not reimplemented -- and stops the moment they return.
The gate, the pool merge, the closure sweep and the prune stay on the device,
because `jobs_store` contains zero SELECTs and moving the pool is Phase 4.

    python3 scripts/sweep_worker.py                 fetch and stage
    python3 scripts/sweep_worker.py --dry-run       fetch and print, write nothing
    python3 scripts/sweep_worker.py --channels greenhouse,ashby

**Why it is worth moving at all, and it is not the CPU.**
`jobs_store.POOL_LOCK` is the string "scan": the sweep's lock is the pool's
write lock. Eight non-sweep callers take it, two on the apply path, and across
13 days of `watch.log` 176 ticks skipped their board reconcile with LockBusy on
it, 9 to 21 every day. A sweep runs a median 26.8 minutes, six times a day.
Every one of those minutes is a minute a Watch or an Apply click can be
deferred, and the fetch is nearly all of them.

**It writes `complete` after the postings are committed, never before.** A
status written first is a lie the moment the write fails, and the device's
closure step reads only `complete` runs -- so a lie here closes an employer's
catalogue on the device. Same ordering as `writes.publish`: commit, then
announce.

**A channel that fails is written as `failed` with its reason, not omitted.**
An absent row and a failed row are different facts, and the device cannot tell
"this channel was not asked" from "this channel could not answer" if the
failure simply is not there. That distinction is brake 1 in
`jobs_store.close_missing`, one process away.
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "sources"))
sys.path.insert(0, str(HERE.parent / "packages" / "schema"))

import search_config  # noqa: E402
import sources  # noqa: E402
from sources import base  # noqa: E402


def now():
    return datetime.now(timezone.utc)


def stamp(moment=None):
    return (moment or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def account_id():
    """The account this sweep belongs to.

    Required rather than defaulted. Every row this writes is account-scoped,
    and a default would file one account's postings under another the first
    time a second account exists -- which is Phase 5 and is close enough that
    guessing now is a bug with a date on it.
    """
    value = (os.environ.get("JOBBY_ACCOUNT_ID") or "").strip()
    if not value:
        raise SystemExit(
            "sweep_worker: JOBBY_ACCOUNT_ID is required. Every row this writes "
            "is account-scoped and there is no safe default.")
    return value


def load_registry(conn, account):
    """The boards to sweep, from the `boards` table, live ones only.

    Falls back to nothing rather than to a file. A container with an empty
    registry fetches nothing and says so; a container that quietly read a
    `companies.json` baked into the image would sweep whatever was true on
    build day for as long as the image lived.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select source, board_slug, employer_id from boards "
            "where account_id = %s and disabled_at is null",
            (account,))
        rows = cur.fetchall()
    registry = {}
    for source, slug, _employer in rows:
        registry[f"{source}:{slug}"] = {
            "name": slug, "ats": source, "token": slug, "aliases": [],
        }
    return registry


def load_search(conn, account):
    """The saved search, from `board_meta`, which class B already mirrors."""
    with conn.cursor() as cur:
        cur.execute("select search_config from board_meta where account_id = %s",
                    (account,))
        row = cur.fetchone()
    if not row or not row[0]:
        return {}
    value = row[0]
    return value if isinstance(value, dict) else json.loads(value)


def run_channel(name, connector, fetcher, config, registry, countries, limit):
    """One channel, with its exception turned into a failure rather than a stop.

    A connector that raises must not take the other twenty-six with it, and
    must not vanish either -- the same rule `ingest.run` applies, restated here
    because this is a different caller and a silent divergence between the two
    would be a channel that behaves differently in the cloud.
    """
    try:
        return connector(fetcher, config=config, companies=registry,
                         settings=None, countries=countries, limit=limit,
                         cursor=now().hour)
    except Exception as exc:                              # noqa: BLE001
        return base.failed(name, f"{type(exc).__name__}: {exc}"[:200])


def stage(conn, account, sweep, name, result, batch=500):
    """Write one channel's postings, then its run row. In that order."""
    postings = result.get("postings") or []
    with conn.cursor() as cur:
        for start in range(0, len(postings), batch):
            chunk = postings[start:start + batch]
            cur.executemany(
                "insert into sweep_postings (account_id, sweep_id, source, "
                "posting) values (%s, %s, %s, %s)",
                [(account, sweep, name, json.dumps(p)) for p in chunk])
        # After the postings, never before. The device reads `complete` runs
        # only, so a status written first is a claim that survives a failed
        # write and closes an employer's catalogue one process away.
        cur.execute(
            "insert into sweep_runs (account_id, sweep_id, source, "
            "finished_at, status, job_count, board_count, unchanged, "
            "listing_complete, reason) "
            "values (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s) "
            "on conflict (account_id, sweep_id, source) do update set "
            "finished_at = excluded.finished_at, status = excluded.status, "
            "job_count = excluded.job_count, board_count = excluded.board_count, "
            "unchanged = excluded.unchanged, "
            "listing_complete = excluded.listing_complete, "
            "reason = excluded.reason",
            (account, sweep, name, _status(result), len(postings),
             _boards(result), int(result.get("unchanged") or 0),
             bool(result.get("listingComplete")),
             (result.get("reason") or "")[:500]))
    conn.commit()


def _status(result):
    # `skipped` stays `skipped`: a channel this run had no reason to ask is not
    # a channel that could not answer, and the device's problems list depends
    # on the difference.
    got = (result or {}).get("status")
    return {"ok": "complete", "error": "failed", "skipped": "skipped"}.get(
        got, "failed")


def _boards(result):
    import re
    found = re.match(r"\s*(\d+)\s+boards", str((result or {}).get("reason") or ""))
    return int(found.group(1)) if found else 0


def sweep(conn, account, channels=None, limit=None, dry_run=False):
    started = time.time()
    sweep_id = str(uuid.uuid4())
    config = load_search(conn, account)
    registry = load_registry(conn, account)
    countries = config.get("locations") or None
    selected = dict(sources.ALL)
    if channels:
        unknown = [c for c in channels if c not in selected]
        if unknown:
            raise SystemExit(f"sweep_worker: unknown channel(s) {unknown}")
        selected = {c: selected[c] for c in channels}

    fetcher = base.Fetcher(root=Path(os.environ.get("JOB_APPLICATION_DIR",
                                                   "/var/lib/jobby")))
    report = {"sweepId": sweep_id, "at": stamp(), "boards": len(registry),
              "channels": {}, "postings": 0, "requests": 0}
    for name, connector in sorted(selected.items()):
        result = run_channel(name, connector, fetcher, config, registry,
                             countries, limit)
        count = len(result.get("postings") or [])
        report["channels"][name] = {
            "status": result.get("status"), "found": count,
            "unchanged": int(result.get("unchanged") or 0),
            "why": (result.get("reason") or "")[:200],
        }
        report["postings"] += count
        if not dry_run:
            stage(conn, account, sweep_id, name, result)
    report["requests"] = fetcher.requests
    report["seconds"] = round(time.time() - started, 1)
    if not dry_run:
        fetcher.save_validators()
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch every channel and stage it.")
    ap.add_argument("--channels", help="comma-separated subset")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and print; write nothing")
    args = ap.parse_args(argv)
    channels = [c.strip() for c in (args.channels or "").split(",") if c.strip()]

    account = account_id()
    # A dry run still connects: the registry and the saved search are what it
    # is dry about writing, not what it reads.
    conn = _connect()
    try:
        report = sweep(conn, account, channels or None, args.limit, args.dry_run)
    finally:
        conn.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    # A sweep where every channel failed exits non-zero, so a scheduler that
    # only reads exit codes still learns something. A sweep with some failures
    # is a real sweep and exits 0; the failures are rows.
    statuses = {c["status"] for c in report["channels"].values()}
    return 0 if statuses - {"error"} else 1


def _connect():
    import jobs_db
    return jobs_db.connect()


if __name__ == "__main__":
    sys.exit(main())
