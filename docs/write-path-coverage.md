# What the coverage gate can and cannot reach

The parity gate needs every write path in `docs/write-paths.md` to fire at least
once during the clock. Some cannot, and leaving them unticked and unexplained
makes the checklist unreadable: a path nobody can reach looks identical to one
nobody has got round to.

**Two lists, kept apart on purpose.** They have different remedies and mixing
them hides both. One is about honesty and cannot be fixed by more work; the
other is about mechanism and mostly can.

## A. Blocked by fabrication — driving them would assert something untrue

These reach real code by writing a false claim into a real job search.

| Path | What driving it would assert |
|---|---|
| a drop from In flight | that an application is mid-submission. In flight is 0, and creating one means running a real application against a real employer |

**Drop-from-In-flight is uncoverable and the gate should close without it.** A
coverage requirement that forces fabrication is worse than an honest gap: the
tick would record that the path works and the store would record an application
that never ran.

The two remaining fabrication cases -- `mark-submitted` and a completion through
`finish.py` -- are *not* on this list, because a fixture application, clearly
named and deleted afterwards, reaches the same code without claiming anything
about a real employer. That is a different act from writing a submission record
for a job nobody applied to. It carries its own conditions: snapshot every table
first, verify the deletion after, confirm nothing survived in either store.
Fixture rows have leaked into production twice.

## B. Blocked by mechanism — nothing untrue, the path just cannot be reached

| Path | Why |
|---|---|
| `jobs_store._persist_pool -> write_private` | **Only exists when the mirror is off.** With it on, `_persist_pool` calls `dual_write.persist`, which writes the file itself. On production this row is unreachable by construction, and that is the same fork that hid the coverage hook for a day -- see CLAUDE.md, "Instrumentation on the branch tests take". |
| dismiss-then-restore of an answer | No answer in the store has a reversible round-trip. A `confirmed` answer refuses dismissal until it is forgotten first, and the five already dismissed refuse restoration. Both calls return a refusal and write nothing, so the counter correctly does not move. Coverable the moment an answer exists in a state where the round trip is a no-op overall; not worth manufacturing one. |
| `jobs_store.main -> mark_passed` | The `pass` subcommand, which means **never offer this job again**. A lasting decision about a real posting, and nobody driving a counter has a basis for making one. Same principle as not writing a submission record for a job nobody applied to. |
| `jobs_store.main -> save_jobs` | The `prune` subcommand. Measured in process without saving on 2026-09-02: it would delete **7 real records**, each `closed over 7 days ago`. The sweep already prunes, so running the CLI is a deletion whose only purpose is moving a counter. |
| `promote.watch -> mark_watched`, `jobs_store.main -> mark_watched` | Drivable in principle: watching a job is a real, reversible action the user takes anyway, and the CLI is the same decision by another door. What makes them uncoverable is the *requirement*, not the path. The window used to reset on every gate-path change, and each of eight resets in two days demanded a fresh real decision about a fresh real job to prove again what had already been proved. **A counter that costs a product decision every time it zeroes is a defect in the requirement.** That is now fixed -- coverage counts over the whole log since dual-write went live -- so these are excused only until one fires organically, which a single Watch click will do. |
| `search_config.save -> write_private` | Newly class B, because `board_meta.search_config` holds it and Found needs it to say what scope it is showing. It fires the first time the reader presses Save on a search, which is an ordinary thing they do. Not driven here for two reasons: `save` bumps a version that every job records in `configVersion` and the banner displays, so it is not the no-op it looks like; and even with `bump=False` it rewrites the reader's own saved search, which is their file. **Ticking a counter does not justify writing somebody's configuration.** |
| `repair_watch_drift.main`, `repair_posted_dates.main`, `backfill_watch_records.main` | Repair scripts, run when something is broken. Driving one on a healthy store is either a no-op that writes nothing, or a repair of damage that does not exist. |

## The gate these feed

Three consecutive green parity days, **and** every write path in
`docs/write-paths.md` observed firing at least once **since dual-write went
live**, minus this list.

Not per clock window. Counting from the clock start zeroed the tally on every
gate-path change, and a path that has fired since dual-write went live has been
observed; observing it again tells nobody anything. `write_path_coverage.py`
reads this document for the exclusions rather than holding a second copy, so
the gate and the reasons cannot drift apart.

## What this leaves

The gate should read: three days of green **and** every path in
`docs/write-paths.md` ticked except the rows above, each of which is named here
with its reason. Anything unticked and *not* on these lists is work outstanding.

Both lists are short on purpose. A long one means the requirement has been
argued down rather than met.
