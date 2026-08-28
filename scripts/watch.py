#!/usr/bin/env python3
"""Background watcher: turns a click on the board into a running agent.

Runs as a launchd agent so nothing has to be typed. Every tick it reads the
three queues in one call and acts on whatever is waiting: marks are applied
inline, a Scan or an Apply spawns the selected provider agent in the background.

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
  python3 scripts/watch.py --interval 5  # loop (what launchd runs)
  python3 scripts/watch.py --install     # install the launchd agent
  python3 scripts/watch.py --uninstall
"""

import argparse
import calendar
import hashlib
import json
import os
import plistlib
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent  # noqa: E402
import browser  # noqa: E402
import document_job  # noqa: E402
import provider_login  # noqa: E402
import providers  # noqa: E402
import toolpath  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
STORE = REPO / "scripts" / "store.py"
LABEL = "ai.jobby.watch"
DOCUMENT_LABEL = "ai.jobby.documents"
PROVIDER_LABEL = "ai.jobby.providers"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DOCUMENT_PLIST = (
    Path.home() / "Library" / "LaunchAgents" / f"{DOCUMENT_LABEL}.plist"
)
PROVIDER_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{PROVIDER_LABEL}.plist"
STATE_DIR = Path.home() / ".job-application"
LOG_DIR = STATE_DIR / "logs"
LOCK_DIR = STATE_DIR / "locks"

# Five seconds keeps clicks responsive while leaving room in the Cloudflare KV
# read budget for document status and settings checks.
DEFAULT_INTERVAL = 5
PROVIDER_CONTROL_INTERVAL_SECONDS = 30
# How often a connection that is failing gets probed again. A provider that
# went unhealthy because its quota was spent stayed that way on the board until
# somebody pressed something, because the heartbeat only republished the cached
# row and nothing re-ran the check. A spent quota comes back on its own, so the
# check has to as well.
UNHEALTHY_RECHECK_SECONDS = 120
_provider_recheck_at: dict[str, float] = {}
# Set on the first idle poll rather than at import, so starting the watcher
# does not immediately probe a provider it knows nothing about yet, and so the
# thirty-second idle poll stays the inert read it is meant to be.
_last_unhealthy_recheck: float | None = None
_last_provider_control_poll = 0.0
# The public API treats a local status older than fifteen minutes as offline.
# Refresh it every ten minutes even when no settings request is waiting.
LOCAL_STATUS_HEARTBEAT_SECONDS = 10 * 60
_last_local_status_publish = 0.0
_local_status_dirty = False
PROVIDER_SERVICE_INTERVAL_SECONDS = 2
PROVIDER_SELECTION_RETRY_SECONDS = 30
_provider_selection_key = None
_provider_selection_status = None
_provider_selection_retry_at = 0.0
DOCUMENT_STATUS_INTERVAL_SECONDS = 30
DOCUMENT_STATUS_STALE_SECONDS = 15 * 60
_last_document_status_poll = 0.0
_document_status_cache = None
# Publishing shells out to vercel, so it is debounced behind an actual change,
# with a floor so a long quiet stretch still refreshes eventually.
PUBLISH_FLOOR_SECONDS = 900
# A board older than twice the floor is stale, and staleness is its own alarm.
#
# The existing alarm fires when a publish is attempted and fails, and that was
# not enough. On 2026-08-27 the board sat a day out of date and nothing rang.
# Two silent conditions stacked: the Vercel quota cooldown makes publish()
# return early without raising, so no attempt means no failure; and underneath
# it the state had grown past the size ceiling, so every attempt after the
# cooldown lifted refused. Both leave a real, complete, wrong board being
# served, and a board cannot announce its own staleness.
#
# So this watches the outcome rather than the attempt. LAST_DEPLOY is written
# only after a publish actually goes out, which makes its age the one honest
# measure of whether the thing is working, whatever the reason it is not.
PUBLISH_STALE_SECONDS = 2 * PUBLISH_FLOOR_SECONDS
# The configured check expects one ping per hour with ten minutes of grace.
# Fifty-five minutes leaves a small scheduling cushion without flooding it.
HEARTBEAT_SECONDS = 55 * 60
# An apply that has not reported a phase in this long is not coming back.
STUCK_AFTER_SECONDS = 75 * 60
# Jitter on the hourly sourcing scan, so a machine that has been up for a week
# is not hitting eighty company boards at exactly hh:00:00 every hour.
TIER1_JITTER_SECONDS = 5 * 60


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


# The tick is single-threaded and runs every few seconds, so no synchronous
# helper it shells out to has any business taking minutes. One that does is
# hung, and it takes the queue, Apply and document polling down with it. The
# queue.mjs calls in this file have carried timeout=60 for exactly this reason;
# `run` was the one that never got it, which is how publishing froze the whole
# watcher for nineteen minutes on 2026-08-25 while an Apply sat waiting.
SUBPROCESS_TIMEOUT_SECONDS = 300
# Publishing is the one that actually hung. sync_board reaches Cloudflare KV and
# Vercel, so it is slower than anything local and still nowhere near this.
PUBLISH_TIMEOUT_SECONDS = 120
# 124 is what `timeout(1)` returns, and it cannot collide with a real exit code
# from any helper here.
TIMED_OUT = 124


def _kill_group(proc):
    """Kill the helper and everything it started."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def run(args, timeout=SUBPROCESS_TIMEOUT_SECONDS, **kw):
    """Run a helper to completion, but never forever.

    `subprocess.run(timeout=...)` kills the direct child and leaves that child's
    own children running, which is the wrong shape here: what hung was the node
    process `sync_board.py` spawns, not `sync_board.py` itself. Killing only the
    parent leaves the hung connection open and lets the next tick stack a second
    one on top of it. So the helper gets its own process group and the whole
    group goes.

    A timeout comes back as a failed result rather than an exception. Every
    caller already handles a non-zero return code, none of them expects to catch
    anything from here, and a raise would turn one slow publish into a crashed
    tick.
    """
    text_input = kw.pop("input", None)
    kw.setdefault("env", toolpath.enriched_env())
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if text_input is not None else None,
        text=True, start_new_session=True, **kw)
    try:
        out, err = proc.communicate(text_input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        return subprocess.CompletedProcess(
            args, TIMED_OUT, out or "",
            f"gave no result within {timeout:g}s and was killed")
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


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

# The last thing the dead-man alarm actually did, so a broken alarm is visible
# from inside rather than only by its silence somewhere else.
LAST_PING = None


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
    global LAST_PING
    try:
        req = urllib.request.Request(target, data=(detail or "").encode()[:9000],
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            LAST_PING = {"at": now_stamp(), "state": state or "ok", "status": r.status}
            return r.status
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Monitoring must never take the watcher down with it. But it must not
        # fail invisibly either: every caller ignores the return, and on an
        # unreachable or misconfigured URL this returned None, which from
        # inside the watcher is indistinguishable from a healthy system with a
        # working alarm. The outcome is recorded so `status()` can say.
        LAST_PING = {"at": now_stamp(), "state": state or "ok",
                     "error": f"{type(exc).__name__}: {exc}"[:200]}
        return None


# --- locks ------------------------------------------------------------------

def pid_alive(pid):
    """Is this pid a live process?

    Non-positive pids are not processes and must be rejected before os.kill
    sees them. In POSIX, kill(0, sig) signals the caller's whole process group
    and kill(-n, sig) signals group n, so os.kill(0, 0) always succeeds and
    pid_alive(0) answered True. A slot lock written with pid 0 was therefore
    immortal: lock_holder never saw it as stale, never cleared it, and the slot
    was gone until someone deleted the file by hand. Two of five apply slots
    were held that way for over a day.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_path(name):
    return LOCK_DIR / f"{name}.lock"


def _durable_browser_owner(name, aid):
    """Recover affinity when a runner dies before writing a parked lock."""
    if name not in browser.SLOTS or not aid:
        return None
    loaded = store(["session-load", "--id", aid])
    if loaded.returncode != 0:
        if "no session with that id" in (loaded.stderr or "").lower():
            return None
        return aid
    try:
        entry = json.loads(loaded.stdout or "{}")
    except json.JSONDecodeError:
        return aid
    return aid if entry.get("browserSlot") == name else None


def lock_holder(name):
    """The live or parked owner of this lock, or None.

    Parked browser slots outlive their runner on purpose. Their lock is cleared
    only after the application reaches a terminal outcome and its page state
    has been removed.
    """
    p = lock_path(name)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        pid = int(data["pid"])
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        p.unlink(missing_ok=True)
        return None
    if data.get("parked") is True:
        return data
    if pid_alive(pid):
        return data
    durable_owner = _durable_browser_owner(name, data.get("note"))
    if durable_owner:
        parked = {
            "pid": 0,
            "at": now_stamp(),
            "note": durable_owner,
            "parked": True,
        }
        _write_lock(name, parked)
        return parked
    # The holder died without cleaning up: a crash, a reboot, a kill.
    p.unlink(missing_ok=True)
    return None


def _write_lock(name, data):
    """Atomically replace one private lock record."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LOCK_DIR, 0o700)
    target = lock_path(name)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def take_lock(name, pid, note=""):
    _write_lock(name, {"pid": pid, "at": now_stamp(), "note": note})


def park_slot(name, aid):
    """Keep a browser slot owned after its runner exits."""
    _write_lock(name, {
        "pid": 0,
        "at": now_stamp(),
        "note": aid,
        "parked": True,
    })


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
    held = lock_holder(name)  # clears it first if the previous holder is dead
    if held:
        if held.get("parked") is True and held.get("note") == note:
            take_lock(name, os.getpid(), note=note)
            return True
        return False
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

# Two very different jobs share the `scan` lock, and only one of them has any
# business holding up an Apply.
#
# `run_scan.py` is an agent sweep: it drives a provider, so it competes with a
# form runner for the one connection `select_provider_for_work` hands out. That
# contention is real and Apply should wait for it.
#
# `ingest.py` is an HTTP pass over the job boards. It touches no provider and no
# browser, and the apply path never calls `jobs_store.save_jobs`, so it cannot
# lose an update to a form runner either. It shares the lock only because an
# agent sweep and an API ingest both rewrite the pool and one at a time is the
# rule, which is a statement about scans and was never a statement about Apply.
#
# Conflating them cost roughly thirteen minutes of dead Apply, six times a day,
# silently: the tick evaluated the branch, found the lock held, spawned nothing,
# and logged nothing. Pressing Apply during a sweep did nothing at all and then
# worked again, with no way to tell those apart from the outside.
INGEST_LOCK_NOTES = frozenset(("tier1-ingest", "ingest"))


def scan_blocking_apply():
    """The scan holder that should hold up Apply, or None.

    Returns the lock record so the caller can say which sweep it is waiting on
    rather than reporting a bare boolean nobody can act on.
    """
    held = lock_holder("scan")
    if not held or held.get("note") in INGEST_LOCK_NOTES:
        return None
    return held


def free_apply_slots():
    """Slot names with no live runner, in order."""
    return [name for name in APPLY_LOCKS if lock_holder(name) is None]


def running_applications():
    """applicationId -> slot, for every apply currently in flight."""
    out = {}
    for name in APPLY_LOCKS:
        held = lock_holder(name)
        if held and held.get("note") and held.get("parked") is not True:
            out[held["note"]] = name
    return out


def _sessions():
    result = store(["session-list"])
    if result.returncode != 0:
        raise TickError("application sessions are unreadable")
    try:
        value = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise TickError("application sessions returned unreadable output") from exc
    if not isinstance(value, list):
        raise TickError("application sessions returned the wrong shape")
    return [entry for entry in value if isinstance(entry, dict)]


def _save_session(entry):
    aid = entry.get("applicationId")
    if not aid:
        raise TickError("cannot save a browser slot without an application id")
    result = run(
        [sys.executable, str(STORE), "session-save", "--id", aid, "--input", "-"],
        input=json.dumps(entry),
    )
    if result.returncode != 0:
        raise TickError(f"could not save browser ownership for {aid}")


def browser_slot_for_application(aid, sessions=None):
    """Return the recorded slot, with a parked lock as a recovery fallback."""
    entries = sessions
    if entries is None:
        loaded = store(["session-load", "--id", aid])
        if loaded.returncode == 0:
            try:
                entry = json.loads(loaded.stdout or "{}")
            except json.JSONDecodeError:
                entry = {}
            entries = [entry] if isinstance(entry, dict) else []
        else:
            entries = []
    for entry in entries:
        if entry.get("applicationId") == aid and entry.get("browserSlot") in APPLY_LOCKS:
            return entry["browserSlot"]
    for slot in APPLY_LOCKS:
        held = lock_holder(slot)
        if held and held.get("parked") is True and held.get("note") == aid:
            return slot
    return None


def persist_browser_slot(aid, slot):
    """Save the slot before a runner can put private form state in it."""
    if slot not in APPLY_LOCKS:
        raise TickError("cannot assign an unknown browser slot")
    loaded = store(["session-load", "--id", aid])
    if loaded.returncode != 0:
        raise TickError(f"no application session for {aid}")
    try:
        entry = json.loads(loaded.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise TickError(f"application session is unreadable for {aid}") from exc
    previous = entry.get("browserSlot")
    if (previous in APPLY_LOCKS and previous != slot
            and not slot_affinity_is_stale(aid, previous)):
        # Still genuinely this application's slot, so moving would strand the
        # form state in it and leave one application owning two profiles.
        raise TickError(f"application {aid} already owns {previous}")
    entry["browserSlot"] = slot
    _save_session(entry)
    return previous == slot


def _latest_history():
    result = store(["history-list"])
    if result.returncode != 0:
        raise TickError("application history is unreadable")
    try:
        events = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise TickError("application history returned unreadable output") from exc
    latest = {}
    for event in sorted((x for x in events if isinstance(x, dict)),
                        key=lambda x: x.get("at") or ""):
        if event.get("applicationId"):
            latest[event["applicationId"]] = event.get("event")
    return latest


def release_browser_slot(aid, slot=None, entry=None):
    """Clear a terminal application's page, then release its durable owner."""
    if entry is None:
        loaded = store(["session-load", "--id", aid])
        if loaded.returncode == 0:
            try:
                entry = json.loads(loaded.stdout or "{}")
            except json.JSONDecodeError as exc:
                raise TickError(f"application session is unreadable for {aid}") from exc
        else:
            entry = None
    slot = slot or ((entry or {}).get("browserSlot"))
    if slot not in APPLY_LOCKS:
        return False

    held = lock_holder(slot)
    if held and held.get("note") != aid:
        raise TickError(f"browser slot {slot} is owned by another application")
    if held and held.get("parked") is not True:
        return False

    cleared = browser.clear_slot(slot)
    if not cleared.get("cleared"):
        park_slot(slot, aid)
        return False

    if entry is not None and entry.get("browserSlot") == slot:
        entry.pop("browserSlot", None)
        _save_session(entry)
    lock_path(slot).unlink(missing_ok=True)
    return True


def reconcile_browser_slots():
    """Restore parked reservations and release terminal ones after cleanup."""
    sessions = _sessions()
    latest = _latest_history()
    by_slot = {}
    released = []
    parked = []
    handled = set()
    session_ids = {entry.get("applicationId") for entry in sessions}

    for entry in sessions:
        aid = entry.get("applicationId")
        slot = entry.get("browserSlot")
        if not aid or slot not in APPLY_LOCKS:
            continue
        if slot in by_slot and by_slot[slot] != aid:
            raise TickError(f"browser slot {slot} is recorded for two applications")
        by_slot[slot] = aid
        # One rule: a slot is held while its application is in flight, and at no
        # other time. Occupied slots equal In flight rows, which is what the
        # board shows and what a person can therefore check by looking.
        #
        # It used to be a list of exceptions -- release when terminal, release
        # when idle in Watching, hold when parked -- and the list was wrong in
        # the direction that costs capacity. A parked application kept its slot
        # so a Retry could reuse the filled tab, which is a real convenience and
        # it deadlocked the pool: five parked applications held all five slots,
        # four queued retries could never get one, and three Drops drained into
        # a fleet that had nothing to give them. Twenty hours, and every
        # application involved looked healthy.
        #
        # The trade is deliberate. Retry now reopens the posting instead of
        # returning to a filled form. A convenience that can take throughput to
        # zero is not a convenience.
        if entry.get("status") != "inflight":
            if release_browser_slot(aid, slot, entry):
                released.append({"applicationId": aid, "slot": slot})
            handled.add(slot)
            continue
        held = lock_holder(slot)
        if held is None:
            park_slot(slot, aid)
            parked.append({"applicationId": aid, "slot": slot})
        elif held.get("note") != aid:
            raise TickError(f"browser slot {slot} is owned by another application")

    # A lock whose application no longer claims it. Two ways to get here: a
    # terminal sync removed the session before the watcher arrived, or the
    # session dropped its `browserSlot` and the loop above never saw the pair.
    # Both leave a lock nobody will ever release by name, which is the shape
    # that took the pool to zero.
    inflight_ids = {
        entry.get("applicationId") for entry in sessions
        if entry.get("status") == "inflight"
    }
    for slot in APPLY_LOCKS:
        # Already dealt with above. Without this the loop releases it a second
        # time and reports it twice, which reads as two slots freed when one was.
        if slot in handled:
            continue
        held = lock_holder(slot)
        if not held or held.get("parked") is not True:
            continue
        aid = held.get("note")
        # by_slot is keyed by slot, so ask it about the slot. Asking whether the
        # application id was a key answered "no" for every live holder and
        # released the whole pool, which the invariant test caught first time.
        if aid in inflight_ids and by_slot.get(slot) == aid:
            continue
        if release_browser_slot(aid, slot, entry=None):
            released.append({"applicationId": aid, "slot": slot})
    return {"parked": parked, "released": released}


def slot_affinity_is_stale(aid, affinity):
    """True when the recorded slot now belongs to a different application.

    Affinity exists so a resumed application returns to the Chrome profile that
    still holds its half-filled form. That only means anything while the slot is
    free or parked in this application's name. Once another application holds
    it, live or parked, the profile behind it is theirs and the recorded slot
    points at nothing this application can ever use again.
    """
    if affinity not in APPLY_LOCKS:
        return False
    held = lock_holder(affinity)
    if held is None:
        return False
    return not (held.get("parked") is True and held.get("note") == aid)


def candidate_apply_slots(aid):
    """Honor durable affinity, otherwise return genuinely unused slots.

    Falling back matters more than the affinity does. This returned [] when the
    recorded slot had been taken by someone else, and [] is not "wait", it is
    "never": the head of the Apply queue could not be started, and because only
    the head is ever started, every click queued behind it stopped too. On
    2026-08-25 that wedged the whole queue for four and a half hours with a free
    slot sitting unused, and no retry could clear it, because pressing Retry
    re-queued behind the same head.
    """
    affinity = browser_slot_for_application(aid)
    if not affinity:
        return free_apply_slots()
    held = lock_holder(affinity)
    if held is None:
        return [affinity]
    if held.get("parked") is True and held.get("note") == aid:
        return [affinity]
    # Stale. Losing the old profile costs a form that has to be filled again;
    # honouring it costs every application behind this one, permanently.
    return free_apply_slots()


def choose_apply_head(apply_items):
    """The first queued application that can actually start.

    Click order is the rule and index 0 wins whenever it can run. It gives way
    only for the one case that otherwise deadlocks: a parked application still
    owns its browser slot, and pressing Retry puts it back in the queue while it
    keeps that slot. If such an application is behind the head, the head is
    waiting for a slot that the tail is holding, and the tail is waiting to
    become the head. Neither can move.

    Observed on 2026-08-25 with all five slots parked by applications sitting at
    positions three to eight, the head holding built documents and no slot, and
    every one of the nine in flight reporting "waiting for an earlier
    application" while nothing at all was running.

    An application that already owns its slot consumes nothing the head is
    waiting for, so letting it go first costs the head nothing and frees a slot
    when it finishes.
    """
    items = [item for item in apply_items if isinstance(item, dict)]
    if not items:
        return None
    head = items[0]
    if candidate_apply_slots(head.get("applicationId")):
        return head
    for item in items[1:]:
        aid = item.get("applicationId")
        if not aid:
            continue
        slot = browser_slot_for_application(aid)
        # Its own parked slot, and only that. A free slot would have been
        # available to the head too, so it is not a reason to reorder.
        if slot and _parked_by(slot, aid):
            return item
    return head


def _parked_by(slot, aid):
    held = lock_holder(slot)
    return bool(held and held.get("parked") is True and held.get("note") == aid)


def split_superseded_drops(claimed, apply_items):
    """Claimed drop requests to execute, and the ones a newer click cancelled.

    The board stamps every queue entry as it takes the click, so for genuine
    clicks `at` is click order, and both stamps come from the same writer in
    the same format. A drop that predates a pending Apply or Retry for the
    same application is the user's old intent: executing it kills the click
    they just made. Observed on 2026-08-27, when a page replaying a stale drop
    memory on every load re-dropped four applications for two hours while the
    user pressed Retry on each. The newer drop still executes, so stopping a
    queued application keeps working, and a missing stamp on either side keeps
    the old rule: the drop wins.
    """
    newest_apply = {}
    for item in apply_items:
        if not isinstance(item, dict):
            continue
        aid = item.get("applicationId")
        at = item.get("at")
        if aid and isinstance(at, str):
            newest_apply[aid] = max(newest_apply.get(aid, ""), at)
    execute, superseded = [], []
    for item in claimed:
        aid = item.get("applicationId") if isinstance(item, dict) else None
        drop_at = item.get("at") if isinstance(item, dict) else None
        apply_at = newest_apply.get(aid, "")
        if aid and isinstance(drop_at, str) and apply_at > drop_at:
            superseded.append({"applicationId": aid,
                               "dropAt": drop_at, "applyAt": apply_at})
        else:
            execute.append(item)
    return execute, superseded


def queue_depth(q, name):
    return len(q.get(name) or [])


def claim_one(prefix, application_id=""):
    """Take one request off a queue. Only ever called from the tick.

    Naming the application takes that one rather than the oldest, which is what
    lets the tick run a request it chose over one that cannot start.
    """
    argv = [toolpath.node(), "queue.mjs", "claim-one", prefix]
    if application_id:
        argv.append(application_id)
    r = subprocess.run(argv, cwd=DEPLOY,
                       capture_output=True, text=True, env=toolpath.queue_env(), timeout=60)
    if r.returncode != 0:
        raise TickError(f"claim failed for {prefix}: {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "[]")


def claim_all(prefix):
    """Take every request from one queue, with a failed write treated as a fault."""
    r = subprocess.run([toolpath.node(), "queue.mjs", "claim", prefix], cwd=DEPLOY,
                       capture_output=True, text=True, env=toolpath.queue_env(), timeout=60)
    if r.returncode != 0:
        raise TickError(f"claim failed for {prefix}: {(r.stderr or '').strip()[:200]}")
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise TickError(f"claim returned junk for {prefix}") from exc


PROVIDER_ACTIONS = frozenset(("connect", "disconnect", "refresh"))


def launch_provider_login(provider_id):
    """Open one allowlisted native CLI sign-in inside macOS Terminal."""
    if provider_id not in providers.PROVIDER_IDS:
        raise TickError("refusing an unknown provider login request")
    command = shlex.join([
        sys.executable,
        str(REPO / "scripts" / "provider_login.py"),
        "--provider",
        provider_id,
    ])
    script = """on run argv
tell application "Terminal"
  activate
  do script (item 1 of argv)
end tell
end run"""
    result = run(["/usr/bin/osascript", "-e", script, command])
    if result.returncode != 0:
        raise TickError("the provider sign-in terminal could not be opened")
    return True


def _recheck_unhealthy(registry, checked_at):
    """Re-probe connections that are connected but failing. Returns their ids.

    Only the failing ones, and only every UNHEALTHY_RECHECK_SECONDS each, so a
    healthy board costs nothing and a broken one is not hammered. A provider
    that is not connected at all is left alone: there is nothing to check until
    the user connects it.
    """
    try:
        status = registry.public_status()
    except providers.ProviderError:
        return []
    rechecked = []
    for row in status.get("providers", []):
        provider_id = row.get("id")
        if not provider_id or not row.get("connected") or row.get("healthy"):
            _provider_recheck_at.pop(provider_id, None)
            continue
        if checked_at < _provider_recheck_at.get(provider_id, 0.0):
            continue
        _provider_recheck_at[provider_id] = checked_at + UNHEALTHY_RECHECK_SECONDS
        try:
            registry.refresh(provider_id)
        except providers.ProviderError:
            # A refusal is the answer, not a fault: the row stays unhealthy and
            # the next window tries again.
            pass
        rechecked.append(provider_id)
    return rechecked


def process_local_provider_controls(q, now=None):
    """Apply settings requests addressed to this Mac and publish safe status."""
    global _last_provider_control_poll, _last_local_status_publish
    global _local_status_dirty, _last_unhealthy_recheck
    checked_at = time.monotonic() if now is None else float(now)
    forced = bool(q.get("providerLocal"))
    if not forced and checked_at - _last_provider_control_poll < PROVIDER_CONTROL_INTERVAL_SECONDS:
        return {}
    _last_provider_control_poll = checked_at
    claimed = claim_all("providerLocal/")
    q["providerLocal"] = []
    if not claimed:
        rechecked = []
        due = (_last_unhealthy_recheck is not None
               and checked_at - _last_unhealthy_recheck >= UNHEALTHY_RECHECK_SECONDS)
        if _last_unhealthy_recheck is None:
            _last_unhealthy_recheck = checked_at
        publishing = (
            _local_status_dirty
            or checked_at - _last_local_status_publish >= LOCAL_STATUS_HEARTBEAT_SECONDS
        )
        if due or publishing:
            registry = providers.ProviderRegistry()
            if due:
                _last_unhealthy_recheck = checked_at
                rechecked = _recheck_unhealthy(registry, checked_at)
            if (rechecked or publishing) and provider_login.publish_status(
                    registry.public_status()):
                _last_local_status_publish = checked_at
                _local_status_dirty = False
        return {"providersRechecked": rechecked} if rechecked else {}
    registry = providers.ProviderRegistry()
    events = []
    rejected = []
    status = None
    for item in claimed:
        provider_id = item.get("provider") if isinstance(item, dict) else None
        action = item.get("action") if isinstance(item, dict) else None
        if provider_id not in providers.PROVIDER_IDS or action not in PROVIDER_ACTIONS:
            rejected.append({"provider": provider_id, "action": action})
            continue
        try:
            if action == "connect":
                connection_key = item.get("connectionKey")
                status = (
                    registry.begin_connect(provider_id, connection_key=connection_key)
                    if connection_key is not None
                    else registry.begin_connect(provider_id)
                )
                row = next(
                    entry for entry in status.get("providers", [])
                    if entry.get("id") == provider_id
                )
                opened = False
                if not (row.get("connected") and row.get("healthy")):
                    opened = launch_provider_login(provider_id)
                events.append({"provider": provider_id, "action": action,
                               "loginOpened": opened})
            elif action == "disconnect":
                status = registry.disconnect(provider_id, forget_secret=True)
                events.append({"provider": provider_id, "action": action})
            else:
                status = registry.refresh(provider_id)
                events.append({"provider": provider_id, "action": action})
        except (providers.ProviderError, StopIteration) as exc:
            raise TickError("the local provider registry could not apply a settings request") from exc
    if status is None:
        status = registry.public_status()
    published = provider_login.publish_status(status)
    if published:
        _last_local_status_publish = checked_at
        _local_status_dirty = False
    else:
        _local_status_dirty = True
    result = {"requests": events, "statusPublished": published}
    if rejected:
        result["rejected"] = rejected
    return {"providerLocal": result}


def select_provider_for_work(apply_head=None, scan_requested=False, now=None):
    """Run one ordered live selection per new work item, with bounded retries."""
    global _provider_selection_key, _provider_selection_status
    global _provider_selection_retry_at
    checked_at = time.monotonic() if now is None else float(now)
    application_id = (
        apply_head.get("applicationId") if isinstance(apply_head, dict) else None
    )
    key = (application_id, bool(scan_requested))
    has_work = bool(application_id or scan_requested)
    should_probe = has_work and (
        key != _provider_selection_key
        or _provider_selection_status is None
        or (
            not _provider_selection_status.get("ready")
            and checked_at >= _provider_selection_retry_at
        )
    )
    if should_probe:
        _provider_selection_status = providers.select_for_work()
        _provider_selection_key = key
        _provider_selection_retry_at = checked_at + PROVIDER_SELECTION_RETRY_SECONDS
        provider_login.publish_status(_provider_selection_status)
    elif not has_work:
        _provider_selection_key = key
        _provider_selection_status = providers.public_status()
    return _provider_selection_status or providers.public_status()


def document_provider_ready(provider_id, now=None):
    """Return local document-worker readiness without polling KV every tick."""
    global _last_document_status_poll, _document_status_cache
    checked_at = time.monotonic() if now is None else float(now)
    if (
        _document_status_cache is None
        or checked_at - _last_document_status_poll >= DOCUMENT_STATUS_INTERVAL_SECONDS
    ):
        try:
            result = subprocess.run(
                [toolpath.node(), "worker-status.mjs", "read"],
                cwd=DEPLOY,
                capture_output=True,
                text=True,
                env=toolpath.queue_env(),
                timeout=60,
            )
            _document_status_cache = (
                json.loads(result.stdout or "null") if result.returncode == 0 else None
            )
        except (OSError, SystemExit, subprocess.TimeoutExpired, json.JSONDecodeError):
            _document_status_cache = None
        _last_document_status_poll = checked_at

    status = _document_status_cache
    if not isinstance(status, dict):
        return False, "the local document worker is offline"
    published = status.get("checkedAt")
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(
            str(published).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False, "the local document worker status is unreadable"
    if age.total_seconds() > DOCUMENT_STATUS_STALE_SECONDS:
        return False, "the local document worker status is stale"
    row = next(
        (
            item for item in status.get("providers") or []
            if isinstance(item, dict) and item.get("id") == provider_id
        ),
        None,
    )
    if not row or row.get("connected") is not True or row.get("status") != "connected":
        return False, f"{provider_id} is not connected for local document work"
    return True, ""


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
    # The Found lane is part of the board now, so a scan that brought in new
    # jobs has to count as a change. Without this the pool would fill up and
    # the published board would keep showing the last set of sessions.
    return (f"{len(history)}|{blocked_message()}|{found_signature()}|"
            f"{provider_signature()}|{render_version()}|{search_config_version()}|"
            f"{profile_signature()}|"
            + "|".join(parts))


def profile_signature():
    """Enough of the Profile page to notice an edit, and no more.

    Size and modification time of the three files behind it. No value is read,
    so nothing a user typed can reach the tick line or the healthchecks payload
    through this. A missing file is a stable empty rather than a fault: the
    resume-content override does not exist until the first edit.
    """
    parts = []
    for name in ("answers.json", "resume-content.json", "profile.json"):
        path = STATE_DIR / name
        try:
            stat = path.stat()
            parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            parts.append(f"{name}:-")
    return ",".join(parts)


def search_config_version():
    """The version of the saved search the board would be rendered with.

    Saving a search changes what the *next* board looks like — its filters, its
    sort, its date window — but it moves none of the data this fingerprint was
    watching, so the republish waited for the fifteen-minute floor. Press Save,
    reload, and the filters were back to the old ones with nothing on screen
    explaining why. It looked exactly like the save had failed.
    """
    path = STATE_DIR / "search-config.json"
    if not path.exists():
        return "none"
    try:
        return str(json.loads(path.read_text()).get("version") or "0")
    except (json.JSONDecodeError, OSError):
        # Unreadable is a real fault, but publishing is not where it gets
        # reported. A stable distinct value is all this owes.
        return "unreadable"


def render_modules():
    """Every module the rendered board is built from, from one source of truth.

    This used to name three files. The deployed bundle carries eleven, because
    dashboard.py and found_lane.py import the rest, so a change to premium_ui,
    company_logos, match_score, jobs_store, brand, ats_match, search_config or
    store altered the page while leaving the fingerprint identical. The board in
    KV then kept serving the previous render against freshly deployed code, and
    the two drifted with nothing reporting it.

    Read from sync_vercel_renderer.FILES so the list cannot be right in one
    place and stale in the other: the same set that gets copied into the
    deployment is the set that decides whether to republish.
    """
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import sync_vercel_renderer
        return tuple(sync_vercel_renderer.FILES)
    except Exception:
        # Never let a fingerprint failure stop a tick. Falling back to the
        # renderers proper still catches the common case.
        return ("dashboard.py", "found_lane.py", "settings_panel.py")


def render_version():
    """What the board would be rendered *by*, not what it would be rendered from.

    The fingerprint above is entirely about data, so a code change that alters
    every card on the board is invisible to it: after a merge the watcher goes
    on serving the previous render until some session happens to move. That is
    exactly what happened when the discovery engine landed — the deploy went
    out, the board did not change, and it looked like the merge had not worked.

    A hash of the two modules that decide what the page looks like. Cheap, and
    it makes the publish self-triggering: one republish on the first tick after
    any change to either, and none after that.
    """
    digest = hashlib.sha256()
    for name in render_modules():
        try:
            digest.update((REPO / "scripts" / name).read_bytes())
        except OSError:
            # A missing renderer is a real fault, but it is publish.py's to
            # report. Here it just has to be a stable, distinct value.
            digest.update(b"missing:" + name.encode())
    return digest.hexdigest()[:12]


def provider_signature():
    """Safe provider state that should trigger a fresh published board."""
    try:
        status = providers.public_status()
    except providers.ProviderError:
        return "unreadable"
    parts = [str(status.get("activeProvider") or "none")]
    for row in status.get("providers") or []:
        parts.append(
            f"{row.get('id')}:{int(bool(row.get('connected')))}:"
            f"{row.get('status') or 'unknown'}"
        )
    return "|".join(parts)


def found_signature():
    """What the Found lane currently holds, cheaply.

    The count of unacted-on jobs plus the last ingest timestamp: both move
    whenever a scan finds anything, loses anything, or fails.
    """
    path = STATE_DIR / "jobs.json"
    if not path.exists():
        return "0:none"
    try:
        jobs = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # A pool that cannot be read is a real fault, and the same rule applies
        # as everywhere else: it must not read as an empty one.
        raise TickError("jobs.json is unreadable")
    new_jobs = sum(1 for job in jobs.values() if job.get("state") == "new")
    return f"{new_jobs}:{(meta_value('lastIngest') or {}).get('at') or 'none'}"


COOLDOWN = STATE_DIR / "publish-cooldown"
# Vercel's free plan allows 100 deployments a day. A board that redeploys the
# whole site on every change burns that in an afternoon, and then nothing
# publishes at all. When the quota is gone, stop trying until it resets rather
# than spending every tick on a request that cannot succeed.
QUOTA_COOLDOWN_SECONDS = 60 * 60


# publish.py writes this whenever a publish reaches KV, whichever route ran it.
LAST_PUBLISH = STATE_DIR / "last-publish"


def board_staleness():
    """Seconds since the board last actually published, or None if never.

    Deliberately not "since the last attempt" and not "since the last tick".
    Every failure mode this has had looked healthy from the attempt side.

    Both markers count, and the newer wins. LAST_DEPLOY is the watcher's own,
    LAST_PUBLISH is written by publish.py however it was invoked. Reading only
    the watcher's marker meant a publish run by hand left the alarm ringing over
    a board that was seconds old, which is how an alarm stops being believed.
    """
    stamps = [path.stat().st_mtime for path in (LAST_DEPLOY, LAST_PUBLISH)
              if path.exists()]
    return time.time() - max(stamps) if stamps else None


def stale_board_warning():
    """The message for the dead-man ping, or empty when the board is current."""
    age = board_staleness()
    if age is None or age <= PUBLISH_STALE_SECONDS:
        return ""
    hours = age / 3600
    return (f"the board has not published for {hours:.1f} hours, against a "
            f"{PUBLISH_STALE_SECONDS / 60:.0f} minute limit. It is still serving "
            "its last snapshot, so it looks fine and is not. Run "
            "`python3 scripts/publish.py` to see the reason.")


# A parked slot is a designed state: an agent filled what it could and left the
# page open for the user. A fleet of them is an outage wearing that state's
# clothes. On 2026-08-27 all five were parked at once with none running, and the
# way that surfaced was the user asking how many applications were in flight.
# Counted by browser calls, four had reached the form and one had made zero, so
# most of them were working as designed and the fleet was still stopped. That is
# the point: no single park was wrong, and capacity was gone anyway.
PARKED_SLOT_STALE_SECONDS = 6 * 3600
PARKED_SLOT_FRACTION = 0.6


def parked_apply_slots():
    """Each parked slot with its age in seconds, oldest first."""
    now = time.time()
    parked = []
    for name in APPLY_LOCKS:
        held = lock_holder(name)
        if not held or held.get("parked") is not True:
            continue
        try:
            # The stamps are UTC. time.mktime reads local, so it lands hours out
            # and an alarm that is hours out is an alarm nobody keeps.
            at = calendar.timegm(
                time.strptime(str(held.get("at")), "%Y-%m-%dT%H:%M:%SZ")
            )
            age = max(0.0, now - at)
        except (TypeError, ValueError):
            age = 0.0
        parked.append({"slot": name, "applicationId": held.get("note") or "",
                       "ageSeconds": int(age)})
    return sorted(parked, key=lambda entry: -entry["ageSeconds"])


def parked_slot_warning():
    """The message for the dead-man ping, or empty while the fleet is working.

    Two ways to earn it, because they are different failures. Most of the fleet
    parked at once is capacity gone whatever the reason. One slot parked for
    most of a day is a page nobody is coming back to.
    """
    parked = parked_apply_slots()
    if not parked:
        return ""
    oldest = parked[0]
    share = len(parked) / float(APPLY_SLOTS or 1)
    if share >= PARKED_SLOT_FRACTION:
        return (f"{len(parked)} of {APPLY_SLOTS} browser slots are parked, oldest "
                f"{oldest['ageSeconds'] / 3600:.1f}h ({oldest['applicationId']}). "
                "New applications cannot start until some are finished or released.")
    if oldest["ageSeconds"] >= PARKED_SLOT_STALE_SECONDS:
        return (f"browser slot {oldest['slot']} has been parked for "
                f"{oldest['ageSeconds'] / 3600:.1f}h on {oldest['applicationId']}, "
                f"against a {PARKED_SLOT_STALE_SECONDS / 3600:.0f}h limit. "
                "It is holding capacity for a page nobody has come back to.")
    return ""


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
    r = run([sys.executable, str(REPO / "scripts" / "sync_board.py")],
            timeout=PUBLISH_TIMEOUT_SECONDS)
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


# --- sourcing ---------------------------------------------------------------

def meta_value(key):
    """One meta key, or None. Never turns a store failure into a missing value."""
    result = store(["meta-get", "--key", key])
    if result.returncode != 0:
        raise TickError(f"store meta-get {key} failed")
    try:
        return json.loads(result.stdout or "{}").get(key)
    except json.JSONDecodeError:
        raise TickError(f"store meta-get {key} returned junk")


def search_config_saved():
    """Has the user saved a search yet?

    The hourly scan arms only after they have. A scan running against nobody's
    search would fill the board with roles nobody asked for, and would make the
    match percentage meaningless because there would be nothing to match
    against.
    """
    return (STATE_DIR / "search-config.json").exists()


def take_saved_config():
    """Pick up a search the board parked, persist it, clear the queue field.

    One extra read and one write, and only when something is actually waiting:
    an empty take is not a write, which matters at 28,800 polls a day.
    """
    result = subprocess.run([toolpath.node(), "queue.mjs", "config-take"], cwd=DEPLOY,
                            capture_output=True, text=True, env=toolpath.queue_env(),
                            timeout=60)
    if result.returncode != 0:
        raise TickError(f"config take failed: {(result.stderr or '').strip()[:200]}")
    try:
        held = json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        raise TickError("config take returned junk")
    if not held or not isinstance(held.get("config"), dict):
        return None
    saved = run([sys.executable, str(REPO / "scripts" / "search_config.py"),
                 "save", "--input", "-"], input=json.dumps(held["config"]))
    if saved.returncode != 0:
        # A config the schema refuses is the user's problem to see, not
        # something to swallow: it is why their next scan would look wrong.
        reason = (saved.stderr or "").strip()[:200] or "rejected by the schema"
        store(["meta-set", "--key", "blocked",
               "--value", f"The saved search was not accepted: {reason}"])
        return {"error": reason}
    try:
        return {"version": json.loads(saved.stdout or "{}").get("version")}
    except json.JSONDecodeError:
        return {"version": None}


def tier1_due(now=None):
    """(due, nextAt). Never due until a search exists."""
    if not search_config_saved():
        return False, None
    now = now or datetime.now(timezone.utc)
    nxt = meta_value("nextTier1ScanAt")
    if not nxt:
        return True, None
    try:
        when = datetime.strptime(nxt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        # An unreadable schedule is a schedule that has to be rebuilt, not one
        # that silently stops the scan forever.
        return True, None
    return now >= when, nxt


def schedule_next_tier1(now=None):
    """Set nextTier1ScanAt to +interval with jitter, and return it.

    Written whether or not the spawn worked. A failed spawn that left the
    schedule alone would retry every three seconds for an hour.
    """
    import random
    now = now or datetime.now(timezone.utc)
    minutes = 60
    config_path = STATE_DIR / "search-config.json"
    if config_path.exists():
        try:
            minutes = int(json.loads(config_path.read_text()).get("tier1IntervalMinutes") or 60)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            minutes = 60
    jitter = random.randint(-TIER1_JITTER_SECONDS, TIER1_JITTER_SECONDS)
    when = now + timedelta(seconds=minutes * 60 + jitter)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    store(["meta-set", "--key", "nextTier1ScanAt", "--value", stamp])
    return stamp


def apply_found_decisions(q):
    """The eye and pass, claimed off the queue and applied to the store.

    Both are idempotent per job id, because the board re-sends the clicks it
    remembers on every load.
    """
    changed = {}
    for queue, flag in (("watch", []), ("pass", ["--pass"])):
        if not q.get(queue):
            continue
        # Claim-all, then promote each. A promote that fails has already had its
        # queue entry taken, so the click is not re-queued here — and that is
        # deliberate rather than an oversight.
        #
        # **The recovery path is the board's localStorage replay.** It remembers
        # every eye and pass, re-sends them on load, and prunes the ones the
        # published board no longer carries. So a transient failure is retried
        # within seconds, and a permanent one (a job id the pool has never heard
        # of) is retried at most once more and then forgotten, because the id is
        # not in the next payload. Re-queueing here instead would race that
        # replay and promote the same job twice.
        #
        # If you are about to "fix" this into a re-queue: that is the `claim`
        # bug's shape in reverse, and the board already handles it.
        claimed = json.loads(subprocess.run(
            [toolpath.node(), "queue.mjs", "claim", f"{queue}/"], cwd=DEPLOY,
            capture_output=True, text=True, env=toolpath.queue_env(), timeout=60
        ).stdout or "[]")
        done, failed = [], []
        for item in claimed:
            job_id = item.get("applicationId")
            if not job_id:
                continue
            result = run([sys.executable, str(REPO / "scripts" / "promote.py"),
                          "--id", job_id] + flag)
            if result.returncode == 0:
                done.append(job_id)
            else:
                # Carry the reason, not just the id: "no job with that id" and
                # "the store would not write" want very different responses,
                # and a bare id in a log line tells you neither.
                failed.append({"jobId": job_id,
                               "why": (result.stderr or "").strip()[:160] or "unknown"})
        if done:
            changed[f"{queue}ed"] = done
        if failed:
            # Board and pool have drifted apart. Visible in the tick line and in
            # the healthchecks payload, which is where a standing condition is
            # meant to show up.
            changed[f"{queue}Failed"] = failed
    return changed


# --- Profile page edits -----------------------------------------------------

def _bank_command(item):
    """The store call one claimed answer edit becomes, or None."""
    action = item.get("action") or "save"
    if action == "delete":
        key = item.get("key")
        if not key:
            return None, None
        return ["answer-delete", "--key", str(key),
                "--version", str(int(item.get("version") or 0)),
                "--edit-id", str(item.get("applicationId") or "")], None
    payload = {
        "editId": item.get("applicationId"),
        "question": item.get("question"),
        "value": item.get("value") or "",
        "version": int(item.get("version") or 0),
    }
    for field in ("key", "state", "tags"):
        if item.get(field) not in (None, ""):
            payload[field] = item[field]
    return ["answer-edit", "--input", "-"], json.dumps(payload)


BASE_RESUME_DIR = STATE_DIR / "base-resumes"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def install_uploaded_resume(upload_id, key):
    """Take an uploaded base resume out of KV and write it beside the store.

    The board can reach KV and the device can reach the filesystem, and this is
    the only place those meet. The key is deleted as it is taken, so a document
    does not sit in KV after the one device that wanted it has it.

    Returns the path, or raises. Refusing loudly matters more than usual here:
    a base resume that silently did not arrive means the next application is
    built from the wrong document and nothing says so.
    """
    result = subprocess.run(
        [toolpath.node(), "queue.mjs", "upload-take", upload_id],
        cwd=DEPLOY, capture_output=True, text=True,
        env=toolpath.queue_env(), timeout=60)
    if result.returncode != 0:
        raise TickError(f"upload {upload_id} could not be taken: "
                        f"{(result.stderr or '').strip()[:200]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise TickError(f"upload {upload_id} returned junk") from exc
    encoded = payload.get("pdf")
    if not encoded:
        raise TickError(f"upload {upload_id} was not in KV; it may have expired")
    import base64
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise TickError(f"upload {upload_id} was not valid base64") from exc
    if not data.startswith(b"%PDF-"):
        raise TickError(f"upload {upload_id} is not a PDF")
    if len(data) > MAX_UPLOAD_BYTES:
        raise TickError(f"upload {upload_id} is larger than 2 MB")
    BASE_RESUME_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BASE_RESUME_DIR, 0o700)
    path = BASE_RESUME_DIR / f"{key}.pdf"
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return path


def _resume_command(item):
    action = item.get("action")
    if action in ("add", "update"):
        payload = {k: item[k] for k in ("key", "label", "countries", "copyFrom", "priority")
                   if item.get(k) is not None}
        if not payload.get("key"):
            return None, None
        if action == "add" and item.get("uploadId"):
            payload["documentPath"] = str(
                install_uploaded_resume(str(item["uploadId"]), payload["key"]))
        return [f"resume-{action}", "--input", "-"], json.dumps(payload)
    if action in ("default", "remove"):
        key = item.get("key")
        if not key:
            return None, None
        return [f"resume-{action}", "--key", str(key)], None
    if action == "content" and isinstance(item.get("content"), dict):
        return ["resume-content-put", "--input", "-"], json.dumps(item["content"])
    return None, None


def close_answered_blockers(answered_keys):
    """Drop parked rows whose question the bank can now answer.

    This is the second door of the grow-only loop. An answer saved on the
    Profile page closes the question everywhere it is blocking, not only where
    it was first asked, which is the whole reason the bank is worth filling.

    Returns the applications that lost their last blocker, which is what the
    caller retries.
    """
    if not answered_keys:
        return [], []
    cleared, freed = [], []
    for entry in _sessions():
        if (entry.get("status") or "").lower() != "review":
            continue
        pending = entry.get("pendingFields") or []
        if not pending:
            continue
        remaining = []
        for item in pending:
            question = item.get("question") if isinstance(item, dict) else None
            resolved = False
            if question:
                found = store(["answer-find", "--question", str(question)])
                if found.returncode == 0:
                    try:
                        answer = json.loads(found.stdout or "{}")
                    except json.JSONDecodeError:
                        answer = {}
                    # Only an answer clears a row. `match: none` with `open`
                    # means the bank is tracking the question and still has
                    # nothing to fill, which is exactly a blocker.
                    resolved = answer.get("match") != "none" and bool(
                        answer.get("value") or answer.get("answer"))
            if not resolved:
                remaining.append(item)
        if len(remaining) == len(pending):
            continue
        aid = entry.get("applicationId")
        if remaining:
            entry["pendingFields"] = remaining
        else:
            entry.pop("pendingFields", None)
            freed.append(aid)
        _save_session(entry)
        cleared.append({"applicationId": aid,
                        "closed": len(pending) - len(remaining),
                        "left": len(remaining)})
    return cleared, freed


def apply_profile_edits(q):
    """Memory Bank and base-resume edits, claimed off the queue and applied.

    Idempotent twice over: the queue dedupes an edit id it already holds, and
    the store refuses an edit whose version has already moved. So the board's
    localStorage replay is free, the same way it is for every other click.

    A refused edit is reported rather than swallowed. An answer the user typed
    and believes is saved, which silently is not, is the worst outcome
    available here.
    """
    changed = {}
    for queue, build in (("answerEdits", _bank_command), ("resumeEdits", _resume_command)):
        if not q.get(queue):
            continue
        claimed = claim_all(f"{queue}/")
        done, refused = [], []
        answered_keys = []
        for item in claimed:
            if not isinstance(item, dict):
                continue
            argv, stdin = build(item)
            if argv is None:
                refused.append({"editId": item.get("applicationId"),
                                "why": "the edit named no action this store knows"})
                continue
            result = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "store.py")] + argv,
                input=stdin, capture_output=True, text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS)
            if result.returncode != 0:
                refused.append({"editId": item.get("applicationId"),
                                "why": (result.stderr or "").strip()[:200] or "the store refused it"})
                continue
            try:
                out = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                refused.append({"editId": item.get("applicationId"),
                                "why": "the store returned junk"})
                continue
            if out.get("applied") is False and out.get("reason") == "conflict":
                refused.append({"editId": item.get("applicationId"),
                                "why": f"the entry moved to version {out.get('version')}"})
                continue
            done.append(out.get("key") or out.get("default") or out.get("removed") or True)
            if queue == "answerEdits" and out.get("stored"):
                answered_keys.append(out.get("key"))
        if done:
            changed[queue] = done
        if refused:
            changed[f"{queue}Refused"] = refused
        if answered_keys:
            cleared, freed = close_answered_blockers(answered_keys)
            if cleared:
                changed["blockersCleared"] = cleared
            for aid in freed:
                # The last question on the card is answered, so the application
                # is no longer waiting on the user and goes back to work without
                # a second click. Through the normal Apply path, so every guard
                # that stops a duplicate submission still runs.
                reason = do_not_start(aid)
                if reason:
                    changed.setdefault("notResumed", []).append(
                        {"applicationId": aid, "why": reason})
                    continue
                enqueued = subprocess.run(
                    [toolpath.node(), "queue.mjs", "enqueue", "apply/", aid],
                    cwd=DEPLOY, capture_output=True, text=True,
                    env=toolpath.queue_env(), timeout=60)
                if enqueued.returncode == 0:
                    changed.setdefault("resumed", []).append(aid)
                else:
                    changed.setdefault("notResumed", []).append(
                        {"applicationId": aid,
                         "why": (enqueued.stderr or "").strip()[:160] or "could not re-queue"})
    return changed


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

def do_not_start(aid):
    """Why this application must not be spawned, or None.

    Three states mean a run would be wrong rather than merely redundant:
    the user dropped it, the user has to do something before an agent can get
    any further, or a submit has already been attempted and is waiting on a
    confirmation nobody has recorded yet. Starting again in that last case is
    how a form gets filled a second time under the user's name.
    """
    loaded = store(["session-load", "--id", aid])
    if loaded.returncode == 0 and (loaded.stdout or "").strip():
        try:
            entry = json.loads(loaded.stdout)
        except json.JSONDecodeError:
            entry = {}
        status = (entry.get("status") or "").lower()
        if status == "dropped":
            return "the user dropped it"
        if status == "review" and entry.get("pendingFields"):
            return "it is parked in Needs you, waiting on the user"

    events = store(["history-list"])
    if events.returncode != 0:
        # Not knowing is not permission to start. A duplicate application is
        # sent under the user's name and cannot be recalled.
        return "the history could not be read, so it cannot be cleared to start"
    try:
        history = json.loads(events.stdout or "[]")
    except json.JSONDecodeError:
        return "the history is unreadable, so it cannot be cleared to start"
    latest = None
    for event in sorted(history, key=lambda e: e.get("at") or ""):
        if event.get("applicationId") == aid:
            latest = event
    if latest and latest.get("event") == "completed":
        return "it is already submitted"
    if latest and latest.get("event") == "submitting":
        return ("a submit was already attempted and no confirmation was recorded. "
                "Press Confirm submitted if it went through, or Stop if it did not.")
    return None


DOCUMENT_PHASES = {
    "submitting": (
        "Building documents on Your Device",
        "Sending the document job to the local worker",
    ),
    "queued": (
        "Building documents on Your Device",
        "Waiting for the local document worker",
    ),
    "claimed": (
        "Building documents on Your Device",
        "Your Device is generating the resume and cover letter",
    ),
    "completed": (
        "Checking documents",
        "Verifying and installing the generated files",
    ),
    "retrieved": (
        "Checking documents",
        "Verifying and installing the generated files",
    ),
    "ready": (
        "Documents ready",
        "Waiting for an on-device application slot",
    ),
}


def set_phase_if_changed(aid, phase, detail):
    """Write a board phase only when its visible value actually changed."""
    loaded = store(["session-load", "--id", aid])
    if loaded.returncode != 0:
        raise TickError(f"no application session for {aid}")
    try:
        entry = json.loads(loaded.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise TickError(f"application session is unreadable for {aid}") from exc
    if entry.get("phase") == phase and entry.get("phaseDetail") == detail:
        return False
    result = run([
        sys.executable,
        str(REPO / "scripts" / "phase.py"),
        "--id",
        aid,
        "--phase",
        phase,
        "--detail",
        detail,
    ])
    if result.returncode != 0:
        raise TickError(f"phase update failed for {aid}")
    return True


def clear_document_failure(aid):
    """Remove a resolved document blocker without touching other blockers."""
    loaded = store(["session-load", "--id", aid])
    if loaded.returncode != 0:
        raise TickError(f"no application session for {aid}")
    try:
        entry = json.loads(loaded.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise TickError(f"application session is unreadable for {aid}") from exc
    pending = entry.get("pendingFields") or []
    remaining = [item for item in pending if not (
        isinstance(item, dict) and item.get("field") == "Document generation"
    )]
    if remaining == pending:
        return False
    if remaining:
        entry["pendingFields"] = remaining
    else:
        entry.pop("pendingFields", None)
    _save_session(entry)
    return True


def document_candidate_order(preferred, work_order, existing_document, is_retry):
    """Connection order for the head's document work, with failures demoted.

    The registry's health probe proves login and model invocation, not
    document generation, so a provider can probe healthy forever while
    failing every document job it is handed. On 2026-08-28 gemini did exactly
    that three times for one application, and each Retry re-picked it while
    two accounts that could do the work sat idle, because the walk only
    skipped providers whose probes failed. A retry of a failed document job
    now tries every other connection first. Demoted, not banned: when the
    failed provider is the only connection it still runs, and a fresh job
    keeps the saved preference untouched.
    """
    ordered = []
    for candidate in [preferred] + list(work_order):
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    if not is_retry or not isinstance(existing_document, dict):
        return ordered
    if existing_document.get("state") != "failed":
        return ordered
    failed = existing_document.get("provider")
    if failed in ordered and len(ordered) > 1:
        ordered = [c for c in ordered if c != failed] + [failed]
    return ordered


def prepare_document_head(item, provider_id):
    """Submit or poll the head document job without claiming the Apply click."""
    if not isinstance(item, dict) or not item.get("applicationId"):
        raise TickError("the Apply queue head has no application id")
    if provider_id not in providers.PROVIDER_IDS:
        raise TickError("the selected provider is not supported")
    aid = item["applicationId"]
    try:
        saved = document_job.get_state(aid)
        if item.get("retry") is True and saved and saved.get("state") == "failed":
            return document_job.retry_failed(aid, provider_id)
        return document_job.sync(aid, provider_id)
    except document_job.DocumentJobError as exc:
        raise TickError(f"document job stopped with {exc.code}") from exc


def park_document_failure(aid, state):
    """Claim one failed Apply request and send it to an explicit Retry state."""
    claimed = claim_one("apply/")
    if not claimed or claimed[0].get("applicationId") != aid:
        raise TickError("the Apply queue changed while parking a document failure")
    code = state.get("errorCode") or "worker_failed"
    # The code alone sent six applications to Settings to look at a provider
    # that was healthy the whole time. When the request itself was refused, the
    # refusal names the missing fact and belongs on the card.
    why = state.get("errorDetail") or ""
    detail = (
        f"The local document worker stopped with {code}"
        + (f": {why}" if why else "")
        + ". Press Retry after checking the provider and worker status in Settings."
    )
    result = run([
        sys.executable,
        str(REPO / "scripts" / "finish.py"),
        "--id",
        aid,
        "--park",
        "--pending",
        "Document generation",
        "--detail",
        detail,
    ])
    if result.returncode != 0:
        raise TickError(f"could not park failed document work for {aid}")
    return {"applicationId": aid, "errorCode": code}


def tick(force_publish=False):
    """One pass. Raises TickError if it could not do its job."""
    changed = {}
    q = poll_queues()
    slot_changes = reconcile_browser_slots()
    if slot_changes["parked"] or slot_changes["released"]:
        changed["browserSlots"] = slot_changes

    # Drops first, and before anything is spawned. A drop can be the user
    # stopping a run that is happening right now, so acting on it late means the
    # agent keeps filling a form the user has already said no to.
    if q.get("drop"):
        claimed = json.loads(subprocess.run(
            [toolpath.node(), "queue.mjs", "claim", "drop/"], cwd=DEPLOY,
            capture_output=True, text=True, env=toolpath.queue_env(), timeout=60
        ).stdout or "[]")
        claimed, superseded = split_superseded_drops(claimed, q.get("apply") or [])
        if superseded:
            changed["dropSuperseded"] = superseded
        dropped = []
        for item in claimed:
            aid = item.get("applicationId")
            if not aid:
                continue
            r = run([sys.executable, str(REPO / "scripts" / "drop.py"),
                     "--id", aid, "--no-publish"])
            if r.returncode == 0:
                dropped.append(aid)
                slot = browser_slot_for_application(aid)
                if slot and release_browser_slot(aid, slot):
                    changed.setdefault("browserSlotsReleased", []).append(
                        {"applicationId": aid, "slot": slot})
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
            slot = browser_slot_for_application(aid)
            browser.serve(slot)
            changed["focus"] = {
                "applicationId": aid,
                **browser.focus(url, slot=slot),
            }

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

    # The saved search, if the board parked one. Persisted before anything is
    # scheduled, so a config saved seconds ago governs the very next scan.
    if q.get("searchConfig"):
        taken = take_saved_config()
        if taken:
            changed["searchConfig"] = taken

    changed.update(apply_found_decisions(q))
    changed.update(apply_profile_edits(q))

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
            for result in applied:
                aid = result.get("applicationId")
                if not aid or result.get("error"):
                    continue
                slot = browser_slot_for_application(aid)
                if slot and release_browser_slot(aid, slot):
                    changed.setdefault("browserSlotsReleased", []).append(
                        {"applicationId": aid, "slot": slot})

    apply_items = q.get("apply") or []
    # A drop that executed this tick beats the applies left for the same
    # application. split_superseded_drops already gave the win to any apply
    # click stamped after the drop, so what reaches here is older intent.
    # phase.py refuses the promotion anyway, which is the fix that holds for
    # every caller; this stops the tick spending a slot and a document lane on
    # work it is about to be refused, and stops the log reporting the same ids
    # under `dropped` and `queued` in one line.
    just_dropped = set(changed.get("dropped") or [])
    if just_dropped:
        apply_items = [item for item in apply_items
                       if not (isinstance(item, dict)
                               and item.get("applicationId") in just_dropped)]
    apply_head = choose_apply_head(apply_items)
    head_id = apply_head.get("applicationId") if isinstance(apply_head, dict) else None
    apply_tail = [item for item in apply_items if item is not apply_head]

    # Tail applications stay visible as queued, but only the head can submit or
    # Poll one serial document lane on this device. Tail applications remain
    # queued so document generation stays bounded and preserves click order.
    for item in apply_tail:
        aid = item.get("applicationId") if isinstance(item, dict) else None
        if aid and set_phase_if_changed(aid, "Queued", "Waiting for an earlier application"):
            changed.setdefault("queued", []).append(aid)

    provider_status = select_provider_for_work(apply_head, bool(q.get("scan")))
    selected_provider = provider_status.get("activeProvider")
    document_state = None
    document_provider = None
    auth_block = None
    selected_provider_ready = False

    if apply_head:
        if not head_id:
            raise TickError("the Apply queue head has no application id")
        busy = running_applications()
        if head_id in busy:
            claimed = claim_one("apply/")
            if not claimed or claimed[0].get("applicationId") != head_id:
                raise TickError("the Apply queue changed while removing a duplicate click")
            q["apply"] = apply_items[1:]
            changed.setdefault("alreadyRunning", []).append(
                {"applicationId": head_id, "slot": busy[head_id]})
            apply_head = None
        elif apply_head.get("retry") is not True:
            reason = do_not_start(head_id)
            if reason:
                claimed = claim_one("apply/")
                if not claimed or claimed[0].get("applicationId") != head_id:
                    raise TickError("the Apply queue changed while declining a request")
                q["apply"] = apply_items[1:]
                changed.setdefault("notStarted", []).append(
                    {"applicationId": head_id, "why": reason})
                apply_head = None

    if apply_head:
        if not selected_provider:
            detail = "Connect an agent account for this Mac in Settings"
            if set_phase_if_changed(head_id, "Waiting for provider", detail):
                changed.setdefault("queued", []).append(head_id)
            auth_block = agent.blocked_reason("no connected agent provider is healthy")
            changed["waitingOnProvider"] = head_id
        else:
            try:
                existing_document = document_job.get_state(head_id)
            except document_job.DocumentJobError:
                existing_document = None
            # Walk the connections rather than standing on the newest one.
            # activeProvider is a preference, and a preference that is not
            # usable for document work has to hand over, not halt: this stopped
            # at gemini, which is connected and locally healthy but not
            # connected for local document work, and every Apply behind it
            # waited on "Waiting for provider" while two accounts that could do
            # the job sat idle. The tick never reached the spawn at all.
            usable_provider = None
            local_why = ""
            remote_why = ""
            considered = []
            candidates = document_candidate_order(
                selected_provider, providers.work_order(), existing_document,
                apply_head.get("retry") is True)
            for candidate in candidates:
                considered.append(candidate)
                ready, why = agent.authenticated(
                    force=not bool(existing_document), provider=candidate
                )
                if not ready:
                    local_why = local_why or why
                    continue
                selected_provider_ready = True
                remote_ready, why = document_provider_ready(candidate)
                if not remote_ready:
                    remote_why = remote_why or why
                    continue
                usable_provider = candidate
                break

            if usable_provider is None and not selected_provider_ready:
                auth_block = agent.blocked_reason(local_why, selected_provider)
                detail = "Reconnect the default provider on this Mac in Settings"
                if set_phase_if_changed(head_id, "Waiting for provider", detail):
                    changed.setdefault("queued", []).append(head_id)
                changed["waitingOnAuth"] = auth_block
            else:
                if usable_provider is None:
                    detail = remote_why + ". Open Settings to finish the connection"
                    if set_phase_if_changed(head_id, "Waiting for provider", detail):
                        changed.setdefault("queued", []).append(head_id)
                    changed["waitingOnDocumentProvider"] = {
                        "applicationId": head_id,
                        "provider": selected_provider,
                        "why": remote_why,
                        "considered": considered,
                    }
                else:
                    selected_provider = usable_provider
                    document_state = prepare_document_head(apply_head, selected_provider)
                    document_provider = document_state.get("provider") or selected_provider
                    state_name = document_state.get("state")
                    if state_name == "failed":
                        parked = park_document_failure(head_id, document_state)
                        q["apply"] = apply_items[1:]
                        changed["documentFailed"] = parked
                        apply_head = None
                    elif state_name not in DOCUMENT_PHASES:
                        raise TickError(f"document job returned invalid state {state_name!r}")
                    else:
                        phase, detail = DOCUMENT_PHASES[state_name]
                        if state_name == "ready":
                            clear_document_failure(head_id)
                        error_code = document_state.get("lastErrorCode")
                        if error_code:
                            detail = f"{detail}. Status checks will retry after {error_code}"
                        if set_phase_if_changed(head_id, phase, detail):
                            changed["documentState"] = {
                                "applicationId": head_id,
                                "state": state_name,
                                "provider": document_provider,
                            }

    # New work uses the first healthy connection. A document job keeps its saved
    # provider while it is being generated, then the form stage can hand off to
    # the currently healthy provider.
    scan_ok = False
    scan_provider = selected_provider
    if q.get("scan"):
        if scan_provider:
            scan_ok, why = agent.authenticated(provider=scan_provider)
        else:
            scan_ok, why = False, "no connected agent provider is healthy"
        if not scan_ok:
            auth_block = agent.blocked_reason(why, scan_provider)
            changed["waitingOnAuth"] = auth_block

    apply_ok = False
    if apply_head and document_job.is_ready(document_state):
        apply_ok, why = agent.authenticated(provider=selected_provider)
        if not apply_ok:
            auth_block = agent.blocked_reason(why, document_provider)
            changed["waitingOnAuth"] = auth_block

    if auth_block:
        store(["meta-set", "--key", "blocked", "--value", auth_block])
    elif blocked_message():
        status = providers.public_status()
        if status.get("ready"):
            store(["meta-set", "--key", "blocked", "--value", ""])
            changed["unblocked"] = True

    # The phone alarm tells the user something is wrong. The board has to tell
    # them what, in the place they are already looking when they ask how many
    # applications are running, because that is the question this answers and
    # asking it out loud is how the last one surfaced. Written every tick, so
    # it clears itself the moment a slot frees.
    parked_now = parked_slot_warning()
    if parked_now != (meta_value("slotsParked") or ""):
        store(["meta-set", "--key", "slotsParked", "--value", parked_now])
        changed["slotsParked"] = parked_now or "cleared"

    # One scan at a time. The runner claims its own scan request only after its
    # forced provider preflight passes.
    if scan_ok and q.get("scan") and not lock_holder("scan"):
        pid = spawn(
            "scan",
            [sys.executable, str(REPO / "scripts" / "run_scan.py"),
             "--provider", scan_provider],
            "scan-runs.log",
            note="scan",
        )
        changed["scanStarted"] = pid

    # The hourly Tier-1 sourcing pass. It shares the scan lock rather than
    # taking one of its own: an agent sweep and an API ingest both rewrite the
    # same pool, and one at a time is the rule.
    #
    # It needs no LLM and no browser, so it costs minutes of HTTP rather than
    # tokens — but it is still spawned detached, because eighty company boards
    # at a second apiece is not something a three-second tick can hold.
    if not lock_holder("scan"):
        due, _ = tier1_due()
        if due:
            nxt = schedule_next_tier1()
            pid = spawn("scan", [sys.executable, str(REPO / "scripts" / "ingest.py"),
                                 "--tier", "1"], "ingest-runs.log", note="tier1-ingest")
            changed["tier1ScanStarted"] = {"pid": pid, "nextAt": nxt}

    # The device claims an Apply click only after the local document worker
    # returned all four verified files and the saved provider passed auth.
    free_slots = free_apply_slots()
    affinity_slot = browser_slot_for_application(head_id) if apply_head else None
    candidate_slots = candidate_apply_slots(head_id) if apply_head else free_slots
    blocking_scan = scan_blocking_apply()
    if apply_ok and apply_head and candidate_slots and not blocking_scan:
        slot = candidate_slots[0]
        retained = affinity_slot == slot
        if reserve(slot, note=head_id):
            try:
                claimed = claim_one("apply/", application_id=head_id)
                if not claimed or claimed[0].get("applicationId") != head_id:
                    raise TickError("the Apply queue changed before the form runner started")
                q["apply"] = apply_tail
                persist_browser_slot(head_id, slot)
                browser.serve(slot)
                pid = spawn(
                    slot,
                    [sys.executable, str(REPO / "scripts" / "run_apply.py"),
                     "--id", head_id, "--slot", slot,
                     "--provider", selected_provider],
                    f"{slot}.log",
                    note=head_id,
                )
            except Exception:
                if retained:
                    park_slot(slot, head_id)
                else:
                    lock_path(slot).unlink(missing_ok=True)
                raise
            changed.setdefault("applyStarted", []).append({
                "slot": slot,
                "pid": pid,
                "applicationId": head_id,
                "provider": selected_provider,
            })
    elif apply_ok and apply_head and candidate_slots and blocking_scan:
        # Say so. An Apply that waits is a legitimate outcome; an Apply that
        # waits without a word is indistinguishable from one that was dropped,
        # which is what this looked like from the board for six sweeps a day.
        changed["applyDeferred"] = {
            "applicationId": head_id,
            "waitingOn": blocking_scan.get("note") or "scan",
            "since": blocking_scan.get("at"),
        }

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
    age = board_staleness()
    out = {"queues": None, "locks": {},
           "health": bool(config().get("HEALTHCHECKS_URL")),
           # Configured is not the same as working, and only one of them is
           # worth knowing.
           "lastPing": LAST_PING,
           # "health" above only ever meant "healthchecks is configured", which
           # read as reassurance through a day-long outage. This is the fact.
           "boardAgeSeconds": None if age is None else int(age),
           "boardStale": bool(stale_board_warning())}
    try:
        out["queues"] = poll_queues()
    except (TickError, subprocess.TimeoutExpired) as exc:
        out["queues"] = {"error": str(exc)}
    for name in APPLY_LOCKS + ["scan"]:
        out["locks"][name] = lock_holder(name)
    parked = parked_apply_slots()
    out["slots"] = {"total": APPLY_SLOTS, "free": len(free_apply_slots()),
                    "running": running_applications(),
                    "parked": parked,
                    "parkedWarning": parked_slot_warning() or None}
    out["blocked"] = blocked_message() or None
    try:
        out["providers"] = providers.public_status()
    except providers.ProviderError as exc:
        out["providers"] = {"error": str(exc)[:160]}
    try:
        due, nxt = tier1_due()
        out["sourcing"] = {"searchSaved": search_config_saved(), "due": due,
                           "nextTier1ScanAt": nxt,
                           "lastIngest": meta_value("lastIngest")}
    except TickError as exc:
        out["sourcing"] = {"error": str(exc)}
    return out


# --- launchd ----------------------------------------------------------------

def _service_python(explicit=None):
    """Choose and retain the interpreter launchd is already allowed to run."""
    candidate = explicit
    if candidate is None and PLIST.exists():
        try:
            current = plistlib.loads(PLIST.read_bytes())
            arguments = current.get("ProgramArguments") or []
            candidate = arguments[0] if arguments else None
        except (OSError, plistlib.InvalidFileException, TypeError, ValueError):
            candidate = None
    candidate = candidate or sys.executable
    path = Path(candidate).expanduser()
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("the launchd Python path must be an absolute executable file")
    return str(path)


def install(interval=DEFAULT_INTERVAL, python_executable=None):
    service_python = _service_python(python_executable)
    for d in (LOG_DIR, LOCK_DIR):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    watcher_plist = {
        "Label": LABEL,
        "ProgramArguments": [service_python, str(REPO / "scripts" / "watch.py"),
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
    document_plist = {
        "Label": DOCUMENT_LABEL,
        "ProgramArguments": [
            service_python,
            str(REPO / "scripts" / "document_worker.py"),
            "--loop",
            "--local",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(REPO),
        "StandardOutPath": str(LOG_DIR / "documents.log"),
        "StandardErrorPath": str(LOG_DIR / "documents.err"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": {
            "PATH": toolpath.search_path(),
            "HOME": str(Path.home()),
            "MALLOC_ARENA_MAX": "2",
            "NODE_OPTIONS": "--max-old-space-size=512",
        },
    }
    provider_plist = {
        "Label": PROVIDER_LABEL,
        "ProgramArguments": [
            service_python,
            str(REPO / "scripts" / "watch.py"),
            "--provider-controls",
            "--interval",
            str(PROVIDER_SERVICE_INTERVAL_SECONDS),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(REPO),
        "StandardOutPath": str(LOG_DIR / "providers.log"),
        "StandardErrorPath": str(LOG_DIR / "providers.err"),
        "ProcessType": "Interactive",
        "EnvironmentVariables": {
            "PATH": toolpath.search_path(),
            "HOME": str(Path.home()),
        },
    }
    PLIST.write_bytes(plistlib.dumps(watcher_plist))
    DOCUMENT_PLIST.write_bytes(plistlib.dumps(document_plist))
    PROVIDER_PLIST.write_bytes(plistlib.dumps(provider_plist))
    for path in (PLIST, DOCUMENT_PLIST, PROVIDER_PLIST):
        run(["launchctl", "unload", str(path)])
    watcher_loaded = run(["launchctl", "load", str(PLIST)])
    documents_loaded = run(["launchctl", "load", str(DOCUMENT_PLIST)])
    providers_loaded = run(["launchctl", "load", str(PROVIDER_PLIST)])
    ok = all(
        result.returncode == 0
        for result in (watcher_loaded, documents_loaded, providers_loaded)
    )
    print(json.dumps({
        "installed": ok,
        "services": {
            "watcher": {"label": LABEL, "plist": str(PLIST)},
            "documents": {
                "label": DOCUMENT_LABEL,
                "plist": str(DOCUMENT_PLIST),
            },
            "providers": {
                "label": PROVIDER_LABEL,
                "plist": str(PROVIDER_PLIST),
                "intervalSeconds": PROVIDER_SERVICE_INTERVAL_SECONDS,
            },
        },
        "intervalSeconds": interval,
        "log": str(LOG_DIR / "watch.log"),
        "error": (
            (
                watcher_loaded.stderr
                or documents_loaded.stderr
                or providers_loaded.stderr
                or ""
            ).strip()[:200]
            or None
        ),
    }, indent=2))
    return 0 if ok else 1


def uninstall():
    for path in (PLIST, DOCUMENT_PLIST, PROVIDER_PLIST):
        run(["launchctl", "unload", str(path)])
        if path.exists():
            path.unlink()
    print(json.dumps({"uninstalled": True}, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Turn board clicks into running agents.")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--interval", type=int, default=0)
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--provider-controls", action="store_true")
    ap.add_argument(
        "--service-python",
        help="absolute Python executable for launchd; later installs retain it",
    )
    args = ap.parse_args()

    if args.install:
        return install(args.interval or DEFAULT_INTERVAL, args.service_python)
    if args.uninstall:
        return uninstall()
    if args.status:
        print(json.dumps(status(), indent=2, sort_keys=True))
        return 0

    if args.provider_controls:
        interval = args.interval or PROVIDER_SERVICE_INTERVAL_SECONDS
        failures = 0
        while True:
            try:
                result = process_local_provider_controls(
                    {"providerLocal": ["poll"]}
                )
                if result:
                    print(f"{now_stamp()} {json.dumps(result, sort_keys=True)}", flush=True)
                failures = 0
            except (TickError, subprocess.TimeoutExpired) as exc:
                failures += 1
                if failures <= 3 or failures % 30 == 0:
                    print(
                        f"{now_stamp()} provider control error: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception as exc:
                failures += 1
                print(
                    f"{now_stamp()} provider control error: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(interval)

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
            stale = "; ".join(
                message for message in
                (stale_board_warning(), parked_slot_warning()) if message
            )
            if stale:
                # Fails the dead-man check rather than pinging healthy. A quiet
                # tick over a day-old board is the exact shape of the outage
                # this exists for, and "idle" would have reported it as fine.
                if consecutive_failures == 0:
                    print(f"{now_stamp()} STALLED: {stale}", file=sys.stderr, flush=True)
                ping_health("fail", stale)
                last_ping = time.time()
            elif heartbeat_due(line, prev_line, time.time() - last_ping):
                ping_health("", (line or "idle")[:2000])
                last_ping = time.time()
        except (TickError, subprocess.TimeoutExpired) as exc:
            consecutive_failures += 1
            # A failing tick and a stale board are the same outage seen from
            # two ends, and the stale branch below never runs when the tick
            # raises. Saying only "tick 4620: publish failed" loses the fact
            # that costs the most: how long the board has been wrong.
            stale_now = stale_board_warning()
            if stale_now:
                exc = TickError(f"{exc}. {stale_now}")
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
