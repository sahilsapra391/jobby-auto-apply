#!/usr/bin/env python3
"""Background watcher: turns a click on the board into a running agent.

Runs as a launchd agent so nothing has to be typed. Every tick it reads the
three queues in one call and acts on whatever is waiting: marks are applied
inline, a Scan or an Apply spawns a Claude session in the background.

Three things here are deliberate, each of them a fix for a way this failed.

Nothing blocks. An application can take forty five minutes. The old design ran
it inline, so the whole board froze for the duration and no other click was
even noticed. Agents are spawned detached and tracked by a lock file.

Up to three applications at once, one agent per application from start to
finish. Slots are lock files holding a pid, so they survive a watcher restart
and a dead runner frees its own slot. The claim happens in this tick and never
in a runner: the queue is one KV key updated by read-modify-write with no
compare-and-swap, so two runners claiming together both take the head and the
same job is applied to twice.

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
import browser  # noqa: E402
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
# How often to tell healthchecks we are alive when nothing is happening. Well
# inside any sane period setting, so a single dropped ping never reads as death.
HEARTBEAT_SECONDS = 30
# An apply that has not reported a phase in this long is not coming back.
STUCK_AFTER_SECONDS = 75 * 60


def single_watcher():
    """Refuse to tick while another watcher is ticking.

    Running `--once` by hand next to the daemon is the obvious thing to do while
    debugging, and it is how two runners once landed in one slot: both processes
    read the queue and picked slots before either had written a lock.
    """
    holder = lock_holder("watcher")
    if holder and holder.get("pid") != os.getpid():
        return False, holder
    take_lock("watcher", os.getpid(), note="ticking")
    return True, None


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
    """Settings that are secrets or machine-specific, kept out of the repo.

    Reads deploy/.env.local and ~/.job-application/config.env, the latter
    winning, because `vercel env pull` regenerates the former and throws away
    anything hand-added.
    """
    return toolpath.settings()


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


def reserve(name, note=""):
    """Claim a slot atomically. False if somebody already holds it.

    O_EXCL rather than exists-then-write. Checking and then writing is two
    steps, and two watchers running at once, a daemon plus a hand-run --once,
    both saw the same slot free and both spawned into it. Two agents in one
    slot means two agents on one browser, which is one of them typing into the
    other's form.
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LOCK_DIR, 0o700)
    lock_holder(name)  # clears it first if the previous holder is dead
    try:
        fd = os.open(lock_path(name), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as fh:
        # Our own pid for now: it is alive, so the slot cannot be mistaken for
        # stale in the moment between reserving it and the runner starting.
        json.dump({"pid": os.getpid(), "at": now_stamp(), "note": note}, fh)
    return True


def spawn(name, argv, logname, note=""):
    """Start a runner detached in a reserved slot. Never waits on it."""
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

# Three applications at once, each owned start to finish by one agent. More than
# this and they contend for the single browser; fewer wastes the wait on forms.
# Derived from browser.SLOTS rather than declared again: a lock with no browser
# behind it spawns a runner that cannot attach to anything.
APPLY_LOCKS = list(browser.SLOTS)
APPLY_SLOTS = len(APPLY_LOCKS)


def free_apply_slots():
    """Slot names with no live runner, in order."""
    return [name for name in APPLY_LOCKS if lock_holder(name) is None]


def running_applications():
    """applicationId -> slot, for every apply currently in flight."""
    out = {}
    for name in APPLY_LOCKS:
        held = lock_holder(name)
        if held and held.get("note"):
            out[held["note"]] = name
    return out


def queue_depth(q, name):
    return len(q.get(name) or [])


def claim_one(prefix):
    """Take exactly one request off a queue. Only ever called from the tick."""
    r = subprocess.run([toolpath.node(), "queue.mjs", "claim-one", prefix], cwd=DEPLOY,
                       capture_output=True, text=True, env=toolpath.queue_env(), timeout=60)
    if r.returncode != 0:
        raise TickError(f"claim failed for {prefix}: {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "[]")


def poll_queues():
    """Every queue in one call. Raises TickError rather than looking empty."""
    try:
        env = toolpath.queue_env()
    except SystemExit as exc:
        raise TickError(str(exc))
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
    """Signature of what is worth spending a deployment on.

    Deliberately excludes `phase`. Publishing the board means redeploying the
    whole site, Vercel's free plan allows 100 of those a day, and a single
    application reports six phases on its way through. Including phase here
    turned thirty applications into a hundred deployments and the quota was
    gone by the afternoon. Which lane a role is in is worth a deployment; how
    far along it is inside In flight is not.
    """
    s = store(["session-list"])
    h = store(["history-list"])
    if s.returncode != 0 or h.returncode != 0:
        raise TickError("store is unreadable")
    sessions = json.loads(s.stdout or "[]")
    history = json.loads(h.stdout or "[]")
    parts = sorted(f"{x.get('applicationId')}:{x.get('status')}" for x in sessions)
    return f"{len(history)}|{blocked_message()}|" + "|".join(parts)


COOLDOWN = STATE_DIR / "publish-cooldown"
# Vercel's free plan allows 100 deployments a day. A board that redeploys the
# whole site on every change burns that in an afternoon, and then nothing
# publishes at all. When the quota is gone, stop trying until it resets rather
# than spending every tick on a request that cannot succeed.
QUOTA_COOLDOWN_SECONDS = 60 * 60


def publishing_paused():
    if not COOLDOWN.exists():
        return None
    left = QUOTA_COOLDOWN_SECONDS - (time.time() - COOLDOWN.stat().st_mtime)
    if left <= 0:
        COOLDOWN.unlink(missing_ok=True)
        return None
    return int(left)


LAST_DEPLOY = STATE_DIR / "last-deploy"
# A floor between deployments, on top of the fingerprint check. A burst of
# activity, three applications finishing within a minute of each other, would
# otherwise spend three of a hundred daily deployments to show the same board
# three times.
MIN_DEPLOY_GAP_SECONDS = 120


def publish():
    left = publishing_paused()
    if left is not None:
        return {"published": False, "pausedForSeconds": left,
                "why": "the deployment quota is spent; the store is still up to date"}
    if LAST_DEPLOY.exists():
        since = time.time() - LAST_DEPLOY.stat().st_mtime
        if since < MIN_DEPLOY_GAP_SECONDS:
            return {"published": False, "deferredForSeconds": int(MIN_DEPLOY_GAP_SECONDS - since),
                    "why": "too soon after the last deployment; it will go out on a later tick"}
    r = run([sys.executable, str(REPO / "scripts" / "sync_board.py")])
    try:
        out = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        raise TickError(f"sync_board produced no result: {(r.stderr or '')[:300]}")
    blob = ((r.stderr or "") + json.dumps(out)).lower()
    if "api-deployments-free-per-day" in blob or "resource is limited" in blob:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        COOLDOWN.write_text(now_stamp())
        return {"published": False, "quotaSpent": True,
                "why": "Vercel allows 100 deployments a day and today's are gone"}
    if r.returncode != 0 or out.get("published") is False:
        raise TickError(f"publish failed: {(r.stderr or out.get('error') or '')[:300]}")
    COOLDOWN.unlink(missing_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LAST_DEPLOY.write_text(now_stamp())
    return out


# --- watchdog ---------------------------------------------------------------

def sweep_stuck(queued=()):
    """Rescue applications abandoned by a runner that died.

    Without this a card sits at 'Queued' forever, which is exactly what the user
    saw: the runner was never able to start, and nothing ever said so.
    """
    owned = running_applications()
    s = store(["session-list"])
    if s.returncode != 0:
        raise TickError("store is unreadable")
    rescued = []
    for x in json.loads(s.stdout or "[]"):
        if x.get("status") != "inflight":
            continue
        # A run that is still going is not stuck, whichever slot holds it.
        if x["applicationId"] in owned:
            continue
        # Nor is one still waiting for a free slot. Everything in the queue is
        # marked inflight so the board reads correctly, but its phase does not
        # move until a runner picks it up, and with a backlog deeper than three
        # that wait can easily outlast the stuck timeout.
        if x["applicationId"] in queued:
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
                 "--detail", "no runner reported in over an hour; press Retry"])
            rescued.append(x["applicationId"])
    return rescued


# --- the tick ---------------------------------------------------------------

def tick(force_publish=False):
    """One pass. Raises TickError if it could not do its job."""
    changed = {}
    q = poll_queues()

    # Drops first, and before anything is spawned. A drop can be the user
    # stopping a run that is happening right now, so acting on it late means the
    # agent keeps filling a form the user has already said no to.
    if q.get("drop"):
        claimed = json.loads(subprocess.run(
            [toolpath.node(), "queue.mjs", "claim", "drop/"], cwd=DEPLOY,
            capture_output=True, text=True, env=toolpath.queue_env(), timeout=60
        ).stdout or "[]")
        dropped = []
        for item in claimed:
            aid = item.get("applicationId")
            if not aid:
                continue
            r = run([sys.executable, str(REPO / "scripts" / "drop.py"),
                     "--id", aid, "--no-publish"])
            if r.returncode == 0:
                dropped.append(aid)
        if dropped:
            changed["dropped"] = dropped

    # "Open & finish" on a parked card. The point is to land the user on the tab
    # the run left behind, with the form still filled, rather than a fresh copy
    # of the application in a browser that has none of the work in it.
    if q.get("focus"):
        claimed = json.loads(subprocess.run(
            [toolpath.node(), "queue.mjs", "claim", "focus/"], cwd=DEPLOY,
            capture_output=True, text=True, env=toolpath.queue_env(), timeout=60
        ).stdout or "[]")
        for item in claimed[-1:]:      # only the most recent click is meaningful
            aid = item.get("applicationId")
            if not aid:
                continue
            loaded = store(["session-load", "--id", aid])
            url = ""
            if loaded.returncode == 0:
                url = (json.loads(loaded.stdout or "{}") or {}).get("url", "")
            if not url:
                roles = json.loads(store(["roles-list"]).stdout or "{}")
                url = (roles.get(aid) or {}).get("url", "")
            if not url:
                changed["focus"] = {"applicationId": aid, "error": "no posting url on record"}
                continue
            browser.serve()
            changed["focus"] = {"applicationId": aid, **browser.focus(url)}

    if q.get("restore"):
        claimed = json.loads(subprocess.run(
            [toolpath.node(), "queue.mjs", "claim", "restore/"], cwd=DEPLOY,
            capture_output=True, text=True, env=toolpath.queue_env(), timeout=60
        ).stdout or "[]")
        back = []
        for item in claimed:
            aid = item.get("applicationId")
            if aid and run([sys.executable, str(REPO / "scripts" / "drop.py"),
                            "--id", aid, "--restore", "--no-publish"]).returncode == 0:
                back.append(aid)
        if back:
            changed["restored"] = back

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

    # Anything sitting in the apply queue is in flight as far as the board is
    # concerned, whether or not a runner has reached it yet. Applications run
    # strictly one at a time, so a queued retry can wait behind a forty-five
    # minute run; leaving the store saying "review" for that whole time meant a
    # reload put the card back under Needs you and it read as the click having
    # failed. The lane is decided here, in the store, so it survives a reload
    # and looks the same on another device.
    for item in q.get("apply", []):
        aid = item.get("applicationId")
        if not aid:
            continue
        loaded = store(["session-load", "--id", aid])
        if loaded.returncode != 0:
            continue
        if (json.loads(loaded.stdout or "{}") or {}).get("status") == "inflight":
            continue
        run([sys.executable, str(REPO / "scripts" / "phase.py"), "--id", aid,
             "--phase", "Queued", "--detail", "waiting for a runner"])
        changed.setdefault("queued", []).append(aid)

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
    if agent_ok and q.get("scan") and not lock_holder("scan"):
        pid = spawn("scan", [sys.executable, str(REPO / "scripts" / "run_scan.py")],
                    "scan-runs.log", note="scan")
        changed["scanStarted"] = pid

    # Up to APPLY_SLOTS applications at once, one agent per application, start to
    # finish. The claim happens HERE rather than inside the runner, and that is
    # the whole point: the queue is a single KV key updated by read-modify-write
    # with no compare-and-swap, so two runners claiming at the same instant both
    # take the same head of the queue and the same job gets applied to twice.
    # Verified, not assumed. This tick is single-threaded, so claiming in it is
    # sequential however many runners are already in flight.
    if agent_ok and not lock_holder("scan"):
        for slot in free_apply_slots():
            if not queue_depth(q, "apply"):
                break
            # Reserve BEFORE claiming. Claiming first and then losing the slot
            # would take a request off the queue with nothing to run it.
            if not reserve(slot, note="starting"):
                continue
            claimed = claim_one("apply/")
            if not claimed:
                lock_path(slot).unlink(missing_ok=True)
                break
            aid = claimed[0].get("applicationId")
            if not aid:
                lock_path(slot).unlink(missing_ok=True)
                continue
            # Already being worked on. The board re-sends Apply clicks it
            # remembers every time the page loads, and enqueue only dedupes
            # against what is still IN the queue, so a reload after this one was
            # claimed puts it straight back. Without this check a second agent
            # takes the same application and it gets submitted twice.
            busy = running_applications()
            if aid in busy:
                lock_path(slot).unlink(missing_ok=True)
                changed.setdefault("alreadyRunning", []).append(
                    {"applicationId": aid, "slot": busy[aid]})
                continue
            q["apply"] = [x for x in q.get("apply", []) if x.get("applicationId") != aid]
            browser.serve(slot)
            pid = spawn(slot, [sys.executable, str(REPO / "scripts" / "run_apply.py"),
                               "--id", aid, "--slot", slot],
                        f"{slot}.log", note=aid)
            changed.setdefault("applyStarted", []).append(
                {"slot": slot, "pid": pid, "applicationId": aid})

    rescued = sweep_stuck({x.get("applicationId") for x in (q.get("apply") or [])})
    if rescued:
        changed["rescued"] = rescued

    busy = sorted(running_applications())
    if lock_holder("scan"):
        busy.append("scan")
    if busy:
        changed.setdefault("running", busy)

    fp = fingerprint()
    statefile = STATE_DIR / "board-fingerprint"
    last = statefile.read_text() if statefile.exists() else ""
    age = time.time() - statefile.stat().st_mtime if statefile.exists() else 1e9
    if force_publish or fp != last or age > PUBLISH_FLOOR_SECONDS:
        result = publish()
        changed["board"] = result
        # Only remember the fingerprint once it has actually gone out. Recording
        # it after a deferred or quota-blocked publish would mark the change as
        # published and it would never be sent.
        if result.get("published"):
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            statefile.write_text(fp)
    return changed


def heartbeat_due(line, last_line, since_last_ping):
    """Should this tick ping healthchecks?

    Ping when something actually changed, and otherwise on a steady heartbeat so
    liveness is reported without flooding. Kept separate from the loop because
    the first version compared `line` against `last_line` after already
    assigning it, which made the change test permanently false: only the timer
    ever fired, and a real event never pinged at all.
    """
    return (bool(line) and line != last_line) or since_last_ping > HEARTBEAT_SECONDS


def status():
    out = {"queues": None, "locks": {}, "health": bool(config().get("HEALTHCHECKS_URL"))}
    try:
        out["queues"] = poll_queues()
    except (TickError, subprocess.TimeoutExpired) as exc:
        out["queues"] = {"error": str(exc)}
    for name in APPLY_LOCKS + ["scan"]:
        out["locks"][name] = lock_holder(name)
    out["slots"] = {"total": APPLY_SLOTS, "free": len(free_apply_slots()),
                    "running": running_applications()}
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
        ok, holder = single_watcher()
        if not ok:
            print(json.dumps({
                "refused": "another watcher is running",
                "holder": holder,
                "why": "two watchers pick slots from the same free list and both "
                       "spawn into it, which puts two agents on one browser",
                "how": "launchctl unload ~/Library/LaunchAgents/ai.jobby.watch.plist",
            }, indent=2))
            return 1
        try:
            changed = tick(force_publish=True)
        except (TickError, subprocess.TimeoutExpired) as exc:
            ping_health("fail", str(exc))
            print(json.dumps({"unhealthy": str(exc)}, indent=2))
            return 1
        ping_health("", json.dumps(changed))
        print(json.dumps(changed or {"quiet": True}, indent=2, sort_keys=True))
        return 0

    ok, holder = single_watcher()
    if not ok:
        print(json.dumps({"refused": "another watcher is already running",
                          "holder": holder}, indent=2))
        return 1
    ping_health("start", f"watcher up, interval {args.interval}s")
    consecutive_failures = 0
    last_line = ""
    last_ping = 0.0
    while True:
        try:
            take_lock("watcher", os.getpid(), note="ticking")
            changed = tick()
            if consecutive_failures:
                print(f"{now_stamp()} recovered after {consecutive_failures} failed ticks",
                      flush=True)
            consecutive_failures = 0
            line = json.dumps(changed, sort_keys=True) if changed else ""
            # A standing condition (waiting on auth, an agent still running)
            # repeats every tick. Log it when it changes, not thirty times a
            # minute, or the log stops being readable exactly when it matters.
            prev_line = last_line
            if line and line != prev_line:
                print(f"{now_stamp()} {line}", flush=True)
            last_line = line
            if heartbeat_due(line, prev_line, time.time() - last_ping):
                ping_health("", (line or "idle")[:2000])
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
