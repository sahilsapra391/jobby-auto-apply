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
import shutil
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
#
# One browser PER SLOT, not one shared between them. Two MCP servers attached to
# the same Chrome over CDP share a browser context and both drift onto the same
# tab: navigate A to example.com and B to example.org, and afterwards both
# report being on example.org. With three concurrent applications that is one
# agent typing into another's form, or pressing Submit on it. Measured, not
# assumed. And Chrome's SingletonLock means separate browsers cannot share a
# profile, so each slot gets its own; clone_logins() carries a sign-in across
# them so the user still only signs in once.
SLOTS = ["apply-1", "apply-2", "apply-3"]
BASE_PORT = 9222
READY_TIMEOUT = 25


def port_for(slot):
    return BASE_PORT + (SLOTS.index(slot) if slot in SLOTS else 0)


def cdp_for(slot):
    return f"http://127.0.0.1:{port_for(slot)}"


def profile_for(slot):
    return STATE / f"browser-profile-{slot}" if slot in SLOTS else PROFILE


# The first slot keeps the original profile directory, so the sign-ins already
# in it carry over rather than needing to be done again.
def _profile_dir(slot):
    return PROFILE if slot == SLOTS[0] else profile_for(slot)


CDP_PORT = BASE_PORT
CDP = f"http://127.0.0.1:{CDP_PORT}"


def config_file(slot):
    return STATE / f"browser-mcp-{slot}.json"


def write_config(slot=None):
    """MCP config for one slot, with absolute paths because launchd gives
    children no PATH. Each slot points at its OWN browser: two agents sharing a
    CDP endpoint end up driving the same tab."""
    slot = slot or SLOTS[0]
    for d in (_profile_dir(slot), ARTIFACTS):
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
                         "--cdp-endpoint", cdp_for(slot),
                         "--output-dir", str(ARTIFACTS / slot)],
                "env": {"PATH": toolpath.search_path()},
            }
        }
    }
    STATE.mkdir(parents=True, exist_ok=True)
    path = config_file(slot)
    path.write_text(json.dumps(cfg, indent=2))
    path.chmod(0o600)
    return path


def config_path(slot=None):
    slot = slot or SLOTS[0]
    path = config_file(slot)
    if not path.exists():
        return write_config(slot)
    return path


def available():
    """(ok, reason). Cheap: can we even reach npx and Chrome."""
    if toolpath.find("npx", required=False) is None:
        return False, "npx is not installed"
    if not Path("/Applications/Google Chrome.app").exists():
        return False, "Google Chrome is not installed"
    return True, ""


def _cdp(path, slot=None, timeout=4):
    """Chrome's DevTools HTTP endpoint. Returns parsed JSON, or None if down."""
    base = cdp_for(slot) if slot else CDP
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip().startswith(("{", "[")) else body
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def running(slot=None):
    return _cdp("/json/version", slot) is not None


def serve(slot=None):
    """Start a slot's browser if it is not already up.

    Detached and in its own session, so it survives the run that started it and
    is not killed along with a runner's process group when an application is
    dropped mid-flight. With no slot, starts the first one, which is what the
    interactive commands use.
    """
    slot = slot or SLOTS[0]
    if running(slot):
        return {"started": False, "alreadyRunning": True, "slot": slot, "cdp": cdp_for(slot)}
    ok, why = available()
    if not ok:
        return {"started": False, "error": why}
    profile = _profile_dir(slot)
    for d in (profile, ARTIFACTS):
        d.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [CHROME,
         f"--remote-debugging-port={port_for(slot)}",
         f"--user-data-dir={profile}",
         "--no-first-run", "--no-default-browser-check",
         # Keep it alive with a window even when the agent closes its last tab.
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=toolpath.enriched_env())
    for _ in range(READY_TIMEOUT * 4):
        if running(slot):
            return {"started": True, "slot": slot, "cdp": cdp_for(slot)}
        time.sleep(0.25)
    return {"started": False, "slot": slot,
            "error": f"Chrome did not open a CDP port within {READY_TIMEOUT}s"}


def clone_logins():
    """Copy the signed-in profile from the first slot to the others.

    Chrome will not let two instances share a user-data-dir, so each slot needs
    its own. Without this the user would have to sign in to LinkedIn once per
    slot. Every browser has to be stopped first: copying a profile Chrome is
    using produces a corrupt one.
    """
    live = [s for s in SLOTS if running(s)]
    if live:
        return {"cloned": False,
                "why": f"stop these first, a profile in use copies badly: {', '.join(live)}",
                "how": "python3 scripts/browser.py --stop"}
    src = _profile_dir(SLOTS[0])
    if not src.exists():
        return {"cloned": False, "why": f"nothing to clone from; {src} does not exist"}
    done = []
    for slot in SLOTS[1:]:
        dst = _profile_dir(slot)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=False, ignore_dangling_symlinks=True,
                        ignore=shutil.ignore_patterns("Singleton*", "*.lock"))
        dst.chmod(0o700)
        done.append(slot)
    return {"cloned": True, "slots": done, "from": str(src)}


def stop_all():
    """Shut every slot's browser down, for cloning or a clean restart."""
    stopped = []
    for slot in SLOTS:
        if not running(slot):
            continue
        # Chrome exposes no clean shutdown over the HTTP endpoint, so close the
        # tabs and let it exit, then fall back to a signal.
        for t in tabs(slot):
            _cdp(f"/json/close/{t['id']}", slot)
        time.sleep(1)
        if running(slot):
            subprocess.run(["pkill", "-f", f"remote-debugging-port={port_for(slot)}"],
                           capture_output=True)
        stopped.append(slot)
    time.sleep(1)
    return {"stopped": stopped, "stillRunning": [s for s in SLOTS if running(s)]}


def tabs(slot=None):
    if slot is None:
        out = []
        for name in SLOTS:
            for t in tabs(name):
                out.append({**t, "slot": name})
        return out
    return [t for t in (_cdp("/json/list", slot) or []) if t.get("type") == "page"]


def _open_tab(url, slot=None):
    """Open a new tab in a slot's browser. Chrome wants PUT for /json/new."""
    base = cdp_for(slot) if slot else CDP
    req = urllib.request.Request(
        f"{base}/json/new?{urllib.parse.urlencode({'': url})[1:]}", method="PUT")
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
    live = [s for s in SLOTS if running(s)]
    if not live:
        return {"focused": False, "why": "no browser is running"}
    # The tab could be in any slot's browser, so look across all of them.
    everything = tabs()
    want = _key(url)
    hit = next((t for t in everything if _key(t.get("url", "")) == want), None)
    if hit is None:
        host = urllib.parse.urlparse(url).netloc
        hit = next((t for t in everything
                    if urllib.parse.urlparse(t.get("url", "")).netloc == host), None)
    opened = False
    slot = hit.get("slot") if hit else live[0]
    if hit is None:
        if not open_if_missing:
            return {"focused": False, "why": "no open tab for this posting"}
        hit = _open_tab(url, slot)
        opened = True
        if hit is None:
            return {"focused": False, "why": "could not open a tab for this posting"}
    _cdp(f"/json/activate/{hit['id']}", slot)
    # Raising the tab inside its window is the part that always works. Fronting
    # the window needs the window server, which a launchd agent may not reach;
    # if it fails the tab is still the active one when the user switches to it.
    subprocess.run(["osascript", "-e",
                    'tell application "Google Chrome" to activate'],
                   capture_output=True, text=True, timeout=10)
    return {"focused": True, "opened": opened, "slot": slot,
            "url": hit.get("url", ""), "title": (hit.get("title") or "")[:120]}


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
    ap = argparse.ArgumentParser(description="Browsers for spawned agents, one per slot.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--config", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--serve", action="store_true",
                   help="start a slot's browser if it is not already up")
    g.add_argument("--serve-all", action="store_true", help="start every slot's browser")
    g.add_argument("--stop", action="store_true", help="shut every slot's browser down")
    g.add_argument("--clone-logins", action="store_true",
                   help="copy the signed-in profile from the first slot to the others, "
                        "so a sign-in is done once rather than once per slot")
    g.add_argument("--tabs", action="store_true", help="what the browsers currently hold")
    g.add_argument("--focus", metavar="URL",
                   help="bring the tab already showing this posting to the front")
    g.add_argument("--login", metavar="URL",
                   help="open this profile in a visible window so you can sign in")
    ap.add_argument("--slot", choices=SLOTS, help="which slot to act on (default the first)")
    args = ap.parse_args()

    if args.config:
        print(str(write_config(args.slot)))
        return 0
    if args.serve:
        out = serve(args.slot)
        print(json.dumps(out, indent=2))
        return 0 if not out.get("error") else 1
    if args.serve_all:
        out = {slot: serve(slot) for slot in SLOTS}
        print(json.dumps(out, indent=2))
        return 0 if all(not v.get("error") for v in out.values()) else 1
    if args.stop:
        print(json.dumps(stop_all(), indent=2))
        return 0
    if args.clone_logins:
        out = clone_logins()
        print(json.dumps(out, indent=2))
        return 0 if out["cloned"] else 1
    if args.tabs:
        print(json.dumps([{"slot": t.get("slot"), "url": t.get("url", ""),
                           "title": (t.get("title") or "")[:90]}
                          for t in tabs()], indent=2))
        return 0
    if args.focus:
        out = focus(args.focus)
        print(json.dumps(out, indent=2))
        return 0 if out["focused"] else 1
    if args.check:
        ok, why = available()
        out = {"ok": ok, "reason": why,
               "slots": {slot: {"running": running(slot), "port": port_for(slot),
                                "profile": str(_profile_dir(slot)),
                                "tabs": len(tabs(slot)) if running(slot) else 0}
                         for slot in SLOTS}}
        print(json.dumps(out, indent=2))
        return 0 if ok else 1

    # Sign in inside the browser the agents actually use, so the session is
    # theirs from then on. A sign-in done in the first slot is carried to the
    # others by --clone-logins, so it is done once rather than once per slot.
    slot = args.slot or SLOTS[0]
    started = serve(slot)
    if started.get("error"):
        print(json.dumps(started, indent=2))
        return 1
    out = focus(args.login)
    out["signInHere"] = (
        f"Sign in in the window that came up ({slot}). Then run "
        "`python3 scripts/browser.py --stop && python3 scripts/browser.py --clone-logins` "
        "to carry it to the other slots.")
    print(json.dumps(out, indent=2))
    return 0 if out["focused"] else 1


if __name__ == "__main__":
    sys.exit(main())
