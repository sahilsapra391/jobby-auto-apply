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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402

STATE = Path.home() / ".job-application"
PROFILE = STATE / "browser-profile"
CONFIG = STATE / "browser-mcp.json"
ARTIFACTS = STATE / "browser-artifacts"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Pinned rather than @latest: an unattended pipeline should not change the tool
# that fills application forms because a release happened overnight.
PACKAGE = "@playwright/mcp@0.0.79"
SERVER = "browser"

# The browser is long-lived and the agent attaches to it, rather than each run
# starting its own and taking it down on the way out. That was the reason a
# parked application sent the user to a blank form: the tab holding twenty
# filled fields died with the run that filled it. Form state lives in the page,
# not in the profile, so the only way to keep it is to keep the page open.
CDP_PORT = 9222
CDP = f"http://127.0.0.1:{CDP_PORT}"
READY_TIMEOUT = 25


def write_config():
    """MCP config with absolute paths, because launchd gives children no PATH."""
    for d in (PROFILE, ARTIFACTS):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
    npx = toolpath.find("npx")
    cfg = {
        "mcpServers": {
            SERVER: {
                "command": npx,
                # --cdp-endpoint attaches to the browser started by serve()
                # instead of launching a throwaway one, so whatever a run leaves
                # on screen is still there once the run has finished.
                #
                # --output-dir keeps Playwright's traces, console logs and page
                # snapshots out of the repo. They default to .playwright-mcp/ in
                # the working directory, and a snapshot of a filled application
                # form holds the user's name, phone and home address.
                "args": ["-y", PACKAGE,
                         "--cdp-endpoint", CDP,
                         "--output-dir", str(ARTIFACTS)],
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


def _cdp(path, timeout=4):
    """Chrome's DevTools HTTP endpoint. Returns parsed JSON, or None if down."""
    try:
        with urllib.request.urlopen(f"{CDP}{path}", timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip().startswith(("{", "[")) else body
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def running():
    return _cdp("/json/version") is not None


def serve():
    """Start the long-lived browser if it is not already up.

    Detached and in its own session, so it survives the run that started it and
    is not killed along with a runner's process group when an application is
    dropped mid-flight.
    """
    if running():
        return {"started": False, "alreadyRunning": True, "cdp": CDP}
    ok, why = available()
    if not ok:
        return {"started": False, "error": why}
    for d in (PROFILE, ARTIFACTS):
        d.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [CHROME,
         f"--remote-debugging-port={CDP_PORT}",
         f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check",
         # Keep it alive with a window even when the agent closes its last tab.
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=toolpath.enriched_env())
    for _ in range(READY_TIMEOUT * 4):
        if running():
            return {"started": True, "cdp": CDP}
        time.sleep(0.25)
    return {"started": False, "error": f"Chrome did not open a CDP port within {READY_TIMEOUT}s"}


def tabs():
    return [t for t in (_cdp("/json/list") or []) if t.get("type") == "page"]


def _open_tab(url):
    """Open a new tab in the long-lived browser. Chrome wants PUT for /json/new."""
    req = urllib.request.Request(
        f"{CDP}/json/new?{urllib.parse.urlencode({'': url})[1:]}", method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def focus(url, open_if_missing=True):
    """Bring the tab already showing this posting to the front.

    Matching is on origin plus path, ignoring query and fragment, because an ATS
    rewrites its own URL as you move through the steps and the tab holding a
    part-filled form rarely still has the address the run started from.

    With nothing open for it, the posting is opened here rather than reported as
    a miss. The point of the button is to land the user in the browser that holds
    the profile and the logins, whether or not a run left something behind.
    """
    if not running():
        return {"focused": False, "why": "the browser is not running"}
    want = _key(url)
    hit = next((t for t in tabs() if _key(t.get("url", "")) == want), None)
    if hit is None:
        host = urllib.parse.urlparse(url).netloc
        hit = next((t for t in tabs()
                    if urllib.parse.urlparse(t.get("url", "")).netloc == host), None)
    opened = False
    if hit is None:
        if not open_if_missing:
            return {"focused": False, "why": "no open tab for this posting"}
        hit = _open_tab(url)
        opened = True
        if hit is None:
            return {"focused": False, "why": "could not open a tab for this posting"}
    _cdp(f"/json/activate/{hit['id']}")
    # Raising the tab inside its window is the part that always works. Fronting
    # the window needs the window server, which a launchd agent may not reach;
    # if it fails the tab is still the active one when the user switches to it.
    subprocess.run(["osascript", "-e",
                    'tell application "Google Chrome" to activate'],
                   capture_output=True, text=True, timeout=10)
    return {"focused": True, "opened": opened, "url": hit.get("url", ""),
            "title": (hit.get("title") or "")[:120]}


def _key(url):
    p = urllib.parse.urlparse(url or "")
    return (p.netloc.lower(), p.path.rstrip("/").lower())


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
    g.add_argument("--serve", action="store_true",
                   help="start the long-lived browser if it is not already up")
    g.add_argument("--tabs", action="store_true", help="what the browser has open")
    g.add_argument("--focus", metavar="URL",
                   help="bring the tab already showing this posting to the front")
    g.add_argument("--login", metavar="URL",
                   help="open this profile in a visible window so you can sign in")
    args = ap.parse_args()

    if args.config:
        print(str(write_config()))
        return 0
    if args.serve:
        out = serve()
        print(json.dumps(out, indent=2))
        return 0 if not out.get("error") else 1
    if args.tabs:
        print(json.dumps([{"url": t.get("url", ""), "title": t.get("title", "")[:100]}
                          for t in tabs()], indent=2))
        return 0
    if args.focus:
        out = focus(args.focus)
        print(json.dumps(out, indent=2))
        return 0 if out["focused"] else 1
    if args.check:
        out = check()
        out["browserRunning"] = running()
        out["openTabs"] = len(tabs())
        print(json.dumps(out, indent=2))
        return 0 if out["ok"] else 1

    # Sign in inside the browser the agents actually use, so the session is
    # theirs from then on. It is a normal visible Chrome, just on a profile of
    # its own, so signing in here is an ordinary sign-in that sticks.
    #
    # This used to launch a second Chrome on the same profile. Two Chromes
    # cannot share one user-data-dir, so once the browser became long-lived that
    # would either fail or corrupt the profile. Reuse the running one instead.
    started = serve()
    if started.get("error"):
        print(json.dumps(started, indent=2))
        return 1
    out = focus(args.login)
    out["signInHere"] = ("Sign in in the window that just came up. Every later run uses "
                         "this same profile, so LinkedIn and anything else stays signed in.")
    print(json.dumps(out, indent=2))
    return 0 if out["focused"] else 1


if __name__ == "__main__":
    sys.exit(main())
