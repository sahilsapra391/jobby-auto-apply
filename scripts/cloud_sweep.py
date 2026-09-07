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
#: The measured sweep was 25.8 seconds for all 27 channels against a four-tenant
#: registry, and the local equivalent against the full 895-board registry runs a
#: median 26.8 minutes. Twelve minutes is comfortably above the first and well
#: below the second, and a cloud fetch that is slower than the laptop has lost
#: its only argument.
WAIT_SECONDS = 12 * 60
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
    fly = toolpath.find("flyctl") or "flyctl"
    result = run([fly, "machine", "start", machine, "--app", app],
                 capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise CloudSweepUnavailable(
            f"flyctl could not start {machine}: "
            f"{(result.stderr or result.stdout or '').strip()[:300]}")
    return asked


def wait_for_sweep(conn, account, since, seconds=WAIT_SECONDS,
                   poll=POLL_SECONDS, sleep=time.sleep):
    """The newest sweep id whose channels have all finished, or raise.

    A run is finished when every row for its sweep carries a `finished_at`.
    `complete` is not the test here -- a sweep where two channels failed is
    still a sweep, and the closure step reads `complete` per channel further
    down. What this waits for is the container being done.
    """
    deadline = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    while True:
        with conn.cursor() as cur:
            cur.execute(
                "select sweep_id, count(*), count(finished_at) "
                "from sweep_runs where account_id = %s and started_at >= %s "
                "group by sweep_id order by min(started_at) desc limit 1",
                (account, since))
            row = cur.fetchone()
        conn.rollback()
        if row and row[1] and row[1] == row[2]:
            return str(row[0])
        if datetime.now(timezone.utc) >= deadline:
            seen = "no sweep started" if not row else (
                f"{row[2]} of {row[1]} channels finished")
            raise CloudSweepUnavailable(
                f"the cloud sweep did not finish within {seconds}s ({seen})")
        sleep(poll)


def staged(conn, account, sweep_id):
    """`{channel: envelope}` for one sweep, in the shape a connector returns.

    Rebuilt rather than stored so that what the merge sees is exactly what
    `base.ok` produces: one definition of a channel result, not two.
    """
    from sources import base
    with conn.cursor() as cur:
        cur.execute(
            "select source, status, job_count, unchanged, listing_complete, "
            "reason from sweep_runs where account_id = %s and sweep_id = %s",
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
    for source, status, count, unchanged, complete, reason in runs:
        mine = postings.get(source, [])
        if status == "failed":
            out[source] = base.failed(source, reason or "the cloud sweep failed")
        elif status == "skipped":
            out[source] = base.skipped(source, reason or "skipped in the cloud")
        else:
            out[source] = base.ok(source, mine, requests=0, note=reason or "",
                                  complete=bool(complete),
                                  unchanged=int(unchanged or 0))
        if len(mine) != int(count or 0) and status == "complete":
            # Said rather than smoothed over: the run row and the rows it
            # counts disagreeing means a partial write, and the closure step is
            # about to read this as a whole listing.
            out[source] = base.failed(
                source, f"staged {len(mine)} postings against a job_count of "
                        f"{count}; refusing to treat it as complete")
    return out


def merge(root, results, now=None, publish=False):
    """Run the existing pipeline over staged results, under the pool lock.

    The connectors are replaced with ones that return what was staged and make
    no request. Everything after that -- the gate, the merge, all five closure
    brakes, the prune, the publish -- is the code that runs today.
    """
    import ingest
    from sources import base

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
            return ingest.run(root, now=now, settings={},
                              fetcher=base.Fetcher(root=root, opener=None),
                              publish=publish)
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
        if not app:
            raise CloudSweepUnavailable(
                f"no cloud sweep configured: set {APP_VAR} and {MACHINE_VAR}")
        if not account:
            raise CloudSweepUnavailable(
                "no JOBBY_ACCOUNT_ID, and every staged row is account-scoped")
        import jobs_db
        conn = jobs_db.connect()
        try:
            asked = start_machine(app, machine)
            report["fetchStartedAt"] = asked.strftime("%Y-%m-%dT%H:%M:%SZ")
            sweep_id = wait_for_sweep(conn, account, asked, seconds=args.wait)
            report["sweepId"] = sweep_id
            report["fetchSeconds"] = round(time.time() - started, 1)
            results = staged(conn, account, sweep_id)
            report["channels"] = {k: v["status"] for k, v in results.items()}
            report["staged"] = sum(len(v.get("postings") or [])
                                   for v in results.values())
        finally:
            conn.close()
    except CloudSweepUnavailable as exc:
        report["fellBack"] = str(exc)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 3
    except Exception as exc:                              # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"[:300]
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    merge_started = time.time()
    ingest_report = merge(root, results, publish=args.publish)
    report["mergeSeconds"] = round(time.time() - merge_started, 1)
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
