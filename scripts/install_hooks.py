#!/usr/bin/env python3
"""Bind this checkout to `.githooks`, and say whether it was bound before.

**`core.hooksPath` is git config, and git config is per clone.** A fresh
`git clone` has it unset, and nothing in a repository can set it on the way in:
git deliberately runs no repository-supplied code during a clone. So a hook
this project relies on is, in every new clone, simply not there -- and a hook
that is not installed does not fail. It is silent, which is the same silence
as a hook that passes.

Measured 2026-09-04, which is how this was found:

    production checkout   core.hooksPath = .githooks    hook active
    ~/dev clone           core.hooksPath = (unset)      hook inert

`.githooks/pre-commit` is the third enforcement point for the image allowlist
and the only one for the `JOBBY_ADD` staging rule, so in that clone `git add -A`
had never been gated and never said so.

**Worktrees are not the gap and it is worth being exact, because the first
write-up of this said they were.** A linked worktree shares the repository's
config, so `git worktree add` from a configured checkout inherits
`core.hooksPath` with no further action. Verified by making one and reading the
value back. The gap is separate clones, which is what `~/dev/jobby-auto-apply`
is.

`tests/test_hooks_are_installed.py` is what makes this script get run: it fails
in any checkout where the binding is missing and names this command. A setup
script nobody is told to run is the artefact that exists and does nothing.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / ".githooks"
EXPECTED = ".githooks"


def configured(cwd=None):
    """The checkout's `core.hooksPath`, or "" when it is unset."""
    try:
        done = subprocess.run(["git", "config", "core.hooksPath"],
                              cwd=str(cwd or REPO), capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def executable(path):
    """A hook that is not executable is skipped by git, silently."""
    try:
        return bool(path.stat().st_mode & stat.S_IXUSR)
    except OSError:
        return False


def hooks():
    return sorted(p for p in HOOKS.glob("*") if p.is_file())


def install(cwd=None):
    subprocess.run(["git", "config", "core.hooksPath", EXPECTED],
                   cwd=str(cwd or REPO), check=True, timeout=10)
    for path in hooks():
        if not executable(path):
            path.chmod(path.stat().st_mode | stat.S_IXUSR)


def main(argv):
    # `--repo` so the refusal can be exercised against a checkout that really
    # has no binding. Without it the only way to see this branch is to unbind
    # the developer's own checkout, and a test that has to break the machine it
    # runs on is a test nobody runs.
    target = REPO
    if "--repo" in argv:
        target = Path(argv[argv.index("--repo") + 1])
    before = configured(target)
    if "--check" in argv:
        if before != EXPECTED:
            print(f"core.hooksPath is {before or 'unset'} in {target}; the "
                  f"pre-commit guards are inert here.\n"
                  f"  python3 scripts/install_hooks.py", file=sys.stderr)
            return 1
        missing = [p.name for p in hooks() if not executable(p)]
        if missing:
            print(f"these hooks are not executable, so git skips them "
                  f"silently: {missing}", file=sys.stderr)
            return 1
        print(f"hooks bound: core.hooksPath={before}, "
              f"{len(hooks())} hook(s), all executable")
        return 0
    install(target)
    after = configured(target)
    if after != EXPECTED:
        print(f"could not set core.hooksPath in {target} "
              f"(it reads {after or 'unset'})",
              file=sys.stderr)
        return 1
    print(f"core.hooksPath {before or 'unset'} -> {after} in {target}")
    for path in hooks():
        print(f"  {path.name}  {'executable' if executable(path) else 'NOT EXECUTABLE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
