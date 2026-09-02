-- RECONSTRUCTED FORWARD, like 0008. Read that file's header first.
--
-- Production's `provider_links` carries five columns no migration declares:
-- `connection_order`, `health`, `detail`, `last_checked_at` and
-- `model_verified_at`. They were added outside the migration system, by code
-- that is not in this repository, and they are exactly the shape
-- `providers.json` has -- so whoever added them meant to mirror the registry
-- and did not finish: the table held zero rows.
--
-- Declared here so the repository can rebuild what production has. `add column
-- if not exists` throughout, so applying it to production is a no-op and
-- applying it anywhere else catches up.
--
-- **`primary` is the other half of the same drift and is deliberately not
-- touched.** 0002 declares it and production does not have it. Dropping it
-- from 0002 would edit an applied migration; adding it to production would
-- invent a column nothing reads. It is left disagreeing, visible in
-- `scripts/schema_drift.py`, until somebody decides which of the two was
-- intended -- `connection_order` looks like its replacement, since failover
-- order is what the Settings page actually explains.
--
-- What these hold, from `providers.json`, which is the only description of
-- them that exists:
--
--   connection_order    the failover order. New work probes connected
--                       accounts in this order and uses the first that can
--                       invoke its pinned model.
--   health              the last probe's verdict: healthy, or why not.
--   detail              the human sentence the Settings card shows under the
--                       provider name.
--   last_checked_at     when the health probe last ran.
--   model_verified_at   when the pinned model was last confirmed served. A
--                       remap or a missing telemetry record fails closed, so
--                       this is not the same as last_checked_at.
--
-- **No credential column, and there must never be one.** `providers.json` also
-- carries a `connectionKey`, and it is absent here on purpose: the board needs
-- to know a provider is connected, healthy and in what order, and needs none
-- of the credential machinery to say so. Keys live in
-- `~/.job-application/provider-secrets/` and reach no queue, no Vercel
-- environment and no KV, which a mirror is exactly the path that would break.

alter table provider_links
  add column if not exists connection_order  integer,
  add column if not exists health            text,
  add column if not exists detail            text,
  add column if not exists last_checked_at   timestamptz,
  add column if not exists model_verified_at timestamptz;
