# ADR 0006: One global job corpus, per-account decisions

- Status: accepted
- Date: 2026-08-29
- Source: §2 and §3, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

Job postings are public. Decisions about them are not. A per-tenant copy of the
corpus is 500,000 rows times the number of accounts.

## Decision

One global `jobs` table with no account scoping and no row-level security.
Per-account state lives in `job_decisions`, keyed `(account_id, job_id)`.

## Rejected

**Per-tenant copies of the corpus.** 500k × N rows, N times the ingest work, and
N chances for two accounts to disagree about what a posting says.

## Consequences

The absence of a `job_decisions` row means Found. That is what keeps the Found
lane free: it is `jobs` minus the account's decisions, not a per-account
materialisation of half a million rows that has to be created when an account
signs up and maintained forever after.

The cost is that Found is a set difference rather than a table scan, so it needs
the partial indexes in §3 and keyset pagination rather than `OFFSET`. That is
Phase 6's gate: p95 under 150 ms at 500k rows, measured against a seeded
database of that size rather than extrapolated from 20k.

`jobs` carrying no `account_id` also means it is the one table where a missing
RLS policy is correct rather than a hole. That is worth stating, because a
Phase 5 audit that asserts "every table has RLS" will flag it, and the right
answer is an explicit exemption with this record behind it rather than a policy
that does nothing.
