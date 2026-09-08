-- The conditional-request cache, so a container's second sweep is not a cold one.
--
-- **Every cloud sweep was a cold sweep and that was the whole performance
-- story.** `base.Fetcher` keeps ETag and Last-Modified in
-- `<root>/source-cache.json`, and the container's root is `/var/lib/jobby` on
-- an ephemeral machine: wiped on every start. Measured 2026-09-08 on the first
-- full cloud sweep against the laptop's own last one:
--
--     laptop     ashby 193 boards -> 1,050 postings   most boards answer 304
--     container  ashby 193 boards -> 6,855 postings   717s, every board whole
--
-- Same boards, same code, six times the payload, because nothing told the
-- container it had seen them before. No machine size fixes that: a faster CPU
-- downloads 6,855 postings faster, it does not stop downloading them.
--
-- **Keyed by URL rather than hung off `boards`, and the difference is not
-- cosmetic.** The cache's own key is the URL, and 814 of the device's entries
-- include feeds that are not boards at all -- adzuna, careerjet, remoteok --
-- which have no row in `boards` and never will. A column on the board row
-- would have covered the registry-driven half and silently dropped the rest.
--
-- Shared mutable state, deliberately: two workers sweeping different channels
-- both want their own URLs and no other's, and a per-URL upsert gives that
-- where the single JSON file could not (each would write the whole map back
-- and lose the other's entries).
create table if not exists http_validators (
  account_id    uuid not null,
  url           text not null,
  etag          text,
  last_modified text,
  -- When the validator was recorded, not when the URL was last fetched. The
  -- prune below is by this, matching `base.Fetcher`'s own 5,000-entry trim.
  seen_at       timestamptz not null default now(),
  primary key (account_id, url)
);

create index if not exists http_validators_age_idx
  on http_validators (account_id, seen_at desc);
