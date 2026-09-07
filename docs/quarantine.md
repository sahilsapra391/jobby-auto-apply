# Quarantined tests

Tests moved out of the collected suite because their failures stopped carrying
information. **A suite that goes red for a reason nobody believes trains
everyone to read past the red**, and this repository's recurring failure is
exactly that: a check that looks like it measures something and does not.

Quarantine is not deletion and it is not a fix. It is an admission, recorded,
that the test costs more than it currently provides. Each entry names what is
known, what is not, and what would bring it back.

Files here are named `quarantined_*.py` so `unittest discover -s tests`, whose
pattern is `test*.py`, does not collect them. Run one deliberately:

    python3 -m unittest tests.quarantined_deploy_lock

`tests/test_quarantine_is_honest.py` keeps this list and those files in step,
so a quarantined test cannot quietly rot into a file nobody can import.

| test | since | why |
|---|---|---|
| `quarantined_deploy_lock.QuarantinedCommandTests.test_take_release_round_trip` | 2026-09-03 | Fails inside a full `discover` run, passes in isolation, with and without the tree that was present when it first failed. Failed once before and was passed over twice. Undiagnosed. |
| `quarantined_deploy_lock.QuarantinedCommandTests.test_extend_needs_the_token` | 2026-09-06 | Same family as the round trip. Takes a **120-second** lease and then makes two more CLI calls against it. Failed once in two full `discover` runs and passed 3 of 3 in isolation. Undiagnosed. |

## The deploy-lock tests are one family, and the lock file is why

**Both quarantined tests shell out to the CLI several times per assertion and
both take a real lease.** `deploy_lock` is a lease rather than a pid lock on
purpose: a deploy is a dozen short-lived commands with no process alive across
them, so the whole of its state is a file whose meaning depends on the wall
clock.

Every test in `CommandTests` points `JOB_APPLICATION_DIR` at its own
`TemporaryDirectory`. **That isolates the path and not the clock**, and a
120-second lease outlives the test that took it by two orders of magnitude, so
whatever ordering or timing makes these fail in a full run is not something a
fresh directory can prevent.

Written as a hypothesis because it is one. What would settle it: an injectable
clock on the lease, or tests that take the shortest lease the CLI accepts and
assert on refusal rather than on duration. Until one is done, **a fix for
either test should be assumed to apply to both**, and a third test in this
shape should be expected rather than treated as a new surprise.

## What the deploy-lock quarantine costs

`LeaseTests` still covers acquire, refusal, expiry and release directly, and
`CommandTests` still covers the refusal exit code and `break`. What left is the
CLI's happy path end to end. That is real coverage and the entry should not
outlive somebody finding fifteen minutes for it.
