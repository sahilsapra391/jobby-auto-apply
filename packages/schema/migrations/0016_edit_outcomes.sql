-- What happened to an edit the reader submitted.
--
-- **A refusal existed only as a log line and a health ping.** `watch.py` builds
-- `changed["answerEditsRefused"]`, prints it, and pings it; nothing persists
-- it, and v2 reads Postgres, so the board could not have shown it however it
-- was drawn. `store.py` can genuinely refuse an edit -- a shape mismatch on the
-- resume wording, a version conflict on an answer -- and its own docstring
-- says why that matters: "An answer the user typed and believes is saved,
-- which silently is not, is the worst outcome available here."
--
-- **Every edit gets a row, not only the refused ones.** An accepted edit and a
-- lost one are indistinguishable if only failures are recorded: both leave
-- nothing, and the reader is told nothing either way. `pending` is written
-- when the edit is enqueued and is the honest state until the device decides.
--
-- **A refusal carries three facts and they do not share a field.** What was
-- submitted, what the store held instead, and the store's own reason. The
-- reader needs all three at once to decide what to do, and `answer_record.py`
-- already establishes the rule: five distinct claims about a question, none
-- allowed to share a field, because blending two costs the ability to tell
-- which you are looking at. A refusal is a different fact from an answer,
-- which is why this is a table and not two columns on `answer_bank`.
--
-- **Nothing is backfilled.** Edits made before this existed have no outcome,
-- and inventing `accepted` for them would be fiction of exactly the kind
-- `answer_record.py`'s salvage refuses -- a claim about what the store did,
-- reconstructed from the fact that it is not complaining now. An absent row
-- means "made before this was recorded", and the board says so.

create table if not exists edit_outcomes (
  account_id  uuid        not null references accounts(id) on delete cascade,
  -- The id the queue already carries. `board.js` enqueues under it and
  -- `watch.py` reports refusals against it, so nothing new has to be minted.
  edit_id     text        not null,
  -- Which queue, because the two are processed by different commands and a
  -- reader looking at a refused resume edit wants to know it was the wording
  -- rather than an answer.
  queue       text        not null,
  state       text        not null,
  -- What the edit was about: an answer key, or a base resume key. Null for the
  -- resume wording, which is one document with no key of its own.
  target      text,
  -- What the reader sent, and what the store held instead. Both jsonb because
  -- an answer is a string and the wording is an object, and a column that had
  -- to be one of those would need a second column for the other.
  submitted   jsonb,
  held        jsonb,
  -- The store's own words, not a paraphrase. `conflict` becomes "the entry
  -- moved to version N" in `watch.py` because that is what the reader can act
  -- on; whatever it says arrives here unchanged.
  reason      text,
  at          timestamptz not null default now(),
  primary key (account_id, edit_id),
  constraint edit_outcomes_state
    check (state in ('pending', 'accepted', 'refused'))
);

-- The board reads one account's recent outcomes, newest first, to draw them
-- beside the fields they belong to.
create index if not exists edit_outcomes_recent
  on edit_outcomes (account_id, at desc);

-- And by target, because the field on the Profile page asks "what happened to
-- my edit of this answer" rather than "what happened lately".
create index if not exists edit_outcomes_target
  on edit_outcomes (account_id, target)
  where target is not null;
