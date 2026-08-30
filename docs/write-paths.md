# Every path that mutates the store

Dual-write means every existing write path writes both the file store and
Postgres. That is only true if the list of write paths is complete, and a
missed one does not fail on the day it is missed: it shows up as a parity
divergence on day five and restarts the seven-day clock at zero.

So this inventory is not prose. `tests/test_write_paths.py` walks the AST of
`scripts/` for every call to a persistence primitive and fails when what it
finds and what is listed here disagree, in either direction. A new call site
fails the build until it is classified. A line here that no longer exists fails
too, because a stale inventory stops being read.

Measured 2026-08-29: **39 call sites across 11 modules**, every one of
them in a table below.

## The primitives

A write path is a call to one of these. Everything else in the store is a read.

    store.write_private          the only function that puts bytes in the store
    jobs_store._persist_pool     the pool's single writer, and the mirror hook
    jobs_store.save_jobs         the pool, under the scan lock
    jobs_store.edit              the pool, read-modify-write under the lock
    jobs_store.save_passed       the pass tombstones
    jobs_store.save_companies    the company index
    jobs_store.mark_watched      a decision, via edit
    jobs_store.mark_passed       a decision, via edit and save_passed
    store.remember_role          a role record, via write_private

`mark_watched`, `mark_passed` and `remember_role` are listed even though each
reaches disk through one of the others. They are the names the rest of the
system calls, so a reader looking for "where does watching get written" finds
them here rather than having to know they funnel.

## The pool has one choke point, and that is where dual-write lives

`save_jobs` and `edit` both used to end in

    with runlock.held(root, POOL_LOCK):
        store.write_private(paths(root)["jobs"], jobs)

so `jobs_store._persist_pool` is now that pair of lines, called by both, and it
is the only place the pool reaches disk. A test asserts neither writer calls
`write_private` directly any more, because a direct call would bypass the
mirror while still passing every other check here. Dual-write hooks there rather than at
each of the callers below, which is what makes the coverage argument short
enough to be checkable: one function, already holding the lock that serialises
every pool writer.

It diffs by per-record digest rather than tracking what a caller changed.
Measured on the real pool, digesting all 20,103 records costs 0.27s against a
0.18s serialisation of the same data and a 99 MB file write, so the exact
answer is cheaper than the bookkeeping needed to approximate it. No field can
change without being seen.

## Class A: the pool and decisions, dual-written in Phase 1

These reach `postings`, `employers` and `job_decisions`, which migrations 0002
and 0003 created.

| Module | Function | Primitive | What it means |
|---|---|---|---|
| `jobs_store` | `_persist_pool` | `write_private` | the pool choke point, and where the mirror runs |
| `jobs_store` | `save_jobs` | `_persist_pool` | the pool, through the choke point |
| `jobs_store` | `edit` | `_persist_pool` | the pool, through the choke point |
| `jobs_store` | `main` | `save_jobs` | the CLI, same path |
| `jobs_store` | `main` | `mark_watched` | the CLI |
| `jobs_store` | `main` | `mark_passed` | the CLI |
| `jobs_store` | `mark_watched` | `edit` | watch |
| `jobs_store` | `mark_passed` | `edit` | drop, the state half |
| `jobs_store` | `mark_passed` | `save_passed` | drop, the tombstone half |
| `jobs_store` | `save_passed` | `write_private` | the tombstone file itself |
| `promote` | `watch` | `mark_watched` | the board's Watch button |
| `promote` | `passed` | `mark_passed` | the board's Pass button |
| `ingest` | `add_postings` | `save_jobs` | new postings from a sweep |
| `ingest` | `run` | `save_jobs` | closures and updates from a sweep |
| `ingest` | `run` | `save_companies` | the company index a sweep rebuilds |
| `sync_board` | `reconcile_pool` | `edit` | reconciliation, and the revive path |
| `repair_watch_drift` | `main` | `save_jobs` | repair |
| `repair_posted_dates` | `main` | `save_jobs` | repair |
| `backfill_watch_records` | `main` | `edit` | repair |

The revive path (`jobs_store.reopen`, expired back to new) and the closure
sweep (`close_missing`) and the cap (`prune`) do not appear as their own rows:
none of them writes. They mutate the in-memory pool and their caller persists
it through the choke point, so they are covered by construction rather than by
being remembered. That is the reason for hooking the primitive.

## Class B: applications, answers and profile, dual-written in Phase 1

These reach `applications`, `application_events`, `answers` and `profiles`.

| Module | Function | Primitive | What it means |
|---|---|---|---|
| `store` | `cmd_session_save` | `write_private` | phase transitions, status, mark-submitted, retry |
| `store` | `cmd_session_save` | `remember_role` | the role behind a session |
| `store` | `cmd_history_append` | `remember_role` | history events |
| `store` | `cmd_role_put` | `remember_role` | a role record |
| `store` | `remember_role` | `write_private` | the shared writer for the three above |
| `store` | `cmd_answer_put` | `write_private` | answer writes |
| `store` | `_bank_write` | `write_private` | answer bank writes, including dismiss and restore |
| `store` | `cmd_profile_replace` | `write_private` | profile writes |

## Class C: not dual-written, and why

Each of these writes something migrations 0002 and 0003 created no table for.
Listing them is the point: a path in neither class A nor C is a path nobody has
thought about.

| Module | Function | Primitive | Why not |
|---|---|---|---|
| `store` | `cmd_init` | `write_private` | creates the store itself, before any account exists |
| `store` | `cmd_meta_set` | `write_private` | device-local metadata, no cloud equivalent |
| `store` | `cmd_meta_set_json` | `write_private` | device-local metadata as JSON, same reason as `cmd_meta_set` above |
| `store` | `cmd_resume_add` | `write_private` | resume registry; §3 has `profiles`, and multi-resume selection is not modelled yet |
| `store` | `cmd_resume_update` | `write_private` | edits an entry in that same unmodelled registry |
| `store` | `cmd_resume_remove` | `write_private` | removes an entry from that same unmodelled registry |
| `store` | `cmd_resume_default` | `write_private` | marks a default in that same unmodelled registry |
| `store` | `cmd_resume_content_put` | `write_private` | the Profile page's wording; travels in the job bundle, not the board |
| `search_config` | `save` | `write_private` | saved searches; §3 has `preferences`, which Phase 6 fills |
| `document_job` | `_save_state` | `write_private` | document-job state; the KV transport owns it and Phase 4 replaces it |
| `document_job` | `_archive_failure` | `write_private` | archives a failed document job, same transport and same Phase 4 replacement |
| `seed_companies` | `main` | `save_companies` | a one-off seeding script, not a runtime path |
| `jobs_store` | `save_companies` | `write_private` | the company index is derived; `employers` is rebuilt from postings |

## Ordering, and which direction is recoverable

**Postgres first, inside its transaction. Then the file.**

    begin
      upsert postings / decisions / answers / ...
    commit                      <- a failure here fails the whole operation
    write the file              <- a failure here leaves Postgres ahead

A Postgres failure raises before the file is touched, so the operation fails
whole and the caller sees it. The two stores stay equal because neither moved.

A file failure after the Postgres commit leaves Postgres ahead of the file.
That is the recoverable direction: the backfill is idempotent and re-running it
brings the file's view forward, and parity will report the difference loudly in
the meantime. The other order is not recoverable, because a file write that
succeeded while Postgres failed is a decision the cloud never learns about, and
nothing in the system would ever look for it.

So a file failure after a commit is **logged loudly and re-raised**, never
swallowed. The log names both halves: what Postgres committed and what the file
write refused to do.

## Concurrency, and the failure this is written against

Three apply slots write concurrently, and a sweep runs every few hours. Parity
reading between the Postgres commit and the file write sees a real, momentary
difference that is not a fault.

The board has published an empty Submitted tab over a good one before, for
exactly this shape of reason, so the rule is narrow and explicit:

**On a divergence, re-read once. If it persists, report it.** Never suppress a
divergence as "probably mid-write". Both reads are logged, with their
timestamps and the file's mtime at each, so anybody looking at a report can see
whether the second read agreed with the first.

A divergence that clears on the second read is still recorded, at a lower level
and with both readings, because a path that is *always* mid-write when parity
looks is a path with a very slow write, and that is worth knowing. What it does
not do is fail the run.
