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
--
--
-- No extensions, which is a deviation from §3 and is deliberate.
--
-- §3 writes `email citext unique` and a `uuid primary key default
-- gen_random_uuid()`. The property each of those buys is kept; the dependency
-- is not, for two measured reasons.
--
-- `gen_random_uuid()` has been in core since PostgreSQL 13 and lives in
-- pg_catalog. Verified on this database (18.6): it answers with no extension
-- installed. `create extension pgcrypto` bought nothing.
--
-- `citext` is worse than unnecessary, it is environment-dependent. An extension
-- installs its type into one schema, and `if not exists` is satisfied
-- database-wide, so once citext exists in `public` this migration fails with
-- `type "citext" does not exist` anywhere `public` is not on the search_path.
-- That is not hypothetical: applying this to Neon put citext in `public`, and
-- the next run of the test suite, which works inside its own schema, failed on
-- exactly that line. A migration whose success depends on where a previous
-- deployment happened to put an extension is a migration that works until a
-- restore, a second schema, or a schema-per-tenant layout.
--
-- The replacement keeps the guarantee and drops the dependency: emails are
-- stored lower-cased, enforced by a constraint, so a plain unique index is
-- case-insensitive uniqueness. The caller normalises, which it has to do
-- anyway; the difference from citext is that the database says so rather than
-- silently accepting `A@x.com` as a second account.

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
-- set-returning function. Unlike the extension types above, this is safe to
-- define per schema: a CHECK constraint binds the function by OID, so it keeps
-- resolving whatever the search_path later becomes.
--
-- Two details are load-bearing:
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
  email      text        not null unique,
  plan       text        not null default 'free',
  created_at timestamptz not null default now(),

  -- Case-insensitive uniqueness, by making the stored form canonical rather
  -- than by making comparison clever. `unique` above then does the work, and
  -- every lookup is a plain equality against a lower-cased argument instead of
  -- a function call somebody will forget.
  constraint accounts_email_is_lowercase check (email = lower(email)),
  constraint accounts_email_looks_like_one check (email like '%_@_%'),

  -- Per-account cutover switches. Phase 1 flips reads to Postgres for one
  -- account before all; Phase 2 does the same for the live board. A flag that
  -- is absent reads as off, which is the only default that makes an
  -- unfinished cutover safe, including for accounts created before the flag
  -- existed. See packages/schema/flags.py.
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
