# What retires with parity, and what would go unwatched

Written before the file store is deleted, because after it the question cannot
be asked from evidence any more.

## Three checks go, not one

Parity does not retire alone. The file store is what it compares against, so
deleting the store retires the whole chain:

| check | what it asserts | survives the deletion |
|---|---|---|
| `verify_parity` | the two stores agree, field by field | **no** -- there is no second store |
| `mirror_fields` | what the mirror writes | **no** -- it describes the mirror |
| `parity_coverage` | parity compares as much as it ever has | **no** -- it measures parity |
| `schema_drift` | the live schema is what the migrations build | yes |
| `write_path_coverage` | which write paths have fired | yes, in part |

**The coverage ratchet dies with its subject**, which is worth saying plainly
because it was built two days earlier and reads like a durable guard. It
measures how much of the database parity looks at; with no parity there is
nothing to measure.

## So what is left, and what it cannot see

What survives checks **shape**. `schema_drift` answers "does this column
exist", and `write_path_coverage` answers "did this code run". Neither answers
"did the writer put the right value in".

That is the class parity was actually earning its keep on. Every divergence it
has caught was a writer writing the wrong thing into a column that existed:

- 469 postings inserted with `logo_domain` NULL because production was running
  a `posting_row` without `_logo_columns` (2026-09-02),
- `applications.phase` going stale on a session the mirror had stopped
  updating,
- 355 rows reported with `postgres=None` on columns Postgres held, which is how
  the hand-written column lists inside `class_b_postgres_state` were found.

**Measured, so this is not an argument.** Asked of the logo case today:

    schema_drift would see: 3 of 3 logo columns present
      -- unchanged whether they hold values or nulls

It would have said nothing, on either side of the fault.

## The gap worth filling, and it is not another parity

A second store to compare against is exactly what is being deleted, so the
replacement cannot be a comparison. What it can be is **the same ratchet idea
pointed at content instead of at coverage**: per column, what share of rows is
populated, held against the highest share this database has ever shown.

    postings.logo_domain   97.6%
    postings.summary       87.9%
    postings.posted_at     90.6%
    postings.years_text    41.5%

Those are today's real numbers. The logo erasure would have shown as
`logo_domain` falling from 97.6% toward 84% as the 469 blank rows accumulated,
which is visible, local, needs no second store, and fails in the loud direction
-- a writer that stops writing reports a smaller number rather than going
quiet.

It is deliberately **not** a replacement for parity and should not be described
as one. It cannot see a value that is wrong rather than absent: a status
written as `active` when it should be `submitted` keeps the column just as
populated. What it catches is the disappearance of a field, which is the shape
every real divergence so far has had.

`parity_coverage.py` already has the machinery -- device-local high-water
marks, observation that only ever raises, and a two-halves way down carrying a
reason -- so this is that file pointed at a different measurement rather than a
new one.

## What still has no watcher afterwards

Said plainly rather than left implied:

- **A value written wrong rather than missing.** Nothing proposed here catches
  it. Parity caught it only because a second store disagreed.
- **A row that should exist and does not.** Parity noticed absences on the
  Postgres side; a population percentage over the rows that are there cannot.

Both are arguments for the population check being a floor rather than a
ceiling, and for deciding what else is wanted **before** the deletion, while
the file store is still there to measure against.
