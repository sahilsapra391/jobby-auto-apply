"""One sweep at a time, in the lock the watcher already reads.

`scripts/ingest.py` reads the whole of `jobs.json`, sweeps every channel for
several minutes, then writes the whole file back. Nothing coordinated that.
Two overlapping runs do not interleave: the second one to finish writes a
store built from a copy read before the first one started, so everything the
first stored is silently gone. No error, no log line, and a jobs.json that is
perfectly valid at the wrong contents.

The daemon serialised its *own* sweeps through `watch.spawn("scan", ...)`,
which takes the `scan` lock. A hand-run `python3 scripts/ingest.py` took
nothing, so the dangerous overlap was never session-against-session, it was
session-against-daemon, and the daemon fires unattended on a schedule.

So this deliberately uses the SAME lock name in the SAME directory with the
SAME record shape as `watch.py`, rather than introducing a second lock. Two
different locks would each be internally consistent and would not exclude each
other, which is precisely the race being fixed. `watch.lock_holder("scan")`
must be able to read what this writes, and does.

One consequence worth stating rather than discovering: `watch.py` gates Apply
on `lock_holder("scan")`, so a hand-run sweep now makes Apply wait, where
before it did not. That is the same behaviour the daemon's own sweeps already
had. It trades a silent lost update for a visible wait, which is the right way
round, but the Apply gate itself is a separate defect and is not fixed here.
"""

import errno
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# Lock names are file names. Keep them boring so a stray value cannot escape
# the directory or collide with a browser slot.
SAFE_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class LockBusy(Exception):
    """Somebody else holds it. Refuse rather than queue."""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pid_alive(pid):
    """Is this pid a live process?

    Non-positive pids are rejected before `os.kill` sees them. In POSIX,
    `kill(0, sig)` signals the caller's own process group and `kill(-n, sig)`
    signals group n, so `os.kill(0, 0)` succeeds and a lock written with pid 0
    would be immortal. `watch.pid_alive` carries the same guard for the same
    reason; see PR #21.
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


def lock_dir(root):
    return Path(root) / "locks"


def lock_path(root, name):
    if not name or set(name) - SAFE_NAME:
        raise ValueError(f"unsafe lock name: {name!r}")
    return lock_dir(root) / f"{name}.lock"


def holder(root, name):
    """The live owner of this lock, or None. Clears a dead holder's record.

    Deliberately narrower than `watch.lock_holder`, which also understands
    parked browser slots and can recover slot affinity from session state.
    Nothing that parks a slot ever takes this lock, and a `parked` record is
    honoured rather than cleared so the two readers can never disagree about
    who owns a file they share.
    """
    path = lock_path(root, name)
    try:
        data = json.loads(path.read_text())
        pid = int(data["pid"])
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
        # An unreadable record is not evidence that anybody is running.
        path.unlink(missing_ok=True)
        return None
    if data.get("parked") is True:
        return data
    if pid_alive(pid):
        return data
    path.unlink(missing_ok=True)
    return None


def _write(root, name, data):
    """Atomically replace one private lock record."""
    directory = lock_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    target = lock_path(root, name)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def owned_by_this_process(root, name):
    """Did our own parent already take this lock on our behalf?

    `watch.spawn` starts the child and *then* calls `take_lock(name, proc.pid)`,
    so a daemon-spawned sweep finds the scan lock held by its own pid. Acquiring
    again would be a self-deadlock, and releasing on exit would be releasing a
    lock this process never took.
    """
    held = holder(root, name)
    return bool(held) and held.get("pid") == os.getpid()


def acquire(root, name, note=""):
    """Claim the lock, or raise LockBusy. Never waits.

    O_EXCL rather than check-then-write: checking and then writing is two steps,
    and two sweeps starting together both saw it free.
    """
    directory = lock_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    held = holder(root, name)  # clears it first if the previous holder is dead
    if held:
        raise LockBusy(
            f"{name} is held by pid {held.get('pid')} since {held.get('at')}"
            + (f" ({held['note']})" if held.get("note") else ""))
    try:
        descriptor = os.open(lock_path(root, name),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise LockBusy(f"{name} was taken while we were claiming it") from None
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise LockBusy(f"{name} was taken while we were claiming it") from None
        raise
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"pid": os.getpid(), "at": _now(), "note": note}, handle)
    return True


def release(root, name):
    """Drop the lock, but only if this process is still the recorded holder."""
    path = lock_path(root, name)
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if data.get("pid") != os.getpid():
        return False
    path.unlink(missing_ok=True)
    return True


class held:
    """Hold `name` for the duration, unless our parent already holds it for us.

        with runlock.held(root, "scan", note="tier1-ingest"):
            ...

    Raises LockBusy on entry if somebody else has it.
    """

    def __init__(self, root, name, note=""):
        self.root = root
        self.name = name
        self.note = note
        self.inherited = False

    def __enter__(self):
        if owned_by_this_process(self.root, self.name):
            # Spawned by the watcher, which took it for us. Not ours to release.
            self.inherited = True
            return self
        acquire(self.root, self.name, self.note)
        return self

    def __exit__(self, *_):
        if not self.inherited:
            release(self.root, self.name)
        return False
