-- What an application is waiting on a person for.
--
-- **This is the Needs you lane's entire content and nothing was mirroring it.**
-- v2 drew "blockers are not mirrored yet" on every card, which was accurate:
-- 61 pending fields across 30 sessions existed only in
-- `~/.job-application/sessions/*.json`, and no column in any table held one.
-- Parity could not have caught it either, because it compared one string per
-- session; see 0008's sibling work in `scripts/mirror_fields.py`.
--
-- **jsonb, holding the list verbatim, and deliberately not normalised into
-- columns.** Measured over the real store: 61 entries across eight different
-- key shapes -- `(field, question, reason)` 52 times, `(question,)` three
-- times, and six one-off combinations of `action`, `blocker`, `buys`, `detail`,
-- `note`, `options`, `why`. Picking columns would mean choosing which of those
-- survive, and the ones that would be dropped are the ones a person needs:
-- "activate the I'm interested link on the open Swiggy posting" lives in
-- `field` on one record and in `blocker` on another.
--
-- **And it is not a question, whatever the key is called.** CLAUDE.md records
-- the measurement: of 29 blocker strings sampled, 0 were question-shaped; every
-- one was agent prose. `answer_record.py` must never join these into an
-- application's answers, and the Needs you lane renders them as what they are,
-- which is a note to the reader about what is in the way.
--
-- Null and absent mean the same thing here: nothing is blocked. An empty list
-- would be a third spelling of it.

alter table applications
  add column if not exists blockers jsonb;
