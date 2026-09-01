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
#: Every lane and the predicate that defines it.
#:
#: **Found is a posting; every other lane is an application.** They are
#: different objects and the database does not link them --
#: `applications.posting_id` is NULL for all 224 rows, measured 2026-08-31 --
#: so no lane below can be derived from another, and the pool's `passed 9`
#: will never equal the applications' `dropped 57`. Those are a card you
#: pressed Pass on and work you abandoned after it existed.
#:
#: **`applications`, not the `sessions` table.** Both hold a status; parity
#: compares the files against `applications`, `applications` has all 89, and
#: the `sessions` table is a pre-§3 leftover that nothing reads and that is
#: already missing a row. See docs/plans/phase3-inputs.md section 7.
#:
#: **Three of the six states `job_decisions` permits are never written.**
#: `queued`, `applying` and `needs_you` are stages of work rather than opinions
#: about a posting, and the work lives in `applications`. The constraint stays
#: permissive; nothing should start writing them, because that is a mirror
#: change on a parity gate path buying a second copy of something applications
#: already hold correctly.
POSTING_LANES = {"found"}

LANES = {
    "found": {
        "label": "Found",
        # The absence of a decision row, and open. That is what keeps it free
        # at 500k: postings minus this account's decisions, never a per-account
        # materialisation. It is also why `found` is not a storable state.
        "where": "p.closed_at is null and d.posting_id is null",
    },
    "watch":  {"label": "Watching",  "where": "a.status = 'watch'"},
    "flight": {"label": "In flight", "where": "a.status = 'inflight'"},
    "act":    {"label": "Needs you", "where": "a.status in ('review', 'active')"},
    "done": {
        "label": "Submitted",
        # **Read the events, not a status.** A `completed` retracted by a later
        # `abandoned` was counted once as submitted and the role fell off the
        # board entirely -- it was neither submitted nor anywhere else. So the
        # lane asks whether a completion is the application's last word.
        #
        # `application_events`, which migration 0002 creates, and not `history`,
        # which is the pre-§3 `jobs_db` table holding the same rows. Both exist
        # on this database and only one is part of the schema; a lane reading
        # the legacy one works today and breaks on any clean deployment, which
        # is how the first version of this was written.
        "where": """exists (
            select 1 from application_events h
             where h.application_id = a.id and h.kind = 'completed'
               and not exists (
                 select 1 from application_events r
                  where r.application_id = a.id
                    and r.kind = 'abandoned' and r.at > h.at))""",
    },
    "dropped": {"label": "Dropped",  "where": "a.status = 'dropped'"},
}

POSTING_COLUMNS = """p.id, p.legacy_id, p.title, p.location_raw, p.remote,
             p.workplace, p.posted_at, p.apply_url, p.years_min, p.years_max,
             p.summary, p.source, p.salary_raw, e.canonical_name as employer,
             e.domain as employer_domain, e.logo_url as employer_logo"""

#: An application lane has no posting to draw from, because nothing links the
#: two. `updated_at` is the ordering: it is when the work last moved, which is
#: the question these lanes answer, and `posted_at` would be unavailable
#: anyway.
#: **An application is named by the posting it points at, or not at all.**
#:
#: `applications` holds no company or role of its own, and `posting_id` was
#: NULL for every row until `scripts/link_applications.py` filled in what could
#: be resolved. So the name comes through a LEFT join to the posting: present
#: for a linked application, null for the rest, and the row says which.
#:
#: Still never `roles`. It carries a company and a role on the same key and
#: would name 196 of 204, and it is a pre-§3 table that no migration creates
#: and nothing maintains: reading it would put a snapshot's content on the
#: board as though it were current. The join is LEFT rather than INNER for the
#: same reason the resolver refuses to guess -- an application whose posting
#: has been pruned must still appear in its lane.
#: `class_b._mirror_roles` says so in its own docstring and mirrors roles.json
#: as *applications* instead. The table is frozen at whatever the backfill left,
#: 196 rows, and a role written today does not reach it -- demonstrated by
#: writing one and watching the count stay at 196.
#:
#: So a lane joining it would look right on this machine and show nothing on a
#: clean deployment, which is the launchd PATH bug's shape. Until §3 carries a
#: name -- either company and role on `applications`, or the posting link of
#: section 5 -- the lanes show the legacy id, and that is the honest answer
#: rather than a join to a table on its way out.
APPLICATION_COLUMNS = """a.id, a.legacy_id, a.status, a.phase, a.updated_at,
             a.created_at, a.posting_id,
             p.title as role, p.apply_url, p.location_raw, p.posted_at,
             p.years_min, p.years_max,
             e.canonical_name as company, e.domain as employer_domain"""

#: The three cases a nulls-last keyset has. `%(after_at)s` NULL means "the
#: cursor row was undated", which is a different question from "no cursor".
def keyset(column):
    """The keyset predicate for a lane ordered on `column`, newest first.

    **One implementation for both lane shapes**, and that is not tidiness. The
    first version had a second copy for the application lanes, and because
    `applications.updated_at` is NOT NULL its null-handling was unreachable --
    so two mutations against it, replacing it with a naive row comparison and
    removing `nulls last`, could not be caught by any test. Untestable
    protection reads as protection and is not. Built once, it is exercised by
    the Found lane's undated rows wherever it is used.

    **The row comparison `(column, id) < (?, ?)` is the usual shape and it is
    wrong.** A NULL on either side makes it NULL rather than true, so the walk
    stops dead at the first undated row and everything after it is unreachable.
    2,087 postings have no date. The three cases a nulls-last ordering actually
    has are written out instead.

    **The casts are load-bearing.** A bare NULL parameter has no type and
    `$2 is null` makes Postgres refuse with "could not determine data type" --
    which is the first page of every lane, so nothing worked at all until they
    were added.
    """
    return f"""(
      %(after_id)s::uuid is null
   or (%(after_at)s::timestamptz is not null and (
            {column} < %(after_at)s::timestamptz
         or {column} is null
         or ({column} = %(after_at)s::timestamptz
             and {{id}} < %(after_id)s::uuid)))
   or (%(after_at)s::timestamptz is null and {column} is null
       and {{id}} < %(after_id)s::uuid)
)""".replace("{id}", column.split(".")[0] + ".id")


def order_by(column):
    """Newest first, undated last. `nulls last` is not optional: PostgreSQL
    sorts nulls FIRST under `desc`, and an index whose ordering differs from
    the query's cannot serve it, so the lane silently sorts the corpus."""
    alias = column.split(".")[0]
    return f"order by {column} desc nulls last, {alias}.id desc"


def page_sql(lane):
    """One page of a lane, newest first, undated last."""
    if lane not in LANES:
        raise KeyError(f"no lane {lane!r}")
    if lane in POSTING_LANES:
        return f"""
select {POSTING_COLUMNS}
  from postings p
  left join job_decisions d
    on d.posting_id = p.id and d.account_id = %(account)s
  left join employers e on e.id = p.employer_id
 where {LANES[lane]["where"]}
   and {keyset('p.posted_at')}
 {order_by('p.posted_at')}
 limit %(limit)s
""".strip()
    return f"""
select {APPLICATION_COLUMNS}
  from applications a
  left join postings p on p.id = a.posting_id
  left join employers e on e.id = p.employer_id
 where a.account_id = %(account)s
   and {LANES[lane]["where"]}
   and {keyset('a.updated_at')}
 {order_by('a.updated_at')}
 limit %(limit)s
""".strip()


def count_sql(lane):
    """The lane's size. Separate from the page so a count never drags a page
    of columns across the wire with it."""
    if lane in POSTING_LANES:
        return f"""
select count(*)
  from postings p
  left join job_decisions d
    on d.posting_id = p.id and d.account_id = %(account)s
 where {LANES[lane]["where"]}
""".strip()
    return f"""
select count(*)
  from applications a
 where a.account_id = %(account)s and {LANES[lane]["where"]}
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
