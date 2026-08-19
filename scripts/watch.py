#!/usr/bin/env python3
"""Background watcher: turns a click on the board into a running agent.

Runs as a launchd agent so nothing has to be typed. Every tick it reads the
three queues in one call and acts on whatever is waiting: marks are applied
inline, a Scan or an Apply spawns a Claude session in the background.

Three things here are deliberate, each of them a fix for a way this failed.

Nothing blocks. An application can take forty five minutes. The old design ran
it inline, so the whole board froze for the duration and no other click was
even noticed. Agents are spawned detached and tracked by a lock file.

One application at a time, still. The lock is what enforces the user's standing
rule, and it survives a watcher restart because it holds a pid rather than
living in memory.

A tick that could not do its job says so. The old watcher logged a healthy
looking cycle every thirty seconds for hours while every queue read was dying
on FileNotFoundError, because a crashed check and an empty queue both look like
a non-zero exit code. Health is now reported from whether the work actually
happened, and pushed to healthchecks.io so silence itself is an alert.

  python3 scripts/watch.py --once        # one tick, for testing
  python3 scripts/watch.py --status      # what is running, no side effects
  python3 scripts/watch.py --interval 2  # loop (what launchd runs)
  python3 scripts/watch.py --install     # install the launchd agent
  python3 scripts/watch.py --uninstall
"""

import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent  # noqa: E402
import toolpath  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
STORE = REPO / "scripts" / "store.py"
LABEL = "ai.jobby.watch"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
STATE_DIR = Path.home() / ".job-application"
LOG_DIR = STATE_DIR / "logs"
LOCK_DIR = STATE_DIR / "locks"

# A click should feel immediate. Ticks are one blob list plus a little local IO,
# so a couple of seconds costs almost nothing and is the difference between the
# board feeling alive and feeling broken.
DEFAULT_INTERVAL = 2
# Publishing shells out to vercel, so it is debounced behind an actual change,
# with a floor so a long quiet stretch still refreshes eventually.
PUBLISH_FLOOR_SECONDS = 900
# An apply that has not reported a phase in this long is not coming back.
STUCK_AFTER_SECONDS = 75 * 60


class TickError(Exception):
    """The tick could not do its job. Never confused with 'nothing to do'."""


def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True,
                          env=toolpath.enriched_env(), **kw)


def store(args):
    return run([sys.executable, str(STORE)] + args)


def blocked_message():
    """The one-line reason the pipeline cannot run, or empty. meta-get returns
    JSON, so reading its stdout raw put a JSON blob on the board once."""
    r = store(["meta-get", "--key", "blocked"])
    if r.returncode != 0:
        return ""
    try:
        return (json.loads(r.stdout or "{}").get("blocked") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


def config():
    """Settings that are secrets or machine-specific, kept out of the repo."""
    out = {}
    envfile = DEPLOY / ".env.local"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# --- health -----------------------------------------------------------------

def ping_health(state="", detail=""):
    """Tell healthchecks.io the watcher is alive and working.

    The point is the silence: if this process dies, or every tick starts
    failing, healthchecks notices that the pings stopped and alerts. That is the
    exact failure that went unnoticed for hours.
    """
    url = config().get("HEALTHCHECKS_URL", "").rstrip("/")
    if not url:
        return None
    target = f"{url}/{state}" if state else url
    try:
        req = urllib.request.Request(target, data=(detail or "").encode()[:9000],
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except (urllib.error.URLError, OSError, ValueError):
        # Monitoring must never take the watcher down with it.
        return None


# --- locks ------------------------------------------------------------------

def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_path(name):
    return LOCK_DIR / f"{name}.lock"


def lock_holder(name):
    """The live pid holding this lock, or None. Clears the lock if it is stale."""
    p = lock_path(name)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        pid = int(data["pid"])
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        p.unlink(missing_ok=True)
        return None
    if pid_alive(pid):
        return data
    # The holder died without cleaning up: a crash, a reboot, a kill.
    p.unlink(missing_ok=True)
    return None


def take_lock(name, pid, note=""):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LOCK_DIR, 0o700)
    lock_path(name).write_text(json.dumps(
        {"pid": pid, "at": now_stamp(), "note": note}))


def spawn(name, argv, logname, note=""):
    """Start a runner detached and record the lock. Never waits on it."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LOG_DIR, 0o700)
    fh = open(LOG_DIR / logname, "a")
    fh.write(f"\n=== spawn {name} {now_stamp()} ===\n")
    fh.flush()
    proc = subprocess.Popen(argv, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT,
                            start_new_session=True, env=toolpath.enriched_env())
    take_lock(name, proc.pid, note)
    return proc.pid


# --- queues -----------------------------------------------------------------

def poll_queues():
    """Every queue in one call. Raises TickError rather than looking empty."""
    env = toolpath.enriched_env()
    env.update({k: v for k, v in config().items() if k == "BLOB_READ_WRITE_TOKEN"})
    if not env.get("BLOB_READ_WRITE_TOKEN"):
        raise TickError("no BLOB_READ_WRITE_TOKEN in deploy/.env.local")
    r = subprocess.run([toolpath.node(), "queue.mjs", "poll"], cwd=DEPLOY,
                       capture_output=True, text=True, env=env, timeout=60)
    if r.returncode != 0:
        raise TickError(f"queue poll failed: {(r.stderr or '').strip()[:300]}")
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        raise TickError(f"queue poll returned junk: {(r.stdout or '')[:200]}")


# --- board ------------------------------------------------------------------

def fingerprint():
    """Cheap signature of everything the board renders."""
    s = store(["session-list"])
    h = store(["history-list"])
    if s.returncode != 0 or h.returncode != 0:
        raise TickError("store is unreadable")
    sessions = json.loads(s.stdout or "[]")
    history = json.loads(h.stdout or "[]")
    parts = sorted(f"{x.get('applicationId')}:{x.get('status')}:{x.get('phase')}"
                   for x in sessions)
    return f"{len(history)}|{blocked_message()}|" + "|".join(parts)


def publish():
    r = run([sys.executable, str(REPO / "scripts" / "sync_board.py")])
    try:
        out = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        raise TickError(f"sync_board produced no result: {(r.stderr or '')[:300]}")
    if r.returncode != 0 or out.get("published") is False:
        raise TickError(f"publish failed: {(r.stderr or out.get('error') or '')[:300]}")
    return out


# --- watchdog ---------------------------------------------------------------

def sweep_stuck():
    """Rescue applications abandoned by a runner that died.

    Without this a card sits at 'Queued' forever, which is exactly what the user
    saw: the runner was never able to start, and nothing ever said so.
    """
    if lock_holder("apply"):
        return []
    s = store(["session-list"])
    if s.returncode != 0:
        raise TickError("store is unreadable")
    rescued = []
    for x in json.loads(s.stdout or "[]"):
        if x.get("status") != "inflight":
            continue
        at = x.get("phaseAt") or x.get("updatedAt") or ""
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                   ).total_seconds()
        except ValueError:
            age = STUCK_AFTER_SECONDS + 1
        if age > STUCK_AFTER_SECONDS:
            run([sys.executable, str(REPO / "scripts" / "phase.py"), "--id", x["applicationId"],
                 "--phase", "Stalled",
                 "--detail", "no runner reported in over an hour; press Apply to retry"])
            rescued.append(x["applicationId"])
    return rescued


# --- the tick ---------------------------------------------------------------

def tick(force_publish=False):
    """One pass. Raises TickError if it could not do its job."""
    changed = {}
    q = poll_queues()

    if q.get("marked"):
        r = run([sys.executable, str(REPO / "scripts" / "apply_marks.py"), "--no-publish"])
        if r.returncode != 0:
            raise TickError(f"apply_marks failed: {(r.stderr or '').strip()[:300]}")
        try:
            applied = json.loads(r.stdout or "{}").get("applied", [])
        except json.JSONDecodeError:
            raise TickError("apply_marks produced no result")
        if applied:
            changed["marks"] = [a.get("company") for a in applied]

    # Nothing that needs a spawned agent is worth starting while the CLI cannot
    # authenticate. The runners preflight too, so the queue is safe either way,
    # but without this the watcher spawns a doomed process every couple of
    # seconds. The check is cached, so this costs nothing per tick.
    agent_ok = True
    if q.get("scan") or q.get("apply"):
        agent_ok, why = agent.authenticated()
        if not agent_ok:
            store(["meta-set", "--key", "blocked", "--value", agent.blocked_reason(why)])
            changed.setdefault("waitingOnAuth", agent.blocked_reason(why))
    elif blocked_message():
        # The queues are empty and the reason may be stale; re-check cheaply so
        # the banner clears itself once the user has logged back in.
        ok, _ = agent.authenticated()
        if ok:
            store(["meta-set", "--key", "blocked", "--value", ""])
            changed["unblocked"] = True

    # One scan at a time, and never while an application is mid-flight: a sweep
    # rewriting the watch list under a running apply is asking for trouble.
    if agent_ok and q.get("scan") and not lock_holder("scan") and not lock_holder("apply"):
        pid = spawn("scan", [sys.executable, str(REPO / "scripts" / "run_scan.py")],
                    "scan-runs.log", note="scan")
        changed["scanStarted"] = pid

    # Strictly one application at a time. This is the user's standing rule and
    # the lock is the only thing enforcing it.
    if agent_ok and q.get("apply") and not lock_holder("apply") and not lock_holder("scan"):
        aid = q["apply"][0].get("applicationId")
        pid = spawn("apply", [sys.executable, str(REPO / "scripts" / "run_apply.py")],
                    "apply-runs.log", note=aid or "")
        changed["applyStarted"] = {"pid": pid, "applicationId": aid}

    rescued = sweep_stuck()
    if rescued:
        changed["rescued"] = rescued

    holder = lock_holder("apply") or lock_holder("scan")
    if holder:
        changed.setdefault("running", holder.get("note") or "agent")

    fp = fingerprint()
    statefile = STATE_DIR / "board-fingerprint"
    last = statefile.read_text() if statefile.exists() else ""
    age = time.time() - statefile.stat().st_mtime if statefile.exists() else 1e9
    if force_publish or fp != last or age > PUBLISH_FLOOR_SECONDS:
        changed["board"] = publish()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        statefile.write_text(fp)
    return changed


def status():
    out = {"queues": None, "locks": {}, "health": bool(config().get("HEALTHCHECKS_URL"))}
    try:
        out["queues"] = poll_queues()
    except (TickError, subprocess.TimeoutExpired) as exc:
        out["queues"] = {"error": str(exc)}
    for name in ("apply", "scan"):
        out["locks"][name] = lock_holder(name)
    out["blocked"] = blocked_message() or None
    return out


# --- launchd ----------------------------------------------------------------

def install(interval=DEFAULT_INTERVAL):
    for d in (LOG_DIR, LOCK_DIR):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(REPO / "scripts" / "watch.py"),
                             "--interval", str(interval)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(REPO),
        "StandardOutPath": str(LOG_DIR / "watch.log"),
        "StandardErrorPath": str(LOG_DIR / "watch.err"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        # launchd hands over PATH=/usr/bin:/bin:/usr/sbin:/sbin and nothing else,
        # so node, vercel and claude are all invisible. The scripts resolve them
        # absolutely via toolpath, and this is the second line of defence for any
        # child process that looks them up itself.
        "EnvironmentVariables": {
            "PATH": toolpath.search_path(),
            "HOME": str(Path.home()),
        },
    }
    PLIST.write_bytes(plistlib.dumps(plist))
    run(["launchctl", "unload", str(PLIST)])
    loaded = run(["launchctl", "load", str(PLIST)])
    ok = loaded.returncode == 0
    print(json.dumps({
        "installed": ok, "label": LABEL, "plist": str(PLIST), "intervalSeconds": interval,
        "log": str(LOG_DIR / "watch.log"),
        "error": (loaded.stderr or "").strip()[:200] or None,
    }, indent=2))
    return 0 if ok else 1


def uninstall():
    run(["launchctl", "unload", str(PLIST)])
    if PLIST.exists():
        PLIST.unlink()
    print(json.dumps({"uninstalled": True}, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Turn board clicks into running agents.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--interval", type=int, default=0)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    if args.install:
        return install(args.interval or DEFAULT_INTERVAL)
    if args.uninstall:
        return uninstall()
    if args.status:
        print(json.dumps(status(), indent=2, sort_keys=True))
        return 0

    if args.once or not args.interval:
        try:
            changed = tick(force_publish=True)
        except (TickError, subprocess.TimeoutExpired) as exc:
            ping_health("fail", str(exc))
            print(json.dumps({"unhealthy": str(exc)}, indent=2))
            return 1
        ping_health("", json.dumps(changed))
        print(json.dumps(changed or {"quiet": True}, indent=2, sort_keys=True))
        return 0

    ping_health("start", f"watcher up, interval {args.interval}s")
    consecutive_failures = 0
    last_line = ""
    last_ping = 0.0
    while True:
        try:
            changed = tick()
            if consecutive_failures:
                print(f"{now_stamp()} recovered after {consecutive_failures} failed ticks",
                      flush=True)
            consecutive_failures = 0
            if changed:
                line = json.dumps(changed, sort_keys=True)
                # A standing condition (waiting on auth, an agent still running)
                # repeats every tick. Log it when it changes, not thirty times a
                # minute, or the log stops being readable exactly when it matters.
                if line != last_line:
                    print(f"{now_stamp()} {line}", flush=True)
                    last_line = line
                # Ping on change, and otherwise at a steady heartbeat, so
                # healthchecks sees liveness without being flooded.
                if line != last_line or time.time() - last_ping > 60:
                    ping_health("", line[:2000])
                    last_ping = time.time()
            elif time.time() - last_ping > 60:
                last_line = ""
                ping_health("", "idle")
                last_ping = time.time()
        except (TickError, subprocess.TimeoutExpired) as exc:
            consecutive_failures += 1
            # Loud on the way in, then quiet, so a long outage does not fill the
            # disk but is never invisible either.
            if consecutive_failures <= 3 or consecutive_failures % 60 == 0:
                print(f"{now_stamp()} UNHEALTHY ({consecutive_failures}): {exc}",
                      file=sys.stderr, flush=True)
            ping_health("fail", f"tick {consecutive_failures}: {exc}")
        except Exception as exc:  # a watcher that dies on one bad tick is useless
            consecutive_failures += 1
            print(f"{now_stamp()} tick error: {exc!r}", file=sys.stderr, flush=True)
            ping_health("fail", repr(exc))
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
