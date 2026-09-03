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

## What the deploy-lock quarantine costs

`LeaseTests` still covers acquire, refusal, expiry and release directly, and
`CommandTests` still covers the refusal exit code and `break`. What left is the
CLI's happy path end to end. That is real coverage and the entry should not
outlive somebody finding fifteen minutes for it.
