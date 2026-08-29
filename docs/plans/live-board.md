# A live board, not a snapshot

**Status: designed, not built. The measurements are real; the change is not made.**

## The complaint, and why it is right

"If I drop a job it takes a full minute to actually drop."

It is worse than a minute. Measured on the running system, 2026-08-29:

```
PUBLISH_FLOOR_SECONDS   900   the board may republish at most every 15 minutes
observed publishes      19:33  19:49  20:05  20:20  20:39  20:54   (~16 min apart)
```

Two different latencies are being confused, and separating them is the whole
design.

**The action is fast.** A click posts to the queue, the watcher picks it up on
its next tick, and the drop is recorded within seconds. That part works.

**The visibility is slow.** The board is a published snapshot. Nothing the user
does appears until the next publish rebuilds the entire state and pushes it to
KV, which is up to fifteen minutes later. The click memory in `localStorage`
exists precisely to paper over this: it replays recent clicks on load so the
card looks dropped before the snapshot agrees. That is a workaround for the
architecture, and it is where the superseded-drop bug came from.

So the board is not slow because the store is on the device. It is slow because
**reads go through a batch publish**. Moving storage to Postgres does not fix
that on its own, and saying otherwise would be the third instance this month of
claiming a change did something it did not.

## What the board does today

```
click ──► POST /api/board (Vercel) ──► KV queue
                                          │
                                   watcher tick, seconds
                                          │
                                   acts on the store
                                          │
                        publish, at most every 15 minutes
                                          │
                                    KV board-state (≤20 MB)
                                          │
                        page load ──► render that snapshot
```

Every arrow after the first is batch. The snapshot is also the reason for the
20 MB ceiling, the embed cap, the state-slimming, the freshness alarm and the
staleness ping: all of them exist to manage a large blob being rebuilt and
shipped whole.

## The change: two paths, not one

The insight is that **the board's writes and the agent's writes are different
kinds of work, and only one of them needs the device.**

A watch, a drop, a pass, a restore is a user decision. It is a row change. It
needs no browser, no provider, no PDF, and no laptop. It should go straight to
the database and be reflected immediately.

An apply run, a document build, a sourcing sweep is agent work. It needs the
provider CLI and the Chrome profile, both of which are on the Mac for reasons
that do not change: a file chooser needs a real filesystem, and employer sites
treat a home connection better than a datacenter one.

Today both go through the same queue, so a decision waits behind the machinery
built for the agent.

```
DECISIONS                            AGENT WORK
click ──► API ──► Postgres           click ──► API ──► Postgres (job queued)
   │         ▲                                            │
   └─ 200 ───┘  ~20 ms                          Mac daemon polls for work
   UI already updated                                     │
                                              runs the browser, the provider
                                                          │
                                              writes the result to Postgres
                                                          │
                                              board sees it on next read
```

### 1. The API writes decisions directly

`/api/board` stops enqueuing watch/drop/pass and writes the row itself. One
`UPDATE jobs SET state = ...` plus one `INSERT INTO history`. The response is
the new state, so the client does not have to guess.

The watcher stops being in the path for these entirely. Its remaining job is
what it is actually for: running applies, building documents, sweeping sources.

### 2. The page reads live

`deploy/api/page.py` queries Postgres instead of inflating a KV blob. The
queries already exist and are indexed, and the slice is bounded the same way
the embed cap bounds it now:

```sql
select ... from jobs
where state = 'new' and posted_at > now() - interval '30 days'
order by posted_at desc limit 3400
```

Measured from the Mac: 1.13s for 3,400 rows. **From Vercel it should be far
lower** and that number has to be measured before this ships, not assumed:
same-region Vercel to Neon is a local hop, and the Mac's 60 ms round trip is
mostly distance. If it is not fast enough, the fallback is a short edge cache
with a version key, not a return to fifteen-minute snapshots.

### 3. Optimistic UI, honestly

The click updates the card immediately and reconciles against the response. The
difference from today's `localStorage` replay is that the server answer arrives
in the same interaction rather than fifteen minutes later, so a failure is
visible while the user is still looking at it. The replay memory can then be
deleted, and with it the superseded-drop logic it needed.

### 4. What retires

- `jobby:board-state` in KV, and with it the 20 MB ceiling.
- `guard_state_size`, `fit_state`, `state_slim`, the embed cap, the headroom
  and growth warnings. All of them manage a blob that no longer exists.
- The board-staleness alarm. A page that reads the database cannot be stale;
  it can only be down, which is a different and louder failure.
- The `localStorage` click memory and `split_superseded_drops`.

That is a large amount of machinery whose only purpose is making a batch
snapshot survivable.

### 5. What does not change

The Mac keeps the provider CLIs and their keys, the Chrome profiles and their
logins, document rendering, and the staged PDFs at submit time. R2 keeps the
artifacts. Those four are physics, not policy.

## Connection handling, which is the real risk

A serverless function opening a Postgres connection per request exhausts a
Postgres server quickly. Three options, in order of preference:

1. **Neon's pooled endpoint**, already what `JOBBY_DATABASE_URL` names, the
   `-pooler` host. Free, and enough for one user.
2. **Neon's serverless HTTP driver**, no connection at all and one HTTP round
   trip per query. Best fit for Vercel, but it is a JavaScript driver and the
   renderer is Python, so it would mean moving the read path to `board.js`.
3. **Hyperdrive**, Cloudflare's pooler. It only helps a Worker, and the hot path
   here is Vercel, so it does not apply unless the renderer moves to Workers.

Start with the pooled endpoint and measure. `worker_key.set-database` already
reports whether the URL is pooled, which is why that flag exists.

## Order of work

1. **Read path first.** Point `page.py` at Postgres behind a flag, with the KV
   snapshot as the fallback. Measure Vercel-to-Neon latency for real. Nothing
   is retired until this is faster than the snapshot it replaces.
2. **Write path second.** Move watch/drop/pass off the queue and into the API.
   This is what makes the click feel instant, and it is small.
3. **Then retire.** Delete the snapshot machinery only once nothing reads it,
   observed rather than inferred, per the rule this repo already carries.
4. **Live push, optional.** Server-sent events so a finished scan appears
   without a reload. Worth it only after the first three, and only if the user
   actually wants the page to move on its own.

## What this does not solve

Applications still take as long as they take: a document build is a provider
call, and a form fill is a browser session. Making the board live makes the
board honest about what is happening. It does not make the agent faster, and
the report should not imply that it does.
