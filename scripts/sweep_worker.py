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


#: How many validators to keep, matching `base.Fetcher`'s own trim. A cache
#: that grows without bound is a table nobody prunes.
VALIDATOR_CAP = 5000


def load_validators(conn, account, root):
    """Write the stored validators into the file `base.Fetcher` reads.

    **The file is the interface, deliberately.** `Fetcher` loads
    `<root>/source-cache.json` in its constructor and writes it in
    `save_validators`, and teaching it about Postgres would put a database
    dependency inside every connector's fetch path -- including on the device,
    where the file is correct and the database is not always reachable. Writing
    the file before the Fetcher exists costs one round trip and changes
    nothing else.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select url, etag, last_modified, seen_at from http_validators "
            "where account_id = %s order by seen_at desc limit %s",
            (account, VALIDATOR_CAP))
        rows = cur.fetchall()
    conn.rollback()
    cache = {url: {"at": at.strftime("%Y-%m-%dT%H:%M:%SZ") if at else None,
                   "etag": etag, "lastModified": modified}
             for url, etag, modified, at in rows}
    path = Path(root) / "source-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache), encoding="utf-8")
    return len(cache)


def save_validators(connect, account, root, batch=500):
    """Upsert what this sweep learned, and prune to the cap.

    Its own connection, on the rule the staging write already follows: this
    runs after the whole fetch, and a connection held since before it is a
    connection Neon closed twenty minutes ago.
    """
    path = Path(root) / "source-cache.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A damaged cache is a slow next run, never a wrong one -- the same
        # judgement `base.Fetcher` makes about reading it.
        return 0
    rows = [(account, url, (v or {}).get("etag"), (v or {}).get("lastModified"))
            for url, v in cache.items() if isinstance(v, dict)]
    conn = connect()
    try:
        with conn.cursor() as cur:
            for start in range(0, len(rows), batch):
                cur.executemany(
                    "insert into http_validators "
                    "(account_id, url, etag, last_modified, seen_at) "
                    "values (%s, %s, %s, %s, now()) "
                    "on conflict (account_id, url) do update set "
                    "etag = excluded.etag, "
                    "last_modified = excluded.last_modified, "
                    "seen_at = excluded.seen_at",
                    rows[start:start + batch])
            cur.execute(
                "delete from http_validators where account_id = %s and url not in ("
                "  select url from http_validators where account_id = %s "
                "  order by seen_at desc limit %s)",
                (account, account, VALIDATOR_CAP))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


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


def stage(connect, account, sweep, name, result, batch=500):
    """Write one channel's postings, then its run row. In that order.

    **A connection per stage, not one for the run.** The first real full sweep
    opened a connection at the start and held it while the fetch worked:
    adzuna staged four seconds in, ashby then took about twenty-four minutes,
    and the stage after it died with `consuming input failed: SSL connection
    has been closed unexpectedly`. Neon closes an idle connection long before a
    795-board sweep is done, so one connection for the run is a connection that
    is dead for every channel after the first slow one.

    `connect` is a callable rather than a connection for that reason. It is
    also why this takes the whole result and returns nothing: the caller must
    not be holding anything across a fetch.
    """
    postings = result.get("postings") or []
    conn = connect()
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
    try:
        conn.commit()
    finally:
        conn.close()


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


def sweep(connect, account, channels=None, limit=None, dry_run=False):
    """Fetch every channel, staging each as it finishes.

    `connect` is a callable and nothing here holds a connection across a fetch.
    The registry and the saved search are read on one connection that is then
    closed, because reading them takes milliseconds and the fetch takes tens of
    minutes.
    """
    started = time.time()
    sweep_id = str(uuid.uuid4())
    conn = connect()
    try:
        config = load_search(conn, account)
        registry = load_registry(conn, account)
    finally:
        conn.close()
    countries = config.get("locations") or None
    selected = dict(sources.ALL)
    if channels:
        unknown = [c for c in channels if c not in selected]
        if unknown:
            raise SystemExit(f"sweep_worker: unknown channel(s) {unknown}")
        selected = {c: selected[c] for c in channels}

    root = Path(os.environ.get("JOB_APPLICATION_DIR", "/var/lib/jobby"))
    # **Before the Fetcher exists**, because it reads the file in its
    # constructor. Without this every cloud sweep is a cold sweep: the
    # container's root is ephemeral, so ashby fetched 6,855 postings from 193
    # boards in 717 seconds where the laptop's cached run fetched 1,050.
    conn = connect()
    try:
        seeded = load_validators(conn, account, root)
    finally:
        conn.close()
    fetcher = base.Fetcher(root=root)
    report = {"sweepId": sweep_id, "at": stamp(), "boards": len(registry),
              "validatorsSeeded": seeded,
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
            stage(connect, account, sweep_id, name, result)
        # **Progress, because a silent container is a container nobody can
        # diagnose.** The first full run emitted nothing for twenty-four
        # minutes and there was no way to tell a slow sweep from a hung one
        # from outside. One line per channel, flushed.
        print(f"{stamp()} {name}: {result.get('status')} "
              f"{count} postings, {round(time.time() - started, 1)}s elapsed",
              flush=True)
    report["requests"] = fetcher.requests
    report["seconds"] = round(time.time() - started, 1)
    if not dry_run:
        fetcher.save_validators()
        report["validatorsStored"] = save_validators(connect, account, root)
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
    report = sweep(_connect, account, channels or None, args.limit, args.dry_run)
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
