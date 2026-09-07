-- Where a cloud sweep puts what it fetched, and where the device picks it up.
--
-- **The container fetches and the device merges, and this is the seam.** The
-- pool cannot move in Phase 3: `jobs_store` contains zero SELECTs, `run()`
-- reads the whole 98 MB pool file, and `_persist_pool` derives its Postgres
-- delta by re-reading that same file. Making the pool live in Postgres is
-- Phase 4's "the device reading Postgres so the file store can finally go".
--
-- So the split is at the one seam that already exists. Measured on 2026-09-07,
-- every connector receives its device state as plain arguments and the only
-- device state any of them writes is the HTTP validator cache: they are
-- functions of (config, registry, keys) returning postings. That half moves.
-- The gate, the merge, the closure and the prune stay where the pool is.
--
-- **This is why it is worth doing at all.** `jobs_store.POOL_LOCK` is the
-- string "scan": the sweep's lock *is* the pool's write lock, and eight
-- non-sweep callers take it, two on the apply path. Across
-- ~/.job-application/logs/watch.log from 2026-08-26 to 2026-09-07 there are 176
-- ticks that skipped their board reconcile with LockBusy on it, 9 to 21 every
-- one of those 13 days, and a sweep's run duration runs a median 26.8 minutes
-- six times a day. Moving the fetch out of that window is worth more than the
-- laptop's CPU.
--
-- Reading a staging table is not reading the pool. The device still merges
-- into its own file, under its own lock, with all five closure brakes.

-- One row per channel per sweep. The plan's §5 table, with the columns it
-- names, and a real writer at last.
create table if not exists sweep_runs (
  id           uuid primary key default gen_random_uuid(),
  account_id   uuid not null,
  sweep_id     uuid not null,
  source       text not null,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  -- running | complete | failed | abandoned. `complete` is written after the
  -- postings are committed, never before: a status written first is a lie the
  -- moment the write fails, and the device's closure step reads only
  -- `complete` runs.
  status       text not null default 'running',
  job_count    int  not null default 0,
  -- Boards asked, and boards that answered 304. A channel that returned less
  -- because most of its boards were unchanged has not under-delivered, and the
  -- device's coverage ratchet needs to tell those apart.
  board_count  int  not null default 0,
  unchanged    int  not null default 0,
  -- Whether the channel claims its answer is the whole of every board it asked
  -- for. Only a complete listing can be evidence a missing posting was taken
  -- down, and `listingComplete` has meant exactly this since the file store.
  listing_complete boolean not null default false,
  -- Kept rather than dropped, on the rule this repository has paid for three
  -- times: the occurrence able to name a cause is the one guaranteed not to be
  -- reproducible later.
  reason       text,
  unique (account_id, sweep_id, source)
);

create index if not exists sweep_runs_recent_idx
  on sweep_runs (account_id, source, started_at desc);

-- The postings themselves, one row per posting per sweep, in the shape
-- `base.posting` already produces. Staging rather than storage: the device
-- claims a sweep, merges it into the pool, and the rows are pruned.
--
-- **jsonb rather than columns, deliberately.** These are not the corpus --
-- `postings` is, and it is written by the device's own dual-write after the
-- merge and the gate have run. A second column-per-field table would be a
-- second definition of a posting that could disagree with `posting_row`, which
-- is the shape this project has met five times. The device parses this back
-- into exactly what a connector returned and runs the existing path over it.
create table if not exists sweep_postings (
  id          bigserial primary key,
  account_id  uuid not null,
  sweep_id    uuid not null,
  source      text not null,
  -- The connector's own record: `base.posting` output, unmodified.
  posting     jsonb not null,
  created_at  timestamptz not null default now()
);

create index if not exists sweep_postings_claim_idx
  on sweep_postings (account_id, sweep_id);
