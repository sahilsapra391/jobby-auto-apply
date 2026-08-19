#!/usr/bin/env python3
"""Give spawned agents a browser of their own.

A `claude -p` session started by the watcher has no MCP servers, so it has no
browser: the Chrome extension attaches to one session at a time and that one
belongs to whoever is at the keyboard. Without this the daemon could tailor the
documents for an application and then had nowhere to put them.

The profile is persistent and separate from the user's real Chrome, at
~/.job-application/browser-profile. Persistent because a fresh profile means
signing in to every ATS on every run, which is the difference between the
pipeline finishing an application and parking it. Separate because a Playwright
session cannot attach to a Chrome the user already has open, and driving their
live profile unattended is not something to do quietly.

  python3 scripts/browser.py --config   # path to the MCP config, written if absent
  python3 scripts/browser.py --check    # can a browser actually launch?
  python3 scripts/browser.py --login <url>   # open a window to sign in by hand
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402

STATE = Path.home() / ".job-application"
PROFILE = STATE / "browser-profile"
CONFIG = STATE / "browser-mcp.json"
# Pinned rather than @latest: an unattended pipeline should not change the tool
# that fills application forms because a release happened overnight.
PACKAGE = "@playwright/mcp@0.0.79"
SERVER = "browser"


def write_config():
    """MCP config with absolute paths, because launchd gives children no PATH."""
    PROFILE.mkdir(parents=True, exist_ok=True)
    PROFILE.chmod(0o700)
    npx = toolpath.find("npx")
    cfg = {
        "mcpServers": {
            SERVER: {
                "command": npx,
                # No --isolated: that flag is presence-only and throws away the
                # profile on exit, which would mean signing in to every ATS on
                # every single run.
                "args": ["-y", PACKAGE,
                         "--browser", "chrome",
                         "--user-data-dir", str(PROFILE)],
                "env": {"PATH": toolpath.search_path()},
            }
        }
    }
    STATE.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    CONFIG.chmod(0o600)
    return CONFIG


def config_path():
    if not CONFIG.exists():
        return write_config()
    return CONFIG


def available():
    """(ok, reason). Cheap: can we even reach npx and Chrome."""
    if toolpath.find("npx", required=False) is None:
        return False, "npx is not installed"
    if not Path("/Applications/Google Chrome.app").exists():
        return False, "Google Chrome is not installed"
    return True, ""


def check():
    ok, why = available()
    if not ok:
        return {"ok": False, "reason": why}
    r = subprocess.run([toolpath.find("npx"), "-y", PACKAGE, "--version"],
                       capture_output=True, text=True, timeout=300,
                       env=toolpath.enriched_env())
    return {
        "ok": r.returncode == 0,
        "version": (r.stdout or "").strip()[:40],
        "reason": "" if r.returncode == 0 else (r.stderr or "").strip()[:200],
        "config": str(config_path()),
        "profile": str(PROFILE),
    }


def main():
    ap = argparse.ArgumentParser(description="Browser for spawned agents.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--config", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--login", metavar="URL",
                   help="open this profile in a visible window so you can sign in")
    args = ap.parse_args()

    if args.config:
        print(str(write_config()))
        return 0
    if args.check:
        out = check()
        print(json.dumps(out, indent=2))
        return 0 if out["ok"] else 1

    # A visible Chrome on the same profile the agents use, so a sign-in done
    # here is a sign-in they inherit.
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    f"--user-data-dir={PROFILE}", "--no-first-run", args.login])
    return 0


if __name__ == "__main__":
    sys.exit(main())
