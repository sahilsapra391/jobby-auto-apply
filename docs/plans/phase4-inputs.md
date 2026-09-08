# Phase 4 inputs

What is true on this machine before the device runner is built, read on
2026-09-08 against the code rather than against the plan. Same role
`phase3-inputs.md` played: the design doc says what to build, this says what is
actually there.

Four independent readings were taken and each was then adversarially verified
against the source. Everything below that decides an action was re-checked by
hand afterwards, because this project's own record says a peer's number gets
checked before it lands in your area.

## 1. What Phase 4 is, in five sentences

The device's launchd services collapse into a supervised runner that the cloud
drives, so merging to `main` reaches the laptop without anybody restarting
anything. The runner pairs once through a device code, keeps an Ed25519 private
key that never leaves the machine, and holds a websocket to a per-account
Durable Object. It is never pushed work: it advertises free capacity and the
cloud answers with signed leases carrying an application id, an account id, an
expiry and a presigned bundle URL. When a release is announced the runner
finishes what it holds, downloads a signed tarball, verifies its hash against a
manifest, swaps a symlink and `os.execv`s itself, with launchd never told.
Phase 4 also deletes the KV click queue and, per the plan's 2026-09-04
amendment, the file store.

## 2. Three claims in the design doc that the code does not support

**Its §1 import table is wrong for the watcher.** `scripts/watch.py` contains no
reference to `dual_write` or `class_b`, and its only reference to `jobs_store`
is a function-local import inside `pool_staleness()`. So "for 35 hours all three
daemons held a stale `dual_write.py`" is false of the watcher, which never
imported it. The doc is right about `class_b`, which `store._mirror_class_b`
imports and `write_private` calls.

**`ai.jobby.providers` runs `watch.py --provider-controls`, not
`provider_login`.** Read off the plist. It therefore carries every module-level
import in `watch.py`, so a `watch.py` edit arms on restarting two services
rather than one. `deploy_verify` had this wrong in the other direction and it is
fixed separately; see section 4.

**There are four jobby launchd entities, not three.** `ai.jobby.parity.plist`
sits in `~/Library/LaunchAgents` and `launchctl print gui/501/ai.jobby.parity`
answers "Could not find service". It is unloaded rather than stale, and the
newest line in `parity.jsonl` is 2026-09-05T21:09:26Z against a 1800-second
interval. **The parity instrument is not running.**

## 3. The process split does not isolate the lane it was built for

Both documents justify `ai.jobby.providers` by saying a slow provider probe
delayed a Connect click. The code does not deliver that isolation:

    watch.tick -> fingerprint() (watch.py:3048, every tick)
               -> provider_signature() (watch.py:1180)
               -> providers.public_status() (providers.py:1384)
               -> ProviderRegistry._locked() (providers.py:1207)
               -> fcntl.flock(fd, LOCK_EX)     <- no LOCK_NB

**The main watcher tick blocks on the same exclusive lock a provider probe holds
while it runs**, and a probe can take up to 150 seconds per provider. So does
`watch.py --status`, the command every deploy in this repository is told to run
first. Whatever the right process count is, it cannot be argued from isolation
this code does not have, and the flock is the thing to fix before the argument
is worth having.

## 4. `live_board` is on, and the sequencing already ran out of order

Read from the row rather than from a docstring:

    accounts.flags = {"live_board": true}   for a56bbd42

`apps/web/app/api/applications/route.ts` still documents itself as "Behind
`live_board`, which is off", and `flags.py` still registers it as a phase 2
flag. Fifth instance in this project of a confident sentence about a mechanism
that stopped anybody checking.

It matters beyond tidiness. `phase4-design.md` §6 sequences the file store
deletion, then the parity retirement, then the `live_board` flip, then the
runner. The flip has happened and the file store is still on disk at 100 MB. And
the plan's own Phase 1 amendment says the deletion's gate is the device reading
its decisions back from Postgres, which is runner work: **the deletion is gated
on work the design doc schedules after the deletion.** That circularity is a
decision somebody has to make, not a detail to reconcile silently.

## 5. Where the two documents disagree

Each of these is a decision, and none of them has a supersession record.

**One process or two.** Rebuild plan §6 and §9 and ADR 0008 all say one process
with internal task groups, one plist, delete the other two. `phase4-design.md`
§2 says two and puts the burden of proof on one. CLAUDE.md's Runtime shape
section forbids the merge outright. Four artifacts, three positions. ADR 0008
still reads accepted, one commit, never amended, and no ADR supersedes it, so a
reader of the ADRs alone gets the superseded answer.

**What the runner reports about itself.** The plan wants `runner_version`
compared against `min_supported`; §4 wants an exec-time SHA-256 because a
self-reported version is the `window.__jb_ran_theme` shape. A hash has no
ordering and `min_supported` needs one. The `devices` table already carries
`runner_version text` and no column for a hash.

**Who verifies the sibling.** §2 gives sibling restart to the supervisor and §4
specifies the hash for the runner alone. Under one process the question does not
arise.

**What the drain waits for.** The plan implies 45 minutes, the design measures
19, and both describe one apply run. `browser.SLOT_COUNT` is 8, `run_apply`
allows 45 minutes per provider attempt with two apply-capable providers,
`document_worker` allows 90 minutes by default and 110 at most, and a detached
cloud sweep can now live two hours. The drain waits for the last of them, which
is an order statistic over eight-plus concurrent things rather than a percentile
of one.

## 6. The verification gap in self-replacement

Today "which commit is this daemon running" has an answer, and it is the only
method by which every two-commits-at-once state here has ever been found: the
plist names an absolute path into the production checkout, so a service start
time compared against that checkout's reflog settles it. **A symlink swap
deletes that method**, and `{"runner_version": "2.3.1"}` does not replace it. A
runner that execs into a half-unpacked directory, or swaps the symlink and fails
to exec, or execs the wrong bytes, keeps answering with the version it believes
it is.

What closes it: the runner reports the SHA-256 of the file it is executing,
computed at exec time from its own resolved symlink target rather than compiled
in, and the cloud refuses to lease to a runner whose hash it cannot match
against the manifest it served.

Three things stand between that and being buildable. **Nothing produces a
manifest today** -- no runner leg in CI, no signed release, no manifest in R2 --
so §6's sequencing depends on a §7 job it does not name as a prerequisite.
`min_supported` still needs an ordered version. And a second binary has no hash
anybody checks.

One operational detail nobody has picked up: the plan writes
`launchd: com.jobby.runner` with `ThrottleInterval 10`, every installed service
is `ai.jobby.*`, and no plist `watch.py --install` writes carries
`ThrottleInterval` at all. With `KeepAlive true` and self-replacement, that
interval decides whether a runner that execs into a broken target retries every
ten seconds forever.

## 7. Today's Phase 3 findings, on named Phase 4 surfaces

**A connection held across a wait.** Phase 4 has four waits: idle between
leases, the drain, the release download, and the pairing code. The shape is not
new, but Phase 4 adds the read: `jobs_store` contains zero SELECTs today and the
deletion gate is the device reading decisions back, so this puts a Postgres read
on the far side of a lease wait for the first time. The existing guard does not
travel: `tests/test_cloud_sweep.py` partitions `inspect.getsource` on the
literal `wait_for_sweep(`, bound to one function in one module by name, and
Phase 4 moves that code into `packages/runner`. Underneath it,
`packages/schema/migrate.connect` sets neither `connect_timeout` nor
`statement_timeout`, and it is what `dual_write` uses for every class A persist.

**A deadline too short discards completed work.** The Phase 4 deadline is the
lease's `expires_at`, and "a lease that expires without a terminal event is
reclaimed and re-offered" is the erring-short direction. The payload is worse
than 44,621 staged rows: a second agent at a form the first may already have
submitted. The wrong input is the observed apply distribution; the enforced
ceiling is about 90 minutes for apply and 110 for documents. The drain has no
deadline in the plan at all, which is the correct direction and should stay.

**A count is not the thing it counts.** The count is `free_slots` in the
capacity advertisement, produced for the runner's own scheduling, with the DO as
a second consumer using it to choose which application ids to lease. The device
already needs which, not how many: `candidate_apply_slots` returns a parked
application's own affinity slot, which `free_apply_slots` does not contain, and
`do_not_start` refuses per application. A lease issued against a scalar for an
application `do_not_start` will refuse books capacity that idles until it
expires.

**The pool lock.** Phase 3 made the 26.8-minute holder conditional rather than
removing it: `watch.py` still spawns `ingest.py --tier 1` whenever
`cloud_sweep.configured()` returns no Fly app. Phase 4 deletes the file the lock
is named for, and deleting the file does not delete what the lock serialised:
`jobs_store.edit` holds it across the caller's whole body and opens the mirror
connection inside it. At least eleven modules reach it, two on the apply path.
**The only lock named anywhere in the Phase 4 material is the deploy lock.**

**Single-threaded and serial.** This is a second, independent argument for two
processes that the design doc does not make. There is no `asyncio` anywhere in
`scripts/`; the device's work is blocking subprocess calls, and `run_apply`
makes seven synchronous `subprocess.run` calls with no timeout on any of them,
one of which takes the pool lock and publishes. "A single async process with
task groups" is a from-scratch concurrency model over code that yields nowhere.

**Wall clock is not linear in work.** The drain is an order statistic over eight
concurrent apply runs plus a document job plus a detached sweep, not a p95 of
one of them. At the doc's own p95 of 16 minutes, one of eight exceeds it about a
third of the time.

## 8. Measured, and unmeasured

Measured on this machine: apply wall time over 64 runs, median 5.7 min, p95
16.0, max 18.6, zero over 45 (say which percentile method; this repo has been
bitten by that once). Kernel priority at rest, documents 4, watch 4, providers
31, and all three have read 46 under load, so 4 and 31 are resting values.
Staged-file signal cleanup firing, verified by removing the handler. Pool lock
for the cloud merge, 30.8 s at 12,632 postings and 50.9 s at 23,207, two
instruments. 176 watcher ticks over 13 days skipped their board reconcile on
`scan`. The tick parses the whole 100 MB pool every five seconds, about 0.27 s.

Unmeasured, and each is a real gap: Connect click latency at Background against
Interactive, **under load rather than at rest**, which is the number the
one-or-two-processes decision turns on and which does not exist anywhere. What
the document worker needs from `NODE_OPTIONS` and `MALLOC_ARENA_MAX`, which
entered in one commit with no reason given and are two questions with different
answers, since `MALLOC_ARENA_MAX` is hardcoded on the provider child anyway and
is a glibc tunable on a machine that does not use glibc. Whether an `os.execv`
from a launchd-supervised process keeps the plist's `EnvironmentVariables`, the
real question being whether the code uses `execv` or `execve`. Tick wall time,
which nothing in `watch.py` measures.

## 9. Decisions taken, 2026-09-08

All eleven are settled. Recorded with the reasoning, because the reasoning is
what a later reader needs when one of them looks wrong.

**1. Fix the providers `LOCK_NB` before measuring anything.** The tick blocking
on a 150-second probe is a bug whatever the process count turns out to be, and
a Connect-click latency taken with it in place is a number about the bug.
Shipped in #224: the probes left the lock and the tick stopped waiting.

**2. One process or two stays open, and the measurement comes first.** Connect
click latency at Background against Interactive, under load rather than at
rest, which is the number that does not exist anywhere.

**3. `POOL_LOCK` becomes a Postgres advisory lock keyed on the account id when
the file store goes.** Same mechanism `migrate.py` already uses, same
semantics. The two apply-path callers take it `LOCK_NB`: an apply that waits on
a sweep is the 176 blocked ticks again in a new place.

**4. The circular sequence resolves by ordering.** Device reads Postgres, then
the deletion, then the rest of the runner. The design doc has it the other way
and it has already run out of order, since `live_board` is on and the file is
still 100 MB on disk. §6 gets rewritten so the gate comes before the thing it
gates.

**5. `ai.jobby.parity` is reloaded.** It was unloaded at the v2 cutover because
parity would report v2 writes as divergences, and the answer to that was a
marker rather than an unloaded instrument. Until the file store goes it is the
only thing asserting the mirror is complete. Reloaded 2026-09-08 into a green
run; the v2 class is labelled `awaiting_device` per migration 0023.

**6. Version AND hash, and they answer different questions.** The hash is
identity, the version is ordering. The cloud refuses a lease if the hash does
not match the manifest **or** the version is below `min_supported`. If the two
disagree, a hash the manifest knows under a different version, that is a
tampered or half-unpacked runner and the lease is refused. `devices` gains a
hash column.

**7. Lease TTL from the enforced ceiling, not the observed distribution.** 90
minutes for an apply, 110 for documents. The observed distribution is exactly
what nearly beat `WAIT_SECONDS` today, by 57 seconds. And the runner stops on
its own lease expiry: a runner still working past a lease the cloud has
reclaimed is two agents on one application.

**8. Capacity stays a scalar and a refused lease is normal.** `do_not_start` is
the authority and it is per application; encoding its answer into the
advertisement is a second copy of it, which is the class this file already
carries five instances of. A refused lease costs one round trip. Log the
refusal rate, and a high one is a different problem with its own diagnosis.

**9. The CI runner leg is built first.** Nothing produces a manifest today, so
hash verification has nothing to verify against. It is the prerequisite §6 does
not name, and it goes before the runner itself.

**10. ADR 0008 is superseded by a new ADR, not amended.** An accepted ADR that
says one process, amended to say two, loses the record of why the first was
wrong. The new one cites the priority measurement and the `LOCK_NB` finding.

**11. The KV click queue deletion moves earlier, and goes with the file
store.** Four Open-and-finish presses lost to the queue's own readers on
2026-09-07 is a live defect available to every button, and the device reading
Postgres is Phase 4's first item anyway.

**12. The gate artifact is the exec-time SHA-256, on a device-only commit.** A
board change is also a Vercel deploy, so a screenshot cannot separate the two.
A `scripts/`-only change can, and the gate commit is made deliberately
device-only.

## 10. On the Phase 4 list, with numbers and no theory

**Greenhouse's post-200 stall now bounds the sweep.** Three measurements, taken
2026-09-08 on the same registry:

    12,632 postings    29.5 min      the first warm sweep
    23,207 postings    66.4 min
    33,899 postings   119.1 min      fetchSeconds 7,143.2 against WAIT_SECONDS 7,200

Greenhouse is the bulk of it: 3,399 s of one 76-minute sweep, about 45 seconds
per board that answers 200 where the throttle floor is 1.0 and the laptop
fingerprints the largest board in the corpus in 3.1 seconds.

**No theory is recorded here on purpose.** Four were written and all four were
refuted arithmetically, including two of this document's own: payload, parse
cost, memory pressure and CPU starvation. `phase3-inputs.md` section 13g has
the refutations and the one thing that survives, which is a shape rather than a
cause: `T ~ N^k` with k = 1.54, 1.56 and 1.73, and any `T = F + cN` with
`F >= 0` has `dlnT/dlnN <= 1`, so no flat per-posting model can produce it at
any parameters.

**The next time it beats `WAIT_SECONDS`, a merge is lost.** Today it cleared
the deadline by 57 seconds. And the residual it is measured against is a
subtraction from a floor nobody has measured: `sweep_runs` has no `requests`
column and `Fetcher.get` retries a status-0 response three times, re-downloading
a multi-megabyte body each time, invisibly. **The next step is that column and a
per-retry log line, not a fifth hypothesis.**

## 11. Phase 4 starts with the device reading Postgres

That is the gate for the file store deletion, for the KV queue, and for
everything after, so it goes first.

Measured before writing any of it, per record and in both directions rather
than by count:

    pool decisions        131      102 applied, 17 watched, 12 passed
    job_decisions         131      102 submitted, 17 watching, 12 dropped
    only on the device      0
    only in Postgres        0
    state disagreements     0
    decisions with no posting row  0

Counts agreeing is the trap this document already records twice, so the sets
were compared. They agree exactly, which is the strongest possible starting
position: a device that read its decisions from Postgres today would see what
it sees now.

`scripts/decisions.py` is the reader and it changes no authority. The file
still decides. The mapping is inverted from `backfill.STATE_MAP` rather than
restated, an unmappable state raises rather than reading as no decision, and a
decision whose posting row is gone is counted rather than silently dropped.
Switching a consumer over is a separate change with its own evidence, one
consumer at a time.
