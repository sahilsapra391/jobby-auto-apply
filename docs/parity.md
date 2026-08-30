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
