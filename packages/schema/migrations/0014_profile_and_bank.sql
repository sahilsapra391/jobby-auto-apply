-- What the Profile page reads: the resume registry, and what each Memory Bank
-- entry is.
--
-- **`answer_categories` owns the taxonomy and it is word-bounded Python.**
-- Three kinds of record share `answers.json` -- 121 questions an employer
-- actually asked, 23 of the user's own standing policies, 12 story-bank
-- entries -- and thirteen categories cover the questions. That module's own
-- docstring records three afternoons lost to substring matching (`india` in
-- `Indiana`, `age` in `average`, `race` in `embrace`) and one more inside its
-- own first draft. A TypeScript copy of those regexes would be a fourth
-- chance at the same afternoon, so the answer is stored rather than
-- recomputed, exactly as migration 0013 does for the employer mark.
--
-- `kind` is question, policy or story. `category` is one of the thirteen or
-- `other`, and only meaningful for a question.
--
-- **`resumes` travels and `writingSamples` still does not**, and the existing
-- exclusion's reason is why the line falls there. It says mirroring them
-- "would put the same wording in two places with no rule about which wins",
-- which is about wording: the resume *content* and the writing samples. The
-- registry is metadata -- which resumes exist, which countries each applies
-- to, which is the default, and the identity block each carries. The Profile
-- page cannot draw a base resume card without it and no document is rendered
-- from it.
--
-- The absolute path does not travel. `/Users/sahilsapra/Documents/Resumes/...`
-- is device-specific and says nothing a reader needs; the filename is what a
-- card shows. That also keeps the one genuinely machine-local fact out of a
-- database that Phase 5 opens to more than one device.

alter table answer_bank
  add column if not exists kind     text,
  add column if not exists category text;

alter table profiles
  add column if not exists resumes jsonb;
