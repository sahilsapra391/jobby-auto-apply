-- RECONSTRUCTED FORWARD, like 0008 and 0010. Read 0008's header first.
--
-- These eight columns are already in production, populated, and declared
-- nowhere. `scripts/schema_drift.py` found them: 25 drifts across 23 live
-- tables against the 17 the migrations build, and nine of those drifts were on
-- this table. `company` and `role` hold values in 204 of 205 rows.
--
-- **The choice was not whether to have them, it was whether to declare them.**
-- Leaving them undeclared means nothing maintains them and parity cannot see
-- them: accurate today, silently stale on the next application, with no
-- mechanism that would ever say so. Declaring makes staleness a divergence
-- instead of a mystery.
--
-- Where each comes from, measured rather than assumed. All 204 populated
-- `company`/`role` pairs match `roles.json` exactly, and `roles.json` carries
-- `company`, `role`, `url`, `region`, `source` and `ats` for 205 entries. So
-- the role index names the application and `class_b._mirror_roles` writes
-- these; the session file, where there is one, is authoritative for the rest.
--
-- `session_deleted_at` is here and empty in every row. It is the difference
-- between an application whose session file was cleaned up after completion
-- and one that never had a session, which the board currently cannot tell
-- apart -- both render as an absence.
--
-- **`session` jsonb is deliberately NOT declared.** It is in production and it
-- is the whole session file as a blob: measured, it carries `pendingFields`
-- for exactly the 30 applications `blockers` covers, plus `status`, `phase`,
-- `company`, `role`, `region`, `source`, `ats` and `url`. Declaring it would
-- make it the fifth spelling of facts three tables already hold, which is what
-- `roles`, `sessions` and `applications` already did to each other. What lives
-- only in that blob gets columns of its own in 0012 instead.

alter table applications
  add column if not exists company            text,
  add column if not exists role               text,
  add column if not exists apply_url          text,
  add column if not exists posted_at          timestamptz,
  add column if not exists region             text,
  add column if not exists source             text,
  add column if not exists ats                text,
  add column if not exists session_deleted_at timestamptz;
