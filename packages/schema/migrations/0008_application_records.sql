-- RECONSTRUCTED, not authored. Read this before changing it.
--
-- **This file was written from the live schema, after the migration it names
-- had already been applied to production.** Version 8 was recorded in
-- `schema_migrations` at 2026-09-02T04:09:47Z, three seconds after 0007, by a
-- session whose file never reached the repository and is not on any branch.
-- `migrate.py plan` then refused every subsequent migration, correctly: a
-- schema that cannot be reproduced from the repository is a schema nobody can
-- deploy again.
--
-- So this is a `pg_dump`-equivalent of `public.board_meta` as it stood, not the
-- original text. What that costs, stated plainly:
--
--   * the checksum will not match the recorded one, and that is expected. The
--     recorded row keeps the original's checksum; `migrate.py` is what decides
--     whether a mismatch is fatal, and this file exists so the table can be
--     created on a clean deployment rather than to satisfy a hash.
--   * the original's comments are gone. Whatever reasoning it carried about
--     why these columns exist and why `blocked` and `slots_parked` are text
--     rather than structured is not recoverable from the schema.
--   * anything it did that left no trace in the final shape -- a backfill, a
--     one-off UPDATE, a dropped column -- is invisible here.
--
-- **If the original ever surfaces, diff it against this file rather than
-- replacing it silently.** A difference is a statement about what production
-- ran and this file does not; it is not a formatting question.
--
-- The name is the recorded one. It says `application_records` and it creates a
-- table called `board_meta`, which is itself a small piece of evidence that
-- the original did more than this, or was renamed mid-flight.

-- What the board knows about itself: when the pool was last swept, when the
-- next sweep is due, and what is in the way. v2's scan banner reads these; it
-- has been drawing a hardcoded fixture instead, which is why it showed a next
-- run in the past.
create table if not exists board_meta (
  account_id        uuid        not null primary key
                    references accounts(id) on delete cascade,
  last_ingest       jsonb,
  next_scan_at      timestamptz,
  scan_requested_at timestamptz,
  scan_started_at   timestamptz,
  scan_finished_at  timestamptz,
  blocked           text,
  slots_parked      text,
  search_config     jsonb,
  updated_at        timestamptz not null default now()
);
