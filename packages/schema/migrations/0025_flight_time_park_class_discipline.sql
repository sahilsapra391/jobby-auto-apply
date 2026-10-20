-- Three session fields the board draws and the mirror never carried.
--
-- **`phase_at`: when the run last moved.** `phase.py` stamps `phaseAt` on
-- every `set_phase`, and v1 used it twice: In flight was ordered by it, newest
-- first, and the card's last cell printed it as a clock. v2 had neither,
-- because the column did not exist, and fell back to `updated_at`, which the
-- class B mirror stamps `now()` on every pass. So the time on every In flight
-- card was the mirror's last write, which looks like a run moving when the
-- run has not.
--
-- **`park_class`: a park that is Jobby's fault rather than the reader's.**
-- `run_apply.py` writes `parkClass: capability` when a run parks having made
-- no browser call at all, and clears it when a later run reaches the form.
-- v1 drew such a card red with "system fault" and a note saying the step is not
-- the reader's. v2 left the branch out because no session carried the key when
-- it was measured, which is true and is the expected state: the key exists only
-- while that fault is live. The writer is four call sites in `run_apply.py`,
-- so the branch is reachable the next time the browser server dies, which is
-- exactly when a reader needs the card to say whose problem it is.
--
-- **`discipline`: the Watching filter's third facet.** The board's Watching
-- select offers "Posted in last 7 days", each region, and each discipline;
-- region was already mirrored from the role index and discipline was not.
--
-- All nullable with no default. A session without the key has none of these
-- facts, and null is how the board already reads "not recorded". Rows that
-- predate the columns are filled from the sessions on disk by
-- `class_b.backfill`, the way 0024 filled `phase_detail`: copying a stamp the
-- device wrote at the time is a mirror, not an invention.
alter table applications
  add column if not exists phase_at timestamptz,
  add column if not exists park_class text,
  add column if not exists discipline text;
