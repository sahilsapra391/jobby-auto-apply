-- The version an answer edit is made against.
--
-- **Without it v2 cannot edit an answer that has been edited before.** Every
-- Memory Bank action carries the version it was made against -- `answer-edit`,
-- `answer-delete`, `answer-dismiss` and `answer-restore` all take `--version`,
-- and `store.bank_version` refuses a mismatch as `conflict`. That refusal is
-- the guarantee and it is correct; what was missing is the number to send.
--
-- `class_b.answer_row` carried question, value, state, tags, sensitive, kind
-- and category, so v2 read no version and sent 0. Measured 2026-09-06 over all
-- 195 stored entries: 156 at version 0, 38 at 1, 1 at 3. So 39 were already
-- uneditable from v2, and 156 was a ceiling that falls to zero with use,
-- because the first successful edit takes a row to 1 and v2 would still say 0.
-- A bank that becomes uneditable as it is used is a bug with a delay on it.
--
-- **Nothing is backfilled and nothing needs to be.** The device's own
-- `answers.json` holds every value; the first sweep after this fills the
-- column from what is already there. A default of 0 is also the truth for an
-- entry that has never been edited, which is what `bank_version` returns for
-- a record with no `version` key.
--
-- The Python board does not need this: `profile_panel.py` reads the file
-- directly and has the version in hand. This is v2-only, introduced by which
-- fields the mirror carried rather than by anything about the write path.

alter table answer_bank
  add column if not exists version integer not null default 0;

comment on column answer_bank.version is
  'The optimistic-concurrency version store.py compares an edit against. '
  'Mirrored so a board can send the version it read; a board that guesses is '
  'refused, correctly, and could never edit a row twice.';
