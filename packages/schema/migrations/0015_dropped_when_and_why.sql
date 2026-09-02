-- When a role was dropped, and the reason if one was given.
--
-- **The Dropped lane's date column was the backfill time on all 58 rows.** It
-- drew `applications.updated_at`, which the class B mirror stamps `now()` on
-- every pass, so a role dropped on 19 August and one dropped yesterday both
-- read as today. That is the same defect `completed_at` fixed for Submitted,
-- one lane over: a column that means "when did we last write this row" standing
-- in for one that means "when did this happen".
--
-- Measured over the real store on 2026-09-02: 58 dropped sessions, 58 carrying
-- `droppedAt`, 58 carrying `droppedFrom` and `droppedStage` -- which were
-- already mirrored -- and 2 carrying `droppedReason`.
--
-- **The reason is mirrored even though only two rows have one.** The board
-- prints it under the stage, and a column that exists for 2 of 58 is a column
-- that tells you why those two went; deriving "no reason recorded" from its
-- absence is exactly what the other 56 mean. Adding it later would cost
-- another migration and another parity clock for the same two rows.
--
-- Null is "not recorded" for both. A dropped application with no drop time is
-- possible -- the pre-§3 rows carry whatever the backfill found -- and a zero
-- or an epoch would be a second spelling of not knowing.

alter table applications
  add column if not exists dropped_at timestamptz,
  add column if not exists dropped_reason text;
