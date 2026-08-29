# ADR 0007: Match scores are computed lazily on the rendered page

- Status: accepted
- Date: 2026-08-29
- Source: §2, §9 Phase 6 and §11, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

Every job shown to a user carries a match score and a rubric. Computing them
eagerly means a score for every (account, job) pair.

## Decision

Filter in SQL first, then score only the rows actually being rendered, roughly
50. Cache in `job_scores (account_id, job_id, score, rubric, computed_at)` and
invalidate on preference change.

## Rejected

**Eager per-account scoring.** 500,000 jobs × 1,000 accounts is 500 million
rows and a permanent backfill job that never catches up. §11 names it as the
thing that actually gets expensive, and §9 says plainly: do not build it.

## Consequences

Scoring moves onto the read path, so it has to be fast enough to sit inside the
150 ms budget or be computed just after the page returns. The cache is what
makes the second page cheap; the first page for a new filter pays.

Invalidation is the part that will be got wrong. A preference change invalidates
every cached score for that account, and there is no partial answer: a rubric
computed against last week's keywords presented as today's score is a wrong
number shown confidently, which is worse than a slow one.

The rubric already rides on every record today and the Found row already draws
it. So this is not new capability, it is the same computation moved off a
snapshot and onto a page.
