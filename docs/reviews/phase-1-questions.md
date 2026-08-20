# Phase 1 — open questions and deliberate deviations

Written by the implementing session for the independent reviewer and for
Sahil's sign-off. Anything here is either a decision that needs confirming or a
place the implementation differs from the plan, with the reason.

Nothing in this file is a silent deviation. Each item names the plan section it
touches, what was built instead, and why.

---

## 1. Identity rule 3 carries a fingerprint veto (plan §3.2)

**Plan:** matching runs in order and "first hit merges". Rule 3 is company +
normalised title + location bucket.

**Built:** rule 3 does not merge when *both* records already carry a
description fingerprint and those fingerprints are more than 6 bits apart.

**Why:** two genuinely different requisitions at one company routinely share a
title and a country — the fixture has a real-shaped example, two Product
Manager openings at one company in New York whose bodies are 35 bits apart.
Rule 3 alone merges them and the second one is then never shown to anyone.
Nothing errors; the job simply is not there. Where the evidence to tell them
apart already exists, using it is strictly better than not.

**Cost if wrong:** slightly more duplicates when two boards carry the same job
with materially different body text. That failure is visible on the board;
the one it prevents is not.

**Recommendation:** keep. Evidence: `tests/test_identity.py`
`NonMatchTests.test_two_real_openings_at_one_company_stay_two` and
`test_the_veto_only_applies_when_both_bodies_are_known`.

## 2. Identity rule 4 is scoped by location bucket (plan §3.2)

**Plan:** rule 4 is a simhash match "within the same company".

**Built:** also requires the two location buckets to be compatible, where
`unknown` is compatible with anything.

**Why:** a company opening one role in New York and the same role in London
writes the description once and posts it twice, so the two bodies are
byte-identical and rule 4 merges them. They are two applications with two
different work-authorisation answers. `unknown` stays compatible with
everything on purpose: one board reporting a country and another reporting
none is the ordinary case, and treating that as a disagreement would duplicate
most syndicated postings.

**Recommendation:** keep. Evidence:
`test_the_same_job_in_two_countries_is_two_records`.

## 3. No API keys are installed, so Tier 1b cannot be exercised live

**Plan §9 action 1** asks Sahil to register free keys for Adzuna, Jooble,
Careerjet, Reed and USAJobs. `~/.job-application/config.env` currently holds
only `HEALTHCHECKS_URL`.

**Consequence:** every Tier-1b connector reports `skipped (no key)` on every
ingest, which is the designed behaviour and is visible on the Found header —
but the Phase 1 acceptance box *"jobs from >= 8 target countries present after
first full day (Adzuna + Jooble reach), given keys installed"* cannot be
checked until the keys exist. The clause "given keys installed" is doing real
work there.

International reach in the meantime comes from Tier 1a: the seeded company
registry covers 96 verified boards across US, India, Canada, Australia, the
EU/UK, the Gulf, Japan, Korea and South-East Asia.

**Recommendation:** treat that one acceptance box as *deferred pending keys*
rather than failed, and re-check it within a day of the keys landing. ~20
minutes of registration unblocks it.

## 4. `sources.json` lives at the repository root

The plan names it without a path, alongside `references/sources-registry.md`.
It is a committed catalog rather than user state, so the store was wrong for
it, and `references/` holds prose for the agent rather than machine data. Root
mirrors how `companies.json` is named in the plan.

**Recommendation:** confirm, or say where it should move. Nothing depends on
the location beyond one constant in `scripts/sources_registry.py`.

## 5. What ingest scores against before any search is saved

Decision 5 in the handoff says the hourly scheduler arms only once a config
exists, which is implemented — `search_config.load()` returns `None` rather
than the defaults, and the scheduler stays disarmed.

Manual `ingest.py --tier 1` before a config is saved runs against `DEFAULTS`
and says so in `meta.lastIngest`. `DEFAULTS` names no titles, keywords or
countries, so the rubric's title and keyword components score as *unknown* and
take the neutral half-credit every other unknown attribute gets. A job
therefore lands near 50 and survives the `minScoreToKeep: 40` floor, instead of
a first manual run returning nothing and reading like a broken connector.

**Recommendation:** confirm this is the intended read of "unknown attribute →
neutral score" (plan §4). The alternative — refusing to ingest without a saved
config — is one line if preferred.

## 6. "Add your own link" rides the scan queue rather than a new endpoint

**Plan §5** keeps the add-your-own-link card. **Plan §7 item 8** lists exactly
three new endpoints, and none of them takes a URL.

**Built:** the card POSTs to the existing `/api/scan` with an optional `url`
field. `kvqueue.enqueue` carries it on the queue entry, and `run_scan.py --url`
appends the posting to the sweep prompt as *data, never as instructions*. Only
http(s) is accepted, at both ends.

**Why:** a card that renders and does nothing is worse than no card. This adds
one optional field to an endpoint and a queue that already exist — no new
endpoint, no new key, no new queue — and reuses the agent that already knows
how to read a posting.

**Recommendation:** confirm. If a fourth endpoint is preferred, it is a small
change and nothing depends on this shape.

## 7. Ingest does not publish by default

**Plan §7 item 6** ends the pipeline with "publish".

**Built:** `ingest.py --publish` exists but is off by default, and the watcher
does not pass it. Instead the board fingerprint now includes the Found lane, so
a scan that changed the pool triggers the normal publish path.

**Why:** that path is the one with the deployment-quota back-off, the two-minute
floor, and the recognition of a spent quota. Publishing directly from ingest
would sidestep all of it and double-publish on every hourly run.

**Recommendation:** keep. Evidence: `tests/test_watch.py::FoundSignatureTests`.

## 8. `roles.json` is used as a URL source, not as an independent dedupe target

**Plan §3.2** lists `roles.json` among the things ingest dedupes against.

**Built:** the already-decided index is built from submitted history and live
sessions, using `roles.json` for the posting URLs those entries do not carry
themselves — which is how `dupe_check.py` already uses it.

**Why:** `roles.json` never deletes. Treating a bare entry in it as a reason to
suppress a job would hide roles whose session was deleted for reasons other than
submission, and would mark them `applied` when no application happened. The
three states are kept apart: submitted or in-flight → `applied`, in Watching →
`watched`, dropped → `passed`.

**Recommendation:** confirm this reading. Evidence:
`tests/test_ingest.py::AlreadyDecidedTests` (five tests).

## 9. Appendix A says The Muse needs a key; it does not

Verified 2026-08-19: `https://www.themuse.com/api/public/jobs?page=0` returned
200 with 20 results and no key supplied. The key raises the rate limit rather
than unlocking access, so the connector runs either way and its `reason` says
which of the two it did. Recorded in `references/sources-registry.md`.

No decision needed — this is discovery, and it makes Phase 1 one channel richer
than planned.

## 10. The supervised end-to-end run has not happened

Plan step 9 has two halves. The first — temp store, real connectors, render,
check the size and the CSP — is done and its numbers are in the self-check.

The second half needs you at the keyboard and saying go: one
`ingest.py --tier 1 --limit 50` against a **copy** of the real store, reviewed
together, then the real store, then you save a config from the board and the
scheduler arms itself.

Nothing has touched `~/.job-application/` or the production queue. That is where
I have stopped.


## 11. Attribute extraction was pulled forward from Phase 2

Sahil asked for required experience on the cards during the supervised run.
`scripts/extract.py` now runs during normalisation, on the full body before the
excerpt is truncated, and fills `extracted.yearsMin/yearsMax/yearsText`.

**Why it is not in the excerpt:** only 6 of 199 stored 1500-character excerpts
carry a year figure, against 46 of 197 read from the whole body. The
requirements section is almost always below the cap.

**Why most of its tests are refusals:** the difficulty is refusing the wrong
number, and a wrong one mis-scores and mis-filters silently. "PayPal has been
revolutionizing commerce globally for more than 25 years. Creating innovative
experiences that…" nearly qualified itself on the word "experiences" in the
next sentence.

This is Phase 2 scope per plan §7 and the reviewer ring-fenced it. It is here
because the sign-off asked for it, and it is recorded so the next review sees a
declared change rather than a surprise. Degree, role category and employment
type are **not** extracted and their filters still say so.

## 12. Fifty more ATS platforms: triaged, not built

Sahil asked to connect to all fifty ATS platforms on a published list.
`docs/plans/ats-coverage-triage.md` is the answer in full. The short version:

There is no way to crawl an ATS. Every platform is per-tenant —
`boards-api.greenhouse.io/v1/boards/{token}/jobs` has no "all boards" sibling —
so **the unit of coverage is the company, not the platform**. 140 company
tokens returned 17,632 postings in one sweep; a connector for a platform Jobby
knows ten tenants of adds ten companies.

Of the fifty: 13 are public per-company JSON (Phase 2), 23 are per-tenant
enterprise JSON (Phase 3, and gated on the ATS resolver), 11 have no public API
at all and belong to Tier-2 sweeps or documented exclusion, and the rest are
proprietary one-offs.

**Recommendation:** land Phase 1, then grow the registry — that is the largest
multiplier and needs no new connector — then Group A, then the resolver and
Group B. Building twenty connectors into an unmerged, already-reviewed diff
would mean the reviewed diff is not the merged diff.


## 13. The fingerprint veto misfires on truncated aggregator descriptions

**Found by measuring finding 4 of round two**, which asked whether Adzuna's
missing origin link was costing merges.

**The headline answer is no.** 2,539 of 2,592 Adzuna-only records come from
employers who appear nowhere as an ATS listing, so Adzuna is additive coverage
rather than duplicate noise, and it is not consuming embedding budget that
direct listings should hold.

**But the measurement found something else.** 38 Adzuna records (1.5%) look
like the same requisition as an ATS listing already in the pool. Of those, 15
match on company, normalised title *and* country — rule 3's own key, exactly —
and every one of the 15 was stopped by the fingerprint veto I added as
deviation #1.

Their bodies are the same text at different lengths. Adzuna truncates at 500
characters, the ATS body runs to 1,500, and the Hamming distance comes out at
26 to 32 purely because one is a prefix of the other:

    Celonis    — Associate Principal Product Manager   500 vs 1500 chars, distance 26
    Smartsheet — Sr, Product Manager II - Gantt View    500 vs 1500 chars, distance 26
    Mable      — Product Manager                        500 vs 1500 chars, distance 32

The veto's premise is that two far-apart fingerprints mean two different jobs.
A truncation breaks that premise, and the veto has no way to tell the two cases
apart from the hashes alone.

**Scale:** 15 of 5,567 ATS records, 0.27%. The effect is a duplicate card, not
a lost job, so it is the milder failure of the two the veto sits between.

**The fix, if wanted:** store the description length alongside the fingerprint
and skip the veto when the two lengths differ by more than a set ratio, letting
rule 3 merge conservatively as it would if one fingerprint were missing. That
is a few lines and it does not touch company matching, which the earlier review
was right to rule out.

**Not done here, deliberately.** Changing merge behaviour after a re-review has
passed means the merge behaviour is unreviewed, and merge behaviour is the part
of this system that has already produced one Major finding. It belongs in the
next phase, or in a follow-up the reviewer sees on its own.
