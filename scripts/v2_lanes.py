#!/usr/bin/env python3
"""The lane queries, written once and generated into TypeScript.

Two implementations of one query is the failure `LogoContractTests` exists for,
so there is one: this module owns the SQL, `sync_v2_styles` emits it as a TS
module, and `apps/web` executes the string rather than composing its own.

**`nulls last` on every lane, and the keyset has to agree with it.** PostgreSQL
sorts nulls FIRST under `order by ... desc`, so a newest-first lane without it
opens with every posting whose date nobody stated -- 2,087 of them on the
corpus as measured 2026-08-30. And an index whose ordering differs from the
query's cannot serve it, so the lane silently falls back to sorting the whole
corpus: `postings_keyset` is declared `(posted_at DESC NULLS LAST, id DESC)`
and the queries below match it exactly.

**The keyset predicate cannot be a row comparison.** `(posted_at, id) < (?, ?)`
is the usual shape and it is wrong here, because a NULL on either side makes
the comparison NULL rather than true, so the walk stops dead at the first
undated row and every row after it is unreachable. The predicate is spelled out
instead, in the three cases that a nulls-last ordering actually has:

    cursor is dated      -> take rows dated earlier, all undated rows, and
                            rows on the same date with a smaller id
    cursor is undated    -> only undated rows with a smaller id, because
                            everything dated sorts before them

**Found is the absence of a decision row.** Not a stored state: it is
`postings` minus this account's decisions, which is what keeps it free at
500k rather than a per-account materialisation. That is also why `found` is not
a storable value and the schema refuses it.
"""

import argparse
import json
import sys

#: Every lane, its predicate, and whether it needs the decisions join.
#:
#: `dropped` is here as a lane because it is a query; it is not a sixth tab.
#: The board surfaces it through the archive dialog, and CLAUDE.md says why.
LANES = {
    "found": {
        "label": "Found",
        # Absence of a decision row, and open. LEFT JOIN ... IS NULL rather
        # than NOT EXISTS so one plan shape serves every lane.
        "where": "p.closed_at is null and d.posting_id is null",
    },
    "watch":  {"label": "Watching",  "where": "d.state = 'watching'"},
    "flight": {"label": "In flight", "where": "d.state in ('queued', 'applying')"},
    "act":    {"label": "Needs you", "where": "d.state = 'needs_you'"},
    "done":   {"label": "Submitted", "where": "d.state = 'submitted'"},
    "dropped": {"label": "Dropped",  "where": "d.state = 'dropped'"},
}

COLUMNS = """p.id, p.legacy_id, p.title, p.location_raw, p.remote, p.workplace,
             p.posted_at, p.apply_url, p.years_min, p.years_max, p.summary,
             p.source, e.canonical_name as employer, e.domain as employer_domain,
             e.logo_url as employer_logo"""

#: The three cases a nulls-last keyset has. `%(after_at)s` NULL means "the
#: cursor row was undated", which is a different question from "no cursor".
#: Casts are required, not decoration. A bare NULL parameter has no type, and
#: `$2 is null` makes Postgres give up with "could not determine data type" --
#: which is the first page of every lane, so nothing would have worked at all.
KEYSET = """(
      %(after_id)s::uuid is null
   or (%(after_at)s::timestamptz is not null and (
            p.posted_at < %(after_at)s::timestamptz
         or p.posted_at is null
         or (p.posted_at = %(after_at)s::timestamptz
             and p.id < %(after_id)s::uuid)))
   or (%(after_at)s::timestamptz is null and p.posted_at is null
       and p.id < %(after_id)s::uuid)
)"""

ORDER = "order by p.posted_at desc nulls last, p.id desc"


def page_sql(lane):
    """One page of a lane, newest first, undated last."""
    if lane not in LANES:
        raise KeyError(f"no lane {lane!r}")
    return f"""
select {COLUMNS}
  from postings p
  left join job_decisions d
    on d.posting_id = p.id and d.account_id = %(account)s
  left join employers e on e.id = p.employer_id
 where {LANES[lane]["where"]}
   and {KEYSET}
 {ORDER}
 limit %(limit)s
""".strip()


def count_sql(lane):
    """The lane's size. Separate from the page so a count never drags a page
    of columns across the wire with it."""
    return f"""
select count(*)
  from postings p
  left join job_decisions d
    on d.posting_id = p.id and d.account_id = %(account)s
 where {LANES[lane]["where"]}
""".strip()


def as_typescript():
    """The same strings, for `apps/web`, with psycopg's named placeholders
    rewritten to node-postgres' positional ones.

    Generated rather than rewritten by hand: two spellings of one query is the
    thing `LogoContractTests` exists to prevent, and a lane predicate is
    exactly the kind of text that gets edited on one side only.
    """
    order = ["account", "after_at", "after_id", "limit"]

    def positional(sql):
        for i, name in enumerate(order, start=1):
            sql = sql.replace(f"%({name})s", f"${i}")
        return sql

    lanes = {k: {"label": v["label"],
                 "page": positional(page_sql(k)),
                 "count": positional(count_sql(k).replace("%(limit)s", ""))}
             for k, v in LANES.items()}
    return (
        "// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
        "//\n"
        "// The lane SQL lives in scripts/v2_lanes.py and is emitted here so\n"
        "// there is one spelling of each predicate. Parameters are positional\n"
        "// for node-postgres, in the order [account, afterAt, afterId, limit].\n"
        "export type Lane = " + " | ".join(f'"{k}"' for k in LANES) + ";\n\n"
        "export const LANES: Record<Lane, { label: string; page: string; count: string }> =\n"
        + json.dumps(lanes, indent=2) + " as const;\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lane", default="found")
    ap.add_argument("--typescript", action="store_true")
    args = ap.parse_args(argv)
    print(as_typescript() if args.typescript else page_sql(args.lane))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
