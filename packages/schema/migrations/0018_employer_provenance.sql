-- Who decided a posting's employer, and what the decision replaced.
--
-- **Added before the resolver, because the resolver's write is the one this
-- project cannot take back.** Measured 2026-09-06: exactly two foreign keys
-- reference `employers` (`postings_employer_id_fkey` and
-- `employers_merged_into_fkey`), `job_scores` carries no employer material,
-- and there is no audit table. So nothing anywhere references the
-- posting-to-employer edge.
--
-- That matters because of the method §11c of `docs/plans/phase3-inputs.md`
-- generalises from the fold: when a change rewrites a row, verify it through a
-- table that references the row and was written earlier. `job_scores` was that
-- witness for the fold. **The employer edge has none**, so the method fails on
-- its very next case, and §9a's scale is why nobody noticed -- at six rows the
-- backfill's stdout print is the before-image; at thousands it is not.
--
-- Two columns and one table, in the cheapest moment there will ever be to add
-- them: before 25,000 rows exist under a rule that recorded nothing.

-- Which rule chose this posting's employer, and when.
--
-- `null` means "decided before this column existed", which is every row today
-- and is the honest reading. It is deliberately not backfilled to 'ingest':
-- that would be a claim about how each of 25,000 rows was decided,
-- reconstructed from the fact that nothing is complaining now, which is the
-- fiction `answer_record.py`'s salvage refuses by name.
alter table postings
  add column if not exists employer_decided_by text,
  add column if not exists employer_decided_at timestamptz;

comment on column postings.employer_decided_by is
  'The rule that chose this posting''s employer: ''ingest'' for the arrival '
  'path at backfill.py:512, or the resolver rule''s own name. Null means '
  'decided before provenance was recorded, and is never backfilled.';

-- What a re-decision replaced, written in the same transaction as the write.
--
-- **The revert path, because there is no other one.** A repointed
-- `employer_id` is never re-derived: a record already in the store is not in
-- the mirror's delta, so no sweep will reconsider it, and no other table holds
-- the old edge. Without this row the previous value is gone the instant the
-- update commits.
--
-- Not an event log and not a general audit table. One row per edge this
-- project deliberately changed, so a revert is a script that reads it back and
-- a review is a query rather than a diff of two corpora.
create table if not exists employer_decisions (
  account_id   uuid        not null references accounts(id) on delete cascade,
  -- The posting whose edge moved. Not a foreign key on purpose: the row has to
  -- outlive a posting that is later pruned, or the audit trail disappears
  -- exactly when somebody is trying to work out what happened to it.
  posting_id   uuid        not null,
  legacy_id    text        not null,
  -- Both sides of the move. `from_employer_id` is null when the posting had
  -- none, which is distinguishable from "not recorded" because a row exists
  -- at all.
  from_employer_id uuid,
  to_employer_id   uuid    not null,
  from_norm_name   text,
  to_norm_name     text    not null,
  -- The rule that decided, and the evidence it decided on. `because` is the
  -- rule's own words, the way `edit_outcomes.reason` carries the store's:
  -- a decision a reader cannot compare against is one they cannot act on.
  decided_by   text        not null,
  because      text,
  decided_at   timestamptz not null default now(),
  -- One row per posting per run, so a re-run of the same backfill is
  -- idempotent rather than a second entry saying the same thing.
  primary key (account_id, posting_id, decided_by)
);

comment on table employer_decisions is
  'Every posting-to-employer edge this project changed deliberately, with what '
  'it replaced. It is the revert path: the edge is never re-derived, the '
  'delta path will not reconsider a stored row, and no other table references '
  'it. Measured 2026-09-06: two foreign keys reference employers and neither '
  'is a witness to this edge.';

create index if not exists employer_decisions_recent
  on employer_decisions (account_id, decided_at desc);
create index if not exists employer_decisions_by_run
  on employer_decisions (account_id, decided_by);
