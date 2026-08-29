#!/usr/bin/env python3
"""The job pool and the decision store, in Postgres.

Why this exists. `jobs.json` was 97 MB for 15,400 records and every process
that touched it loaded the whole thing: ingest, publish, dashboard, sync_board.
Measured 2026-08-29, that is about 360 MB resident at today's size and roughly
4.7 GB at the 200,000-record cap, on a machine with 16 GB and about 4.8 GB free,
with ingest and publish routinely overlapping. Disk was never the constraint.
Memory was, and no cap value fixes it -- the fix is to stop loading the pool to
answer questions about part of it.

**The latency is the design constraint, not the storage.** Neon in us-east-2
answers a round trip from this Mac in about 60 ms. That is nothing for a query
returning ten thousand rows and ruinous for ten thousand queries returning one
row each: the same sweep costs 0.06 seconds one way and ten minutes the other.
So every function here is bulk by construction. There is deliberately no
`get_job(id)`, because the first caller to loop over one is the one that makes
the pipeline slower than the file it replaced.

Shape of the row. The whole record stays in a `jsonb` column, and the fields
worth querying are lifted into real columns beside it. That keeps the store
honest about what it is -- a document store with indexes -- rather than
pretending to a normalisation nobody has designed yet, and it means a record
gaining a field does not need a migration. The lifted columns are exactly the
ones the board filters and sorts on.
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store  # noqa: E402

CONFIG = Path("~/.job-application/config.env").expanduser()
URL_NAME = "JOBBY_DATABASE_URL"


class DatabaseError(Exception):
    pass


def database_url(config_path=None):
    """The connection URL, or None when the store is still file-backed.

    Read from config.env rather than the environment, for the same reason the
    Healthchecks URL is: a launchd service inherits almost nothing, and a
    secret that only works when someone's shell exported it is a secret that
    works interactively and fails at 3am.
    """
    path = Path(config_path) if config_path else CONFIG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == URL_NAME:
            return value.strip() or None
    return None


def connect(url=None):
    """One connection. Callers hold it for a whole operation, not per row."""
    url = url or database_url()
    if not url:
        raise DatabaseError(
            f"{URL_NAME} is not set in {CONFIG}. "
            "Add it with: python3 scripts/worker_key.py set-database")
    try:
        import psycopg
    except ImportError as exc:                       # pragma: no cover
        raise DatabaseError(
            "the psycopg driver is not installed: pip install 'psycopg[binary]'"
        ) from exc
    return psycopg.connect(url, connect_timeout=20)


SCHEMA = """
create table if not exists jobs (
    id                text primary key,
    company           text,
    norm_company      text,
    title             text,
    norm_title        text,
    state             text not null default 'new',
    canonical_url     text,
    canonical_source  text,
    source_class      text,
    country           text,
    workplace         text,
    score             integer,
    posted_at         timestamptz,
    first_seen_at     timestamptz,
    last_seen_at      timestamptz,
    closed_at         timestamptz,
    record            jsonb not null
);

-- The board's own three questions: what is new and recent, what is new and
-- scores well, and what do we hold for this employer on this platform. The
-- third is the staleness sweep's, and it is the one that would table-scan.
create index if not exists jobs_state_posted  on jobs (state, posted_at desc nulls last);
create index if not exists jobs_state_score   on jobs (state, score desc nulls last);
create index if not exists jobs_board         on jobs (canonical_source, norm_company);
create index if not exists jobs_url           on jobs (canonical_url);

-- The decision store. Small, and the half that must never be lost: what the
-- person watched, dropped, applied to. Kept in the same database as the pool
-- because almost every question worth asking joins across both.
create table if not exists sessions (
    application_id text primary key,
    status         text,
    company        text,
    role           text,
    updated_at     timestamptz,
    record         jsonb not null
);

create table if not exists history (
    id             bigserial primary key,
    application_id text,
    event          text,
    at             timestamptz,
    record         jsonb not null
);
create index if not exists history_application on history (application_id, at);

create table if not exists roles (
    application_id text primary key,
    company        text,
    role           text,
    url            text,
    record         jsonb not null
);

create table if not exists companies (
    key      text primary key,
    name     text,
    ats      text,
    token    text,
    record   jsonb not null
);
create index if not exists companies_ats on companies (ats, token);

create table if not exists answers (
    slug    text primary key,
    record  jsonb not null
);

create table if not exists passed (
    id      text primary key,
    record  jsonb not null
);
"""

# Which record fields become real columns. Kept as data rather than spelled out
# in the insert, so the lifted set and the schema cannot drift apart silently.
LIFTED = (
    ("company",          lambda j: j.get("company")),
    ("norm_company",     lambda j: _norm_company(j.get("company"))),
    ("title",            lambda j: j.get("title")),
    ("norm_title",       lambda j: j.get("normTitle") or jobs_store.norm_title(j.get("title"))),
    ("state",            lambda j: j.get("state") or "new"),
    ("canonical_url",    lambda j: j.get("canonicalUrl")),
    ("canonical_source", lambda j: j.get("canonicalSource")),
    ("source_class",     lambda j: jobs_store._class_of(j)),
    ("country",          lambda j: ((j.get("location") or {}).get("country") or "").upper() or None),
    ("workplace",        lambda j: j.get("workplace")),
    ("score",            lambda j: _score_of(j)),
    ("posted_at",        lambda j: jobs_store.parse_stamp(j.get("postedAt"))),
    ("first_seen_at",    lambda j: jobs_store.parse_stamp(j.get("firstSeenAt"))),
    ("last_seen_at",     lambda j: jobs_store.parse_stamp(j.get("lastSeenAt"))),
    ("closed_at",        lambda j: jobs_store.parse_stamp(j.get("closedAt"))),
)

_WORD = re.compile(r"[^a-z0-9]+")


def _norm_company(name):
    return _WORD.sub(" ", (name or "").casefold()).strip() or None


def _score_of(job):
    total = (job.get("score") or {}).get("total")
    return int(total) if isinstance(total, (int, float)) else None


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def _row(job_id, job):
    return [str(job_id)] + [fn(job) for _name, fn in LIFTED] + [json.dumps(job)]


def replace_jobs(conn, jobs, batch=2000):
    """Write every job, in batches. Returns how many rows were written.

    `executemany` over one prepared statement rather than a statement per row:
    at 60 ms a round trip, a per-row insert of 15,400 records is fifteen
    minutes, and this is seconds.
    """
    columns = ["id"] + [name for name, _ in LIFTED] + ["record"]
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "id")
    sql = (f"insert into jobs ({', '.join(columns)}) values ({placeholders}) "
           f"on conflict (id) do update set {updates}")
    written = 0
    items = list(jobs.items())
    with conn.cursor() as cur:
        for start in range(0, len(items), batch):
            chunk = items[start:start + batch]
            cur.executemany(sql, [_row(k, v) for k, v in chunk])
            written += len(chunk)
    conn.commit()
    return written


def _replace_simple(conn, table, key_column, rows, extra=()):
    """One small table, replaced wholesale. Used for the decision store."""
    columns = [key_column] + [name for name, _ in extra] + ["record"]
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != key_column)
    sql = (f"insert into {table} ({', '.join(columns)}) values ({placeholders}) "
           f"on conflict ({key_column}) do update set {updates}")
    payload = []
    for key, record in rows:
        payload.append([str(key)] + [fn(record) for _n, fn in extra] + [json.dumps(record)])
    if payload:
        with conn.cursor() as cur:
            cur.executemany(sql, payload)
    conn.commit()
    return len(payload)


def counts(conn):
    """Row counts per table. The cheapest possible proof a migration landed."""
    out = {}
    with conn.cursor() as cur:
        for table in ("jobs", "sessions", "history", "roles", "companies",
                      "answers", "passed"):
            cur.execute(f"select count(*) from {table}")
            out[table] = cur.fetchone()[0]
    return out


def state_census(conn):
    with conn.cursor() as cur:
        cur.execute("select state, count(*) from jobs group by state order by 2 desc")
        return dict(cur.fetchall())
