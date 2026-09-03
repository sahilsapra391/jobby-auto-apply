# Phase 4 design notes: the device runner

Design only, written in the parity window on 2026-09-03. Nothing here is built
and nothing here touches a gate path.

Phase 4 is the phase that ends the restart problem, and the day this was written
supplied the strongest argument for it anyone could have assembled by hand. Read
section 1 before section 2, because the argument is better than the plan's own.

Sources: `docs/plans/jobby-v2-rebuild-plan.md` section 6 and Phase 4,
`docs/adr/0008-one-device-process.md`. Every number below was read off this
machine on 2026-09-03.

## 1. The argument, measured this morning

The plan says "merging is not deploying" and leaves it there. What actually
happened:

```
Sep 1 16:39:43   ai.jobby.documents, ai.jobby.providers start   (bd76537)
Sep 1 16:49:57   ai.jobby.watch starts                          (0a64800)
Sep 1 17:16:02   pull -> 6a2f46b      no restart
Sep 1 17:54:25   pull -> 4198d25      no restart
Sep 1 20:51:26   pull -> 1a87673      no restart
Sep 2 18:09:56   pull -> 633b421      no restart
```

**Four pulls, no restarts, 35 hours.** The resident half of production ran 21
commits behind the disk.

**And the sharp version of that is not "old code".** It is that the machine ran
two commits at once, chosen by nobody, split by a rule nobody can apply
reliably: a file reached through a resident import arms on the restart, a file
reached only by a spawned process arms on the pull, and one deploy carries both.
The three services hold these:

```
ai.jobby.watch       watch -> jobs_store -> dual_write
                     watch -> document_job -> store -> class_b
ai.jobby.documents   document_worker -> document_contract -> store -> dual_write, class_b
ai.jobby.providers   provider_login -> settings_panel
```

So for 35 hours all three daemons held a `dual_write.py` and a `class_b.py` from
before the two `on conflict` fixes, while every spawned `ingest.py` loaded the
fixed ones. Nothing broke, and the reason is narrow rather than reassuring:
`watch.py` performs no class A pool write itself (its own comment at line 439
says the apply path never calls `save_jobs`, and there is no call site), so the
stale resident copy was held and not exercised. `ai.jobby.documents` is the one
where it was exercised, because its class B session writes really do go through
the resident `class_b.py`.

That is the case for Phase 4 in one paragraph: **the current design makes the
arming moment a property of each file's call path, and no human enumerates that
correctly under deploy pressure.** This repository has the receipts: a session
told five peers "#120 arms on the restart" while the same deploy carried two
PRs that armed on the pull, and it took three sessions converging to correct it.

## 2. What is stale in the plan

| claim | as written | measured 2026-09-03 | verdict |
|---|---|---|---|
| why three services exist | "a slow provider probe delayed a Connect click" | three different **resource profiles**; `Interactive` pri 31 against `Background` pri 4, measured | **two processes is the expected outcome**, not one |
| signal-safe staged cleanup | "register handlers ... prove it with a test" | **already built** in `run_apply`, and it works | done, and the test now exists |
| drain window | implied by a 45-minute run | 62 runs: median **5.7 min**, p95 16.0, max **18.6**, zero over 45 | 45 is a timeout, not a duration |
| anything else | -- | nothing built | accurate |

### The reason there are three services is not the reason ADR 0008 gives

ADR 0008 says the third service exists because a slow provider probe delayed a
Connect click, and proposes an async task inside one process. Read the installed
plists and there are **three resource profiles**, not one latency problem:

```
ai.jobby.watch       ProcessType Background   LowPriorityIO true    --interval 5
ai.jobby.documents   ProcessType Background   LowPriorityIO true
                     NODE_OPTIONS=--max-old-space-size=512  MALLOC_ARENA_MAX=2
ai.jobby.providers   ProcessType Interactive  LowPriorityIO unset   --interval 2
```

**And the scheduling difference is real, not nominal.** On the live machine
right now, same nice value, different kernel priority:

```
88611  documents  Background    nice 0   pri  4
92892  watch      Background    nice 0   pri  4
88615  providers  Interactive   nice 0   pri 31
```

`asyncio` task groups have no per-task scheduling class. macOS has per-thread
QoS and Python's standard library has no binding for it, and `os.setpriority`
with `PRIO_PROCESS` is per-process by definition. **So one process means one
scheduling class for the provider lane and the document worker together**, and
neither choice is free: `Background` throttles the Connect click that the third
service was created to protect, and `Interactive` removes the throttle from the
document worker that `LowPriorityIO` exists to contain.

The environment split has the same shape. `NODE_OPTIONS` and `MALLOC_ARENA_MAX`
are set for the document service alone; in one process they are set for every
node child that process ever spawns, the renderer and the Vercel CLI included.

**So two processes is the expected outcome, and one is the thing that has to be
demonstrated.** Writing it the other way round -- one process, with two as a
fallback if something goes wrong -- gets the burden of proof backwards, because
the priority difference is measured and the claim that it does not matter is
not. `Interactive` at 31 against `Background` at 4 is a real difference that a
single process cannot express, and no amount of `asyncio` structure creates one.

The shape to build:

```
jobby-runner        supervisor: watch loop, document work, leases, re-exec
                    ProcessType Background, LowPriorityIO, the node caps
jobby-controls      the provider control lane, nothing else
                    ProcessType Interactive, no throttle
```

Two plists instead of three, and the second one is small, does no work that
takes time, and is the only thing a Connect click touches.

**And the restart problem is not solved by the process count.** It is solved by
`KeepAlive` plus self-replacement, and those work identically for two processes
as for one: the supervisor re-execs itself, and it can restart its sibling the
same way launchd would. Collapsing three into one was never what fixed the
deploy; it was one design that happened to arrive alongside the thing that did.
Reading Phase 4 as "the collapse phase" is what makes the priority measurement
look like an obstacle rather than an input.

One process remains available and should be adopted the moment it is earned:
**time a Connect click with the provider lane installed at `Background`, against
the same click at `Interactive`.** If there is no measurable difference, merge
them and ADR 0008 is vindicated with evidence rather than reasoning. That is a
cheap measurement and nobody has taken it, which is the whole reason the ADR's
own closing line asks Phase 4 to demonstrate rather than assert.

### The signals bullet is already built, and it now has a reading

`run_apply._install_discard_signal_handlers` registers `SIGTERM`, `SIGINT` and
`SIGHUP`, calls `browser.discard_staged()`, restores the default disposition and
re-raises, so the exit status still reports the signal. It was written for the
plan's own stated reason, and it cites the same precedent the plan cites.

It had never been observed firing. It fires. Driven on this machine against a
real process and a real signal:

```
before SIGTERM   activity.json  cover.pdf  resume.pdf
after  SIGTERM   activity.json
exit status      143   (128 + 15)
```

Both PDFs gone from disk, `activity.json` deliberately kept, signal disposition
correctly restored. **Verified by breaking it**: with the handler removed, the
same signal leaves all three files in place.

So Phase 4's negative test (d) already passes against code that exists, and what
is missing is the test rather than the behaviour. Two things follow. That
assertion belongs in the suite now rather than in Phase 4, because a guard
nobody runs is a guard that decays. And the *supervisor* has no signal handler
at all today, which is the half Phase 4 genuinely adds: `run_apply` cleans up
after itself, and nothing cleans up after `watch.py`.

### The drain window is 19 minutes, not 45

`run_apply.TIMEOUT_SECONDS = 45 * 60`, and the 45 has propagated into CLAUDE.md
and into how this phase reads. It is a ceiling. Measured over the 62 apply runs
on this machine with a readable wall time:

```
median 5.7 min    p75 7.7    p90 11.0    p95 16.0    max 18.6
runs over 45 minutes: 0 of 62
```

"Never interrupt an in-flight application" therefore costs a worst-observed
19-minute wait and a typical 6, against an update that is not urgent by
construction. That makes drain-before-replace cheap, and it removes the only
real argument for interrupting a run, which should not exist as an option at
all.

## 3. What the plan gets right and should not be redesigned

The lease verification is correct and its ordering is correct. A lease carries
`account_id` and a signature, the runner verifies against a pinned cloud key and
rejects a foreign `account_id`, and the plan says to write that negative test
before the happy path. Keep both instructions. Anything that can reach the
socket can otherwise make this laptop open a URL and type into it under a real
name.

`KeepAlive true` as the thing that makes self-replacement safe is right, and it
is why the re-exec does not have to involve launchd.

Refusing new leases when `runner_version < min_supported` is right, and it is
the only part of the update path that must not wait for a drain.

## 4. What self-replacement costs, and it is a verification gap

Today the question "which commit is this daemon running" has an answer:
`ProgramArguments` names an absolute path into the production checkout, so a
service start time compared against that checkout's reflog settles it. That
comparison is how every instance of the two-commits-at-once state has ever been
found, including this morning's.

**A symlink swap deletes that method and the plan does not replace it.** The
plist points at `/usr/local/bin/jobby-runner`, the runner replaces its own
target, and the checkout's reflog stops being the authority.

**`{"runner_version":"2.3.1"}` in the capacity advertisement does not replace
it, and this is a change to the plan rather than a refinement of it.** A version
a runner reports about itself is the `window.__jb_ran_theme` shape exactly: a
flag set by the thing being measured, which reads as "it ran", is true, and is
useless. A runner that re-execs into a half-unpacked directory, or swaps the
symlink and fails to exec, or execs a binary whose bytes are wrong, keeps
answering with the version it believes it is. Every one of those is a state the
reflog comparison would have caught and a version string cannot.

Replace it with a hash the runner did not choose:

```
the runner reports   the SHA-256 of the file it is executing, computed at exec
                     time from its own resolved symlink target -- not a
                     constant compiled into it
the cloud verifies   that hash against the manifest SHA-256 the cloud itself
                     served for that version, and refuses to lease to a runner
                     whose hash it cannot match
```

The second half is what makes it a verification rather than a nicer self-report.
A hash the runner computes is still the runner's word; a hash the cloud can
compare against what it shipped is not.

Same distinction as the R2 manifest's `--verify` being deep by default: a
stamped version survives a corrupted body, so a version-only check calls
corruption healthy. And the same reason `migrate.py status` is not schema
verification -- it compares the record against the directory, which is an
adjacent question that reads as coverage.

And one operational property worth keeping from the current design: the deploy
lock. Self-replacement removes the human from the deploy, which removes the
thing the lock excludes. It does not remove the need to know that a replacement
is in progress, so `runner.state` needs `replacing` visible from the board, not
just internal to the state machine.

## 5. What to measure before building it

- **Connect click latency with the provider lane at `Background`.** This is the
  measurement ADR 0008 asks for and nobody has taken. It does not decide whether
  Phase 4 ships -- two processes is the plan -- it decides whether one process
  becomes available. Install the provider lane under `Background`, time a
  Connect, compare against `Interactive`. If there is no measurable difference,
  merge them and the ADR is vindicated with evidence.
- **What the document worker actually needs from `NODE_OPTIONS` and
  `MALLOC_ARENA_MAX`.** Both were added for a reason nobody has written down. If
  they are load-bearing, one process has to set them globally and the blast
  radius is every node child; if they are not, delete them and the constraint
  disappears.
- **Whether `os.execv` from a launchd-supervised process on this macOS version
  keeps the plist's `EnvironmentVariables`.** The runner inherits `PATH` from
  `toolpath.search_path()` and launchd hands over almost nothing, so an exec that
  loses it produces a runner that cannot find node, vercel or the CLIs, and it
  will look like a provider fault.
- Nothing further on the staged-file cleanup. `tests/test_signal_clears_staged_documents.py`
  now sends a real SIGTERM, SIGINT and SIGHUP to a real process and reads the
  directory afterwards, asserts `activity.json` survives, asserts the exit status
  still reports the signal, and carries the handler-absent mutation so it cannot
  pass vacuously. Verified by disabling `browser.discard_staged`, which fails it.
  Phase 4's negative test (d) is done.

## 6. The sequencing claim, so it is not re-derived later

Phase 4 does not depend on Phase 3 finishing. It depends on the parity gate
closing, because the runner's own writes go through the same mirror, and a
device that replaces itself while two stores are being reconciled is two moving
parts where one would do.

Order: parity gate closes, file store deleted, parity retires, `live_board`
flips, then the runner collapses. The runner is the last thing to change,
because it is the only component whose failure is a real form on a real
employer's site with a real name attached.
