-- The reusable answers, under a name the legacy store does not already hold.
--
-- Migration 0002 wrote `create table if not exists answers (...)` with §3's
-- shape. It did nothing, because `public.answers` already existed: a legacy
-- `(slug, record)` jsonb table with 190 rows, created by
-- scripts/migrate_to_postgres.py long before any of this. `if not exists`
-- matches on the *name*, so a differently-shaped table holding that name makes
-- the statement a silent no-op and the migration reports success.
--
-- **The tests could not see it and it is worth being precise about why.** Every
-- database test runs in its own scratch schema, which is what keeps them from
-- writing the live store. A scratch schema has no legacy tables, so `answers`
-- was free there and the migration created it correctly on every run. The
-- isolation that makes the tests safe is the same isolation that hides a
-- collision with `public`. `tests/test_corpus_schema.py` now seeds the legacy
-- names into its scratch schema before migrating, which is the only way that
-- class of failure can be reached from a test.
--
-- The same trap was avoided by accident for the corpus: `postings` was chosen
-- over §3's `jobs` deliberately, because `public.jobs` obviously existed. The
-- lesson is that "obviously" was doing the work, and it did not extend to a
-- table nobody was thinking about.
--
-- `answer_bank` rather than `answers`, and it is not a placeholder name to be
-- renamed later. It is what the code already calls this: `store._bank_write`,
-- `store.bank_version`. The legacy `answers` table is dropped in the same
-- contract migration as the legacy `jobs`, once parity has been green for a
-- week; renaming this one afterwards would be a second contract migration to
-- buy a word.

create table if not exists answer_bank (
  account_id uuid        not null references accounts(id) on delete cascade,
  key        text        not null,
  question   text,
  value      text,
  state      text,
  tags       text[]      not null default '{}',
  sensitive  boolean     not null default false,
  updated_at timestamptz not null default now(),
  primary key (account_id, key)
);

comment on table answer_bank is
  '§3 calls this `answers`. That name is held by a legacy (slug, record) table '
  'until the contract migration drops it, and `create table if not exists` '
  'against a taken name is a silent no-op.';
comment on column answer_bank.sensitive is
  'Kept on the record even though nothing masks on it today. It is what a '
  'per-user board will mask on and what Class 2 field encryption will key on, '
  'and re-deriving it later from question text alone would lose every row the '
  'answer store''s own state caught.';

create index if not exists answer_bank_by_state
  on answer_bank (account_id, state);
