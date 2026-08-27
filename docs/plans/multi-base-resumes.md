# Multi-base resumes

**Status: plan, not built.** Written for review before any of it is written as
code, because it changes the resume-selection path that every application
depends on.

## What changes

Today a base resume is a region. `profile.json` holds two, `us` and `in`, each
carrying a contact block, and one is marked default. `resume_build.identity_block`
picks between them by matching the posting's country against `appliesTo`, and
falls back to the default when nothing matches. Both render from the same
`templates/resume-content.json`; only the contact line differs.

That is one rule with one dimension, and it is the wrong shape for the thing it
is being asked to do. A person applying to a growth PM role and a platform PM
role wants two different documents whether or not the two postings are in the
same country. A person with eight years of experience applying to a role asking
for three wants a shorter one. Region is one axis of several, and it happens to
be the one that got built first.

So: a base becomes **content plus a rule for when it applies**, an account may
hold any number of them, and the runner picks one per job, deterministically,
and records why.

## The data model

`profile.json` `resumes[]` grows from `{key, path, appliesTo, identity, default}`
to:

```json
{
  "id": "in-growth",
  "label": "India · Growth & lifecycle PM",
  "identity": { "phone": "…", "location": "…", "fullAddress": { … } },
  "content": "in-growth",
  "selector": { "countries": ["IN"], "titleAny": ["growth", "lifecycle", "retention"] },
  "priority": 20,
  "default": false
}
```

Three things worth defending in that shape.

`identity` does not move. It is already per-base and already correct, and it is
the field a wrong answer on is a real error: the India contact block on a US
application is a mistake that has been prevented once and should stay
prevented.

`content` is a *reference*, not the content. The wording lives beside the store
as `resume-content-<id>.json`, the same place the Profile page's editor already
writes the single override today. Inlining several thousand words per base into
`profile.json` makes every profile read carry every base's text, and
`profile-get` is called on every render.

`priority` is an integer the user controls, and it exists because selectors will
overlap and somebody has to win. See below.

## The selector language, and what it can honestly be built on

**Reuse `search_config`'s vocabulary. Do not invent a second filter language.**
It already defines `REGION_PACKS`, `WORKPLACES`, `DEGREE_LADDER`, `SEARCH_SCOPES`
and, most usefully, `UNKNOWN_KEYS`, the explicit list of dimensions where "we
do not know" is a distinct answer from "no". A selector that silently treats
unknown as non-matching would quietly stop selecting.

Which dimensions are actually available is a measurement, not a preference.
Taken against the real 14,293-job pool on 2026-08-26:

| Field | Populated | Usable as a selector |
|---|---|---|
| `location.country` | 94.5% | Yes. The strongest axis by a distance. |
| `normTitle` | 100% | Yes. This is how role targeting has to work. |
| `extracted.yearsMin` / `yearsMax` | 64.1% | Yes, with an explicit unknown branch. |
| `workplace` | 38.9% | Partial. 8,726 records say nothing. |
| `tags` | 39.6% | Partial. |
| `sections` | 88.7% | Partial, and expensive to match against. |
| `extracted.roleCategory` | **0%** | **No. Never populated.** |
| `extracted.degree` | **0%** | **No.** |
| `extracted.employmentType` | **0%** | **No.** |
| `industry` | **0%** | **No.** |
| `salary` | 7.3% | No. |

**This is the finding that shapes the whole feature.** The obvious way to write
this plan is "select by role category, location and experience band". Role
category does not exist. The key is present on all 14,293 records and its value
is null on all 14,293 of them, which is the most misleading possible state: code
written against it compiles, runs, matches nothing, and looks fine. CLAUDE.md
already records the same measurement from 2026-08-25 for the Found card, and it
has not changed since.

So v1 selectors are:

- `countries: ["IN"]` and `regionPacks: ["EU"]`, expanded through
  `search_config.expand_locations` so one definition of what EU means survives.
- `titleAny: ["growth", "lifecycle"]` and `titleNone: [...]`, matched against
  `normTitle` with `match_score`'s existing stemming and negation handling
  (`_stems`, `_phrase_hit`, `_negated`), so "not a growth role" does not match
  "growth".
- `yearsAtMost: 5` / `yearsAtLeast: 8`, against `extracted.yearsMin`, with
  `unknownYears: "match" | "skip"` defaulting to `skip`, following the
  `UNKNOWN_KEYS` precedent.
- `workplaceAny: ["remote"]`, same unknown treatment.

`roleCategory` is deliberately absent. If someone populates it later it becomes
one more key in the same evaluator, which is the point of putting the evaluator
somewhere shared.

## Selection, which must be explainable or it is not shippable

For a job, evaluate every base's selector, keep the matches, take the highest
`priority`. A tie or no match resolves to the base marked `default`. No model is
involved and none should be: this is a predicate over structured fields, it runs
in the runner where the resume is built, and a language model in this path would
make a deterministic choice non-reproducible for no benefit.

Every application records the decision, not just the outcome:

```json
"resumeBase": { "id": "in-growth", "why": "country IN; title matched 'growth'",
                "considered": ["in-growth", "in-general"], "priority": 20 }
```

This is the same honesty the match percentage already carries. A wrong resume on
a posting is otherwise the least diagnosable failure in the system: the document
is plausible, nothing errors, and the only evidence is a person reading their own
resume and noticing it is the wrong one. `roles.json` already keeps a record per
role after the session is deleted, so the base decision belongs there too, or the
reason is lost exactly when someone goes looking for it.

## The gate does not move

Each base is a real base. `resume_build.assert_integrity` and the layout gate run
against it, and a tailored resume built from it must clear the same checks it
clears today. Adding a base must not become the way an unverified layout gets in.

Concretely: a base is not selectable until it has rendered once and passed. A base
that has never passed is listed in the Profile page as unverified and is skipped
by selection, falling through to the next match. Failing closed here costs a
slightly wrong resume; failing open costs a broken PDF sent to an employer.

## What the Profile page shows

The page already lists bases. It gains four things.

Each base states its selector in plain language, generated from the selector
rather than typed by hand: "India · Product roles · up to 5 years". A rule the
user cannot read is a rule they cannot correct.

**Overlaps are surfaced, not resolved silently.** Two bases matching the same job
is not an error, it is a priority question, and the person who wrote both rules
is the one who should answer it. Show which pairs overlap and on what.

**Gaps are surfaced too.** If some share of the current Found pool matches no
base, say so with the number, because "falls back to the default" is fine when
it is three jobs and wrong when it is nine hundred.

And a base can be tried against the pool before it is saved: given a selector,
say how many of the jobs on the board it would claim and show ten of them. A
rule you cannot test before committing to it is a rule you find out about on a
real application.

## Where the content comes from, stated plainly

A new base's content is created by **copying an existing base and editing it**,
through the structured editor the Profile page already has. That is the whole of
v1.

**Cloning an arbitrary uploaded PDF is not part of this and the UI must not imply
it is.** `resume_build.extract` matches the exact markup Jobby's own template
emits, and the test that proves byte-for-byte fidelity runs over 42
`applications/*/.resume.html` files, every one of which Jobby rendered itself. It
demonstrates nothing about a stranger's document. "Any number of base resumes" is
real. "From any PDF" is not, and belongs to the onboarding phase where a stranger
arrives with a resume and nothing else.

## The overlap with preference learning, which is not a coincidence

`docs/jobby-preference-learning-plan.md` §4 describes a learner over job features:
title, location, seniority, extracted years, workplace, industry. A selector is a
hand-written predicate over that same feature set. They should not each grow their
own evaluator.

So there is one evaluator, one place "unknown" is defined per dimension, and
several callers. **It is an extraction, not a greenfield module**, and reading
the code first changed which code it extracts from.

`match_score.gates()` is the wrong target. It runs citizenship regexes over the
title and excerpt and appends two booleans the caller passes in. No schema, no
user-authored predicate, no per-dimension unknown, no plain-language rendering,
and it answers a different question: not "does this job match my rule" but "why
is this role capped". Extracting it would be extracting the wrong thing.

The right target is the `score_*` family, which is already most of what this
plan was about to specify from scratch:

- `score_location` resolves the wanted set through `search_config.expand_locations`,
  decides country and workplace separately, and **returns a list naming which
  dimensions were unknown** so nothing has to re-derive it.
- `score_experience` reads `extracted.yearsMin` with `yearsMax` as fallback,
  judges against the floor because "nobody is ever turned down for having too
  few years above a stated ceiling", and returns `NEUTRAL` plus `True` for
  unknown when the posting states no number.
- Both return a **plain-language reason for every branch**: "IN is in scope",
  "asks for 7 years, 2 over the ceiling", "the posting does not state a
  requirement". That is the selector's plain-language rendering, already
  written.

And `found_lane`'s filters are not a second dialect. The client filters on the
`unknowns` array that `match_score` computed and shipped in the payload, so
"unknown" already has exactly one determination point.

So: split each `score_*` into a predicate returning `(matched, why, unknown)`
and a thin weighting layer over it. `scripts/job_predicate.py` is where the
predicate halves live. Match scoring keeps its current behaviour by calling
predicate-plus-weights. Base selection calls the predicate half and gets the
reason string for `resumeBase.why` for free. The learner is the third caller.

The thing to avoid is writing a fourth filter dialect for selectors, which is
what "build a selector language" naturally becomes if nobody reads
`score_location` first.

## Migration

Today's two bases become two identities with `selector: {countries: ["US"]}` and
`{countries: ["IN"]}`, `priority: 10`, and the existing default marker preserved.
Behaviour is identical on day one, which is the point: the migration is not the
feature and must not be where the risk is. The single `resume-content.json`
becomes both bases' content reference until someone edits one of them apart.

## What could go wrong

The failure this design is most exposed to is a selector that matches nothing,
because of the null-field problem above. Mitigation is the try-before-you-save
count and the gap warning, both of which turn a silent miss into a number on
screen.

The second is priority thrash: a user adds a base, it outranks an older one they
had forgotten about, and applications quietly change document. The recorded
`resumeBase.why` is what makes that diagnosable after the fact, and the overlap
display is what should prevent it before.

The third is that this lands on the path a real application runs through. It
should not merge in the same window as a watched end-to-end run.

## Out of scope

Per-base cover letter templates. Automatic base *generation* from a job
description. Uploading a PDF as a base. Any of these is a plan of its own.
