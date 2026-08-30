-- The legacy records folded into a posting when they shared its board key.
--
-- Two stored records can derive one `(source, board_slug, external_id)`, which
-- is the unique index doing its job: it means two rows for one posting. On the
-- real pool there is exactly one such pair, the same Braze posting reached
-- once through `boards.greenhouse.io` and once through
-- `job-boards.greenhouse.io`, which the legacy URL-keyed dedupe kept as two.
--
-- The loser gets no row of its own, and that left parity with a permanent
-- divergence: a legacy id present in the file store and findable nowhere in
-- Postgres. Measured after the first full resync, it was the only difference
-- left, 1 of 16,123.
--
-- **The fix is to record the fact, not to exempt it.** An exemption in the
-- parity comparison is a filter, and a filter is where the 4,779 missing
-- records hid the first time. `merged_legacy_ids` makes the loser findable:
-- the record is represented in Postgres, under the row that won, and parity
-- resolves it by looking there. Nothing is hidden and nothing is lost.
--
-- It is an array rather than a second table because it is read on exactly one
-- path, the parity lookup, and is written only when a collision happens. A
-- join table for a column that holds one element across the whole corpus would
-- be machinery in place of a fact.

alter table postings
  add column if not exists merged_legacy_ids text[] not null default '{}';

comment on column postings.merged_legacy_ids is
  'Legacy record ids that share this posting''s board key and have no row of '
  'their own. Parity resolves a file record through here when legacy_id '
  'misses, so a merge is a recorded fact rather than an exemption.';

-- GIN, because the lookup is `merged_legacy_ids && array[...]` over the whole
-- corpus and a sequential scan of 16,123 rows on every parity run is the kind
-- of cost that gets a check switched off.
create index if not exists postings_merged_legacy_ids
  on postings using gin (merged_legacy_ids);
