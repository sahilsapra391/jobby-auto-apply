#!/usr/bin/env python3
"""Start the cloud fetch, wait for it, and merge what it staged.

**The pool lock is taken for the merge and for nothing else.** That is the whole
point of moving the fetch. `jobs_store.POOL_LOCK` is the string `"scan"` and it
is the same lock file `watch.take_lock("scan")` writes, so a local sweep holds
the pool's write lock for its entire run: a median 26.8 minutes, six times a
day, and 176 ticks across 13 days skipped their board reconcile with LockBusy
on it. Eight non-sweep callers take that lock and two are on the apply path.

So this process holds `cloud-sweep` for its whole life -- which stops two
overlapping cloud sweeps and blocks nothing, because `scan_blocking_apply`
reads `scan` and not this -- and takes `scan` only around the merge.

    fetch     no lock at all, in a container in iad
    wait      no lock, polling sweep_runs
    merge     scan, for as long as the merge takes

**The merge is the existing path, unchanged.** `ingest.run` is called with
`sources.ALL` swapped for connectors that return what the container staged, so
the gate, the pool merge, all five closure brakes, the prune and the publish
are the same code that runs today. That is deliberate rather than clever: the
alternative was splitting `run()` into a fetch half and a merge half, and the
merge half is the most consequential function in this repository. This is also
exactly the shape `tests/test_ingest.py` already drives.

**A cloud sweep that cannot run falls back to the local one.** No Fly app
configured, the machine will not start, the container failed, nothing staged:
each returns a named reason and the caller runs `ingest.py --tier 1` as before.
A sourcing pass that silently does not happen is the failure this project has
met more than any other.
"""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "sources"))

import jobs_store  # noqa: E402
import runlock  # noqa: E402
import toolpath  # noqa: E402

#: Which Fly app and machine to start. Absent means "no cloud sweep", which is
#: a fallback rather than an error: the device can still sweep locally, and a
#: half-configured deployment must not stop sourcing.
APP_VAR = "JOBBY_FLY_APP"
MACHINE_VAR = "JOBBY_FLY_MACHINE"

#: How long to wait for the container before giving up and falling back.
#:
#: **Derived from a measured warm sweep, and the earlier reasoning was wrong
#: about what this bounds.** Twelve minutes came from a 25.8-second run against
#: a four-tenant registry and the argument that "a cloud fetch slower than the
#: laptop has lost its only argument". The first full warm sweep against the
#: real 795-board registry, on 2026-09-08, took **29.5 minutes** (06:04:09 to
#: 06:33:37, 27 channels, 12,632 postings), against the device's own median of
#: 26.8. The cloud fetch is not faster and was never going to be: 795 boards at
#: `MIN_INTERVAL_PER_HOST` is 13 minutes of deliberate sleeping before any work,
#: and the container is a shared 1x vCPU where the laptop is not.
#:
#: Speed was the wrong argument. What moved is the **pool lock**, measured on
#: the same sweep at 30.8 seconds by two independent instruments, against a
#: local sweep holding it for the whole 26.8 minutes. The device is not waiting
#: on anything during the fetch, so its wall clock costs nothing.
#:
#: So this bounds a *hung* container, not a slow one. Forty-five minutes is
#: about 1.5x the measured warm sweep -- headroom for a colder one, since 77 of
#: greenhouse's 353 boards still answered 200 on that run -- and comfortably
#: inside `tier1IntervalMinutes`, which defaults to 4 hours. Overrunning it
#: falls back to a local sweep, which costs the 26.8 minutes of lock this
#: exists to avoid, so erring long is the cheap direction.
WAIT_SECONDS = 45 * 60
POLL_SECONDS = 10


class CloudSweepUnavailable(Exception):
    """The cloud path cannot run, with a reason the caller should print."""


def configured(settings=None):
    values = settings if settings is not None else toolpath.settings()
    app = (values.get(APP_VAR) or os.environ.get(APP_VAR) or "").strip()
    machine = (values.get(MACHINE_VAR) or os.environ.get(MACHINE_VAR) or "").strip()
    return (app, machine) if app and machine else (None, None)


def start_machine(app, machine, run=None):
    """Start the stopped worker. Returns the moment it was asked.

    **`run` defaults to None and is resolved here, not in the signature.** A
    default of `subprocess.run` is bound once when the `def` executes, so
    `mock.patch.object(cloud_sweep.subprocess, "run", ...)` does not reach it
    -- and a test written that way does not fail, it runs the real `flyctl`.
    That happened while writing the tests below: one of them started a real
    machine command against the real app and was saved only by the runner
    having no Fly token. Same frozen-default shape
    `NoStorePathIsAFrozenDefaultTests` exists for, one type over.
    """
    run = run or subprocess.run
    asked = datetime.now(timezone.utc)
    # `required=False`, or `find` raises SystemExit on a machine with no
    # flyctl and the `or "flyctl"` after it is dead code guarding a None the
    # function never returns. It read as a fallback; CI, which has no flyctl,
    # was the first place it ran. The injected `run` ignores argv[0] anyway.
    fly = toolpath.find("flyctl", required=False) or "flyctl"
    result = run([fly, "machine", "start", machine, "--app", app],
                 capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        return asked
    said = str(result.stderr or "") + str(result.stdout or "")
    if True:
        # **A machine already running is not a failure to start it.** The
        # sweep this process exists to merge is already in flight, and
        # refusing here would fall back to a local sweep that then fights the
        # cloud one for the pool lock -- two sweeps rewriting the same store,
        # which is the thing `SWEEP_LOCK` exists to prevent.
        if "already started" in said.lower() or "already running" in said.lower():
            return asked
        raise CloudSweepUnavailable(
            f"flyctl could not start {machine}: {said.strip()[:300]}")
    return asked


def machine_state(app, machine, run=None):
    """What Fly says the machine is doing, or "" when it cannot be read."""
    run = run or subprocess.run
    # `required=False`, or `find` raises SystemExit on a machine with no
    # flyctl and the `or "flyctl"` after it is dead code guarding a None the
    # function never returns. It read as a fallback; CI, which has no flyctl,
    # was the first place it ran. The injected `run` ignores argv[0] anyway.
    fly = toolpath.find("flyctl", required=False) or "flyctl"
    # **`machines list --json`, not `machine status --json`.** The latter is not
    # a flag flyctl has: it exits non-zero with usage text, so this returned ""
    # forever, `wait_for_sweep` correctly refused to read could-not-look as
    # stopped, and a finished sweep sat staged while the device waited out its
    # deadline. The guard did its job; the command it guarded was wrong.
    #
    # Third unverified flyctl invocation in this feature, after `--command`
    # (which appends to an ENTRYPOINT rather than replacing it) and `--config`
    # (resolved against the build-context argument). A flag that reads
    # plausibly is not a flag that exists, and none of the three failed until
    # it ran.
    result = run([fly, "machines", "list", "--app", app, "--json"],
                 capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return ""
    try:
        machines = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(machines, list):
        return ""
    for entry in machines:
        if str((entry or {}).get("id") or "") == machine:
            return str(entry.get("state") or "")
    # Absent from a listing that was read is not the same as unreadable: a
    # machine that no longer exists cannot be waited for.
    return "destroyed"


def wait_for_sweep(connect, account, since, app=None, machine=None,
                   seconds=WAIT_SECONDS, poll=POLL_SECONDS, sleep=time.sleep,
                   run=None):
    """The newest sweep id, once the container has actually stopped.

    **`connect` is a callable, and a connection per poll.** This is the same
    fault `sweep_worker.stage` was fixed for and it was left open one file
    away: the wait is the longest-lived thing in the process, 29.5 minutes on
    the measured warm sweep, and Neon closes an idle connection long before
    that. Held across the wait, the device died with `consuming input failed:
    SSL connection has been closed unexpectedly` **after** the container had
    finished writing all 27 channels -- a completed fetch reported as a failed
    run. A rule stated in one function's docstring is not a rule the codebase
    holds, and the closer the neighbour the less likely anybody checks.

    **"Every row has a `finished_at`" is trivially true when one row exists.**
    `sweep_worker.stage` commits per channel, so a sweep two seconds old with
    greenhouse written and twenty-six channels still to come satisfies that
    condition exactly. Measured on the first real run: it returned a sweep of
    1 channel and 50 postings at 22:57:49, and the merge treated a twenty-
    seventh of a sweep as a whole one. Nothing was closed, because brake 1
    never lets a channel it did not see be touched -- but the guard that saved
    it was one process away, and the wait had no business relying on it.

    **The machine stopping is the signal.** It is the container's own statement
    that it has no more to write, and it cannot be true early. The row count is
    kept as a second condition rather than the only one, so a machine that
    Fly reports stopped without the sweep having written anything is still a
    failure with a reason rather than an empty merge.
    """
    deadline = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    stopped = False
    while True:
        if app and machine and not stopped:
            state = machine_state(app, machine, run=run)
            # "" is could-not-read, which is not "stopped". Waiting on an
            # unreadable state until the deadline is the safe direction: the
            # alternative merges a sweep still being written.
            stopped = state in ("stopped", "destroyed", "failed")
        elif not app:
            stopped = True                    # no machine to wait on

        row = None
        if stopped:
            conn = connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "select sweep_id, count(*), count(finished_at) "
                        "from sweep_runs where account_id = %s "
                        "and started_at >= %s "
                        "group by sweep_id order by min(started_at) desc "
                        "limit 1",
                        (account, since))
                    row = cur.fetchone()
                conn.rollback()
            finally:
                conn.close()
            if row and row[1] and row[1] == row[2]:
                return str(row[0])

        if datetime.now(timezone.utc) >= deadline:
            if not stopped:
                raise CloudSweepUnavailable(
                    f"the machine was still running after {seconds}s; "
                    f"`flyctl logs --app {app} --machine {machine}` says why")
            seen = "it stopped without writing a sweep" if not row else (
                f"it stopped with {row[2]} of {row[1]} channels finished")
            raise CloudSweepUnavailable(
                f"no usable sweep within {seconds}s ({seen})")
        sleep(poll)


def staged(conn, account, sweep_id):
    """`{channel: envelope}` for one sweep, in the shape a connector returns.

    Rebuilt rather than stored so that what the merge sees is exactly what
    `base.ok` produces: one definition of a channel result, not two.
    """
    from sources import base
    with conn.cursor() as cur:
        cur.execute(
            "select source, status, job_count, unchanged, unchanged_slugs, "
            "listing_complete, reason from sweep_runs "
            "where account_id = %s and sweep_id = %s",
            (account, sweep_id))
        runs = cur.fetchall()
        cur.execute(
            "select source, posting from sweep_postings "
            "where account_id = %s and sweep_id = %s order by id",
            (account, sweep_id))
        rows = cur.fetchall()
    conn.rollback()
    postings = {}
    for source, posting in rows:
        value = posting if isinstance(posting, dict) else json.loads(posting)
        postings.setdefault(source, []).append(value)

    out = {}
    for source, status, count, unchanged, slugs, complete, reason in runs:
        mine = postings.get(source, [])
        if status == "failed":
            out[source] = base.failed(source, reason or "the cloud sweep failed")
        elif status == "skipped":
            out[source] = base.skipped(source, reason or "skipped in the cloud")
        else:
            # **`unchanged_slugs` travels, not just its length.** The count
            # answers the coverage ratchet and the list answers the canary,
            # and rebuilding with only the count is what made a warm sweep
            # report spacex and snowflake silent: both had legitimately 304'd,
            # `canary_problem`'s excuse reads the list, and the list was empty
            # because nothing had carried it. Migration 0022.
            out[source] = base.ok(source, mine, requests=0, note=reason or "",
                                  complete=bool(complete),
                                  unchanged=int(unchanged or 0),
                                  unchanged_slugs=_slugs(slugs))
        if len(mine) != int(count or 0) and status == "complete":
            # Said rather than smoothed over: the run row and the rows it
            # counts disagreeing means a partial write, and the closure step is
            # about to read this as a whole listing.
            out[source] = base.failed(
                source, f"staged {len(mine)} postings against a job_count of "
                        f"{count}; refusing to treat it as complete")
    return out


def _slugs(value):
    """The staged `unchanged_slugs` as a list of strings.

    psycopg gives a jsonb column back as the parsed value, but a driver or a
    fixture may hand over the text, and a string iterated as a sequence would
    make every character a board slug. Refused rather than iterated.
    """
    from sources import base
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
        if isinstance(value, (str, bytes)):
            # `'"spacex"'` is legal JSON and decodes to a string, which
            # `board_slugs` would then refuse -- correctly, and silently. Said
            # here so the refusal has a reason attached to it.
            return []
    return base.board_slugs(value)


def merge(root, results, now=None, publish=False, lock_seconds=None):
    """Run the existing pipeline over staged results, under the pool lock.

    The connectors are replaced with ones that return what was staged and make
    no request. Everything after that -- the gate, the merge, all five closure
    brakes, the prune, the publish -- is the code that runs today.
    """
    import ingest
    from sources import base

    # A list rather than a return value: the caller needs the span even when
    # the merge raises, and an exception carrying no measurement is how "the
    # work was done and the run looked failed" happened here once already.
    lock_seconds = [] if lock_seconds is None else lock_seconds

    def connector_for(envelope):
        def connector(fetcher, **_):
            return envelope
        return connector

    original = dict(ingest.sources.ALL)
    ingest.sources.ALL.clear()
    ingest.sources.ALL.update({name: connector_for(env)
                               for name, env in results.items()})
    try:
        # **The only lock this process takes, and only now.** `runlock.held` is
        # reentrant, so `jobs_store.save_jobs` taking it again inside the merge
        # is fine and is why the merge does not deadlock on itself.
        with runlock.held(root, jobs_store.POOL_LOCK, note="cloud-merge"):
            # **Timed here rather than around the call, because the number the
            # whole phase exists to move is this one.** `mergeSeconds` measured
            # from outside includes the connector swap and would keep including
            # whatever gets added next to it; a reader comparing a sweep
            # against the 26.8-minute device baseline needs the span the pool
            # was actually unavailable for, from the process that made it
            # unavailable.
            held_at = time.time()
            try:
                return ingest.run(root, now=now, settings={},
                                  fetcher=base.Fetcher(root=root, opener=None),
                                  publish=publish)
            finally:
                lock_seconds.append(round(time.time() - held_at, 1))
    finally:
        ingest.sources.ALL.clear()
        ingest.sources.ALL.update(original)


def discard(conn, account, sweep_id):
    """Remove a merged sweep's staged rows. Staging, not storage."""
    with conn.cursor() as cur:
        cur.execute("delete from sweep_postings where account_id = %s "
                    "and sweep_id = %s", (account, sweep_id))
        cur.execute("delete from sweep_runs where account_id = %s "
                    "and sweep_id = %s", (account, sweep_id))
    conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Start the cloud fetch, wait, and merge what it staged.")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument(
        "--merge-only", action="store_true",
        help=("merge the newest finished staged sweep without starting a "
              "machine; for a fetch that already landed"))
    ap.add_argument("--wait", type=int, default=WAIT_SECONDS)
    args = ap.parse_args(argv)

    root = jobs_store.root_dir(None)
    settings = toolpath.settings()
    app, machine = configured(settings)
    account = (settings.get("JOBBY_ACCOUNT_ID")
               or os.environ.get("JOBBY_ACCOUNT_ID") or "").strip()
    report = {"at": jobs_store.now_stamp(), "app": app, "machine": machine}
    started = time.time()
    try:
        if not app and not args.merge_only:
            raise CloudSweepUnavailable(
                f"no cloud sweep configured: set {APP_VAR} and {MACHINE_VAR}")
        if not account:
            raise CloudSweepUnavailable(
                "no JOBBY_ACCOUNT_ID, and every staged row is account-scoped")
        import jobs_db
        # **No connection is held across the wait.** `wait_for_sweep` opens one
        # per poll and `staged` opens its own, because the wait is 29.5 minutes
        # on a measured warm sweep and Neon closes an idle connection long
        # before that. Held, the device lost a completed 27-channel fetch to
        # `consuming input failed: SSL connection has been closed unexpectedly`
        # -- the same fault `sweep_worker.stage` was fixed for, left open one
        # file away.
        if args.merge_only:
            # **A fetch that already landed needs no machine, and starting one
            # is not harmless.** `main` always started the machine, so an
            # attempt to merge staged work kicked off a *second* sweep and then
            # waited for it -- which is how a finished 27-channel sweep sat in
            # `sweep_postings` while the device waited out a deadline on a run
            # it had just caused. The staged sweep is the subject; the machine
            # is not involved.
            asked = datetime(1970, 1, 1, tzinfo=timezone.utc)
            report["mergeOnly"] = True
            sweep_id = wait_for_sweep(jobs_db.connect, account, asked,
                                      app=None, machine=None, seconds=0,
                                      sleep=lambda _s: None)
        else:
            asked = start_machine(app, machine)
            report["fetchStartedAt"] = asked.strftime("%Y-%m-%dT%H:%M:%SZ")
            sweep_id = wait_for_sweep(jobs_db.connect, account, asked,
                                      app=app, machine=machine,
                                      seconds=args.wait)
        report["sweepId"] = sweep_id
        report["fetchSeconds"] = round(time.time() - started, 1)
        conn = jobs_db.connect()
        try:
            results = staged(conn, account, sweep_id)
        finally:
            conn.close()
        report["channels"] = {k: v["status"] for k, v in results.items()}
        report["staged"] = sum(len(v.get("postings") or [])
                               for v in results.values())
    except CloudSweepUnavailable as exc:
        report["fellBack"] = str(exc)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 3
    except Exception as exc:                              # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"[:300]
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    merge_started = time.time()
    lock_seconds = []
    # `ingest.run` returns `(report, jobs)`. Treating it as a report raised
    # after the merge had already written the pool, which is the worst place
    # for a reporting bug to be: the work was done and the run looked failed.
    ingest_report, _jobs = merge(root, results, publish=args.publish,
                                 lock_seconds=lock_seconds)
    report["mergeSeconds"] = round(time.time() - merge_started, 1)
    # The span the pool was actually locked for, which is a subset of
    # `mergeSeconds` and is the figure Phase 3 is judged on.
    report["lockSeconds"] = lock_seconds[0] if lock_seconds else None
    report["found"] = ingest_report.get("found")
    report["kept"] = ingest_report.get("kept")
    report["closed"] = ingest_report.get("closed")
    report["problems"] = ingest_report.get("problems")

    import jobs_db
    conn = jobs_db.connect()
    try:
        discard(conn, account, report["sweepId"])
    finally:
        conn.close()
    report["totalSeconds"] = round(time.time() - started, 1)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
