#!/usr/bin/env python3
"""Where the live schema and the repository's migrations disagree.

**Three separate drifts were found by tripping over them, one at a time**, and
that is what this exists to stop. On 2026-09-02: migration 0008 recorded as
applied with no file anywhere; `board_meta` present with no migration creating
it; and `provider_links` holding five columns no migration declares while
missing one that 0002 does. Each was discovered by a different piece of work
failing for a different reason, and each looked like an isolated incident until
the third.

The three are one property: **the schema in the database is not the schema in
the repository, and nothing was measuring the difference.** `migrate.py status`
compares the *migrations table* against the *migrations directory*, which is a
narrower question -- it answers "has every file been applied" and is silent
about whether applying them would produce what is actually there.

So this compares the shapes. It applies every migration to a scratch schema on
the same database, reads both catalogs, and reports the difference table by
table and column by column.

    python3 scripts/schema_drift.py                 against JOBBY_DATABASE_URL
    python3 scripts/schema_drift.py --url ...       against another

Read-only with respect to the target schema. The scratch schema it builds is
created, read and dropped.

It exits 1 on any drift, so it can gate a deploy. **A clean exit means the
repository can rebuild what production has**, which is the only useful sense of
"the migrations are up to date".
"""

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packages" / "schema"))

import migrate  # noqa: E402

#: Tables that predate the migration system and are deliberately not declared.
#:
#: **Named individually, with the reason, rather than pattern-matched.** These
#: are the pre-section-3 tables `class_b` and the legacy board still read;
#: CLAUDE.md describes `roles` as frozen at whatever the backfill left. A
#: pattern would also swallow the next undeclared table, which is the thing
#: this script is for.
LEGACY = {
    "jobs": "the pre-section-3 pool table. Superseded by `postings`.",
    "companies": "pre-section-3. Superseded by `employers`.",
    "history": "pre-section-3. Superseded by `application_events`.",
    "roles": "pre-section-3 role index, frozen at 196 rows and maintained by "
             "nothing. `v2_lanes` refuses to join it for that reason.",
    "sessions": "pre-section-3 session table. Superseded by `applications`.",
    "passed": "pre-section-3 pass list. Superseded by `job_decisions`.",
    "schema_migrations": "the ledger itself.",
}


def columns_of(conn, schema):
    """{table: {column: type}} for one schema."""
    out = {}
    with conn.cursor() as cur:
        cur.execute(
            """select table_name, column_name, data_type
                 from information_schema.columns
                where table_schema = %s
                order by table_name, ordinal_position""", (schema,))
        for table, column, kind in cur.fetchall():
            out.setdefault(table, {})[column] = kind
    return out


def rebuild(conn, schema, directory=None):
    """Apply every migration into a scratch schema and return its shape."""
    with conn.cursor() as cur:
        cur.execute(f'drop schema if exists "{schema}" cascade')
        cur.execute(f'create schema "{schema}"')
        cur.execute(f'set search_path to "{schema}"')
    conn.commit()
    try:
        migrate.run(conn, directory or migrate.MIGRATIONS, False)
        conn.commit()
        return columns_of(conn, schema)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{schema}" cascade')
            cur.execute("set search_path to public")
        conn.commit()


def drift(live, declared):
    """Every disagreement, as a list of dicts. Never a boolean."""
    findings = []
    for table in sorted(set(live) - set(declared)):
        if table in LEGACY:
            continue
        findings.append({"kind": "table_not_declared", "table": table,
                         "columns": sorted(live[table])})
    for table in sorted(set(declared) - set(live)):
        findings.append({"kind": "table_not_created", "table": table})
    for table in sorted(set(live) & set(declared)):
        if table in LEGACY:
            continue
        here, there = live[table], declared[table]
        for column in sorted(set(here) - set(there)):
            findings.append({"kind": "column_not_declared", "table": table,
                             "column": column, "type": here[column]})
        for column in sorted(set(there) - set(here)):
            findings.append({"kind": "column_not_created", "table": table,
                             "column": column, "type": there[column]})
        for column in sorted(set(here) & set(there)):
            if here[column] != there[column]:
                findings.append({"kind": "type_differs", "table": table,
                                 "column": column, "live": here[column],
                                 "declared": there[column]})
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url")
    ap.add_argument("--schema", default="drift_check",
                    help="scratch schema to build the declared shape in")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    conn = migrate.connect(args.url, announce=False)
    live = columns_of(conn, "public")
    declared = rebuild(conn, args.schema)
    findings = drift(live, declared)

    if args.json:
        import json
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            if f["kind"] == "table_not_declared":
                print(f"  table only in the database  {f['table']} "
                      f"({len(f['columns'])} columns)")
            elif f["kind"] == "table_not_created":
                print(f"  table only in the migrations {f['table']}")
            elif f["kind"] == "column_not_declared":
                print(f"  column only in the database  "
                      f"{f['table']}.{f['column']} :: {f['type']}")
            elif f["kind"] == "column_not_created":
                print(f"  column only in the migrations "
                      f"{f['table']}.{f['column']} :: {f['type']}")
            else:
                print(f"  type differs                 "
                      f"{f['table']}.{f['column']} "
                      f"live={f['live']} declared={f['declared']}")
        print()
        print(f"{len(findings)} drifts, over {len(live)} live tables and "
              f"{len(declared)} the migrations build")
        if not findings:
            print("the repository can rebuild what this database has")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
