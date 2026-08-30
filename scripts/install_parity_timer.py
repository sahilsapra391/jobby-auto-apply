#!/usr/bin/env python3
"""Install the scheduled parity check as its own launchd agent.

    python3 scripts/install_parity_timer.py --install
    python3 scripts/install_parity_timer.py --status
    python3 scripts/install_parity_timer.py --uninstall

**A fourth agent rather than a task inside the watcher**, and the reason is the
one `ai.jobby.providers` already demonstrates: the watcher is a long-lived
process that holds its modules in memory, so anything added to it arms on a
restart and cannot be changed without cycling the apply slots. Parity is a
read-only check that must keep running while everything else is being deployed,
which is exactly when the two stores are most likely to drift apart. Coupling it
to the thing it is watching is how a check stops reporting at the moment it
matters.

It runs every 30 minutes. `verify_parity` re-reads once on a divergence and the
whole run costs about 4 seconds against the live store, so the interval is set
by how soon somebody should know rather than by cost.

The seven-day clock is counted from `~/.job-application/logs/parity.jsonl`,
which this appends to through `parity_cron.py`.
"""

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LABEL = "ai.jobby.parity"
PLIST = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
INTERVAL_SECONDS = 1800


def plist_body(python):
    root = Path("~/.job-application").expanduser()
    return {
        "Label": LABEL,
        # The interpreter, with the script as an argument. Never the script
        # directly: a LaunchAgent may not exec a file under ~/Documents, and
        # launchd answers `Operation not permitted`, exit 126, without ever
        # reaching the program. The three existing services take the same shape
        # for the same reason.
        "ProgramArguments": [python, str(REPO / "scripts" / "parity_cron.py")],
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        # Its own log, not the watcher's. A check that shares a log with the
        # thing it is checking is one nobody can read during an incident.
        "StandardOutPath": str(root / "logs" / "parity.out"),
        "StandardErrorPath": str(root / "logs" / "parity.err"),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "HOME": str(Path("~").expanduser()),
            # The interpreter is pinned the way watch.py --install pins its
            # own, for the reason CLAUDE.md records: under macOS Documents,
            # Privacy controls can deny a newly selected interpreter access
            # even when the plist itself loads fine.
            "JOBBY_PYTHON": python,
        },
    }


def install(python=None):
    python = python or sys.executable
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    (Path("~/.job-application").expanduser() / "logs").mkdir(
        parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist_body(python)))
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True)
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
        capture_output=True, text=True)
    if result.returncode != 0:
        # Never discarded: a timer that failed to load and said nothing is a
        # parity check that reports nothing while looking installed.
        print(f"launchctl bootstrap failed: {result.stderr.strip()}",
              file=sys.stderr)
        return 1
    print(f"installed {LABEL}, every {INTERVAL_SECONDS // 60} minutes")
    print(f"  plist   {PLIST}")
    print(f"  log     ~/.job-application/logs/parity.jsonl")
    return 0


def status():
    uid = os.getuid()
    result = subprocess.run(["launchctl", "print", f"gui/{uid}/{LABEL}"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"{LABEL} is not loaded")
        return 1
    for line in result.stdout.splitlines():
        if any(k in line for k in ("state =", "last exit code",
                                   "runs =", "program =")):
            print(line.strip())
    return 0


def uninstall():
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True)
    if PLIST.exists():
        PLIST.unlink()
    print(f"removed {LABEL}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--status", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    parser.add_argument("--python", help="interpreter to pin")
    args = parser.parse_args(argv)

    if args.install:
        return install(args.python)
    if args.status:
        return status()
    return uninstall()


if __name__ == "__main__":
    raise SystemExit(main())
