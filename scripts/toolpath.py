#!/usr/bin/env python3
"""Absolute paths to the external binaries this skill shells out to.

launchd starts agents with PATH=/usr/bin:/bin:/usr/sbin:/sbin and nothing else.
node, vercel and claude all live in /opt/homebrew/bin on this machine, so every
bare-name subprocess call in a launchd-run script dies with FileNotFoundError.
That is not hypothetical: the watcher ran for hours reporting healthy cycles
while every queue read and every publish failed exactly this way.

Resolving to an absolute path once, here, means a script behaves the same
whether it is run from a shell, from launchd, or from a spawned agent.

  python3 scripts/toolpath.py        # show what resolves, and what is missing
"""

import json
import os
import shutil
import sys
from pathlib import Path

# Homebrew on Apple silicon, then Intel, then nvm's current, then the system.
# Ordered by how likely it is to be the one the user actually installed.
EXTRA_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    str(Path.home() / ".nvm" / "current" / "bin"),
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".bun" / "bin"),
    "/usr/bin",
    "/bin",
]

_cache = {}


def search_path():
    """PATH plus the directories launchd omits, without duplicates."""
    seen, out = set(), []
    for d in os.environ.get("PATH", "").split(os.pathsep) + EXTRA_DIRS:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return os.pathsep.join(out)


def find(name, required=True):
    """Absolute path to `name`, or None when it is genuinely not installed."""
    if name in _cache:
        return _cache[name]
    found = shutil.which(name, path=search_path())
    if found is None:
        # Deliberately not cached. Caching a miss would make a later required
        # lookup return None instead of raising, and would hide a tool the user
        # installed after the process started.
        if required:
            raise SystemExit(
                f"job-application: cannot find `{name}`. Looked on PATH and in "
                f"{', '.join(EXTRA_DIRS)}. Install it, or add its directory to EXTRA_DIRS "
                f"in scripts/toolpath.py."
            )
        return None
    _cache[name] = found
    return found


def node(required=True):
    return find("node", required)


def vercel(required=True):
    return find("vercel", required)


def claude(required=True):
    return find("claude", required)


def python(required=True):
    """This interpreter, always. Never a different python3 that happens to be on PATH."""
    return sys.executable


def enriched_env(env=None):
    """An environment whose PATH can see the tools, for children we do not exec directly."""
    out = dict(env if env is not None else os.environ)
    out["PATH"] = search_path()
    return out


def report():
    out = {"searchPath": search_path().split(os.pathsep), "tools": {}}
    for name in ("node", "vercel", "claude"):
        out["tools"][name] = find(name, required=False) or "MISSING"
    out["tools"]["python"] = python()
    out["ok"] = all(v != "MISSING" for v in out["tools"].values())
    return out


if __name__ == "__main__":
    r = report()
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)
