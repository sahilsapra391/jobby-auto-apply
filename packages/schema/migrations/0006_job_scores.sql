-- The match score, which decides what the Found lane shows first.
--
-- **Account-scoped, not a column on `postings`.** The score is computed from
-- the reader's own saved search: the same posting scores differently for two
-- people, so a column on the shared corpus would be one account's opinion
-- stored as a property of the job. ADR 0007 and §6 both say `job_scores`, and
-- Phase 5 is the reason -- putting it on `postings` works perfectly until the
-- second account exists and then silently serves them each other's ranking.
--
-- Not a legacy name. `create table if not exists` matches on the *name*, so a
-- differently-shaped table already holding it makes this a silent no-op and
-- the migration still reports success, which is exactly what happened to
-- `answers` in 0002. Checked against the live database before writing this:
-- `to_regclass('job_scores')` is null, and the name does not appear in the
-- twenty tables `public` holds.
--
-- The rubric travels whole. `total` is what the lane sorts on, and `parts`,
-- `why`, `gates` and `unknowns` are what the card's breakdown draws; splitting
-- them would mean a second read for every opened card. They are small: five
-- integers, five interned strings, and two usually-empty lists.
--
-- `config_version` is here so a score can be told from a stale one. Nothing
-- reads it yet. When preference-driven invalidation lands (ADR 0007), this is
-- the column it keys on, and inventing it later would mean a backfill over
-- every row rather than a comparison.

create table if not exists job_scores (
  account_id     uuid        not null references accounts(id) on delete cascade,
  posting_id     uuid        not null references postings(id) on delete cascade,
  total          smallint    not null,
  parts          jsonb       not null default '{}'::jsonb,
  why            jsonb       not null default '{}'::jsonb,
  gates          jsonb       not null default '[]'::jsonb,
  unknowns       jsonb       not null default '[]'::jsonb,
  config_version text,
  computed_at    timestamptz not null default now(),
  primary key (account_id, posting_id)
);

-- The Found lane's other ordering. `nulls last` is not needed here because
-- `total` is NOT NULL, but the tie-breaker is, for the same reason every other
-- lane has one: a keyset over a non-unique column repeats or skips rows at the
-- page boundary without it.
create index if not exists job_scores_lane
  on job_scores (account_id, total desc, posting_id desc);
