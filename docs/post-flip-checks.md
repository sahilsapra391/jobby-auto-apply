# What to check when `live_board` flips

Written before the flip, on purpose. The point of writing it early is that
after the flip every observation is made by someone who wants it to have
worked.

## The first check is not that the write landed

**It is that the device acted on it.** A row appearing in Postgres proves the
route ran. It does not prove the click did anything, and on this system today
it would not, for a reason worth stating before anything else.

### Measured 2026-09-03: a v2 write and the daemon's work queue are different systems

```
the daemon's action queue   Cloudflare KV
                            watch.poll_queues -> deploy/queue.mjs
                            -> kvqueue.mjs -> kv.mjs

v2's writes                 Postgres only
                            insert into applications
                            insert into application_events
                            insert into job_decisions
```

`apps/web/app/api/applications/route.ts` and `_ui/writes.ts` contain no
enqueue, no KV call, nothing that reaches the queue the watcher polls. The one
occurrence of the word "queued" in that route is a docstring. In the other
direction, `scripts/jobs_store.py` contains **zero SELECTs**, and no module
under `scripts/` reads Postgres back into the file store.

So after the flip, pressing Watch or Apply on a v2 card writes a Postgres row,
**v2's own lane view reads Postgres and moves the card**, and the watcher never
learns anything happened. The board would say Watching. The application would
never start.

That is the failure this project has met before under a different name: the
board republished all day while its data was a day old, fresh and stale at
once, because the thing being measured and the thing being reported were not
the same thing.

**This is not an argument against flipping.** It is an argument for knowing
which half the flip turns on. Reading, sorting, filtering, opening a record,
following a document link: all of that is v2 reading Postgres and all of it
works. What does not yet exist is the path from a v2 decision to the machine
that does the work.

### Two things the plan lists that are now stale

`docs/plans/phase2-live-half.md` names three preconditions. Re-measured
2026-09-03 against production:

- **Migration 0007 is applied.** The database holds 1 through 15 and
  `idempotency_keys` exists. The plan says it does not and that every mutation
  would 500 inside `replay`; that was true on 2026-09-02 and is not true now.
- **`live_board` is `false`** on the one account, as expected.
- The Dropped dialog and the parity clock items are unchanged.

## The sequence

Each step says what it proves and, more importantly, what a pass would still
be consistent with.

**1. Before touching the flag, record the two numbers you will compare
against.** The count of `applications` rows and the mtime of the pool.

```bash
python3 scripts/watch.py --status | head -40
```

A pass here proves nothing. It is the baseline, and without it every number
afterwards is a number with nothing to be compared to.

**2. Flip it, then read one lane.** Not for correctness, for a 500. The write
route reads the flag before it touches `idempotency_keys`, so a broken flip
shows up as an error on the first mutation and not before.

**3. Press one thing, and watch the device, not the screen.** This is the
check. Press Watch on a single Found card, then:

```bash
python3 scripts/watch.py --status | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['queues'], indent=2))"
```

**A card that moves to Watching in v2 is not evidence.** v2 reads the row it
just wrote. The evidence is a queue entry, or a session, or a slot lock: some
state on the device that could only exist if the device heard about the click.
On today's code that will not appear, and the useful thing is to see it not
appear rather than to infer it.

**4. Press the same thing on the Python board and confirm the device still
hears it.** The two boards are both live during the cutover, so the question
"did the flip break the working path" is separate from "does the new path
work", and only one command tells them apart.

**5. Then, and only then, look at whether the row is right.** Compare the
`applications` row v2 wrote against what the Python board would have written.
This is last because a correct row that nothing acts on is the failure above,
and checking correctness first is how it gets missed.

## What silence means, per step

| step | silent means |
|---|---|
| 2 | the route never ran, or it ran and the flag read false. Not "fine". |
| 3 | **expected today**, and it is the finding rather than a fault to debug |
| 4 | the flip broke the working path. This is the one to roll back on. |
| 5 | nothing was compared. Parity is retired after the flip, so no other check is watching this. |

## Rolling back

`live_board` is one boolean on one account row and turning it off is
immediate. Nothing else has to be undone: writes that landed are real rows in
`applications` and the mirror already holds real rows there, so a v2 write is
not distinguishable from a device write after the fact and does not need to be
removed. That is also why step 1's baseline count matters. Without it there is
no way to say afterwards how many rows the experiment added.

## What is deliberately not on this list

**Parity.** It is retired after the flip by the Option C decision, and it could
not answer this anyway. Parity compares two stores and reports whether the
mirror lost anything in transit. A v2 write that the device never sees is
present and correct in both. Both stores agreeing on a decision nothing acted
on is a green parity run, in exactly the way 122 postings filed under
`Appcast Enterprise` were a green parity run.
