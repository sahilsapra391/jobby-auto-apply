#!/usr/bin/env python3
"""Check a deploy landed, instead of assuming it. Read-only, exits non-zero when it did not.

Run it immediately after the restarts:

    python3 scripts/deploy_verify.py

**The question this answers is not "is the checkout current".** It is "does each
running daemon hold the code that is on the disk", and those come apart every
time, because a long-lived Python process does not reload modules. A file a
service reaches through a resident `import` arms on the **restart**; a file it
reaches only by spawning arms on the **pull**; and one deploy routinely carries
both, so no single sentence about when a deploy arms is true. `CLAUDE.md` says
to enumerate that per file and not to trust anyone, including yourself, to
derive it per deploy. This enumerates it.

Measured on this machine 2026-09-03, which is why it exists: four pulls landed
between Sep 1 17:16 and Sep 2 18:09 with no restart, and all three services ran
21 commits behind the disk for 35 hours holding a pre-fix `dual_write.py` and
`class_b.py`. Every check anyone ran that day passed. The reflog comparison is
the only method that has ever caught this state, and it is a whole-repo
inference; the mtime comparison below is the same question asked per file.

Four things, and the third is the one nothing else asks.

1. The checkout is on `main` and level with `origin/main`.
2. Every service is running, and started after the last pull.
3. **For each service, no file in its own resident import closure has an mtime
   newer than that service's start time.** A hit is a named, per-file statement
   that this daemon is stale for that module.
4. No reflog entry fell between the pull and the restarts. Checking only "the
   start times postdate the entry I expect" passes for two sessions separately
   while leaving production on two commits at once, which happened on
   2026-08-28.

**What it says when it cannot look matters as much as what it says when
something is wrong**, because a check that can only fail in one direction is
half a check. Anything unreadable is reported as `unknown` and is a failure, not
a pass: an unreadable start time and a fresh start time are not the same claim.

The closure is static. It cannot see a dynamic import or a `subprocess` built
from a string, so it is a floor rather than a proof, and it says so in its own
output rather than letting a clean run read as one.
"""

import argparse
import ast
import json
import os
import plistlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# The plists are the definition of production: they name the checkout the
# services actually execute, by absolute path. Reading the repo this script
# happens to sit in would inspect a worktree and call it production.
SERVICES = {
    "ai.jobby.watch": "watch",
    "ai.jobby.documents": "document_worker",
    "ai.jobby.providers": "provider_login",
}
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


def _run(args, cwd=None):
    """Return stdout, or None. Never raises: a missing tool is an `unknown`."""
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def plist_for(label):
    path = LAUNCH_AGENTS / f"{label}.plist"
    try:
        return plistlib.loads(path.read_bytes())
    except Exception:                                   # noqa: BLE001
        return None


def production_repo():
    """The checkout the installed plists execute, or None if they disagree."""
    roots = set()
    for label in SERVICES:
        data = plist_for(label)
        if not data:
            continue
        for arg in data.get("ProgramArguments") or []:
            if arg.endswith(".py") and "/scripts/" in arg:
                roots.add(str(Path(arg).resolve().parent.parent))
    if len(roots) == 1:
        return Path(roots.pop())
    return None if not roots else sorted(roots)


def pid_of(label):
    """launchctl's own answer. `ps` cannot tell a service from a stray process."""
    out = _run(["launchctl", "list"])
    if out is None:
        return None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2] == label:
            return int(parts[0]) if parts[0].strip("-").isdigit() else None
    return None


def started_at(pid):
    """Process start time as epoch seconds, or None."""
    if pid is None:
        return None
    out = _run(["ps", "-o", "lstart=", "-p", str(pid)])
    if not out:
        return None
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %d %b %H:%M:%S %Y"):
        try:
            # `mktime` reads local time, and `ps -o lstart` prints local time,
            # so this pairing is correct. Elsewhere in this repo `mktime` on a
            # UTC string is a bug that put an alarm hours out; the difference
            # is what the input is, not which function is safe.
            return time.mktime(time.strptime(out.strip(), fmt))
        except ValueError:
            continue
    return None


def closure(repo, entry):
    """Static import closure of one entrypoint, restricted to `scripts/`.

    A floor, not a proof: a dynamic import or a module reached only through a
    string is invisible here. That limit is printed with the result.
    """
    scripts = repo / "scripts"
    names = {p.stem for p in scripts.glob("*.py")}
    seen, stack = set(), [entry]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in names:
            continue
        seen.add(cur)
        try:
            tree = ast.parse((scripts / f"{cur}.py").read_text())
        except Exception:                               # noqa: BLE001
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                stack.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                stack.append(node.module.split(".")[0])
    return seen


# `ps -o lstart` prints whole seconds. A file written inside the same second a
# process started cannot be ordered against it, and saying "stale" there would
# be asserting something the clock cannot support. Under this margin the answer
# is `indeterminate`, which still fails, because a check that cannot see is not
# a check that saw nothing. A real deploy restarts seconds after the pull and
# never lands here.
PS_RESOLUTION_SECONDS = 2


def stale_modules(repo, entry, since):
    """Modules this service imports whose file is newer than the service.

    Returns (name, mtime, verdict) where verdict is "stale", "indeterminate" or
    "unreadable". mtime rather than git history on purpose: a pull rewrites the
    file, and the file is what an already-running interpreter did not read.
    """
    out = []
    for name in sorted(closure(repo, entry)):
        path = repo / "scripts" / f"{name}.py"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            # Narrow, and not the case it reads like: `closure` is built from a
            # glob of `scripts/*.py`, so a module with no file is filtered out
            # one step before this. What is left for this branch is the race
            # between that glob and this `stat`. A service whose import is
            # genuinely missing does not start, and `pid_of` says so first.
            out.append((name, None, "unreadable"))
            continue
        if mtime > since + PS_RESOLUTION_SECONDS:
            out.append((name, mtime, "stale"))
        elif mtime > since - PS_RESOLUTION_SECONDS:
            out.append((name, mtime, "indeterminate"))
    return out


def reflog_between(repo, low, high):
    """HEAD moves whose reflog timestamp falls in (low, high]."""
    out = _run(["git", "reflog", "--date=unix",
                "--format=%gd\t%h\t%gs"], cwd=str(repo))
    if out is None:
        return None
    moves = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or "@{" not in parts[0]:
            continue
        stamp = parts[0].split("@{", 1)[1].rstrip("}")
        if not stamp.isdigit():
            continue
        when = int(stamp)
        if low < when <= high:
            moves.append((when, parts[1], parts[2]))
    return moves


def _stamp(epoch):
    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", help="checkout to inspect; default is the one "
                                       "the installed plists execute")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    failures, report = [], {}

    repo = Path(args.repo).resolve() if args.repo else production_repo()
    if repo is None or isinstance(repo, list):
        print("FAIL  the installed plists do not agree on one checkout: %s" % (repo,),
              file=sys.stderr)
        return 2
    report["repo"] = str(repo)

    # 1. the checkout
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo))
    head = _run(["git", "rev-parse", "HEAD"], cwd=str(repo))
    _run(["git", "fetch", "--quiet", "origin", "main"], cwd=str(repo))
    remote = _run(["git", "rev-parse", "origin/main"], cwd=str(repo))
    behind = _run(["git", "rev-list", "--count", "HEAD..origin/main"], cwd=str(repo))
    report["checkout"] = {"branch": branch, "head": (head or "")[:7],
                          "originMain": (remote or "")[:7], "behind": behind}
    if branch != "main":
        failures.append("the production checkout is on %r, not main" % branch)
    if behind is None:
        failures.append("could not count commits behind origin/main")
    elif behind != "0":
        failures.append("the checkout is %s commits behind origin/main" % behind)

    # 2 and 3. the services, and what each one is holding
    report["services"] = {}
    earliest_start = None
    for label, entry in SERVICES.items():
        pid = pid_of(label)
        began = started_at(pid)
        row = {"pid": pid, "startedAt": _stamp(began), "entry": entry}
        if pid is None:
            failures.append("%s is not running" % label)
        elif began is None:
            failures.append("%s: start time unreadable, which is not the same "
                            "as recent" % label)
        else:
            earliest_start = began if earliest_start is None else min(earliest_start, began)
            stale = stale_modules(repo, entry, began)
            row["staleModules"] = [
                {"module": name, "mtime": _stamp(m), "verdict": v}
                for name, m, v in stale]
            hard = [n for n, _, v in stale if v == "stale"]
            soft = [n for n, _, v in stale if v != "stale"]
            if hard:
                failures.append(
                    "%s started %s and imports %d module(s) modified since: %s"
                    % (label, _stamp(began), len(hard), ", ".join(hard)))
            if soft:
                failures.append(
                    "%s: cannot order %s against a start time `ps` reports to "
                    "the second; treat as unverified rather than fresh"
                    % (label, ", ".join(soft)))
        report["services"][label] = row

    # 4. did the tree move between the pull and the restarts
    if earliest_start is not None:
        moves = reflog_between(repo, earliest_start, int(time.time()))
        if moves is None:
            failures.append("the reflog could not be read, so 'nothing moved "
                            "between the pull and the restarts' is unverified")
        else:
            late = [m for m in moves if m[0] > earliest_start]
            report["reflogAfterEarliestStart"] = [
                {"at": _stamp(w), "commit": c, "what": s} for w, c, s in late]
            for when, commit, what in late:
                failures.append("HEAD moved to %s at %s, after a service had "
                                "already started: %s" % (commit, _stamp(when), what))

    report["limits"] = ("the import closure is static: a dynamic import or a "
                        "module reached through a string is invisible to it, so "
                        "a clean run is a floor and not a proof")
    report["ok"] = not failures
    report["failures"] = failures

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("checkout  %s" % repo)
        c = report["checkout"]
        print("          branch %s, HEAD %s, origin/main %s, behind %s"
              % (c["branch"], c["head"], c["originMain"], c["behind"]))
        for label, row in report["services"].items():
            print("%-20s pid %-7s started %s"
                  % (label, row["pid"], row["startedAt"]))
            for item in row.get("staleModules") or []:
                print("%-20s   %-13s scripts/%s.py  modified %s"
                      % ("", item["verdict"].upper(), item["module"], item["mtime"]))
        for move in report.get("reflogAfterEarliestStart") or []:
            print("reflog    %s  %s  %s" % (move["at"], move["commit"], move["what"]))
        print()
        print("NOTE      %s" % report["limits"])
        if failures:
            print()
            for line in failures:
                print("FAIL      %s" % line)
        else:
            print("OK        every service postdates the pull and holds no "
                  "module newer than itself")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
