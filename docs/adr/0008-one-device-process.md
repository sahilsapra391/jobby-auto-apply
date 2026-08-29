# ADR 0008: One device process, not three

- Status: accepted
- Date: 2026-08-29
- Source: §2 and §6, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

`python3 scripts/watch.py --install` owns three launchd services: the queue
watcher and form orchestrator, serial document generation, and a provider
control lane polling every two seconds. The third exists because a slow provider
or application probe delayed a Connect or Disconnect click.

## Decision

One supervised async process with internal task groups, under one launchd plist
with `KeepAlive true` and `RunAtLoad true`.

## Rejected

**Keeping three services.** Three services is the thing being complained about:
merging code does not deploy it, and a deploy means restarting all three in the
right order and verifying each against the reflog.

## Consequences

The constraint that produced the split is real and does not go away. A slow
provider probe must still not delay a Connect click. The answer is an async task
inside one process rather than a second daemon, and Phase 4 has to demonstrate
that rather than assert it.

`KeepAlive true` is what makes self-replacement safe. The runner downloads a
signed release, verifies its SHA-256 against the manifest, swaps a symlink and
`os.execv`s itself; launchd is not involved and does not need telling. That is
the mechanism that answers "merging is not deploying" on the device.

Two rules are not negotiable. An application under way is a real form on a real
employer's site with a real name attached, so an update waits for it. And a
`finally` block does not run on a signal, so the staged-PDF cleanup needs real
`SIGTERM` and `SIGINT` handlers, proven by sending the actual signal and then
asserting the staging directory is empty on disk. This repo has already paid for
that lesson once: the firewall's call counter was written at the end of `main()`
for two months, and no run that reached a form ever wrote one, because both CLIs
signal their MCP child rather than closing its stdin.
