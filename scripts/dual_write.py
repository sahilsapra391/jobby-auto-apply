#!/usr/bin/env python3
"""Mirror the file store into Postgres, Postgres first.

Phase 1 makes Postgres the system of record while the file store is still
written. Every write path listed in `docs/write-paths.md` goes through here, and
the ordering is the whole design:

    begin
      upsert / delete in Postgres
    commit                      <- a failure fails the whole operation
    write the file              <- a failure leaves Postgres ahead

**A Postgres failure must fail the operation, not fall through to the file.**
A file write that succeeded while Postgres failed is a decision the cloud never
learns about, and nothing in the system will ever look for it: no reconciliation
can find a change nobody recorded. The other direction is recoverable, because
the backfill is idempotent and parity reports the gap loudly meanwhile. So the
order is not a preference, it is the difference between a bad hour and lost
work.

**A file failure after the commit is logged loudly and re-raised.** Never
swallowed. The log names both halves, so whoever reads it knows Postgres is
ahead rather than guessing.

The diff is by per-record digest rather than by trusting a caller to declare
what it touched. Measured on the real pool, digesting all 20,103 records costs
0.27s against a 0.18s serialisation and a 99 MB file write, so the exact answer
is cheaper than the bookkeeping needed to approximate it, and no field can
change without being seen. `prune` deletes, `reopen` revives and `close_missing`
closes without any of them saying so; a digest sees all three.
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import backfill  # noqa: E402


class DualWriteError(Exception):
    """A refusal. Never raised for a store that is legitimately empty."""


_LOOK_IT_UP = object()


def resolved_url():
    """**The one resolver**, and it answers "which database does this device
    mirror to". `enabled()` and `connect()` both come here and cannot disagree.

    They used to answer different questions from different places: `enabled()`
    asked `jobs_db.database_url()`, which reads config.env under the store root,
    and `connect()` asked `migrate.database_url()`, which prefers
    `JOBBY_TEST_DATABASE_URL`. A caller could be told the device had a database
    and then be handed a different one. That split is the root cause of all
    three of 2026-08-30's incidents rather than a fourth: `HOME=$(mktemp -d)`
    hid it by emptying both sides at once, resolving credentials through the
    store root removed the accident, and pointing `JOBBY_TEST_DATABASE_URL` at
    production was the same hole wearing a third name.

    **`JOBBY_TEST_DATABASE_URL` is deliberately not consulted here**, and that
    is not a second resolver sneaking back. It is a different question.
    `migrate.database_url` answers "which database is the schema tool pointed
    at", and a test database is exactly the right answer for `pgtest`, for the
    migration runner and for a CLI told where to go. This answers "where does
    this device's store mirror to", and a throwaway container is never that.

    Consulting it here turned dual-write on for every store-writing test in CI,
    which has that variable set for `pgtest`. Measured: 57 errors across
    test_ingest and test_found_script. Migrating CI's `public` so the mirror
    had somewhere to go fixed 44 of them and left 13 that pass alone and fail
    in a suite -- tests seeing each other's rows through one shared schema. A
    clear failure traded for flakiness is a bad trade, and the mirror tests do
    not need it anyway: every one of them takes a `pgtest` scratch schema
    rather than calling `connect()`.
    """
    import migrate
    url = migrate._device_url()
    if not url:
        return None
    # The test-runner guard still applies: a test process may not mirror to the
    # device's own database however it got hold of the URL.
    if migrate.device_url_refusal():
        return None
    _trace_device_url()
    return url


def _trace_device_url():
    """Say who reached the device's database, when asked to.

    Off unless JOBBY_TRACE_DEVICE_URL names a file, because this is for hunting
    a specific class of bug: a child process resolving the device's database
    while its parent believes it is isolated. Grepping for callers that take a
    root gives dozens of candidates and no evidence; this makes the write name
    its own caller.
    """
    import os
    target = os.environ.get("JOBBY_TRACE_DEVICE_URL")
    if not target:
        return
    try:
        import traceback, sys, json
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "pid": os.getpid(),
                "argv": sys.argv[:4],
                "root": os.environ.get("JOB_APPLICATION_DIR", "<unset>"),
                "underTest": "unittest" in sys.modules or "pytest" in sys.modules,
                "stack": [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}"
                          for f in traceback.extract_stack()[:-1]][-14:],
            }) + "\n")
    except Exception:
        pass


def enabled(url=_LOOK_IT_UP):
    """Whether Postgres is configured at all.

    `enabled()` reads the configured URL. `enabled(url=None)` asks about an
    absent one and answers False, which is a different question: conflating the
    two made "no database" and "go and find the database" the same argument,
    and a caller holding an explicit None got whatever this machine happened to
    have configured.

    A machine with no database mirrors nothing, which has to be allowed: the
    suite runs on runners and on fresh clones. It is dangerous only if nothing
    notices, so `verify_parity` asserts this is true before it reports green.
    A run that mirrored nothing can then never also claim the stores agree.
    """
    if url is _LOOK_IT_UP:
        url = resolved_url()
    return bool((url or "").strip())


def connect():
    """A session fit to mirror through. Never a pooled one.

    Migrations need a session for their advisory lock and this needs one for
    its transaction, and Neon's configured URL is the pooled endpoint. See
    packages/schema/migrate.py, which owns the rewrite.

    The URL is passed explicitly rather than letting `migrate.connect` look it
    up again, so that what `enabled()` reported and what this opens are one
    value read once.
    """
    import migrate
    return migrate.connect(url=resolved_url(), announce=False)


_ACCOUNT_CACHE = {}


def account_id(conn, email=None):
    """The account decisions are attributed to.

    Single-tenant today, so it is the one account row, and that is asserted
    rather than assumed: a second account arriving without this being revisited
    would silently attribute one person's decisions to another. Phase 5 replaces
    this with the authenticated account and this function goes away.
    """
    if email:
        return backfill.account_id_for(conn, email)
    cached = _ACCOUNT_CACHE.get("id")
    if cached:
        return cached
    with conn.cursor() as cur:
        cur.execute("select id from accounts order by created_at limit 2")
        rows = cur.fetchall()
    if not rows:
        raise DualWriteError(
            "no account exists, so a decision cannot be attributed. Create "
            "one before dual-write is enabled.")
    if len(rows) > 1:
        raise DualWriteError(
            "more than one account exists and dual-write still assumes one. "
            "Attributing every decision to the oldest would give one person's "
            "choices to another; wire the authenticated account through first.")
    _ACCOUNT_CACHE["id"] = rows[0][0]
    return rows[0][0]


# --------------------------------------------------------------------------
# Reading, where empty and broken must not look alike
# --------------------------------------------------------------------------

def read_pool(path):
    """The pool, or a named failure.

    `json.loads(stdout or "[]")` is the shape this refuses to be: a read that
    failed and a store that is empty produce the same value, so an outage reads
    as an empty pool and every count downstream is confidently wrong. Zero bytes
    counts as broken, because a zero-byte pool file is a truncated write and
    never a legitimately empty store.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DualWriteError(f"{path.name} could not be read: {exc}") from exc
    if not text.strip():
        raise DualWriteError(
            f"{path.name} is empty. A zero-byte pool is a truncated write, "
            f"not an empty store; an empty store is '{{}}'.")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise DualWriteError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DualWriteError(
            f"{path.name} holds {type(data).__name__}, not a mapping")
    return data


# --------------------------------------------------------------------------
# The diff
# --------------------------------------------------------------------------

def _digest(record):
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()


def digest_pool(jobs):
    """id -> digest, for every record. Keys are sorted inside each digest so a
    record rewritten in another key order is not a change; without that every
    save would look like a full rewrite and push 20,103 rows."""
    return {job_id: _digest(record) for job_id, record in jobs.items()}


def delta(before, after):
    """(added, changed, removed) ids between two digest maps."""
    now = digest_pool(after) if not _is_digests(after) else after
    added = set(now) - set(before)
    removed = set(before) - set(now)
    changed = {k for k in set(now) & set(before) if now[k] != before[k]}
    return added, changed, removed


def _is_digests(value):
    return bool(value) and isinstance(next(iter(value.values())), bytes)


# --------------------------------------------------------------------------
# The mirror
# --------------------------------------------------------------------------

def mirror_pool(conn, account_id, before, jobs, batch=500):
    """Push everything that changed into Postgres, in one transaction.

    `before` is the digest map from before the caller's mutations, or `{}` to
    mean "mirror everything". Returns a report; raises on any failure, having
    written nothing.
    """
    added, changed, removed = delta(before, jobs)
    touched = added | changed
    report = {"added": len(added), "changed": len(changed),
              "removed": len(removed)}
    if not touched and not removed:
        return report

    records = [jobs[job_id] for job_id in sorted(touched)]
    with conn.transaction():
        if records:
            employers, contested = backfill.upsert_employers(conn, records, batch)
            postings, merged = backfill.upsert_postings(conn, records,
                                                        employers, batch)
            backfill.upsert_decisions(conn, records, postings, account_id, batch)
            report["merged"] = merged
            report["contested_domains"] = contested

            # A decision that went back to `new`, or a posting that reopened,
            # leaves a row that must go. `upsert_decisions` only ever writes;
            # it cannot know a state left the decided set, because the record
            # it is looking at no longer says so.
            undecided = [postings[r["id"]] for r in records
                         if r.get("id") in postings
                         and backfill.STATE_MAP.get(r.get("state"),
                                                    (False, None))[1] is None]
            for chunk in _chunks(undecided, batch):
                conn.execute(
                    "delete from job_decisions where account_id = %s "
                    "and posting_id = any(%s)", (account_id, list(chunk)))

        for chunk in _chunks(sorted(removed), batch):
            conn.execute("delete from postings where legacy_id = any(%s)",
                         (list(chunk),))
    return report


def _chunks(rows, size):
    rows = list(rows)
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


# A resync that would remove more than this share of the corpus is refused
# without an explicit override. The shape is §5's closure guard: a read that
# failed must never be read as "the store is empty", and a resync is the one
# operation here that deletes in bulk.
RESYNC_DELETE_CEILING = 0.25


def resync(conn, account_id, jobs, force=False, batch=500):
    """Bring Postgres in line with the file store, deletions included.

    `mirror_pool` learns what was removed by diffing against a `before` map,
    which is right when a caller just mutated the pool and wrong here: this is
    the gap-closer, run when the two stores have drifted apart with nobody
    watching, and there is no `before`. So removal is computed against what
    Postgres actually holds.

    That matters because `prune` deletes. Between the backfill and dual-write
    going live, the pool went from 20,103 records to 16,123 and Postgres kept
    all of them: 4,314 rows the file no longer had. An upsert-only backfill can
    never close that, so parity would have stayed red forever with nothing
    wrong.

    **The deletion is bounded.** A truncated pool read would otherwise delete
    the corpus, which is the same failure §5 guards the closure sweep against:
    a connector that errors must never be read as a board returning nothing.
    `read_pool` already refuses an unreadable file, and this is the second
    line, because the first one only covers the failures that look like
    failures.
    """
    if not jobs:
        raise DualWriteError(
            "refusing to resync against an empty pool: that would delete the "
            "whole corpus, and an empty pool is a truncated read far more "
            "often than it is the truth")

    with conn.cursor() as cur:
        cur.execute("select legacy_id from postings")
        held = {row[0] for row in cur.fetchall()}

    doomed = sorted(held - set(jobs))
    if held and not force:
        share = len(doomed) / len(held)
        if share > RESYNC_DELETE_CEILING:
            raise DualWriteError(
                f"refusing to delete {len(doomed)} of {len(held)} postings "
                f"({share:.0%}), over the {RESYNC_DELETE_CEILING:.0%} ceiling. "
                f"That is the shape of a truncated pool read rather than a "
                f"prune. Re-run with force=True if the pool really did shrink "
                f"that far.")

    report = mirror_pool(conn, account_id, {}, jobs, batch)
    with conn.transaction():
        for chunk in _chunks(doomed, batch):
            conn.execute("delete from postings where legacy_id = any(%s)",
                         (list(chunk),))
    report["removed"] = len(doomed)
    return report


def _record_timing(entry, root=None):
    """One JSONL line per mirrored write, for the question Phase 3 has to
    answer before it moves sourcing to the cloud.

    "The mirror is fast enough" is not a number, and at 500k postings the
    per-sweep cost is the thing that decides whether §5's connector upsert can
    write through this path at all. Measuring it at 20k while the pool is small
    is cheap; discovering it at 500k is a rewrite.

    Best-effort by construction: a failure to record a duration must never
    fail the write it is timing.
    """
    try:
        base = Path(root) if root else Path("~/.job-application").expanduser()
        log = base / "logs" / "mirror-timing.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        entry = dict(entry,
                     at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def persist(conn, account_id, path, before, jobs, on_file_failure=None,
            batch=500):
    """Mirror to Postgres, then write the file. In that order, always.

    `on_file_failure` receives the message when the file write fails after a
    successful commit. It exists so the caller can route it to its own log; the
    exception is re-raised regardless, because a caller told nothing would
    carry on believing both stores moved.
    """
    import time
    started = time.time()
    report = mirror_pool(conn, account_id, before, jobs, batch)
    mirror_seconds = time.time() - started

    file_started = time.time()
    try:
        _write_file(Path(path), jobs)
    except Exception as exc:
        message = (
            f"POSTGRES IS AHEAD OF THE FILE. The mirror committed "
            f"({report['added']} added, {report['changed']} changed, "
            f"{report['removed']} removed) and writing {path} then failed: "
            f"{exc}. Postgres holds the newer state; re-running the backfill "
            f"is the repair. Parity will report this until it is done.")
        if on_file_failure is not None:
            on_file_failure(message)
        else:                                            # pragma: no cover
            print(message, file=sys.stderr)
        _record_timing({"mirrorSeconds": round(mirror_seconds, 3),
                        "fileSeconds": None, "records": len(jobs),
                        "added": report["added"], "changed": report["changed"],
                        "removed": report["removed"], "fileWriteFailed": True})
        raise

    file_seconds = time.time() - file_started
    total = mirror_seconds + file_seconds
    report["mirrorSeconds"] = round(mirror_seconds, 3)
    report["fileSeconds"] = round(file_seconds, 3)
    _record_timing({
        "mirrorSeconds": round(mirror_seconds, 3),
        "fileSeconds": round(file_seconds, 3),
        "mirrorShare": round(mirror_seconds / total, 3) if total else None,
        "records": len(jobs),
        "added": report["added"], "changed": report["changed"],
        "removed": report["removed"],
    })
    return report


def _write_file(path, jobs):
    """Atomic replace, so a failure never truncates the pool in place."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(jobs), encoding="utf-8")
    tmp.replace(path)
