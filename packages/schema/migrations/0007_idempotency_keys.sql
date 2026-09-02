-- One row per accepted mutation, so a retry returns the first answer rather
-- than doing it twice.
--
-- **Before anything is built on top of the write path, not after.** Retrofitting
-- idempotency means auditing every caller that already exists; adding it first
-- means every caller is born with it. A double-tap on a phone is then a
-- category of bug that never opens.
--
-- `request_digest` is here because a key reused with a *different* body is a
-- client fault, and returning the first response to it would answer a question
-- nobody asked. That case is refused loudly rather than served from the cache.
--
-- Kept 24 hours. Long enough to cover a retry after a network stall or an app
-- resume, short enough that the table stays small; the sweep is a `delete`
-- where `created_at` is older, which the index below serves.
--
-- Not a legacy name: `to_regclass('idempotency_keys')` is null and the name does
-- not appear in the twenty tables `public` holds. Checked, because
-- `create table if not exists` matches on the name and a differently-shaped
-- table holding it makes this a silent no-op that still reports success, which
-- is exactly what happened to `answers` in 0002.

create table if not exists idempotency_keys (
  account_id     uuid        not null references accounts(id) on delete cascade,
  key            text        not null,
  request_digest text        not null,
  status         smallint    not null,
  response       jsonb       not null,
  created_at     timestamptz not null default now(),
  primary key (account_id, key)
);

create index if not exists idempotency_keys_expiry
  on idempotency_keys (created_at);
