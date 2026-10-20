#!/usr/bin/env python3
"""The job pool and the decision store, in Postgres.

Why this exists. `jobs.json` was 97 MB for 15,400 records and every process
that touched it loaded the whole thing: ingest, sync_board and the old
board's publish.
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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity  # noqa: E402
import jobs_store  # noqa: E402

URL_NAME = "JOBBY_DATABASE_URL"


def config_path_default():
    """Where this process's credentials live, resolved on every call.

    **Through the store root, never `Path.home()` frozen at import.** A process
    told to operate on store X must not read the credentials of store Y, and
    until 2026-08-30 every one of them did: `CONFIG` was
    `Path("~/.job-application/config.env").expanduser()` evaluated when the
    module loaded, so no environment variable could reach it afterwards.

    That is what let a test reach the live database through a child process.
    `finish.py` already exports its `--root` as `JOB_APPLICATION_DIR`, so a
    child handed a temporary store had its store redirected correctly and read
    the real `config.env` anyway. `tests/test_brief_finish.py` spawns exactly
    that child, and it wrote six `submitting` rows into the device's Postgres
    on a run where the in-process guard was live and firing 885 times, because
    a subprocess has a clean `sys.modules` and nothing in it looked like a
    test.

    The two halves are different guards for different holes and both are
    needed. `device_url_refusal` catches a test that connects in-process; this
    catches the work that test causes somewhere else.
    """
    return (Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application"))
            .expanduser() / "config.env")


class DatabaseError(Exception):
    pass


def database_url(config_path=None):
    """The connection URL, or None when the store is still file-backed.

    **The file first, and the reason is still the right one.** A launchd
    service inherits almost nothing, and a secret that only works when
    someone's shell exported it is a secret that works interactively and fails
    at 3am. Worse than failing: a service that picked up a stray exported URL
    would write to whatever database that names, which on this machine is the
    difference between the mirror and production.

    **Then the environment, because a container has no file.** The sourcing
    worker runs the same connectors in an image with no `config.env` and no
    home directory worth the name, and the environment is the only channel a
    platform has for a secret. File-first keeps every guarantee the device
    had -- with a file present the environment is never consulted -- and adds
    the one the container needs.

    **The environment path refuses under a test runner**, on exactly the
    reasoning `migrate.device_url_refusal` sets out: a test that reached the
    device's database wrote 39 rows into `applications`, emptied
    `answer_bank`, and restarted the parity clock. `HOME=$(mktemp -d)` covered
    that only incidentally, by making the file unreadable, and reading the
    environment would have walked straight past it. There is deliberately no
    variable to switch this off; a variable set once and forgotten is how a
    guard stops existing.
    """
    path = Path(config_path) if config_path else config_path_default()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == URL_NAME and value.strip():
            return value.strip()
    found = (os.environ.get(URL_NAME) or "").strip()
    if not found:
        return None
    if _under_a_test_runner():
        sys.stderr.write(
            f"jobs_db: refusing to read {URL_NAME} from the environment under a "
            f"test runner. Point JOBBY_TEST_DATABASE_URL at a throwaway "
            f"database, or pass an explicit url=.\n")
        sys.stderr.flush()
        return None
    return found


def _under_a_test_runner():
    return "unittest" in sys.modules or "pytest" in sys.modules


def connect(url=None):
    """One connection. Callers hold it for a whole operation, not per row."""
    url = url or database_url()
    if not url:
        raise DatabaseError(
            f"{URL_NAME} is not set in {config_path_default()}. "
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

def _norm_company(name, alias_map=None):
    """identity's normaliser, not a third one.

    This was a private regex, and it disagreed with `identity.norm_company` on
    56 of 894 registry companies: 'Adecco' became 'adecco' here and
    'adecco careers' there, and non-ASCII was destroyed ('Zurich' with an
    umlaut lost the letter, because [^a-z0-9]+ eats what isalnum() keeps).

    That matters because this value populates `jobs.norm_company`, and the
    `jobs_board` index over it is the staleness sweep's key.
    `jobs_store.close_missing` states the rule outright: "The company half of
    the key has to be normalised the same way the sweep normalised it, or
    nothing matches and this closes nothing forever." Two normalisers is one
    too many.
    """
    return identity.norm_company(name, alias_map) or None


def _score_of(job):
    """The score as an integer, or None. Never raises on junk.

    `jobs_store.parse_stamp` makes the same promise for the same reason: one
    malformed record must not take a migration down. It could -- Python's JSON
    decoder accepts NaN, and int(float('nan')) raises ValueError, which would
    abort the batch and roll back every good record with it. `bool` is
    excluded because it is an `int` subclass and True would otherwise store 1.
    """
    total = (job.get("score") or {}).get("total")
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        return None
    try:
        return int(total)
    except (ValueError, OverflowError):
        return None


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def _row(job_id, job):
    """One row. A field that cannot be lifted becomes NULL rather than an
    exception: the record itself still travels whole in `record`, and losing
    an index value on one job is not worth losing the other 15,399. The
    traceback from the first version named the lambda, not the job."""
    values = []
    for _name, fn in LIFTED:
        try:
            values.append(fn(job))
        except Exception:                        # noqa: BLE001 - see docstring
            values.append(None)
    return [str(job_id)] + values + [json.dumps(job)]


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
