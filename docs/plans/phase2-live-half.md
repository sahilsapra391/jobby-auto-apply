# Phase 2, the live half: build order

**Design only. Nothing here is built, and none of it starts until the Phase 1
gate closes.** `docs/plans/phase2-live-board.md` is the design; this is the
order to build it in and what each step has to prove. It is short because that
document is now correct.

The read-only half is done and deployed: lanes from Postgres, keyset paging
with two orderings, filters, the score mirrored, applications linked to
postings where that can be known honestly.

## What the settled design changed about this plan

Three things, all from building the read-only half against real data, and each
one removes work rather than adding it.

**`applications` is the source of truth.** The write path targets it and
`application_events`, not `job_decisions`. That table keeps one job, the Found
lane's exclusion set, and nothing else reads it. Measured: 125 decision rows
against 204 applications, and Submitted read from a decision state gives 100
where the events give 104.

**The event kinds follow from that.** `application.changed` carries an
application id. A posting entering the exclusion set is `posting.decided`,
which is a different event rather than a `from: "found"` on the first one,
because Found is an absence and has no `from`.

**The score is already in Postgres**, so the channel never carries one and
`job_scores` is not on this path at all.

## Build order

Each step is a merge, and each has one thing it must prove before the next
begins.

### 1. The write endpoint, no channel

`POST /api/applications` on Vercel. Sets `app.account_id`, writes the row or
appends the event, commits, returns. No Durable Object yet, so the board still
learns about its own writes by refetching.

Idempotent on a client-supplied `Idempotency-Key`, stored 24 hours, before
anything else is built on top: a double-tap on mobile is a category of bug
rather than an incident, and retrofitting idempotency means auditing every
caller.

**Proves:** a write from v2 reaches Postgres and the file store agrees. This is
the step that turns off "no writes from v2", so it needs the non-owner role
decision made first or it inherits the owner credential permanently.

**Restarts the parity clock**, and this is the one place in the phase where
that is unavoidable: a second writer is exactly what parity exists to check.

### 2. TenantChannel, receiving only

One Durable Object per account, named by account id, so isolation is structural
rather than a filter somebody can forget. It accepts connections, assigns a
monotonic `seq`, keeps a 500-event ring, and fans out. It does **not** write to
Postgres: the seam is one-directional, and a channel that could also write makes
"which one is right" a question with no answer.

Hibernatable WebSocket API from the start. Retrofitting hibernation means
revisiting every piece of in-memory state.

**Proves:** two tabs on the same account see the same `seq` sequence, and a
third account's channel is a different object with its own.

### 3. The route publishes, the client applies

Step 1's route posts to the channel after its commit. The board applies events
to the lane it is showing.

**The commit precedes the publish and that ordering is not a preference.** A
published event with no row behind it is a board showing a change the database
does not have, and it vanishes on the next reconnect, which is worse than a slow
board because the user watched it work. Step 3 failing after step 2 is the
recoverable direction: log it loudly, do not retry inline, do not roll back.

**Proves:** a click in one tab moves the card in another, and killing the
publish leaves the row correct and the board briefly stale.

### 4. Reconnect

`resume_from` with a replay when the ring still covers it and an explicit
`resync` when it does not. **Never a silent gap.** The failure this prevents is
already in this repository's history: a queue wipe destroyed fourteen requests
while the board still drew them as in flight, and a client that misses events
without knowing is a client showing fiction with full confidence.

The ring is 500 events. An account generating more than that while a tab is
closed gets a resync, which is correct and cheap. **Resync is the designed path,
not the failure path**, so do not grow the buffer to avoid it.

**Proves:** the two negative tests, and they are written before the happy path.
Kill the socket mid-session and assert the client resyncs rather than diverging.
Publish 200 events while disconnected and assert either a full replay or an
explicit resync, never a gap.

## The gate

> Measured p95 click-to-visible **under 250 ms** across all lanes, on a real
> device over a real network, recorded.

**The stopwatch is client-side, on this Mac, over a real network.** Naming the
measurement point is the whole of it: server-side execution for a Found page is
0.6 ms by date and 39.6 ms by score, and a client on this Mac sees 250 to 550 ms
for the same queries, almost all round trip. **0.6 ms says the query is fine and
says nothing about whether the board feels live.**

The number to record is from `performance.now()` at the click to the frame that
paints the change, on the deployed board, over the network the reader actually
has. Anything measured on the server or on localhost is a different quantity
wearing the same units.

A third test worth adding, from Phase 1's experience: assert the Found lane puts
an undated posting last, against a seeded database, **through the real API
rather than the SQL**. The SQL is pinned already; the lane is what the reader
sees, and the two can drift.

## What this half must not do

- **Not enable RLS.** Phase 5, and the non-owner role goes in before the
  policies, not after. An owner bypasses its own policies through ownership
  rather than through `rolbypassrls`, so the obvious assertion passes on a
  connection with no isolation.
- **Not move CRUD to Workers.** Vercel route handlers for CRUD, the channel and
  the cron fan-out only on Workers, seam one-directional.
- **Not touch the runner.** Form filling, document rendering and provider calls
  stay on the device.
- **Not start before the gate closes.** The clock is what says the mirror can be
  trusted, and step 1 adds a writer to the thing it is measuring.

## What is still outstanding from the read-only half

Neither blocks this, and both are cheap to lose track of:

- the `logo_plan` split, which exists only because of a page-weight budget this
  board does not have, is still carried rather than collapsed
- the production checkout under `~/Documents`, parked as a Phase 4 item because
  that phase rewrites the launchd plists anyway
