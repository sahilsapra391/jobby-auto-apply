-- The board registry, and the first place a dead board is remembered.
--
-- **`companies.json` already has the fields for this and nothing has ever
-- written them.** Measured 2026-09-07 over all 895 entries: `lastFetchAt` is
-- null on 895 of 895, and `lastStatus` is the literal string "unfetched" on
-- 890 of 895 (the other five say "unresolved (invalid tenant token
-- discarded)", written once by `seed_companies` at seed time). The only two
-- writers are `seed_companies.py:747` and `:848`, both at seeding. No sweep
-- has ever recorded that a board answered.
--
-- So the failure signal today is a per-sweep aggregate and nothing else: the
-- channel note says "6 token(s) 404 and need re-resolving" and names no board.
-- Across the 2026-09-07 sweep that is 9 dead boards out of 638 registry-driven
-- ones -- ashby 2 of 194, greenhouse 6 of 353, lever 1 of 91 -- 54 wasted
-- requests a day at the tier-1 interval, and **no way to say which nine**.
--
-- The waste is small at 895 boards and is not the reason. §5 of the rebuild
-- plan grows this registry by tenant discovery at scale toward 200k postings,
-- where a probe-discovered board that never answers is the common case rather
-- than the rare one, and "one dead board burns a slice of every sweep forever"
-- is a statement about that registry rather than this one.

create table if not exists boards (
  id                   uuid primary key default gen_random_uuid(),
  account_id           uuid not null,
  source               text not null,
  board_slug           text not null,
  employer_id          uuid references employers(id),
  -- How this board entered the registry: a role the user saw, a tenant probe,
  -- a hand entry. Kept because a probe-discovered board that fails is a
  -- different fact from a hand-entered one that fails, and at 200k the first
  -- kind is most of them.
  discovered_via       text,
  last_ok_at           timestamptz,
  last_failed_at       timestamptz,
  -- **Consecutive, not total.** A board that 404s once during an ATS outage
  -- and answers for the next month is healthy, and a total would retire it.
  -- Reset to zero by any success.
  consecutive_failures int not null default 0,
  -- The last failure in the board's own words, kept rather than dropped, on
  -- the rule this repository has paid for three times: the occurrence able to
  -- name the cause is the one guaranteed not to be reproducible later.
  last_failure         text,
  -- Set when the breaker trips. A tripped board is not deleted: it stops being
  -- enqueued, and it stays visible with its reason so a person can see what
  -- the sweep stopped asking for. Cleared by a manual or timed reset.
  disabled_at          timestamptz,
  disabled_reason      text,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (account_id, source, board_slug)
);

-- The sweep's own question: which boards should this run enqueue. It asks for
-- one source's live boards and nothing else, so the index carries the
-- predicate rather than making the planner filter a growing table.
create index if not exists boards_live_idx
  on boards (account_id, source)
  where disabled_at is null;

-- The operator's question, and the one the aggregate could never answer: which
-- boards has the sweep given up on, newest first.
create index if not exists boards_disabled_idx
  on boards (account_id, disabled_at desc)
  where disabled_at is not null;
