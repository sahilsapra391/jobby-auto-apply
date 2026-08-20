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
