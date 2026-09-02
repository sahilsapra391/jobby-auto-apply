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

#: A completion that is the application's last word. **Written once**: `done`
#: is defined by it and the working lanes are defined by its negation, and two
#: spellings of one predicate is what the lane SQL is generated to prevent.
SUBMITTED = """exists (
            select 1 from application_events h
             where h.application_id = a.id and h.kind = 'completed'
               and not exists (
                 select 1 from application_events r
                  where r.application_id = a.id
                    and r.kind = 'abandoned' and r.at > h.at))"""

LANES = {
    "found": {
        "label": "Found",
        # The absence of a decision row, and open. That is what keeps it free
        # at 500k: postings minus this account's decisions, never a per-account
        # materialisation. It is also why `found` is not a storable state.
        "where": "p.closed_at is null and d.posting_id is null",
    },
    #: **A submitted application is out of the working lanes, whatever its
    #: status says.** `applications.status` is a session's last-known state and
    #: a completed event is a statement about the world: an employer received
    #: this, under a real name, and it cannot be un-sent. One row had both --
    #: `actively-ai-agent-product-manager`, status `active`, completed
    #: 2026-08-19 with no later abandonment -- and it drew in Needs you and in
    #: Submitted at once.
    #:
    #: Submitted wins because the costs are not symmetric. A submitted
    #: application shown as needing work invites the reader to fill and send it
    #: again, and a duplicate application under a real name cannot be recalled.
    #: A working application shown only as submitted is a row in the wrong
    #: place, which someone notices and nothing else follows from.
    #:
    #: The predicate is the same one `done` uses, and it is written once.
    "watch":  {"label": "Watching",
               "where": f"a.status = 'watch' and not {SUBMITTED}"},
    "flight": {"label": "In flight",
               "where": f"a.status = 'inflight' and not {SUBMITTED}"},
    "act":    {"label": "Needs you",
               "where": f"a.status in ('review', 'active') and not {SUBMITTED}"},
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
        "where": SUBMITTED,
    },
    "dropped": {"label": "Dropped",  "where": "a.status = 'dropped'"},
}

POSTING_COLUMNS = """p.id, p.legacy_id, p.title, p.location_raw, p.remote,
             p.workplace, p.posted_at, p.apply_url, p.years_min, p.years_max,
             p.summary, p.source, p.salary_raw, e.canonical_name as employer,
             e.domain as employer_domain, e.logo_url as employer_logo,
             s.total as score, s.parts as score_parts, s.gates as score_gates,
             s.unknowns as score_unknowns,
             p.logo_src, p.logo_domain, p.logo_alt"""

#: **The posting first, the application's own mirrored name second.** A linked
#: application takes its title and employer from the live pool; the rest fall
#: back to `applications.company` and `applications.role`, which migration 0011
#: declares and `class_b._mirror_roles` keeps current from `roles.json`. Those
#: columns existed in production undeclared and unmaintained, which is why the
#: earlier answer here was to refuse them: a frozen index rendered as current
#: puts confident wrong names on the most visible surface. Declared, mirrored
#: on every class B pass and compared by parity, that objection is answered --
#: staleness is now a divergence rather than a mystery. `linked` still travels
#: so the Posting column can say "posting not linked" where the link is what
#: matters rather than the name.
#:
#: `completed_at` is the submitted date and `updated_at` is not. Measured over
#: all 104 submitted applications: they differ on every one, because the mirror
#: writes `updated_at` as `now()` on every pass. v2's Submitted column drew
#: `updated_at` and showed the backfill time against completions three days to
#: two weeks earlier. `completed_payload` carries the confirmation text, the
#: resume filename and the cover-letter note, which is the only record of what
#: an employer received.
#:
#: An application lane has no posting to draw from when nothing links the
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
             a.posting_id is not null as linked,
             coalesce(p.title, a.role) as role,
             coalesce(p.apply_url, a.apply_url) as apply_url,
             p.location_raw,
             coalesce(p.posted_at, a.posted_at) as posted_at,
             p.years_min, p.years_max,
             coalesce(e.canonical_name, a.company) as company,
             e.domain as employer_domain,
             p.logo_src, p.logo_domain, p.logo_alt,
             a.blockers, a.core_skills, a.fit_score, a.alignment,
             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,
             (select h.at from application_events h
               where h.application_id = a.id and h.kind = 'completed'
               order by h.at desc limit 1) as completed_at,
             (select h.payload from application_events h
               where h.application_id = a.id and h.kind = 'completed'
               order by h.at desc limit 1) as completed_payload"""

#: The three cases a nulls-last keyset has. `%(after_at)s` NULL means "the
#: cursor row was undated", which is a different question from "no cursor".
def keyset(column, cast="timestamptz", ident=None):
    """The keyset predicate for a lane ordered on `column`, highest first.

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
    #: The cursor column is not always a date. Ordering Found by match score
    #: walks a smallint, and a `::timestamptz` cast on it fails outright, which
    #: is why this takes the type rather than assuming one. The id tie-breaker
    #: is always a uuid, and `job_scores.total` is NOT NULL so its null arms are
    #: unreachable -- kept because the predicate is one implementation and a
    #: second copy for the numeric case is exactly the duplication this
    #: function's own history warns about.
    #: **The tie-breaker is the row's identity, never the sort column's
    #: table.** Deriving it from the column's alias worked while every lane
    #: sorted on its own table and emitted `s.id` the moment Found sorted on
    #: `job_scores.total`, which has no id: the whole lane 500'd with "column
    #: s.id does not exist". Passed in explicitly now.
    ident = ident or column.split(".")[0]
    return f"""(
      %(after_id)s::uuid is null
   or (%(after_at)s::{cast} is not null and (
            {column} < %(after_at)s::{cast}
         or {column} is null
         or ({column} = %(after_at)s::{cast}
             and {{id}} < %(after_id)s::uuid)))
   or (%(after_at)s::{cast} is null and {column} is null
       and {{id}} < %(after_id)s::uuid)
)""".replace("{id}", ident + ".id")


def order_by(column, ident=None):
    """Highest first, absent last. `nulls last` is not optional: PostgreSQL
    sorts nulls FIRST under `desc`, and an index whose ordering differs from
    the query's cannot serve it, so the lane silently sorts the corpus."""
    alias = ident or column.split(".")[0]
    return f"order by {column} desc nulls last, {alias}.id desc"


#: What the Found lane may be ordered by, and the type its cursor walks. The
#: board offers exactly these two (`found_lane.SORT_OPTIONS`), so v2 offers
#: exactly these two: "Newest" and "Match %".
SORTS = {
    "new": ("p.posted_at", "timestamptz"),
    "score": ("s.total", "int"),
}

#: The board's own wording for them, from `found_lane.SORT_OPTIONS`.
SORT_LABELS = {"new": "Newest", "score": "Match %"}

#: The date windows the board's own radio row offers, and the interval each
#: means. From `found_lane.DATE_WINDOWS`, so the two cannot drift.
WINDOWS = {"6h": "6 hours", "24h": "24 hours", "7d": "7 days",
           "14d": "14 days", "30d": "30 days"}

#: The Found lane's filters, as one predicate each.
#:
#: **Parameters rather than query variants.** Three optional filters and two
#: sorts is sixteen generated queries per lane, and every one of them a place
#: for the predicate to be spelled differently. A NULL parameter means the
#: filter is off, so there is one query and the caller decides.
#:
#: `websearch_to_tsquery` rather than `plainto_tsquery`, because the board's
#: keyword box takes phrases in quotes and `-word` to exclude, and the reader
#: who types them should get what they asked for rather than a token soup.
#:
#: "direct listings only" is `sourceClass = 'ats'`, which is `direct_rank` 0.
#: The board draws the same chip on the same rows: `canonical_rank` puts ats at
#: 0, aggregator and feed at 2, and employer and other at 3, and the card's
#: chip is `rank <= 1`.
FILTERS = """
   and (%(q)s::text is null
        or p.search_tsv @@ websearch_to_tsquery('english', %(q)s::text))
   and (%(within)s::text is null
        or (p.posted_at is not null
            and p.posted_at >= now() - %(within)s::interval))
   and (not coalesce(%(direct)s::boolean, false)
        or p.sources @> '[{"sourceClass":"ats"}]'::jsonb)"""


def page_sql(lane, sort="new"):
    """One page of a lane, highest first, absent last."""
    if sort not in SORTS:
        raise KeyError(f"no sort {sort!r}")
    if lane not in LANES:
        raise KeyError(f"no lane {lane!r}")
    if lane in POSTING_LANES:
        column, cast = SORTS[sort]
        # Postings are the row here whatever the ordering reads.
        return f"""
select {POSTING_COLUMNS}
  from postings p
  left join job_decisions d
    on d.posting_id = p.id and d.account_id = %(account)s
  left join employers e on e.id = p.employer_id
  left join job_scores s
    on s.posting_id = p.id and s.account_id = %(account)s
 where {LANES[lane]["where"]}
   and {keyset(column, cast, "p")}{FILTERS}
 {order_by(column, "p")}
 limit %(limit)s
""".strip()
    return f"""
select {APPLICATION_COLUMNS}
  from applications a
  left join postings p on p.id = a.posting_id
  left join employers e on e.id = p.employer_id
 where a.account_id = %(account)s
   and {LANES[lane]["where"]}
   and {keyset('a.updated_at')}{APPLICATION_SEARCH}
 {order_by('a.updated_at')}
 limit %(limit)s
""".strip()


def row_sql(lane):
    """One named row, but only if the lane still holds it.

    **The lane's own predicate answers, and the client never recomputes it.**
    A board applying an event has to know whether the subject it just heard
    about still belongs where it is drawing. Deriving that from the event's own
    `to` field would be a second spelling of `LANES[lane]["where"]` in another
    language, which is the thing this module exists to prevent, and it would be
    wrong first for `done`, whose membership is two correlated subqueries over
    the event log and not a status at all.

    An empty answer is the answer. It means "not in this lane any more", which
    is what lets the board take the row out rather than nag the reader to
    reload, and it is the same emptiness for a row that moved, was dropped, or
    never existed. The board does not need those told apart; it needs to stop
    drawing it.

    Ordered and limited like a page so the row arrives shaped the same. A lane
    keyed on an id returns one row, and the limit is belt and braces rather
    than a bound anything relies on.
    """
    if lane not in LANES:
        raise KeyError(f"no lane {lane!r}")
    if lane in POSTING_LANES:
        return f"""
select {POSTING_COLUMNS}
  from postings p
  left join job_decisions d
    on d.posting_id = p.id and d.account_id = %(account)s
  left join employers e on e.id = p.employer_id
  left join job_scores s
    on s.posting_id = p.id and s.account_id = %(account)s
 where {LANES[lane]["where"]}
   and p.id = %(row_id)s::uuid
 limit 1
""".strip()
    return f"""
select {APPLICATION_COLUMNS}
  from applications a
  left join postings p on p.id = a.posting_id
  left join employers e on e.id = p.employer_id
 where a.account_id = %(account)s
   and {LANES[lane]["where"]}
   and a.id = %(row_id)s::uuid
 limit 1
""".strip()


#: The per-lane search, which every lane but Found offers.
#:
#: **`ilike` over the columns the row draws, not `search_tsv`.** Postings carry
#: a generated tsvector because there are 22,000 of them and a description to
#: index; an application lane holds tens of rows and its searchable text is the
#: four fields on the card -- company, role, location and the skills. A reader
#: typing "toast" wants the row that says Toast, and stemming a company name is
#: how "Toast" stops matching itself.
#:
#: `core_skills` is joined into the haystack rather than searched as an array,
#: because a partial word should still hit: "product" matching
#: `product-management` is the point of a skill search.
APPLICATION_SEARCH = """
   and (%(q)s::text is null
        or concat_ws(' ', coalesce(e.canonical_name, a.company),
                          coalesce(p.title, a.role),
                          p.location_raw, a.region,
                          array_to_string(a.core_skills, ' '))
            ilike '%%' || %(q)s::text || '%%')"""


def count_sql(lane):
    """The lane's size. Separate from the page so a count never drags a page
    of columns across the wire with it."""
    if lane in POSTING_LANES:
        # The count carries the filters too, or the tab badge says 18,104 over
        # a lane showing nine.
        return f"""
select count(*)
  from postings p
  left join job_decisions d
    on d.posting_id = p.id and d.account_id = %(account)s
 where {LANES[lane]["where"]}{FILTERS}
""".strip()
    return f"""
select count(*)
  from applications a
  left join postings p on p.id = a.posting_id
  left join employers e on e.id = p.employer_id
 where a.account_id = %(account)s and {LANES[lane]["where"]}{APPLICATION_SEARCH}
""".strip()


def as_typescript():
    """The same strings, for `apps/web`, with psycopg's named placeholders
    rewritten to node-postgres' positional ones.

    Generated rather than rewritten by hand: two spellings of one query is the
    thing `LogoContractTests` exists to prevent, and a lane predicate is
    exactly the kind of text that gets edited on one side only.
    """
    #: Every parameter any query here can take, in a fixed order so the
    #: numbering is deterministic. Which of them a given query actually uses is
    #: read off that query, not assumed.
    known = ["account", "after_at", "after_id", "limit", "q", "within",
             "direct", "row_id"]

    def positional(sql):
        """`(sql, params)` with **contiguous** numbering for this query alone.

        **A global name-to-position map does not work and the failure is
        obscure.** The Found count uses `account` and the three filters and
        never mentions the cursor or the limit, so a shared numbering gave it
        `$1, $5, $6, $7`; Postgres cannot infer a type for a parameter the
        statement does not reference, and the whole page 500s with "could not
        determine data type of parameter $2" no matter what is passed for it.

        So each query is numbered from 1 over the parameters it actually names,
        and it carries the list, which is also what stops a caller guessing.
        """
        used = [name for name in known if f"%({name})s" in sql]
        for i, name in enumerate(used, start=1):
            sql = sql.replace(f"%({name})s", f"${i}")
        return sql, used

    # **Every sort a lane offers, emitted as its own query.** Only Found has
    # more than one, and building the alternative by string-substituting the
    # order clause on the client would be a second spelling of the predicate,
    # which is the thing generating this file exists to prevent.
    lanes = {}
    for k, v in LANES.items():
        sorts = SORTS if k in POSTING_LANES else {"new": None}
        page_text, page_params = positional(page_sql(k))
        count_text, count_params = positional(
            count_sql(k).replace("%(limit)s", ""))
        row_text, row_params = positional(row_sql(k))
        emitted = {}
        for name in sorts:
            text, params = positional(page_sql(k, name))
            emitted[name] = {"sql": text, "params": params}
        lanes[k] = {
            "label": v["label"],
            "page": page_text,
            "pageParams": page_params,
            "count": count_text,
            "countParams": count_params,
            "row": row_text,
            "rowParams": row_params,
            "sorts": emitted,
        }
    return (
        "// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
        "//\n"
        "// The lane SQL lives in scripts/v2_lanes.py and is emitted here so\n"
        "// there is one spelling of each predicate. Parameters are positional\n"
        "// for node-postgres, in the order [account, afterAt, afterId, limit].\n"
        "export type Lane = " + " | ".join(f'"{k}"' for k in LANES) + ";\n\n"
        "export type Sort = " + " | ".join(f'"{s}"' for s in SORTS) + ";\n\n"
        "// The date windows the board's radio row offers, from\n"
        "// found_lane.DATE_WINDOWS. The value is the interval the SQL casts.\n"
        "export const WINDOWS: Record<string, string> = "
        + json.dumps(WINDOWS) + ";\n\n"
        "// What each sort is called in the control, taken from the\n"
        "// board's own found_lane.SORT_OPTIONS.\n"
        "export const SORT_LABELS: Record<Sort, string> = "
        + json.dumps(SORT_LABELS) + ";\n\n"
        "// Each query carries the parameters it takes, in order. Numbering is\n"
        "// contiguous per query: a shared map leaves gaps Postgres cannot type.\n"
        "export type LaneQuery = { sql: string; params: string[] };\n\n"
        "export const LANES: Record<Lane, { label: string; page: string; "
        "pageParams: string[]; count: string; countParams: string[]; "
        "row: string; rowParams: string[]; "
        "sorts: Record<string, LaneQuery> }> =\n"
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
