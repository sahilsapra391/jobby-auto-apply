#!/usr/bin/env python3
"""Spawn headless Claude sessions, and refuse to spend work on a broken one.

The watcher cannot tailor a resume or drive a browser, so anything real is done
by a spawned `claude -p` session. That makes the CLI's auth a load-bearing
dependency, and it fails in a way worth handling explicitly: an expired or
revoked OAuth token makes every spawn exit immediately with a 401. Because the
worker claims a queued request before running it, an unnoticed 401 silently
eats the user's clicks one per cycle.

So: preflight before claiming, cache the answer briefly, and record the failure
where the user will actually see it rather than in a log nobody opens.

  python3 scripts/agent.py --check    # is a spawned agent usable right now?
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".job-application" / "agent-auth.json"
# Long enough that a run of applications does not re-preflight constantly,
# short enough that a re-login is picked up without restarting the watcher.
CACHE_SECONDS = 600
PREFLIGHT_TIMEOUT = 90

AUTH_MARKERS = ("oauth", "401", "authenticate", "unauthorized", "invalid api key",
                "please run /login", "credentials")


def _probe():
    """Cheapest possible round trip that proves the CLI can reach the API."""
    try:
        r = subprocess.run([toolpath.claude(), "-p", "Reply with exactly: OK"],
                           capture_output=True, text=True, timeout=PREFLIGHT_TIMEOUT,
                           cwd=str(REPO), env=toolpath.enriched_env())
    except subprocess.TimeoutExpired:
        return False, "the claude CLI did not respond within 90s"
    except FileNotFoundError as exc:
        return False, str(exc)
    blob = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode == 0 and "OK" in blob:
        return True, ""
    low = blob.lower()
    if any(m in low for m in AUTH_MARKERS):
        return False, blob.splitlines()[0][:200] if blob else "authentication failed"
    return False, (blob.splitlines()[0][:200] if blob else f"claude exited {r.returncode}")


def authenticated(force=False):
    """(ok, reason). Cached, because a preflight before every spawn is wasteful."""
    if not force and STATE.exists():
        try:
            cached = json.loads(STATE.read_text())
            if time.time() - cached.get("at", 0) < CACHE_SECONDS:
                return cached["ok"], cached.get("reason", "")
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    ok, reason = _probe()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"ok": ok, "reason": reason, "at": time.time()}))
    STATE.chmod(0o600)
    return ok, reason


def invalidate():
    """Drop the cache, so the next check re-probes. Call after a spawn 401s."""
    STATE.unlink(missing_ok=True)


def looks_like_auth_failure(text):
    low = (text or "").lower()
    return any(m in low for m in AUTH_MARKERS)


def blocked_reason(reason):
    """One line the board can show the user, with the fix rather than the error."""
    return (f"Claude CLI cannot authenticate ({reason}). "
            "Run `claude login` in a terminal, then this resumes on its own.")


def main():
    ap = argparse.ArgumentParser(description="Check whether spawned agents can run.")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.parse_args()
    ok, reason = authenticated(force=True)
    print(json.dumps({"ok": ok, "reason": reason,
                      "hint": None if ok else blocked_reason(reason)}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
