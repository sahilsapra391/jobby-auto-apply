# Phase 2: the live board

**Design only. No implementation until the Phase 1 gate closes.**

Phase 2 is the phase the user feels: a click takes up to fifteen minutes to
appear. This document is what to build, why each piece is shaped the way it is,
and which parts of the current board must not be lost in the move.

## It is a rewrite, and §2's justification is wrong about that

§2 chooses "Next.js on Vercel, git-deployed" and defends it with "already works,
already deploys on merge. Do not move it during a rebuild that is changing
everything else."

The host and the deploy trigger are already true. **Next.js is not.** There is
no Next.js anywhere in the repository. The board is:

```
scripts/dashboard.py      2,559 lines   builds the whole board as one HTML string
scripts/found_lane.py     2,550 lines   the Found lane, its filters and its script
scripts/premium_ui.py       565 lines   tokens, layout, motion
deploy/api/page.py          281 lines   a Python serverless function that renders it
```

`dashboard.build(state, generated)` takes the entire published state and returns
a complete HTML document. There is nothing to port. Every line of it assumes the
board is a photograph of the store taken at publish time, and Phase 2 exists to
stop taking photographs.

So read the decision as: **the host and the deploy trigger stay; the framework
is new.** ADR 0004 records this. Anyone budgeting Phase 2 as a port will be
wrong by an order of magnitude.

## What is actually being killed

Not the renderer. The *snapshot*, and everything that exists to make a snapshot
fit through a pipe:

| Thing | Why it exists today | After |
|---|---|---|
| `jobby:board-state` in KV | the board is a blob | gone; the board queries Postgres |
| the 15-minute publish floor | rebuilding from the full pool is expensive | gone; a write publishes an event |
| the 20 MB state ceiling | one KV value | gone |
| the embed cap (~21 bytes a job) | the HTML carries the pool | gone; the client fetches a page at a time |
| `rendererSchemaVersion` drift checks | two deploy routes meeting in production | narrower: the API is versioned, not the HTML |

Measured 2026-08-30: the published state is 3.30 MB against a 3.7 MB budget.
That budget is the reason Found cards cannot carry an employer domain string,
and it is why `logo_plan` returns a single digit and the client re-derives the
domain. Phase 2 removes the constraint that forced that split, and the split
should then be removed rather than left as a fossil.

**Still outstanding as of 2026-09-01.** v2 inherited the split rather than
collapsing it: the client still derives the domain and `logo_plan` still returns
a digit, on a board with no embed cap and no reason for either. Recorded here as
undone rather than quietly carried, because a fossil nobody has noticed is how
the next reader concludes the constraint still exists.

## The pieces

### 1. TenantChannel, one Durable Object per account

Named by account id, so isolation is structural rather than a filter somebody
can forget. Single-threaded per instance, which is what makes `seq` a real total
order rather than a best effort.

Responsibilities, and nothing else:

- accept WebSocket connections from browser tabs and from the device runner
- assign a monotonic `seq` per account
- keep a ring buffer of the last 500 events
- fan out to every open socket
- answer `resume_from` with a replay or an explicit `resync`

It does **not** write to Postgres. ADR 0005 makes the seam one-directional:
Vercel writes the row, commits, then posts the event here. A DO that could also
write would make "which one is right" a question with no answer.

Hibernation matters at 1,000 accounts: use the hibernatable WebSocket API so an
idle account costs nothing between events.

### 2. The event envelope

```json
{
  "seq": 10412,
  "account_id": "…",
  "kind": "application.changed",
  "at": "2026-08-30T14:02:11.418Z",
  "payload": { "application_id": "…", "to": "dropped" }
}
```

Kinds: `application.changed`, `application.phase`, `application.completed`,
`application.needs_you`, `posting.decided`, `pool.delta`, `device.status`,
`runner.update_required`.

**There is no `from: "found"`, because Found is not a state.** It is the absence
of a decision row, which §5 says and an earlier draft of this section
contradicted by showing `{"from": "found", "to": "dropped"}` as its worked
example. A posting leaving Found and an application changing lane are two
different events, not two values of one field: the first is a `job_decisions`
insert with no application behind it yet, and it is `posting.decided`.

Three rules learned the hard way elsewhere in this repository:

**An event carries ids and a diff, never a rendered row.** A rendered row is a
snapshot again, in smaller pieces, and it puts presentation on the write path.

**One kind per fact.** `answer_record.py` records five distinct claims about a
question and refuses to let any two share a field, for the reason that blending
two costs the ability to tell which you are looking at. Same rule here: a
`phase` and a `completed` are different events even when they arrive together.

**The payload is small on purpose.** Phase 1 measured the mirror's cost as
linear in payload at about 0.83 s per MB, and the same arithmetic applies to
fan-out: a wide event multiplied by every open tab is the shape of a cost that
does not appear until there are users.

### 3. Write path, and why the order is not a preference

```
1. browser POSTs /api/applications on Vercel
2. route sets app.account_id, writes the applications row or appends to
   application_events, COMMITS
3. route posts the event to the account's DO, carrying the application id
4. DO assigns seq, appends to the ring, fans out
5. route returns.   target: p95 under 120 ms click-to-response
```

**`applications` is the source of truth and this design keys on it.** An earlier
draft wrote to `job_decisions`, which is a projection that was never finished
and will not be. Measured 2026-09-01: 125 decision rows against 204
applications, and a Submitted lane read from a decision state gives 100 where
the events give 104.

`job_decisions` keeps exactly one job: the Found lane's exclusion set. Found is
`postings` minus this account's decision rows, and that absence is what keeps it
free at 500k rather than a per-account materialisation. Nothing else reads it,
and a posting entering it is `posting.decided` rather than a lane change.

**The commit precedes the publish.** A published event with no row behind it is
a board showing a change the database does not have, and on the next reconnect
it vanishes. That is worse than a slow board, because the user saw it work.

Step 3 failing after step 2 is the recoverable direction: the row is right, the
board is briefly stale, and the next fetch corrects it. Log it loudly; do not
retry inline and do not roll back the commit. This is the same asymmetry class B
dual-write settled in Phase 1, and it lands the same way round.

Every mutating endpoint is idempotent on a client-supplied `Idempotency-Key`,
stored 24 hours. Double-tap on mobile stops being a category of bug.

### 4. Reconnect: replay or resync, never a silent gap

Client stores `last_seq`. On reconnect it sends `{"resume_from": 10399}`.

- ring buffer still covers it → replay the events after it
- it does not → `{"kind": "resync"}` and the client refetches the visible lane

**Never a silent gap.** The current failure this prevents: a queue wipe
destroyed fourteen requests while the board still drew them as in flight. A
client that misses events and does not know it is a client showing fiction with
full confidence.

The ring buffer is 500 events. That is a policy, so state it: an account
generating more than 500 events while a tab is closed gets a resync, which is
correct and cheap. Do not grow the buffer to avoid resyncs; resync is the
designed path, not the failure path.

### 5. Keyset pagination, per lane

```sql
select … from postings p
left join job_decisions d on d.posting_id = p.id and d.account_id = $1
where p.closed_at is null and d.posting_id is null      -- Found
  and (p.posted_at, p.id) < ($2, $3)
order by p.posted_at desc nulls last, p.id desc
limit 50
```

Never `OFFSET`. The indexes are already in migration 0002.

**`nulls last` is load-bearing and Phase 1 has the scar.** PostgreSQL sorts
nulls first under `order by … desc`, so a newest-first lane without it opens
with every posting whose date nobody stated: 1,942 of 20,103 on the corpus at
the time. The indexes are declared `nulls last` to match, because an index whose
ordering differs from the query's cannot serve it and the lane silently falls
back to sorting the whole corpus. `tests/test_corpus_schema.py` pins the
ordering, the index definitions, and a keyset walk that must not repeat or skip
the undated row.

**An ordering that spans a join has no index to serve it, and that is a Phase 6
input rather than a thing to fix here.** Sorting Found by match score orders on
`job_scores.total` while the lane is a query over `postings`, so Postgres
top-N heapsorts the whole lane: measured warm against Neon on 2026-09-01,
**39.6 ms for the score ordering against 0.6 ms for the date ordering**, which
does use its index. At 21,888 rows that is fine. At §11's 500k it is the first
thing to break, and it is the same class of design input the payload
measurement was for Phase 3: a ratio that transfers even though the absolute
number does not.

The tie-breaker is the lane's own row identity, never the sort column's table.
Deriving it from the sort column's alias is correct only while every lane
orders on its own table, and emits `s.id` the moment one does not.

Lane predicates:

| Lane | Predicate |
|---|---|
| Found | `postings.closed_at is null` and no `job_decisions` row |
| Watching | `applications.status = 'watch'` |
| In flight | `applications.status = 'inflight'` |
| Needs you | `applications.status in ('review','active')` |
| Submitted | a `completed` event with no later `abandoned` |
| Dropped | `applications.status = 'dropped'` |

**Five of the six read `applications`, not a decision state**, and an earlier
draft of this table had all six on `job_decisions`. See §3 for the measurement.
Submitted specifically is an event question rather than a status one: a
`completed` in `application_events` not retracted by a later `abandoned`. That
is 104 where a decision state gives 100, and the four that differ are the
reason the distinction exists.

Found being *the absence of a row* is what keeps it free: it is `postings` minus
this account's decisions, not a per-account materialisation of 500k rows. That
is also why `found` is not a storable decision state, and the schema refuses it.

Client-side: a virtualised list. 500k rows never reach the browser.

### 6. Scoring is eager, because the lane sorts by it

**You cannot order by a value computed after choosing which rows to compute it
for.** An earlier draft of this section said score only the ~50 rows being
rendered, and that works exactly as long as the score is a decoration. Sorting
Found by match is the feature, and it needs the score for every candidate row
before the page is chosen. The two cannot both be true, and the draft asserted
both.

So the score is mirrored with the pool. Measured 2026-09-01: 21,866 of 21,890
pool records carry one, and all 18,104 Found rows have theirs. A record with no
score is skipped rather than written as zero, because a zero sorts below every
genuine low scorer and reads as a decision rather than an absence.

`job_scores (account_id, posting_id, total, parts, why, gates, unknowns,
config_version, computed_at)`. **The rubric is not one blob**: the card draws
each component as its own bar, and a single `rubric` column means a second read
for every opened card.

Account-scoped, because the score is computed from the reader's own saved
search. A column on `postings` works perfectly until the second account exists
and then silently serves each account the other's ranking, which is a Phase 5
failure caught here, where it is cheap.

**ADR 0007's cross-product warning moves to Phase 6, and it is still right
there.** §11 puts every account times every posting at 500 million rows and a
permanent backfill. The shape to start from is scoring at ingest for the
accounts whose preferences a posting could match, rather than for every account,
which is a filter on the write side rather than laziness on the read side.

## Coexistence: both boards live until cutover

The old board must keep working while the new one is built, and the switch must
be per account and reversible.

```
jobby.fyi        -> jobby-board  (rooted at deploy/)  the Python snapshot board
v2.jobby.fyi     -> jobby-v2     (rooted at apps/web) the Next.js board
```

**Two projects on two domains, and the second one is not a path off the first.**
An earlier draft of this document wrote `jobby.fyi/v2/ -> apps/web` as though
that line were a routing table. It is not one. A Vercel domain maps to exactly
one project, `jobby.fyi` maps to `jobby-board`, and `jobby-board` is rooted at
`deploy/`, so `apps/web` cannot build in it. Serving `/v2/` off that domain
needs one of:

- a proxy rewrite in `deploy/vercel.json` to a second project, which requires a
  custom domain on that project anyway (the team's SSO is
  `all_except_custom_domains`, so a `*.vercel.app` origin is unreachable), adds
  an edge-to-origin hop to every request including every chunk, and puts the
  Python board's configuration on the new board's critical path;
- Vercel's microfrontends path routing, which is a billed feature per project
  past a cap and introduces a config shared between the two apps;
- one build producing both, which is the coupling this section exists to
  prevent.

**The requirement behind the path was isolation, and the path was the thing
that cost it.** Neither project's configuration mentions the other now, so
there is nothing to contend over and no deploy of one that can break the
other. The `/v2/` string bought nothing the cutover needs: the flag gates a
redirect, and a redirect to a subdomain is the same redirect.

Do not re-propose the path. Reintroducing `basePath` is the tell.

**Everything on `v2.jobby.fyi` is behind `apps/web/middleware.ts`, and that is
the only gate there is.** `all_except_custom_domains` protects previews and
`*.vercel.app` URLs and deliberately exempts custom domains, which is what lets
`jobby.fyi` answer a scripted request at all. The corollary is the part to
hold on to: the platform protection covering this app throughout development is
exactly the protection that stops covering it the moment its domain is
attached. So the middleware is HTTP Basic against `DASHBOARD_PASSWORD`, the
same credential and the same mechanism `deploy/api/page.py` already uses, over
every path including `_next/static` (those chunks are the board's own code and
a lane's shape is inferable from them). **An unset `DASHBOARD_PASSWORD` is a
503, never an open door**, because a missing environment variable is the most
likely way this ends up public and it looks like nothing at all.

`tools/check_deployed_shape.mjs` asserts all of that against the traced bundle,
including booting a second server with the variable absent, since every other
assertion runs with a password configured and none of them can see that case.

**`accounts.flags.live_board` decides who sees which**, and it exists already:
Phase 0 built the registry, the column, and the boolean-only constraint. The
flag gates the *redirect*, not the code path, so `v2.jobby.fyi` stays reachable
directly for testing while the flag is off.

Cutover order, and each step is reversible until the last:

1. `v2.jobby.fyi` renders read-only from Postgres. No writes, no DO.
   **Explain every difference against the old board, rather than eliminate
   it.** Equality was never achievable and demanding it produces either a false
   pass or days spent chasing a legitimate divergence. Measured on the first
   comparison: Watching 18 against 16 and Dropped 57 against 9, both correct,
   because `applications.posting_id` was NULL for every row and the old board
   reads a frozen `roles` snapshot that nothing maintains. A difference with a
   measured cause is a pass; a difference nobody can account for is not.
2. The DO ships. The new board subscribes and applies events. Writes still go
   through the existing endpoints.
3. `live_board` on for account 1, redirecting `jobby.fyi` to `v2.jobby.fyi`.
   Both boards remain correct; the old one is just slower.
4. The old board's publish path is switched off and `jobby:board-state` stops
   being written. **This is the irreversible step** and it wants its own week.
5. `jobby.fyi` is moved onto the new project and `jobby-board` is retired.
   `dashboard.py`, `found_lane.py`, `premium_ui.py` and `deploy/api/page.py` are
   deleted in one revertable commit. Moving the domain is a project setting
   rather than a rewrite rule, which is the other thing the two-project shape
   buys: the final step has no routing config to unwind.

### What must not be lost in the move

**The renderer-sync check.** `scripts/sync_vercel_renderer.py` keeps
`deploy/renderer/` in step with `scripts/`, and CI fails a PR when they drift.
It exists because three commits of UI work once sat merged and invisible while
the deployment served an older bundle, and because the renderer read a favicon
from `deploy/public/`, which every laptop has and the serverless bundle does
not. A Next.js app has the same two hazards in different clothes: a stale build
and an asset that resolves locally and 404s deployed. Phase 2 owes CI an
equivalent check before step 3, not after.

**The design system.** Soft Premium is not decoration. `premium_ui.py` carries
tokens, the three-state theme rule, and the reduced-motion query, and CLAUDE.md
records what each of them cost to get right: no `view-transition-name` on
`.panel` because two panels differing by tens of thousands of pixels stretched
both snapshots; no fill mode on a panel keyframe because a 50,000px layer
exceeds the max texture and rasterizes blank; hover moves only what you press.
Those are constraints on the new board too, and they are cheaper to carry across
than to rediscover.

**The logo contract.** `LogoContractTests` compares two implementations of one
rule because the derivation lives in Python and in the embedded script. Phase 2
removes the reason for the split (the page-weight budget), so the right move is
to delete the duplicate rather than port it.

**Never an ATS or aggregator favicon as an employer mark**, and monograms stay
the required fallback. About one company in six lands on one.

## What this phase must not do

- **Not touch the runner.** Form filling, document rendering and provider calls
  stay on the device. §13.
- **Not enable RLS.** Phase 5, with a non-owner role. Enabling it against an
  owner role that bypasses it silently would look exactly like it worked.
- **Not move CRUD to Workers.** ADR 0005: Vercel route handlers for CRUD,
  Workers only for the channel and the cron fan-out, seam one-directional.
- **Not build a cross-product scorer.** §11 and ADR 0007. Scoring the pool for
  one account is what §6 does; scoring every account times every posting is the
  500-million-row shape Phase 6 has to solve.

### The obligation this phase creates, named where it was created

**v2 connects to Postgres as the database owner, and Phase 5 inherits that.**
`JOBBY_DATABASE_URL` on the `jobby-v2` project is the device's own credential,
taken deliberately on 2026-09-01 because the routes are GET-only and
password-gated and the alternative was holding a working read-only board back
on a credential it will not keep.

**The trap is that the obvious Phase 5 assertion passes on it.** Measured on
postgres:18, policy enabled, `app.account_id` set, neither role a superuser:

| role | `rolbypassrls` | rows visible of 2 |
|---|---|---|
| owner of the table | f | **2** |
| non-owner with select | f | 1 |

A table owner bypasses its own policies through *ownership*, not through the
role attribute. So `pg_roles.rolbypassrls is false` is true of a connection with
no isolation at all, and enabling RLS against this credential would produce a
green migration, a passing suite and nothing else. The non-owner read-only role
goes in **before** the policies rather than after.

Two more things this phase must not let become permanent:

- **"No writes from v2" is enforced by discipline, not by the credential.**
  Against a read-only role an accidental write fails loudly; against this one it
  succeeds and the file store falls behind on exactly the rows it touched.
  **This is also why `live_board` cannot flip when the parity clock closes:**
  the first v2 write is a divergence parity is right to report, so the flip is
  gated on the file store being deleted rather than on the clock. The order,
  and why writeback must not be built to bridge it, is in `docs/post-gate.md`.
- **The production checkout sits under `~/Documents`**, where macOS Privacy
  controls can deny access after an OS update: on 2026-09-01 they denied git
  access to a worktree there mid-session, and all four launchd plists execute
  from that path. Parked as a **Phase 4** item, because that phase rewrites the
  plists anyway and the move is free there. It is not free now: it needs a
  deploy window and a restart of all three services, and it is the one place
  where getting it wrong stops real applications.

## The gate

> Measured p95 click-to-visible **under 250 ms** across all lanes, on a real
> device over a real network, recorded. Not a local benchmark.

**The stopwatch is client-side, on this Mac, over a real network**, and naming
the measurement point is not pedantry: the same system passes or fails
depending on where it sits. Measured 2026-09-01, server-side execution for a
Found page is 0.6 ms by date and 39.6 ms by score, while a client on this Mac
sees 250 to 550 ms for the same queries. Almost all of that is round trip.
**0.6 ms says the query is fine and says nothing about whether the board feels
live.**

Two negative tests before the happy path, per §9: kill the websocket mid-session
and assert the client resyncs rather than diverging; publish 200 events while
disconnected and assert either full replay or an explicit resync, never a silent
gap.

A third worth adding, from Phase 1's experience: assert the board's Found lane
ordering puts an undated posting last, against a seeded database, through the
real API rather than the SQL. The SQL is already pinned; the lane is what the
user sees, and the two can drift.

## `idempotency_keys` is an audit trail nobody designed as one

**It stores the response, and that is what made a bug diagnosable three hours
after the Vercel log was gone.**

`writes.remember` keeps `(account, key, request_digest, status, response)` so a
retried mutation can be answered with its first result. On 2026-09-07 an
application had `status = inflight` in Postgres and `watch` in the device's
session file, the apply queue was empty, and the live log had rolled. The row
recorded at 00:18:36Z read:

```json
{"kind": "application.changed", "published": true, "applicationId": "01aa..."}
```

`kind` is returned only by the status path in `api/applications/route.ts`;
`decide` returns `{postingId, decision, state}`. So the shape of the stored
response identified which branch ran, which identified the control, which
identified the bug: three v2 controls posted `status: "inflight"`, `order` was
never called for it, and the route wrote the row anyway.

**Worth keeping deliberately rather than by accident.** A live tail answers
"what is happening"; this answers "what happened", and only one of those is
available afterwards. Two properties make it work and both are easy to lose in
a tidy-up:

- the **response** is stored, not just the status. A status code would have
  said 200 and named no branch.
- rows are kept for `IDEMPOTENCY_HOURS` (24) rather than deleted on success.

It is not a general audit log and should not become one: it is keyed by the
client's own key, it holds only mutations, and a request that never reached
`remember` leaves nothing. But for "which route ran, with what answer, when",
it is the only durable record v2 has, and it cost nothing to have.
