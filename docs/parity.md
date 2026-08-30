# Two stores, and what it means for them to agree

    python3 scripts/verify_parity.py
    python3 scripts/verify_parity.py --json

Phase 1 makes Postgres the system of record. Until the file store is deleted,
both exist and both are written, and `verify_parity.py` is the thing that says
whether they agree. It exits non-zero on any divergence and runs in CI and on a
schedule.

This document exists because the first question anybody asks of a parity job is
"why is it red", and the answer for the first several thousand rows would have
been "because the two stores were measured four hours apart".

## The 20,103 against 15,400, resolved

The rebuild plan's baseline says 20,103 jobs. The `jobs` table in Neon holds
15,400. Both are correct, and neither is the other's error.

Measured 2026-08-29 at 23:23 UTC:

```
jobs.json            20,103      mtime 21:25:05 UTC
neon public.jobs     15,400
  in both            15,324
  file only           4,779
  neon only              76
  state differs         106   (89 expired->new, 17 new->expired)
```

**Neon is a point-in-time copy of `jobs.json`, taken at about 17:22:29 UTC on
2026-08-29.** That is the maximum `last_seen_at` in the table, and the maximum
`first_seen_at` is 17:22:01. Nothing in Neon was first seen after 17:22.

Everything else follows from four hours passing:

- **The 4,779 file-only records are one sweep.** Every one is `state = new`,
  and every `firstSeenAt` falls between 20:59:11 and 21:25:00 UTC. The pool
  gained them after the copy was taken. The file's `firstSeenAt` histogram has
  54 records in the 20:00 hour and 4,725 in the 21:00 hour, and Neon has none
  in either.
- **The 76 Neon-only records were pruned from the file afterwards.** All 76 are
  `new` in Neon and simply absent from `jobs.json`. `jobs_store.prune` caps and
  ages out the pool, and it ran after the copy.
- **The 106 state differences are the pool doing its job.** 89 records that were
  `expired` in the copy are `new` in the file, which is
  `jobs_store`'s revive path, from `expired` back to `new` only. The other 17
  went the ordinary direction.

So the honest one-line version: the plan measured the live store, `jobs_db`
measured a snapshot of it, the snapshot is four hours old, and one scan sweep
happened in between.

## What that means for the parity job, which is the point

**A parity job pointed at the legacy `jobs` table would report 4,961
divergences on its first run and none of them would be a fault.** It would be
red from the day it was written, which is how a red build becomes something
people acknowledge rather than read.

Three rules follow, and they are the parity contract:

**Parity compares the file store against the new normalised tables**, the ones
Phase 1 creates and dual-write keeps current. Not against the legacy jsonb
table, which is a backfill source with no writer and is dropped under
`-- jobby:contract` once parity is green.

**The backfill is idempotent and re-runnable.** It has to be, because it cannot
be atomic with the start of dual-write: the daemon is running, a sweep can land
between the two, and any record created in that window belongs to neither. A
backfill that can be run again closes that gap; one that can only be run once
turns it into a permanent 4,779-shaped hole that parity reports forever and
nobody can fix without a manual repair.

**A record absent from one side is a divergence, not a filter.** It is tempting
to make parity compare only the intersection, because that is what makes it
green. The intersection is exactly where the 4,779 hid. Parity reports
`only_in_file` and `only_in_postgres` counts separately from field mismatches,
and any of the three being non-zero is a failure.

## The comparison basis

Parity reads the file store through `jobs_store` and Postgres through the same
accessors the product uses, so it tests the path rather than the data. For each
job id present in either store it compares the fields the normalised schema
carries, and it reports:

```
only_in_file        ids the file has and Postgres does not
only_in_postgres    ids Postgres has and the file does not
field_mismatch      ids in both, with the field and both values
```

Fields the new schema deliberately does not carry are listed in
`packages/schema/BACKFILL.md` with a reason each, and parity does not compare
them. That list is the difference between "we chose not to keep this" and "we
lost this", and it is checked by a test that every key present in the legacy
records is either mapped to a column or named on the dropped list.

## The two merged-field drops, named

A green report currently reads

    divergences 0, transient 0, merged-field drops 2

and a number in a green report with no explanation is a number people stop
reading. There are exactly two and they are both the same record:

| Record | Field | File store | Postgres |
|---|---|---|---|
| `9c9cf0baa815c1eb` | `apply_url` | `.../boards.greenhouse.io/braze/jobs/8139241?gh_jid=8139241` | `.../job-boards.greenhouse.io/braze/jobs/8139241` |
| `9c9cf0baa815c1eb` | `desc_fingerprint` | `simhash64:acc5bde5e8f76426` | `simhash64:a24fbde5f85f6de4` |

`9c9cf0baa815c1eb` and `5f10861ba0ce8980` are two stored records for one Braze
posting, Greenhouse job 8139241, reached through two hostnames. The legacy
dedupe keys on the URL so it kept both; the board key sees one posting and
folds them, and `5f10861ba0ce8980` wins by `BOARD_KEY_WINNER`.

**Both exemptions are the merge, not a fault.** The URL differs because the two
hostnames are the whole reason there were ever two records. The fingerprint
differs because the two pages were fetched separately and Greenhouse renders
them with slightly different boilerplate, so the simhashes land 4 bits apart,
inside the distance `identity.similar` treats as the same posting and outside
exact equality.

Nothing else about the pair differs. Their `state` is compared like any other
record's and agrees, because a merge cannot be allowed to hide a disagreement
about whether a job is expired.

**The count is asserted at exactly 2.** A third would mean either a new
collision in the corpus or a field starting to differ that did not before, and
both are things somebody should look at rather than watch join a running total.
`tests/test_verify_parity.py` fails on any other number, so the exemption
cannot quietly grow into a category.

## A moving pool is not a divergence

The pool changes while parity runs. A sweep can insert a record between reading
the file and reading Postgres, and that record is genuinely in one and not the
other for a few seconds.

Parity therefore reads the file store first, records its mtime, and re-reads it
at the end. If the file changed underneath the run, the run is inconclusive and
says so rather than reporting the difference as a fault. An inconclusive run
exits non-zero like any other failure, because a parity job that can quietly
decline to answer is a parity job that reports green while checking nothing.
The scheduled run retries; the exit code never lies about having checked.

## What the mirror costs, measured under a real sweep

Phase 3 moves sourcing to the cloud and §5's connector upsert writes through
this path, so the question is not whether the mirror is fast enough today but
where it stops being fast enough. Measured on the tier-1 sweep of
2026-08-30T03:49:15Z, the first to run with dual-write live:

```
sweep duration        1,264s (21.1 min), 943 requests, 27 channels
found -> kept         17,129 -> 640
pool                  16,123 -> 16,282

mirror                29.75s
file write             0.57s
mirror share of persist  98.1%
mirror share of INGEST    2.35%
delta                 223 added, 4,673 changed, 64 removed = 4,960 rows
per changed row       6.0 ms
```

**Two numbers, and the smaller one is the reassuring one.** The mirror is 98%
of the time spent persisting, which sounds alarming and is not: persisting is
30 seconds of a 21-minute sweep. Against the whole ingest it is 2.35%, and the
sweep is dominated by HTTP to 943 boards. Nothing about today's cadence is
threatened by this.

**The scaling is the part to plan around, and it is linear in the delta rather
than in the pool.** `mirror_pool` touches only what changed, so a sweep that
changes 30% of a 16,282-row pool costs 30 seconds. The same 30% churn against
§11's 500,000-row target is roughly 152,000 rows, and at 6.0 ms each that is
about 15 minutes of mirroring per sweep, against a 4-hour cadence.

That is survivable and it is not comfortable. **The obvious explanation for it
is wrong, and the only reason anybody knows that is that it was measured
instead of argued.**

The guess was the generated `search_tsv` column and its GIN index: linear in
the delta, and exactly what an ordinary upsert does not pay. Timed in a scratch
schema over 5,000 rows, holding row count and statement count fixed:

```
variant     seconds  ms/row   what it isolates
full           9.38    1.88   as shipped: generated tsv + GIN
tsv_only       8.67    1.73   generated tsv, no GIN index
plain          8.57    1.71   neither: the floor
narrow         1.40    0.28   last_seen_at only, on the full table
```

GIN maintenance is 8% of the cost and the tsvector computation is 1%. Together
they are under a tenth of it, and Phase 3 would have optimised them and gained
almost nothing.

**The driver is the payload.** Holding the row count and the ten statements
fixed and varying only how wide each row is:

```
summary      payload  seconds  ms/row
180 words      8.0MB     8.12    1.62
40 words       2.2MB     3.29    0.66
empty          0.6MB     2.02    0.40
```

Linear at about 0.83 s per MB over the wire. It is the bytes, not the indexing
and not the round trips: all three ran in ten statements.

So the number to carry forward is 6.0 ms per changed row on the real table, and
the lever is **how much of each row is sent**, not what the table does with it
once it arrives. `docs/plans/phase3-inputs.md` carries what follows from that.

Re-measure on a materially larger pool before trusting the 500k projection,
which is arithmetic on one observation. The two benchmarks above are separate
measurements and are quoted as such; the projection is not.
