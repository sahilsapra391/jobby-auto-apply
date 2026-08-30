-- Where an employer went when another one won its domain.
--
-- `employers.domain` is unique per §3, and on the real corpus that caught
-- something true rather than something malformed: `sharegate.com` is claimed by
-- both `sharegate` and `sharegate migrate chezworkleap`, which are one employer
-- the name normaliser did not merge. One collision in 1,934 employers on
-- 2026-08-29.
--
-- The backfill resolves it by giving the domain to the lexicographically first
-- claimant. That is deterministic, which is what parity needs, and it is also
-- lossy in a way worth fixing before it matters: the loser is left with a null
-- domain and no record of why, so nothing will ever match it to the winner
-- again and the next sweep re-creates the same split.
--
-- `merged_into` is that record. The loser keeps its row, its postings and its
-- name, and points at the employer that holds the domain.
--
-- **This is not a resolver and must not be mistaken for one.** Nothing follows
-- the pointer yet. Reading it is Phase 3 work, where the connector upsert needs
-- an employer resolution step before it can key on anything but the raw name;
-- see docs/employer-identity.md. Until then the column records the one signal
-- the backfill is confident about, so that the information exists when
-- somebody comes to use it.

alter table employers
  add column if not exists merged_into uuid references employers(id);

comment on column employers.merged_into is
  'The employer that won this one''s domain. Set by the backfill only on an '
  'exact normalised-domain collision. Nothing follows it yet: Phase 3 owns '
  'employer resolution. Null means no known duplicate, not "checked".';

-- An employer cannot be merged into itself: that is a cycle of length one and
-- any resolver following the pointer would loop.
alter table employers
  add constraint employers_merge_is_not_self
  check (merged_into is null or merged_into <> id);

create index if not exists employers_merged_into
  on employers (merged_into) where merged_into is not null;
