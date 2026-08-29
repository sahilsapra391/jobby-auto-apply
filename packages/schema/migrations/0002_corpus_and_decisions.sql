-- The §3 schema: a global job corpus, and per-account decisions about it.
--
-- Expand-only. Nothing here touches the seven unmanaged tables, and the legacy
-- jsonb `jobs` table keeps its name and its rows: it is the backfill source and
-- is dropped in a later contract migration once parity has been green for a
-- week. Two tables named `jobs` cannot coexist, so the new corpus is `postings`
-- until that contract migration renames it. That is a deviation from §3's
-- naming and it is deliberate: the alternative is dropping the backfill source
-- in the same release that starts reading from its replacement, which is
-- exactly what expand-then-contract exists to prevent.
--
--
-- Three additions to §3, each because §3 as written cannot be populated.
--
-- `postings.legacy_id`. §3 gives `jobs` a uuid primary key and no other
-- identity. Every application, every decision, every board link and every R2
-- artifact path in this repository references the existing 16-hex record id,
-- which is `sha1(dedupe_key)[:16]` from scripts/identity.py. Without a column
-- holding it, nothing existing can find its row and parity cannot match a
-- record to its counterpart. It is `not null unique` because it is the real
-- identity today and the uuid is the identity Phase 5 will hand out.
--
-- `employers.norm_name`. §3 has `canonical_name text not null` and `domain text
-- unique`, and 13,622 of 20,103 records have no employer domain at all, so
-- `domain` cannot be the upsert key. Names arrive with varying case and
-- punctuation, so the key is the normalised form, from identity.norm_company,
-- and `canonical_name` is what a person reads.
--
-- `postings.workplace`. §3 carries `remote boolean not null default false`,
-- which cannot represent the three values the store actually holds: remote
-- (3,768), hybrid (1,940) and onsite (974), against 13,421 that say nothing.
-- Collapsing those to a boolean makes "we do not know" and "we know it is not
-- remote" the same answer, which is the shape of mistake this repository keeps
-- paying for. `remote` is kept because §5's connectors and §6's filters are
-- written against it, and it is derived from `workplace`.

create table if not exists employers (
  id             uuid        primary key default gen_random_uuid(),
  canonical_name text        not null,
  norm_name      text        not null unique,
  domain         text        unique,
  logo_url       text,
  created_at     timestamptz not null default now(),

  constraint employers_norm_name_is_lowercase
    check (norm_name = lower(norm_name))
);

comment on column employers.norm_name is
  'identity.norm_company(canonical_name). The join and upsert key, because '
  'a domain is known for under a third of postings.';


create table if not exists postings (
  id             uuid        primary key default gen_random_uuid(),
  legacy_id      text        not null unique,

  source         text        not null,
  board_slug     text        not null,
  external_id    text        not null,
  employer_id    uuid        not null references employers(id),

  title          text        not null,
  norm_title     text,
  location_raw   text,
  city           text,
  region         text,
  country        char(2),
  remote         boolean     not null default false,
  workplace      text,

  posted_at      timestamptz,
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null,
  closed_at      timestamptz,

  apply_url      text        not null,
  years_min      smallint,
  years_max      smallint,
  years_text     text,

  -- <= 2 KB, for cards and search. The full body lives in R2 under
  -- body_r2_key and is fetched only when a card is opened or a document is
  -- built. Measured: the store's excerpts are capped at 1,500 characters, so
  -- nothing is lost today and the ceiling is here for what ingest writes next.
  summary        text,
  body_r2_key    text,

  desc_fingerprint text,
  seen_count     integer     not null default 1,
  salary_raw     text,
  lang           text,
  tags           text[]      not null default '{}',
  sources        jsonb       not null default '[]'::jsonb,
  sections       jsonb,

  search_tsv     tsvector generated always as (
                   setweight(to_tsvector('english', coalesce(title, '')),   'A') ||
                   setweight(to_tsvector('english', coalesce(summary, '')), 'B')
                 ) stored,

  constraint postings_summary_fits
    check (summary is null or length(summary) <= 2048),
  constraint postings_country_is_two_letters
    check (country is null or country ~ '^[A-Z]{2}$'),
  constraint postings_years_are_ordered
    check (years_min is null or years_max is null or years_min <= years_max),
  constraint postings_seen_at_least_once
    check (seen_count >= 1),
  constraint postings_sources_is_an_array
    check (jsonb_typeof(sources) = 'array'),

  unique (source, board_slug, external_id)
);

comment on column postings.legacy_id is
  'The 16-hex record id every existing application, decision and artifact '
  'path references. sha1(dedupe_key)[:16], from scripts/identity.py.';
comment on column postings.closed_at is
  'Null means open. Set from the legacy `closedAt`, which the store sets if '
  'and only if state is `expired`: measured 1,298 of 1,298 on 2026-08-29.';
comment on column postings.workplace is
  'remote | hybrid | onsite, or null for not stated. `remote` is derived '
  'from it; this keeps "not stated" distinguishable from "not remote".';

-- §3's indexes.
--
-- `nulls last` is on every posted_at ordering and it is load-bearing. In
-- PostgreSQL `order by x desc` puts nulls FIRST, so an index written without
-- it puts the 1,942 postings with no posting date at the top of a board sorted
-- newest-first. A date nobody stated is not the newest thing in the pool, and
-- the ordering the index supports has to be the ordering the query asks for or
-- the index is simply not used.
create index if not exists postings_search
  on postings using gin (search_tsv);
create index if not exists postings_open_recent
  on postings (posted_at desc nulls last) where closed_at is null;
create index if not exists postings_open_country
  on postings (country, posted_at desc nulls last) where closed_at is null;
create index if not exists postings_keyset
  on postings (posted_at desc nulls last, id desc) where closed_at is null;
create index if not exists postings_employer
  on postings (employer_id) where closed_at is null;
create index if not exists postings_legacy_id
  on postings (legacy_id);


-- Account-scoped tables. `account_id` is present and populated from here on;
-- row-level security is Phase 5 and is deliberately not enabled yet, because
-- enabling it against an owner role that bypasses it silently would look like
-- it worked. See docs/adr/0001, which records that `neondb_owner` has
-- rolbypassrls = true.

create table if not exists profiles (
  account_id       uuid        primary key references accounts(id) on delete cascade,
  base_resume_text text,
  identity         jsonb       not null default '{}'::jsonb,
  updated_at       timestamptz not null default now()
);

create table if not exists preferences (
  account_id    uuid        primary key references accounts(id) on delete cascade,
  keywords      text[]      not null default '{}',
  countries     char(2)[]   not null default '{}',
  posted_within interval,
  updated_at    timestamptz not null default now()
);

create table if not exists answers (
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

create table if not exists job_decisions (
  account_id   uuid        not null references accounts(id) on delete cascade,
  posting_id   uuid        not null references postings(id) on delete cascade,
  state        text        not null,
  decided_at   timestamptz not null default now(),
  decided_from text,
  primary key (account_id, posting_id),

  -- Absence of a row means Found, which is what keeps the Found lane a set
  -- difference rather than a per-account copy of the corpus. So `found` is
  -- not a legal stored value: a row saying `found` and no row at all would be
  -- two spellings of one state, and the lane would have to check both.
  constraint job_decisions_state_is_known check (state in (
    'watching', 'dropped', 'queued', 'applying', 'needs_you', 'submitted'))
);

create index if not exists job_decisions_by_state
  on job_decisions (account_id, state);

create table if not exists applications (
  id         uuid        primary key default gen_random_uuid(),
  account_id uuid        not null references accounts(id) on delete cascade,
  posting_id uuid        references postings(id) on delete set null,
  legacy_id  text,
  status     text        not null,
  phase      text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (account_id, legacy_id)
);

create table if not exists application_events (
  id             bigserial   primary key,
  account_id     uuid        not null references accounts(id) on delete cascade,
  application_id uuid        not null references applications(id) on delete cascade,
  kind           text        not null,
  payload        jsonb       not null default '{}'::jsonb,
  at             timestamptz not null default now()
);

create index if not exists application_events_by_application
  on application_events (application_id, at desc);

create table if not exists devices (
  id             uuid        primary key default gen_random_uuid(),
  account_id     uuid        not null references accounts(id) on delete cascade,
  name           text,
  pubkey         text        not null unique,
  runner_version text,
  last_seen_at   timestamptz,
  revoked_at     timestamptz,
  created_at     timestamptz not null default now()
);

create table if not exists provider_links (
  account_id   uuid        not null references accounts(id) on delete cascade,
  provider     text        not null,
  status       text        not null,
  model_pinned text,
  connected_at timestamptz,
  updated_at   timestamptz not null default now(),
  primary key (account_id, provider)
);

comment on table provider_links is
  'That a provider is connected and which model is pinned. Never a key: the '
  'key stays in ~/.job-application at 0600 and the device reports status up.';

create table if not exists submit_tokens (
  id             uuid        primary key default gen_random_uuid(),
  account_id     uuid        not null references accounts(id) on delete cascade,
  application_id uuid        not null references applications(id) on delete cascade,
  issued_at      timestamptz not null default now(),
  expires_at     timestamptz not null,
  consumed_at    timestamptz
);

-- §3's submit invariant, moved out of code and into the database so it holds
-- even against a buggy or replayed runner. Consuming a token is
--   update submit_tokens set consumed_at = now()
--    where id = $1 and consumed_at is null returning id
-- and zero rows returned means the run does not get to submit. Not a check, an
-- outcome.
create unique index if not exists one_completion_per_application
  on application_events (application_id) where kind = 'completed';

-- §3 also asks for
--   create unique index one_consumption_per_token
--     on submit_tokens (id) where consumed_at is not null;
-- and that one is not created here, because `id` is already the primary key.
-- A partial unique index on a column that is unique over the whole table
-- enforces nothing the primary key does not, and shipping it would suggest to
-- the next reader that single-consumption is enforced by something other than
-- the conditional UPDATE. It is the `where consumed_at is null` in that
-- statement that does the work: the second attempt matches no row.
-- Recorded rather than silently omitted.
