#!/usr/bin/env python3
"""One deploy at a time, held by a lease a second session finds rather than is told about.

On 2026-08-28 two sessions announced a deploy of the same production checkout
from the same base about a minute apart. Both named themselves sole owner. Both
had gated correctly and both sets of numbers agreed. Neither had read the other,
because both wrote in the gap between finishing a gate and acting on it, which is
exactly the gap the other's announcement lands in.

**The gates were not insufficient, they were irrelevant.** Every reading either
session took was about the machine, and the hazard was another session's
intention, which leaves no trace in the machine until it acts. No sixth check
would have caught it. What was missing was a place for the intention to live, and
that is all this is.

It is a lease rather than a pid lock because a deploy is a pull, three restarts
and a verification, a dozen short-lived commands over several minutes with no
single process alive across them. `runlock` learned about leases rather than
gaining a sibling module, so `runlock.holder` and this agree about who owns the
file. Two internally consistent locks that do not exclude each other is the race,
not the fix.

The lease expires so a session that dies mid-deploy cannot wedge production, and
nothing renews it in the background: a lease that renews itself while its owner
is gone is the wedge it was written to prevent. Extend it deliberately with
`extend` if the work is genuinely still running.

    python3 scripts/deploy_lock.py take --owner <session> --note "cc860a3 -> 1a69452"
    python3 scripts/deploy_lock.py status
    python3 scripts/deploy_lock.py release --token <token>
    python3 scripts/deploy_lock.py break --reason "holder died mid-deploy"
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runlock  # noqa: E402

NAME = "deploy"
# Long enough for a pull, three restarts and a verification without anyone
# watching a clock, short enough that a session which dies mid-deploy is not
# still holding production at the end of a coffee. The two real deploys measured
# on 2026-08-28 took 8 and 14 seconds from pull to last restart.
DEFAULT_LEASE_SECONDS = 20 * 60
MAX_LEASE_SECONDS = 60 * 60


def state_root():
    return Path(os.environ.get("JOB_APPLICATION_DIR")
                or Path.home() / ".job-application").expanduser()


def current(root=None):
    """The live deploy lease, or None. Expired leases are cleared by the read."""
    return runlock.holder(root or state_root(), NAME)


def describe(record):
    if not record:
        return "deploy lock is FREE"
    left = runlock.seconds_left(record)
    parts = [f"deploy lock is HELD by {record.get('owner') or 'an unnamed session'}",
             f"since {record.get('at')}"]
    if record.get("note"):
        parts.append(f"for {record['note']}")
    if left is not None:
        parts.append(f"{left}s left")
    return ", ".join(parts)


def cmd_take(args):
    root = state_root()
    seconds = min(max(int(args.lease), 1), MAX_LEASE_SECONDS)
    try:
        token = runlock.acquire(root, NAME, note=args.note or "",
                                lease_seconds=seconds, owner=args.owner or "")
    except runlock.LockBusy:
        held = current(root)
        # The refusal names the holder rather than saying "busy", because the
        # next question is always who, and a caller that has to go and look it
        # up will guess instead.
        return {"taken": False, "reason": describe(held), "holder": held}
    return {"taken": True, "token": token, "holder": current(root)}


def cmd_status(_args):
    held = current()
    return {"held": bool(held), "holder": held, "message": describe(held)}


def cmd_release(args):
    root = state_root()
    dropped = runlock.release(root, NAME, token=args.token)
    if not dropped:
        return {"released": False,
                "reason": "the token does not match the current holder",
                "holder": current(root)}
    return {"released": True}


def cmd_extend(args):
    """Push the deadline out, only for the session that proves it holds it."""
    root = state_root()
    held = current(root)
    if not held:
        return {"extended": False, "reason": "nothing holds the deploy lock"}
    if not args.token or str(held.get("token") or "") != str(args.token):
        return {"extended": False,
                "reason": "the token does not match the current holder"}
    runlock.release(root, NAME, token=args.token)
    seconds = min(max(int(args.lease), 1), MAX_LEASE_SECONDS)
    token = runlock.acquire(root, NAME, note=held.get("note") or "",
                            lease_seconds=seconds, owner=held.get("owner") or "")
    return {"extended": True, "token": token, "holder": current(root)}


def cmd_break(args):
    """Take it away from whoever holds it. Deliberately noisy and never silent."""
    root = state_root()
    held = current(root)
    if not held:
        return {"broken": False, "reason": "nothing holds the deploy lock"}
    runlock.lock_path(root, NAME).unlink(missing_ok=True)
    # Printed rather than returned, so it reaches a person watching a terminal
    # even when the JSON is piped into something that only reads one field.
    print(f"BROKE the deploy lock held by {held.get('owner') or 'an unnamed session'}"
          f" since {held.get('at')}: {args.reason}", file=sys.stderr)
    return {"broken": True, "was": held, "reason": args.reason}


COMMANDS = {"take": cmd_take, "status": cmd_status, "release": cmd_release,
            "extend": cmd_extend, "break": cmd_break}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    take = sub.add_parser("take")
    take.add_argument("--owner", default="", help="which session is deploying")
    take.add_argument("--note", default="", help="what is being deployed")
    take.add_argument("--lease", type=int, default=DEFAULT_LEASE_SECONDS)

    sub.add_parser("status")

    release = sub.add_parser("release")
    release.add_argument("--token", required=True)

    extend = sub.add_parser("extend")
    extend.add_argument("--token", required=True)
    extend.add_argument("--lease", type=int, default=DEFAULT_LEASE_SECONDS)

    broke = sub.add_parser("break")
    broke.add_argument("--reason", required=True,
                       help="why the previous holder is not coming back")

    args = parser.parse_args(argv)
    result = COMMANDS[args.command](args)
    print(json.dumps(result, indent=2))
    # A refusal has to be a non-zero exit as well as a JSON field. A caller that
    # checks only the exit code must not read "somebody else is deploying" as
    # permission to deploy.
    failed = result.get("taken") is False or result.get("released") is False \
        or result.get("extended") is False or result.get("broken") is False
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
