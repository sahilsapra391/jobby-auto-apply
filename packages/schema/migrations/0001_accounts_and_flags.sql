-- Accounts, and the per-account flags every later cutover is gated on.
--
-- This is the first migration under management. The seven tables already in
-- the Neon database (answers, companies, history, jobs, passed, roles,
-- sessions) were created by scripts/migrate_to_postgres.py before this runner
-- existed and are deliberately not adopted here; see docs/migrations.md, "The
-- unmanaged tables". They are replaced wholesale by the §3 schema in Phase 1,
-- so writing a baseline for them now would be writing history for tables that
-- are about to stop existing.
--
-- Nothing in here references those tables, which is what makes it safe to run
-- against both the populated Neon database and an empty one.

create extension if not exists citext;
create extension if not exists pgcrypto;

-- A flags value is an object whose every value is a real JSON boolean.
--
-- `"false"` is truthy in JavaScript and in Python, and these flags are read by
-- a TypeScript route handler and a Python runner. A column that can hold the
-- string means a flag that is on in the browser and off on the device, with
-- both readers behaving exactly as written. Enforcing it in each reader is a
-- rule the next reader has to remember; enforcing it here is one they cannot
-- get wrong.
--
-- A function because CHECK cannot contain a subquery and `jsonb_each` is a
-- set-returning function. Two details are load-bearing:
--
--   · It is total. `jsonb_each` *raises* on a non-object rather than returning
--     nothing, and an exception thrown inside a CHECK surfaces as "cannot call
--     jsonb_each on a non-object" instead of a named constraint violation. The
--     explicit typeof test is what keeps the failure legible.
--   · `coalesce(..., true)` is what makes `{}` legal: bool_and over no rows is
--     null, and a CHECK returning null passes, so relying on that would mean
--     the empty case works by accident rather than on purpose.
--
-- One constraint rather than two. A separate `jsonb_typeof(flags) = 'object'`
-- check was here first and was removed: once this function is total it rejects
-- a non-object too, so nothing could tell the two constraints apart, and a
-- mutation run deleting the object check broke no test. A guard no test can
-- distinguish is a guard that is not there.
create or replace function jobby_flags_are_boolean(f jsonb)
returns boolean language sql immutable as $$
  select case
    when jsonb_typeof(f) is distinct from 'object' then false
    else coalesce(
      (select bool_and(jsonb_typeof(value) = 'boolean') from jsonb_each(f)),
      true)
  end
$$;

create table if not exists accounts (
  id         uuid        primary key default gen_random_uuid(),
  email      citext      not null unique,
  plan       text        not null default 'free',
  created_at timestamptz not null default now(),

  -- Per-account cutover switches. Phase 1 flips reads to Postgres for one
  -- account before all; Phase 2 does the same for the live board. A flag that
  -- is absent reads as off, which is the only default that makes an
  -- unfinished cutover safe -- see packages/schema/flags.py.
  --
  -- jsonb rather than a flags table because these are read on every request
  -- alongside the account row and written by hand a few times a release. A
  -- join to answer "is this on" costs more than it documents.
  flags      jsonb       not null default '{}'::jsonb,

  -- An object of booleans. `not null` alone is satisfied by `[]` and by `"x"`,
  -- and every read of either returns nothing, which looks exactly like every
  -- flag being off.
  constraint accounts_flags_is_a_boolean_object
    check (jobby_flags_are_boolean(flags))
);

comment on column accounts.flags is
  'Per-account cutover switches. Absent reads as off. Values must be JSON '
  'booleans: a string "false" is true in every language that will read this.';
