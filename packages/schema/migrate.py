#!/usr/bin/env python3
"""Ordered, checksummed, expand-only schema migrations.

    python3 packages/schema/migrate.py status
    python3 packages/schema/migrate.py plan
    python3 packages/schema/migrate.py apply
    python3 packages/schema/migrate.py apply --allow-contract

The connection URL comes from `JOBBY_DATABASE_URL` in `~/.job-application/
config.env`, the same place `scripts/jobs_db.py` reads it from and for the same
reason: a launchd service inherits almost nothing, so a secret that only works
when somebody's shell exported it works interactively and fails at 3am.
`--url` overrides it and `JOBBY_TEST_DATABASE_URL` is what CI's container sets.

Three properties, in the order they matter.

**Expand-only by default.** §7 of the rebuild plan says a destructive migration
never ships in the same release as the code that stops reading the column,
because a runner in the field may be a version behind for minutes. That policy
is worth nothing as prose, so it is a refusal here: a migration containing a
drop, a truncate, a rename or a type change is rejected unless the file itself
carries `-- jobby:contract <reason>` *and* the operator passes
`--allow-contract`. Either half alone is not enough. The file's declaration is
what a reviewer sees in the diff; the flag is what stops a contract migration
riding along unnoticed in an ordinary deploy.

**Nothing half-applies.** Each migration runs inside one transaction together
with its own ledger row, so a failure leaves neither the DDL nor a record
claiming it succeeded. Postgres gives this away for free because its DDL is
transactional -- but only to a runner that does not commit the schema change and
then record it, which is a window wide enough to be the whole bug.

**The database and the repo have to agree.** Every applied migration is stored
with the SHA-256 of the file that produced it. A file edited after it was
applied, a file deleted after it was applied, and a migration numbered below one
already applied are all refused. Each of those makes the schema unreproducible
from the repository, and each is silent in a runner that only asks "which
versions have I not seen yet".

The destructive scanner does not read the file as text. Comments, string
literals and dollar-quoted bodies are removed first, then each statement is
matched with word boundaries. `dropped_at` is a column, `-- we drop this in
N+2` is a note, and `values ('drop table jobs')` is data; a scanner keyed on
`"drop" in sql` refuses all three, and refusing honest migrations is how a
guard gets weakened until it stops binding. `tests/test_migration_guards.py`
holds both directions.
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIGRATIONS = HERE / "migrations"
CONFIG = Path("~/.job-application/config.env").expanduser()
URL_NAME = "JOBBY_DATABASE_URL"

# The advisory-lock namespace. Any constant works; it only has to be the same
# constant in every runner, which is the point of it being here rather than
# derived from something a caller passes in.
LOCK_NAMESPACE = "jobby.schema.migrations"

# The PostgreSQL major version this schema is written for, and the one CI must
# test against. Neon reported 18.6 on 2026-08-29.
#
# One constant rather than a comparison between the two servers, because CI
# cannot reach Neon and must not: giving a runner the production URL to check a
# version number would put the database holding real applications one typo away
# from every branch. So the pin is the contract, and each side is checked
# against it where it is reachable. `tests/test_postgres_version_is_pinned.py`
# holds all three legs, including the workflow's own image tag, which is the
# one that needs no database at all.
#
# Raising this is a deliberate act: Phase 1's schema uses a generated stored
# column and partial indexes, and later phases will use more. A container one
# major behind the endpoint accepts DDL that production rejects, and the first
# anyone hears of it is a failed migrate step in a deploy.
POSTGRES_MAJOR = 18


def server_major(conn):
    """The connected server's major version, from `server_version_num`.

    Not `show server_version`, which is a display string: Neon appends a build
    hash ("18.6 (c5250a2)") and parsing it is a second thing to get wrong.
    `server_version_num` is an integer, 180006 for 18.6, major = n // 10000.
    """
    with conn.cursor() as cur:
        cur.execute("show server_version_num")
        return int(cur.fetchone()[0]) // 10000

NAME_RE = re.compile(r"^(\d{4})_[A-Za-z0-9][A-Za-z0-9_]*\.sql$")
CONTRACT_RE = re.compile(r"jobby:contract\b(?P<reason>.*)")


class MigrationError(Exception):
    """A refusal. Never raised for a migration that merely has not run yet."""


class Migration:
    __slots__ = ("version", "name", "path", "sql", "checksum")

    def __init__(self, version, name, path, sql):
        self.version = version
        self.name = name
        self.path = path
        self.sql = sql
        self.checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

    def __repr__(self):                                  # pragma: no cover
        return f"<Migration {self.name}>"


# --------------------------------------------------------------------------
# Reading SQL as SQL, rather than as text that happens to contain keywords
# --------------------------------------------------------------------------

def split_code_and_comments(sql):
    """Return (code, comments).

    `code` is the statement text with every comment, string literal and
    dollar-quoted body replaced by a space, so the scanner sees only things
    that execute. `comments` is the comment text alone, which is where a
    `-- jobby:contract` declaration has to live to count.

    Written as one pass rather than a chain of regexes because the constructs
    nest: a `--` inside a string literal opens no comment, and a quote inside a
    comment opens no string. Two independent passes disagree about which of
    those came first, and the disagreement is exploitable.
    """
    code, comments = [], []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]

        if ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            comments.append(sql[i + 2:end])
            code.append(" ")
            i = end
            continue

        if ch == "/" and sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            comments.append(sql[i + 2:max(i + 2, j - 2)])
            code.append(" ")
            i = j
            continue

        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2                            # escaped quote
                        continue
                    j += 1
                    break
                j += 1
            code.append(" ")
            i = j
            continue

        if ch == '"':                                     # quoted identifier
            j = sql.find('"', i + 1)
            j = n if j == -1 else j + 1
            code.append(sql[i:j])
            i = j
            continue

        if ch == "$":
            tag = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
            if tag:
                token = tag.group(0)
                end = sql.find(token, i + len(token))
                code.append(" ")
                i = n if end == -1 else end + len(token)
                continue

        code.append(ch)
        i += 1

    return "".join(code), "\n".join(comments)


# Each entry is a thing that destroys data, destroys a shape a live reader may
# depend on, or rewrites a column in place. `drop default` is deliberately not
# here: it changes no stored row, and refusing it would block the ordinary
# expand step of making a backfilled column required.
_DESTRUCTIVE = [
    (re.compile(r"\bdrop\s+(?:table|schema|database|index|view|"
                r"materialized\s+view|column|constraint|type|sequence|"
                r"function|trigger|extension)\b", re.I), "drops an object"),
    (re.compile(r"\bdrop\s+not\s+null\b", re.I), "drops a not-null constraint"),
    (re.compile(r"\btruncate\b", re.I), "truncates a table"),
    (re.compile(r"\brename\s+(?:to|column|constraint)\b", re.I),
     "renames an object a live reader may still use"),
    (re.compile(r"\balter\s+column\s+(?:\"[^\"]+\"|\w+)\s+"
                r"(?:set\s+data\s+)?type\b", re.I), "rewrites a column type"),
]

_DELETE_RE = re.compile(r"\bdelete\s+from\b", re.I)
_WHERE_RE = re.compile(r"\bwhere\b", re.I)


def destructive_statements(sql):
    """Every statement in `sql` that is not expand-only, in file order.

    Returns statement text, not a boolean, because a runner that says "this
    migration is destructive" and stops has told whoever is fixing it that
    there was one problem.
    """
    code, _ = split_code_and_comments(sql)
    found = []
    for raw in code.split(";"):
        statement = " ".join(raw.split())
        if not statement:
            continue
        if any(pattern.search(statement) for pattern, _ in _DESTRUCTIVE):
            found.append(statement)
            continue
        # An unqualified DELETE empties the table. With a WHERE it is an
        # ordinary data fix, which migrations legitimately do.
        if _DELETE_RE.search(statement) and not _WHERE_RE.search(statement):
            found.append(statement)
    return found


def contract_declaration(sql):
    """The `-- jobby:contract <reason>` reason, or None.

    Read from the comments alone. A declaration inside a string literal is data
    that happens to spell the token, and honouring it would let any migration
    smuggle a contract past review.
    """
    _, comments = split_code_and_comments(sql)
    match = CONTRACT_RE.search(comments)
    if not match:
        return None
    return match.group("reason").strip(" \t:-")


def check_expand_only(name, sql, allow_contract=False):
    """Raise unless `sql` may be applied. Silent when there is nothing to say."""
    bad = destructive_statements(sql)
    if not bad:
        return

    shown = "; ".join(bad[:3]) + (" ..." if len(bad) > 3 else "")
    reason = contract_declaration(sql)

    if reason is None:
        if allow_contract:
            raise MigrationError(
                f"{name} contains destructive statements but carries no "
                f"`-- jobby:contract <reason>` declaration, so --allow-contract "
                f"does not cover it: {shown}")
        raise MigrationError(
            f"{name} is not expand-only: {shown}. Split it per "
            f"docs/migrations.md, or if the contract half is genuinely due, "
            f"declare it with `-- jobby:contract <reason>` and re-run with "
            f"--allow-contract.")

    if not reason:
        raise MigrationError(
            f"{name} declares `jobby:contract` with no reason. Name the "
            f"release that stopped reading it, so the next person can check "
            f"the expand half actually shipped first.")

    if not allow_contract:
        raise MigrationError(
            f"{name} is a contract migration ({reason}). Contract migrations "
            f"never ride along in an ordinary deploy: re-run with "
            f"--allow-contract when the expand half is live everywhere.")


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

_CONSTRAINT_WORDS = ("constraint", "primary", "unique", "check", "foreign",
                     "exclude", "like")


def _balanced(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    raise MigrationError("unbalanced parentheses in a create table")


def _split_top_level(body):
    depth, item, out = 0, [], []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(item))
            item = []
        else:
            item.append(ch)
    if "".join(item).strip():
        out.append("".join(item))
    return out


def declared_tables(directory=None):
    """{table: {columns}} that the migrations claim to create.

    Parsed out of the files rather than written down beside them, because a
    hand-maintained list is a second declaration and it can be wrong in the
    same direction as the first. Comments are stripped first: 0005's prose
    quotes the very `create table` statement it exists to replace, and a
    scanner reading raw text reports 0005 as the problem it fixes.

    This exists because `create table if not exists` matches on the name and
    not the shape. `public.answers` was a legacy `(slug, record)` table, so
    0002's declaration of `answers` did nothing and was recorded as applied.
    Comparing this against `information_schema.columns` is the check that
    catches the class rather than the instance.
    """
    tables = {}
    for migration in discover(directory):
        code, _comments = split_code_and_comments(migration.sql)
        for match in re.finditer(
                r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)\s*\(",
                code, re.I):
            body = _balanced(code, match.end() - 1)
            columns = set()
            for item in _split_top_level(body):
                item = item.strip()
                if not item:
                    continue
                if item.split()[0].lower().strip('"') in _CONSTRAINT_WORDS:
                    continue
                columns.add(item.split()[0].strip('"'))
            tables.setdefault(match.group(1), set()).update(columns)
        for match in re.finditer(
                r"alter\s+table\s+(\w+)\s+add\s+column\s+"
                r"(?:if\s+not\s+exists\s+)?(\w+)", code, re.I):
            tables.setdefault(match.group(1), set()).add(match.group(2))
    return tables


def parse_version(name):
    match = NAME_RE.match(name)
    if not match:
        raise MigrationError(
            f"{name} is not a migration filename. They are NNNN_name.sql, "
            f"four digits, e.g. 0007_add_job_scores.sql.")
    version = int(match.group(1))
    if version == 0:
        raise MigrationError(
            f"{name} uses version 0, which is reserved: zero is the value an "
            f"unparsed integer takes, and a failed parse must never be able "
            f"to look like a real migration. Number from 0001.")
    return version


def discover(directory=None):
    """Every migration in `directory`, ordered by version.

    Refuses a directory it cannot read, a file it does not recognise, a
    duplicate version and an empty migration. Ignoring an unrecognised file is
    how a migration named wrongly never runs, and nobody finds out until the
    column is missing in production.
    """
    directory = Path(directory) if directory else MIGRATIONS
    if not directory.is_dir():
        raise MigrationError(f"{directory} is not a directory")

    found = {}
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.is_dir():
            continue
        version = parse_version(path.name)               # raises on a stray
        try:
            sql = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MigrationError(f"{path.name} could not be read: {exc}") from exc

        code, _ = split_code_and_comments(sql)
        if not code.strip().strip(";"):
            raise MigrationError(
                f"{path.name} has no statements. An empty migration takes a "
                f"version number that can never be reused.")

        if version in found:
            raise MigrationError(
                f"two migrations share version {version:04d}: "
                f"{found[version].name} and {path.name}. One of them will "
                f"never run.")
        found[version] = Migration(version, path.name, path, sql)

    return [found[v] for v in sorted(found)]


# --------------------------------------------------------------------------
# The database side
# --------------------------------------------------------------------------

LEDGER_DDL = """
create table if not exists schema_migrations (
  version    integer     primary key,
  name       text        not null,
  checksum   text        not null,
  applied_at timestamptz not null default now(),
  applied_by text        not null default current_user
)
"""


def database_url(config_path=None):
    """The connection URL, or None."""
    for name in ("JOBBY_TEST_DATABASE_URL", URL_NAME):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    path = Path(config_path) if config_path else CONFIG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == URL_NAME:
            return value.strip() or None
    return None


def is_pooled(url):
    """Whether `url` points at a connection pooler.

    Neon names its pooled endpoint `<endpoint>-pooler.<region>...`, and the
    generic PgBouncer signal is `pgbouncer=true` in the query string.
    """
    from urllib.parse import urlsplit, parse_qsl
    parts = urlsplit(url)
    host = (parts.hostname or "")
    if "-pooler." in host or host.endswith("-pooler"):
        return True
    return any(k == "pgbouncer" and v.lower() in ("true", "1")
               for k, v in parse_qsl(parts.query))


def direct_url(url):
    """The same database, reached without the pooler."""
    from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = re.sub(r"-pooler(?=\.|$)", "", host)
    netloc = ""
    if parts.username:
        netloc += parts.username
        if parts.password:
            netloc += f":{parts.password}"
        netloc += "@"
    netloc += host
    if parts.port:
        netloc += f":{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != "pgbouncer"]
    return urlunsplit((parts.scheme, netloc, parts.path,
                       urlencode(query), parts.fragment))


def connect(url=None, announce=True):
    """A connection fit to migrate through. Never a pooled one.

    A transaction pooler hands each transaction whatever backend session is
    free, which breaks two things this runner depends on and breaks them
    quietly. `pg_advisory_lock` is session-scoped, so the lock would be taken
    on one backend and the migrations run on another -- the lock would appear
    to work and would exclude nobody. And a `SET` outside a transaction lands
    on a session the next statement may not get, which is how an earlier
    version of the live tests created tables in `public` on the developer's
    database while asserting they were in a scratch schema.

    Neon refuses `options=-c search_path=...` on its pooled endpoint outright
    and its error names the fix: use an unpooled connection. So the rewrite is
    done here, and said out loud, because a connection URL silently pointing
    somewhere other than where the operator typed is worse than the friction of
    telling them.
    """
    url = url or database_url()
    if not url:
        raise MigrationError(
            f"{URL_NAME} is not set in {CONFIG} and no --url was given")
    if is_pooled(url):
        url = direct_url(url)
        if announce:
            print("migrate: the configured URL is pooled; migrations need a "
                  "session, so connecting to the direct endpoint instead",
                  file=sys.stderr)
    try:
        import psycopg
    except ImportError as exc:                           # pragma: no cover
        raise MigrationError(
            "the psycopg driver is not installed: pip install 'psycopg[binary]'"
        ) from exc
    return psycopg.connect(url, autocommit=True)


def _lock_key(conn):
    """A stable bigint for the advisory lock, scoped to the current schema.

    Scoped rather than global so a test schema and production do not serialise
    against each other, and so two databases sharing a Postgres instance do not
    either. Postgres advisory locks are per-database, not per-schema, so the
    schema has to be in the key explicitly.
    """
    with conn.cursor() as cur:
        cur.execute("select current_schema()")
        schema = cur.fetchone()[0] or "public"
    digest = hashlib.sha256(f"{LOCK_NAMESPACE}:{schema}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def applied(conn):
    """version -> {name, checksum, applied_at, applied_by}, creating the
    ledger if it does not exist. An empty dict means no migration has run; it
    never means the query failed, because a failure raises."""
    with conn.cursor() as cur:
        cur.execute(LEDGER_DDL)
        cur.execute("select version, name, checksum, applied_at, applied_by "
                    "from schema_migrations order by version")
        return {r[0]: {"name": r[1], "checksum": r[2],
                       "applied_at": r[3], "applied_by": r[4]}
                for r in cur.fetchall()}


def _reconcile(known, have):
    """Refuse every way the repo and the database can disagree.

    Runs before anything is applied, so a run that is going to be refused does
    not first half-change the schema.
    """
    by_version = {m.version: m for m in have}

    for version in sorted(known):
        record = known[version]
        migration = by_version.get(version)
        if migration is None:
            raise MigrationError(
                f"migration {version:04d} ({record['name']}) is recorded as "
                f"applied but is not in the migrations directory. The schema "
                f"can no longer be reproduced from the repository; restore "
                f"the file rather than deleting the row.")
        if migration.checksum != record["checksum"]:
            raise MigrationError(
                f"migration {version:04d} ({migration.name}) has changed since "
                f"it was applied. An applied migration is history: add a new "
                f"one instead of editing this.")

    high_water = max(known) if known else 0
    for migration in have:
        if migration.version in known:
            continue
        if migration.version < high_water:
            raise MigrationError(
                f"migration {migration.version:04d} ({migration.name}) is "
                f"below {high_water:04d}, which has already been applied. "
                f"Renumber it above the high-water mark: applying it now "
                f"produces a schema no fresh database will reproduce.")


def status_rows(conn, directory=None):
    """(version, name, state) per migration, and whether the two agree.

    States: `pending`, an ISO timestamp, `CHANGED SINCE APPLIED`,
    `MISSING FROM DISK`. `agrees` is false for either of the last two.

    Separate from `main` so the exit code can be tested without running the
    CLI, which connects with the default search_path and would do its work in
    `public` on the developer's live database. A `status` that printed
    `CHANGED SINCE APPLIED` and exited 0 shipped in the first version of this
    file, and was found by checking the exit code rather than the output: a
    pipeline gated on `status` would have called a divergent schema healthy.
    """
    known = applied(conn)
    have = discover(directory)
    rows, agrees = [], True

    for migration in have:
        record = known.get(migration.version)
        if record is None:
            state = "pending"
        elif record["checksum"] != migration.checksum:
            state = "CHANGED SINCE APPLIED"
            agrees = False
        else:
            state = record["applied_at"].isoformat(timespec="seconds")
        rows.append((migration.version, migration.name, state))

    for version in sorted(set(known) - {m.version for m in have}):
        rows.append((version, known[version]["name"], "MISSING FROM DISK"))
        agrees = False

    rows.sort(key=lambda r: r[0])
    return rows, agrees


def plan(conn, directory=None, allow_contract=False):
    """What `run` would apply, without applying it. Same refusals."""
    have = discover(directory)
    for migration in have:
        check_expand_only(migration.name, migration.sql, allow_contract)
    known = applied(conn)
    _reconcile(known, have)
    return [m for m in have if m.version not in known]


def run(conn, directory=None, allow_contract=False):
    """Apply everything pending. Returns the migrations applied, in order."""
    have = discover(directory)

    # Every check that can be made without the database is made before the
    # lock is taken, and every check that needs it is made before the first
    # statement runs. A refusal must never leave the schema half-moved.
    for migration in have:
        check_expand_only(migration.name, migration.sql, allow_contract)

    key = _lock_key(conn)
    with conn.cursor() as cur:
        cur.execute("select pg_advisory_lock(%s)", (key,))
    try:
        known = applied(conn)
        _reconcile(known, have)

        done = []
        for migration in have:
            if migration.version in known:
                continue
            # One transaction for the DDL and the ledger row together. A
            # runner that commits the schema change and then records it has a
            # window in which a crash leaves the change applied and unrecorded,
            # and the next run then applies it again.
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(migration.sql)
                    cur.execute(
                        "insert into schema_migrations (version, name, checksum)"
                        " values (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum))
            done.append(migration)
        return done
    finally:
        # Released whatever happened. A runner that dies holding this wedges
        # every later deploy until somebody finds the session and kills it.
        with conn.cursor() as cur:
            cur.execute("select pg_advisory_unlock(%s)", (key,))


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("status", "plan", "apply"))
    parser.add_argument("--url", help="override the connection URL")
    parser.add_argument("--dir", help="override the migrations directory")
    parser.add_argument("--allow-contract", action="store_true",
                        help="permit migrations declaring `jobby:contract`")
    args = parser.parse_args(argv)

    directory = Path(args.dir) if args.dir else MIGRATIONS
    try:
        conn = connect(args.url)
    except MigrationError as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "status":
            rows, agrees = status_rows(conn, directory)
            for version, name, state in rows:
                print(f"{version:04d}  {name:<44} {state}")
            # Pending is normal and exits 0. Drift and a missing file are the
            # database and the repository disagreeing, which is never normal.
            return 0 if agrees else 1

        if args.command == "plan":
            pending = plan(conn, directory, args.allow_contract)
            if not pending:
                print("nothing pending")
            for migration in pending:
                print(f"would apply {migration.version:04d}  {migration.name}")
            return 0

        done = run(conn, directory, args.allow_contract)
        if not done:
            print("nothing pending")
        for migration in done:
            print(f"applied {migration.version:04d}  {migration.name}")
        return 0
    except MigrationError as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
