-- What only the session blob was carrying, given columns of its own.
--
-- 0011 declares the eight columns production already had. These six are the
-- other half of the same measurement: facts that exist **only** inside
-- `applications.session`, which that migration explains why it will not
-- declare. Each is a lane drawing "not captured" over data that exists.
--
--   core_skills     the skill pills Watching shows. 53 of 96 sessions carry
--                   them; v2 renders "not captured" for all 17 rows.
--   fit_score       the match percentage, 50 of 96.
--   alignment       the rubric sentence behind it, 50 of 96: "Match 89% --
--                   title 35/35, experience 20/20, location 15/15, keywords
--                   9/20, freshness 10/10. Rubric only; no fit read written
--                   yet." That whole string is what HOW YOU LINE UP is for,
--                   and it is empty on every v2 row.
--   dropped_from    the lane a dropped application came from, 58 of 96.
--   dropped_stage   how far it had got, 58 of 96, in the reader's words:
--                   "waiting on you: TELUS Digital posting shows Job not
--                   found; reopen or refresh the same posting".
--   seen_on_boards  how many boards carried the posting.
--
-- **`alignment` is prose and `fit_score` is a number, and they are two
-- columns.** The sentence is not derivable from the score -- it names which
-- rubric components were unknown -- and the score is not parseable back out of
-- the sentence without a regex over free text. Storing one and computing the
-- other is the substring-matching mistake in a new place.
--
-- `core_skills` is text[] rather than jsonb: it is a list of slugs with no
-- structure inside them, and an array is what a lane filters on.

alter table applications
  add column if not exists core_skills    text[],
  add column if not exists fit_score      smallint,
  add column if not exists alignment      text,
  add column if not exists dropped_from   text,
  add column if not exists dropped_stage  text,
  add column if not exists seen_on_boards smallint;
