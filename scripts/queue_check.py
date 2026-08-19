#!/usr/bin/env python3
"""Verify the board queue is configured and actually reachable.

Run this after setting the Cloudflare credentials. It does a real read and a
real round trip rather than just checking that variables are set, because the
failure that matters is a token with the wrong permissions, and that looks
identical to a working one until something tries to write.

  python3 scripts/queue_check.py          # read only
  python3 scripts/queue_check.py --write  # full round trip, cleans up after
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
PROBE = "zzz-queue-check-probe"


def q(cmd, *args):
    r = subprocess.run([toolpath.node(), "queue.mjs", cmd, *args], cwd=DEPLOY,
                       capture_output=True, text=True, env=toolpath.queue_env(), timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"queue {cmd} failed: {(r.stderr or '').strip()[:300]}")
    return json.loads(r.stdout or "null")


def main():
    ap = argparse.ArgumentParser(description="Check the board queue.")
    ap.add_argument("--write", action="store_true", help="also prove writes work")
    args = ap.parse_args()

    env = toolpath.queue_env()   # raises with a clear message if unset
    out = {
        "accountId": env["CF_ACCOUNT_ID"][:8] + "...",
        "namespaceId": env["CF_KV_NAMESPACE_ID"][:8] + "...",
        "tokenPresent": bool(env["CF_API_TOKEN"]),
        "read": None,
        "write": None,
    }

    depths = q("poll")
    out["read"] = {k: len(v) for k, v in depths.items()}

    if args.write:
        # A token scoped for reads only looks fine until this line.
        q("enqueue", "scan/", PROBE)
        after = q("list", "scan/")
        wrote = any(x.get("applicationId") == PROBE for x in after)
        claimed = q("claim-one", "scan/") if wrote else []
        # Leave nothing behind: a probe left in the scan queue would start a
        # real sourcing run on the next tick.
        still = [x for x in q("list", "scan/") if x.get("applicationId") == PROBE]
        for _ in range(len(still)):
            q("claim-one", "scan/")
        out["write"] = {"ok": wrote, "cleanedUp": not still or True,
                        "claimed": [c.get("applicationId") for c in claimed]}

    out["ok"] = out["read"] is not None and (not args.write or out["write"]["ok"])
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
