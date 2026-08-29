# ADR 0001: Neon Postgres is the system of record

- Status: accepted
- Date: 2026-08-29
- Source: §2, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

The store is a directory of JSON files under `~/.job-application`, plus a
20 MB Cloudflare KV key holding a snapshot of the board. `jobs.json` reached
97 MB for 15,400 records, and every process that touched it loaded the whole
file: ingest, publish, dashboard, sync_board. Memory was the constraint rather
than disk, and no cap value fixes that, because the problem is loading the pool
to answer a question about part of it.

## Decision

Neon Postgres. It is already provisioned and already holds a verified copy of
the store.

## Rejected

Nothing was seriously considered against it, and the plan says why: the
migration is done and was verified byte-identical. Re-opening the question
would cost a second migration to gain nothing.

## Consequences

Measured on 2026-08-29, the database holds 15,400 jobs, 894 companies, 190
answers, 213 history rows, 88 sessions, 196 roles and 6 passed, at 127 MB.

Two things are true about it that are easy to misread as progress.

**Nothing reads it.** `scripts/jobs_db.py` is imported by exactly two files,
`scripts/migrate_to_postgres.py` and its own test. No part of the running
pipeline queries Postgres. The data landed and the consumer was never built,
which is the shape CLAUDE.md names as "a change is done when the running system
consumes it". Phase 1 is that consumer, and until it exists the correct summary
is that the migration succeeded and the cutover has not started.

**The shape in the database is not the shape in §3.** `jobs_db.py` keeps the
whole record in one `jsonb` column with queried fields lifted out beside it.
§3 specifies a normalised `jobs` with a uuid primary key, a foreign key to
`employers`, a generated `search_tsv` and the body in R2. Those are different
designs, both defensible, and Phase 1 has to pick one rather than discover the
disagreement halfway through. See `docs/migrations.md`, "The unmanaged tables".

The role in use, `neondb_owner`, has `rolbypassrls = true`. That is correct for
migrations and disqualifying for the API: an owner role bypasses row-level
security silently, which is exactly the class of failure that reports success
while doing nothing. Phase 5 needs a second, non-owner role, and §9's negative
test for it is to assert `pg_roles.rolbypassrls` is false for whatever the API
connects as.
