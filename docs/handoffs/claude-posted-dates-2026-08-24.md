# Posted-date contract handoff, 2026-08-24

## Why this changed

Workday CXS returned `postedOn: "Posted 13 Days Ago"`, but its connector wrote
`postedAt: null`. The Found client then fell back to `firstSeenAt`, so a role
that was thirteen days old displayed `seen yesterday`. The same fallback made
undated jobs pass recent-date filters and sort as newly posted.

## Contract

`postedAt` is the only job-age field. It must come from a source field that
means posted, published, or the source record's publication creation time.
`firstSeenAt` records when Jobby discovered the sighting. It remains in the
store for provenance and stale-record handling, but is absent from the compact
Found payload and cannot drive labels, filters, sorting, or retention priority.

Workday CXS publishes exact relative ages. `Posted Today`, `Posted Yesterday`,
and `Posted N Days Ago` resolve against `America/New_York` on fetch. An inexact
age such as `Posted 30+ Days Ago` remains unknown.

Greenhouse uses `first_published`, not `updated_at`. Recruitee uses
`published_at`, not `updated_at`. Workable uses `published_on`, not the time a
draft record was created. Jooble exposes `updated`, which is an
aggregator refresh date rather than the employer's publication date, so it
remains unknown. Pinpoint, JazzHR, and Rippling also remain unknown with their
current public response shapes.

## Regression coverage

`tests/test_sources.py` pins Workday's Eastern calendar conversion, exact and
inexact relative values, and the refusal to promote update timestamps.
`tests/test_found_script.py` sends a record discovered today with no posted
date through the real compact payload and real client filter. It must display
`Posted date unknown`, sort after dated records, and fail a strict date window.
`tests/test_jobs_store.py` pins the same rule at cap-retention priority.

## Production backfill

After merge, run the Workday connector through the normal Tier-1 ingest and
publish path. Existing records merge by identity and gain the newly normalized
`postedAt` value. Do not edit `jobs.json` directly. Confirm the production pool
has Workday posted dates, publish the board, and verify that no `seen today` or
`seen yesterday` job-age chip remains.

Records written before this contract may retain a Greenhouse `updated_at`, a
Recruitee `updated_at`, a Workable `created_at`, or a Jooble `updated` value.
Run `scripts/repair_posted_dates.py` first. It is dry by default and clears only
records where one of those connectors remains canonical. `--apply` persists
through the atomic private-store writer. Fresh connector runs then restore real
publication dates where the source offers one.

The refresh happens before search gating for an identity already in the store.
This ordering is deliberate. A saved seven-day window may reject a thirteen-day
Workday sighting, but that known record still needs to learn that it was posted
thirteen days ago. A gated new identity never enters the store. The ingest
report exposes the count as `datesRefreshed`.
