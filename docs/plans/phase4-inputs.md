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

## 9. Decisions that need Sahil, not more reading

1. One process or two, and if the answer is "measure first", who takes the
   Connect-click reading under load, and when?
2. Does the `providers` flock become `LOCK_NB` before that argument is had,
   given the tick and `--status` both block on it today?
3. Does the runner report a version alongside the hash so `min_supported` can
   order it, and which wins when they disagree?
4. Is the lease TTL derived from the enforced per-attempt ceiling (about 90
   minutes apply, 110 documents) or from the observed distribution, and does the
   runner stop working when its own lease expires?
5. Does the capacity advertisement carry which application ids the device can
   run, or stay a scalar and accept refused leases as normal?
6. What replaces `POOL_LOCK` when the file goes, given eleven modules take it
   and two are on the apply path?
7. Which comes first, the file store deletion or the runner, given the deletion
   is gated on runner work the design doc schedules after it?
8. Does the runner leg of CI get built before Phase 4 starts, since nothing
   today produces the manifest the hash verification compares against?
9. Is ADR 0008 amended, superseded, or left standing while the design doc
   contradicts it?
10. Does the KV click queue deletion move earlier, given the single
    `jobby:queue` key lost four Open and finish presses to its own readers on
    2026-09-07?
11. What artifact does the Phase 4 gate accept as proof the laptop replaced
    itself, given a board change is also a Vercel deploy?
