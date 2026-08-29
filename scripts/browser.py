#!/usr/bin/env python3
"""Give spawned agents a browser of their own.

A headless agent session started by the watcher has no MCP servers by default,
so it has no browser. Without this the daemon could tailor the documents for an
application and then have nowhere to put them.

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
import hashlib
import json
import os
import plistlib
import shutil
import stat
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
CHROME_INFO_PLIST = Path("/Applications/Google Chrome.app/Contents/Info.plist")
# Short, because the caller asks every slot in a loop and a wedged Chrome
# answers nothing at all. Eight slots at the four seconds `_cdp` defaults to
# would cost half a minute of a four-second tick.
CHROME_VERSION_TIMEOUT = 2
# Pinned rather than @latest: an unattended pipeline should not change the tool
# that fills application forms because a release happened overnight.
PACKAGE = "@playwright/mcp@0.0.79"
SERVER = "browser"
APPLICATION_SERVER = "jobby"
APPLICATION_MCP = Path(__file__).resolve().parent / "application_mcp.py"
BROWSER_MCP_PROXY = Path(__file__).resolve().parent / "browser_mcp_proxy.py"

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
# How many applications run at once. This is the single definition: watch.py
# derives its locks from it, because two hardcoded lists that must agree is a
# drift waiting to happen, and disagreement here means a runner attaching to
# a browser that was never started.
#
# Eight since 2026-08-27. It was five, and five parked applications took all
# five, which is the reason the pool now empties on anything but a live run
# rather than the reason it was widened; a pool that can deadlock deadlocks at
# any size. Ports run 9222 through 9229.
SLOT_COUNT = 8
SLOTS = [f"apply-{i}" for i in range(1, SLOT_COUNT + 1)]
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


def gemini_settings_file(slot):
    return STATE / f"gemini-settings-{slot}.json"


def _private_artifact_directory(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        path.mkdir(parents=True, mode=0o700)
        info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
    ):
        raise OSError("unsafe browser artifact directory")
    path.chmod(0o700)
    return path.resolve(strict=True)


def artifact_root_for(slot):
    if slot not in SLOTS:
        raise ValueError("unknown browser slot")
    root = _private_artifact_directory(ARTIFACTS)
    slot_root = _private_artifact_directory(root / slot)
    if slot_root.parent != root:
        raise OSError("unsafe browser slot artifact directory")
    return slot_root


def _copy_verified_pdf(source_value, destination, expected_size, expected_digest):
    source = Path(source_value)
    if not source.is_absolute() or source.is_symlink():
        raise ValueError("application upload source must be an absolute regular file")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 6
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
    ):
        raise ValueError("application upload fingerprint is invalid")

    source_fd = os.open(
        source,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    destination_fd = -1
    digest = hashlib.sha256()
    copied = 0
    try:
        source_info = os.fstat(source_fd)
        if not stat.S_ISREG(source_info.st_mode):
            raise ValueError("application upload source is not a regular file")
        destination_fd = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            copied += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)

    if copied != expected_size or digest.hexdigest() != expected_digest.lower():
        destination.unlink(missing_ok=True)
        raise ValueError("application upload changed after validation")
    with destination.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            destination.unlink(missing_ok=True)
            raise ValueError("application upload is not a PDF")
    destination.chmod(0o600)
    return {
        "path": str(destination.resolve(strict=True)),
        "size": copied,
        "sha256": digest.hexdigest(),
    }


# Every staging this process has done, so the discard does not depend on a
# caller remembering what it staged. run_apply.main has a dozen early returns
# and the runs whose documents matter most are the ones that took one.
_STAGED = []

# What a staged document is. Deliberately a small allowlist rather than
# "everything that is not activity.json": the next file written into this
# directory should have to be named here before anything deletes it.
DOCUMENT_SUFFIXES = frozenset((".pdf", ".html", ".htm", ".docx", ".txt"))


def discard_staged():
    """Remove every set of PDFs this process staged. Returns what it removed."""
    out = []
    while _STAGED:
        slot, application_id = _STAGED.pop()
        out.append(discard_application_uploads(slot, application_id))
    return out


def discard_application_uploads(slot, application_id):
    """Remove the staged PDFs for one application. Returns what it deleted.

    A file chooser hands the browser a path on a filesystem, so the two PDFs
    have to exist locally at the moment of submission -- R2 cannot be the source
    there. What does not have to happen is them staying afterwards, and until
    now they did: 151 documents were sitting in the tree with R2 already holding
    every one of them.

    So the copy is a copy for the length of a run and no longer. This is called
    from a `finally`, because the runs whose documents matter most are the ones
    that ended badly, and a cleanup that only happens on the happy path is a
    cleanup that misses exactly those.

    It never raises. Losing a staged duplicate is not worth failing a run over,
    and R2 is the record; what it does do is report, so a directory that will
    not go away is visible rather than silently accumulating again.
    """
    result = {"applicationId": application_id, "removed": [], "kept": []}
    try:
        target = artifact_root_for(slot) / "uploads" / str(application_id)
        if target.is_symlink() or not target.is_dir():
            return result
        for path in sorted(target.glob("*")):
            # Documents only. `activity.json` lives here too, written by the
            # firewall, and it is what tells a capability failure apart from a
            # real park -- deleting it would trade a duplicate PDF for the
            # ability to say why a run stopped.
            if path.suffix.lower() not in DOCUMENT_SUFFIXES:
                continue
            try:
                if path.is_file() and not path.is_symlink():
                    size = path.stat().st_size
                    path.unlink()
                    result["removed"].append({"name": path.name, "bytes": size})
                else:
                    result["kept"].append({"name": path.name, "why": "not a plain file"})
            except OSError as exc:
                result["kept"].append({"name": path.name, "why": str(exc)[:120]})
        try:
            target.rmdir()
        except OSError:
            # Something else is in there -- activity.json, most likely, which
            # the firewall writes and which is not a document.
            pass
        # The guarantee is checked rather than assumed: a delete that silently
        # did nothing is the failure this exists to prevent.
        left = [p.name for p in target.glob("*.pdf")] if target.is_dir() else []
        if left:
            result["kept"].extend({"name": n, "why": "still present after delete"} for n in left)
    except OSError as exc:                        # noqa: BLE001 - reported
        result["error"] = str(exc)[:160]
    return result


def stage_application_uploads(slot, application_id, artifacts):
    """Copy only the two verified PDFs into Playwright's permitted root."""
    if (
        not isinstance(application_id, str)
        or not application_id
        or len(application_id) > 128
        or ".." in application_id
        or not all(character.isalnum() or character in "-_." for character in application_id)
    ):
        raise ValueError("application id is unsafe for upload staging")
    if not isinstance(artifacts, dict) or set(artifacts) != {"resume", "coverLetter"}:
        raise ValueError("application upload artifacts are incomplete")

    slot_root = artifact_root_for(slot)
    uploads = _private_artifact_directory(slot_root / "uploads")
    target = uploads / application_id
    if target.is_symlink():
        raise OSError("unsafe application upload directory")
    if target.exists():
        info = target.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("unsafe application upload directory")
        shutil.rmtree(target, ignore_errors=False)
    target.mkdir(mode=0o700)

    try:
        staged = {}
        for label, filename in (
            ("resume", "resume.pdf"),
            ("coverLetter", "cover-letter.pdf"),
        ):
            source = artifacts[label]
            if not isinstance(source, dict):
                raise ValueError("application upload metadata is invalid")
            staged[label] = _copy_verified_pdf(
                source.get("path"),
                target / filename,
                source.get("size"),
                source.get("sha256"),
            )
        # Registered only once the copies are actually there, so a failed
        # staging does not schedule a delete of somebody else's directory.
        _STAGED.append((slot, str(application_id)))
        return staged
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def server_config(slot=None, application_context=None):
    """Canonical Playwright MCP server definition for every agent CLI."""
    slot = slot or SLOTS[0]
    profile = _profile_dir(slot)
    profile.mkdir(parents=True, exist_ok=True)
    profile.chmod(0o700)
    output_root = artifact_root_for(slot)
    child = [
        toolpath.find("npx"), "-y", PACKAGE,
        "--cdp-endpoint", cdp_for(slot),
        "--output-dir", str(output_root),
    ]
    if application_context is None:
        # Diagnostic configs do not start provider agents. Apply runs always
        # supply an application context and pass through the firewall below.
        return {
            "command": child[0],
            "args": child[1:],
            "env": {"PATH": toolpath.search_path()},
        }

    resume_upload = _application_value(application_context, "uploadResume")
    cover_upload = _application_value(application_context, "uploadCover")
    for path in (resume_upload, cover_upload):
        if not Path(path).is_absolute():
            raise ValueError("application upload paths must be absolute")
    return {
        "command": str(Path(sys.executable).resolve()),
        "args": [
            str(BROWSER_MCP_PROXY),
            "--upload", resume_upload,
            "--upload", cover_upload,
            "--activity", str(activity_path(slot, application_context)),
            "--",
            *child,
        ],
        "env": {
            "PATH": toolpath.search_path(),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
    }


def _chrome_log_path(slot):
    """Where a slot's browser writes what it has to say about itself."""
    return STATE / "logs" / f"chrome-{slot}.log"


def activity_path(slot, application_context):
    """Where the firewall records what the agent reached for.

    Per slot and application, beside the staged uploads, so it is removed with
    them and never outlives the run it describes.
    """
    application_id = _application_value(application_context, "applicationId")
    # _application_value bars nulls and newlines and nothing else, which is
    # enough for a command argument and not enough for a path segment. The
    # staging check is the one that matters here, so it is the one used.
    if (len(application_id) > 128 or ".." in application_id
            or not all(character.isalnum() or character in "-_."
                       for character in application_id)):
        raise ValueError("application id is unsafe for an activity path")
    return artifact_root_for(slot) / "uploads" / application_id / "activity.json"


def _application_value(application_context, name):
    if not isinstance(application_context, dict):
        raise ValueError("application context must be an object")
    value = application_context.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"application context needs {name}")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError(f"application context has an unsafe {name}")
    return value.strip()


def application_server_config(application_context):
    """A stdio server bound to one application and two upload artifacts.

    The paths are argv, not model-controlled tool arguments. The server checks
    that they are absolute, regular PDF files before accepting any MCP calls.
    ``resumeHtml`` is optional because the server can derive the worker's
    standard hidden render-source name from the resume PDF.
    """
    if not isinstance(application_context, dict):
        raise ValueError("application context must be an object")
    unknown = sorted(
        set(application_context) - {
            "applicationId", "resume", "cover", "resumeHtml",
            "uploadResume", "uploadCover",
        }
    )
    if unknown:
        raise ValueError("application context has an unsupported field")
    application_id = _application_value(application_context, "applicationId")
    resume = _application_value(application_context, "resume")
    cover = _application_value(application_context, "cover")
    if (len(application_id) > 128 or ".." in application_id
            or not all(c.isalnum() or c in "-_." for c in application_id)):
        raise ValueError("application context has an unsafe applicationId")
    if not Path(resume).expanduser().is_absolute() or not Path(cover).expanduser().is_absolute():
        raise ValueError("application artifact paths must be absolute")
    args = [
        str(APPLICATION_MCP),
        "--application-id", application_id,
        "--resume", resume,
        "--cover", cover,
    ]
    resume_html = application_context.get("resumeHtml")
    if resume_html is not None:
        resume_html = _application_value(application_context, "resumeHtml")
        if not Path(resume_html).expanduser().is_absolute():
            raise ValueError("resumeHtml must be an absolute path")
        args.extend(["--resume-html", resume_html])
    return {
        "command": str(Path(sys.executable).resolve()),
        "args": args,
        "env": {
            "PATH": toolpath.search_path(),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
    }


def server_configs(slot=None, application_context=None):
    """Canonical server mapping for Claude, Codex, and Gemini.

    With no application context this preserves the original browser-only
    contract. Apply runs pass a mapping with ``applicationId``, ``resume`` and
    ``cover`` to add the narrow ``jobby`` server.
    """
    servers = {
        SERVER: server_config(slot, application_context=application_context)
    }
    if application_context is not None:
        servers[APPLICATION_SERVER] = application_server_config(application_context)
    return servers


def mcp_servers(slot=None, application_context=None):
    """Backward-compatible alias for callers using the generic MCP name."""
    return server_configs(slot, application_context)


def write_config(slot=None, application_context=None):
    """Claude-compatible MCP config for one isolated browser slot."""
    slot = slot or SLOTS[0]
    cfg = {"mcpServers": server_configs(slot, application_context)}
    STATE.mkdir(parents=True, exist_ok=True)
    path = config_file(slot)
    path.write_text(json.dumps(cfg, indent=2))
    path.chmod(0o600)
    return path


def config_path(slot=None, application_context=None):
    slot = slot or SLOTS[0]
    # Re-render on every run. It is tiny, and this prevents a pinned package or
    # slot definition change from leaving an old unattended config in place.
    return write_config(slot, application_context)


def write_gemini_settings(slot=None, application_context=None):
    """Gemini CLI system settings with the same isolated browser server.

    The runner points only its process at this file. Gemini's normal home stays
    untouched so its locally cached account login remains available.
    """
    slot = slot or SLOTS[0]
    servers = server_configs(slot, application_context)
    for server in servers.values():
        server["trust"] = True
    # Import here so providers can still load this module lazily for apply
    # invocations without a module-import cycle.
    import providers  # noqa: PLC0415

    cfg = providers.gemini_hardened_settings("apply")
    cfg["mcp"] = {"allowed": list(servers), "excluded": []}
    cfg["admin"]["mcp"] = {"enabled": True}
    cfg["mcpServers"] = servers
    STATE.mkdir(parents=True, exist_ok=True)
    path = gemini_settings_file(slot)
    path.write_text(json.dumps(cfg, indent=2))
    path.chmod(0o600)
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


def _launch_argv(slot, profile):
    """The slot browser's launch command.

    --use-mock-keychain is load-bearing and permanent. Without it Chrome asks
    the macOS login keychain for its storage key, and after Chrome updates on
    disk underneath a long-lived instance, or whenever the keychain is locked
    for the launch context, that request becomes a modal "Keychain Not Found"
    dialog no agent can see and no amount of Cancel makes stop. It looped on
    the user's screen on 2026-08-28. An isolated agent profile needs nothing
    from the OS keychain, so these browsers never consult it, on this machine
    or any other user's. tests/test_chrome_never_touches_the_keychain.py pins
    the flag here and on the PDF renderer, the repo's only two launch sites.
    """
    return [CHROME,
            f"--remote-debugging-port={port_for(slot)}",
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            "--use-mock-keychain",
            # No on-device AI model, in any of these profiles.
            #
            # Chrome downloads its own local model per profile, into
            # OptGuideOnDeviceModel. Measured 2026-08-29: 4.0 GB in each of the
            # eight apply profiles, 32 GB in total, which was 84% of everything
            # in ~/.job-application. Nothing in Jobby calls it: the intelligence
            # is the provider CLIs, and these browsers only fill forms.
            #
            # The disk was never the visible symptom, which is why it sat there.
            # Nothing failed, nothing warned, and the profiles are not somewhere
            # anyone looks -- it surfaced only when the whole store was measured
            # for a different reason.
            "--disable-features=OptimizationGuideOnDeviceModel,"
            "OptimizationGuideModelDownloading,TextSafetyClassifier",
            # Keep it alive with a window even when the agent closes its last tab.
            "about:blank"]


def installed_version():
    """The Chrome build sitting on disk right now, or "" if it cannot be read.

    macOS replaces the whole bundle in place when Keystone lands a release, so
    a browser started before an update carries on executing a binary that is no
    longer at that path. Nothing in Chrome tells the fleet this happened, and
    the two answers, this one and running_version, are the only place it shows.

    Empty on any failure rather than raising. The one caller uses this to decide
    whether to take browsers down, and a recycle keyed on a version that could
    not be read would empty the fleet the first time the plist moved.
    """
    try:
        info = plistlib.loads(CHROME_INFO_PLIST.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return ""
    version = info.get("CFBundleShortVersionString")
    return version.strip() if isinstance(version, str) else ""


def running_version(slot=None):
    """The build a slot's browser is actually executing, or "" if it is down.

    From CDP's own ``Browser`` field, which carries the full build
    (``Chrome/151.0.7922.175``). Deliberately not the ``User-Agent`` string
    beside it: that is rounded to ``151.0.0.0`` to stop sites fingerprinting the
    patch level, so comparing it against the installed build would report every
    slot as drifted forever.
    """
    payload = _cdp("/json/version", slot, timeout=CHROME_VERSION_TIMEOUT)
    if not isinstance(payload, dict):
        return ""
    name = payload.get("Browser")
    if not isinstance(name, str) or "/" not in name:
        return ""
    return name.split("/", 1)[1].strip()


def running_launch(slot):
    """The argv of the live browser main for this slot, or [] if none.

    Read from the process table because nothing else knows. CDP reports the
    build a browser is running and says nothing about how it was started, so a
    browser launched before a flag was added to _launch_argv is invisible to
    every other check here: same binary, same version, different configuration.

    Helpers are excluded by --type. They inherit much of the main's command line,
    and counting one as the main would answer about a renderer.

    Deliberately not a launch record written beside the slot at serve() time.
    That was proposed and it inverts what this is for: a file we wrote is our
    claim about a process, while ps is the process. The two can disagree, and
    the case that started this is exactly where they would have. pid 54233 was
    launched by code that predated both the flag and any such record, so it
    would have had no entry, and a check keyed on records has to read a missing
    one as either "stale" or "unknown". ps answered it with neither guess. The
    benefit offered, that a record outlives the process, is not one: a browser
    that has exited raises no dialog and needs no recycling.

    Truncation was the fragility worth checking and it is not one here. macOS ps
    returned complete argv at every length probed on 2026-08-28, up to 8,211
    bytes with the final argument intact; the slot's own line is 238. A guard
    against a limit that was not reached would be machinery for a fault nobody
    has seen, so there is none. Re-measure before assuming it holds elsewhere.
    """
    if slot not in SLOTS:
        raise ValueError("unknown browser slot")
    result = subprocess.run(["ps", "-Ao", "command="], capture_output=True,
                            text=True, timeout=20)
    port = f"--remote-debugging-port={port_for(slot)}"
    profile = f"--user-data-dir={_profile_dir(slot)}"
    for line in (result.stdout or "").splitlines():
        argv = line.split()
        if port in argv and profile in argv and not any(
                item.startswith("--type=") for item in argv):
            return argv
    return []


def missing_launch_flags(slot):
    """Flags _launch_argv sets now that this slot's browser was not given.

    A running Chrome cannot be told a new flag, so the only way to apply one is
    to start the process again. That makes this the second half of drift: the
    binary can be current while the configuration is two commits old.
    """
    argv = running_launch(slot)
    if not argv:
        return []
    wanted = [item for item in _launch_argv(slot, _profile_dir(slot))
              if item.startswith("--")]
    return [flag for flag in wanted if flag not in argv]


def _wait_until_down(slot, seconds):
    """Poll until the slot stops answering CDP. False if it never does."""
    deadline = time.monotonic() + seconds
    while True:
        if not running(slot):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def stop_slot(slot, timeout=10):
    """Shut one slot's browser down. False if one is still up afterwards.

    The answer is about the state the slot is left in, not about whether this
    call had to do anything, because that is what every caller acts on: a browser
    that had already exited is as stopped as one this took down.

    Which is why both waits are polls rather than a fixed sleep. SIGTERM asks
    Chrome to exit and Chrome takes its time about it, so reading the port the
    instant after `pkill` returns catches a browser mid-shutdown and calls it a
    refusal. Measured against a real Chrome on 2026-08-28: it reported False
    every time while the browser was in fact going down. A recycler that trusts
    that answer reports a slot it successfully retired as one it could not.
    """
    if slot not in SLOTS:
        raise ValueError("unknown browser slot")
    if not running(slot):
        return True
    # Chrome exposes no clean shutdown over the HTTP endpoint, so close the
    # tabs and let it exit, then fall back to a signal.
    for t in tabs(slot):
        _cdp(f"/json/close/{t['id']}", slot)
    if _wait_until_down(slot, min(2, timeout)):
        return True
    subprocess.run(["pkill", "-f", f"remote-debugging-port={port_for(slot)}"],
                   capture_output=True)
    return _wait_until_down(slot, timeout)


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
    # Chrome's own output goes to a per-slot file rather than DEVNULL. It is
    # detached and outlives its parent, so a pipe would have no reader and could
    # fill and block it; a file has neither problem and keeps the reason a
    # browser refused to start. Truncated at each launch, so it describes the
    # browser that is running rather than growing across restarts.
    log = _chrome_log_path(slot)
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "wb")
    try:
        subprocess.Popen(
            _launch_argv(slot, profile),
            stdout=handle, stderr=subprocess.STDOUT,
            start_new_session=True, env=toolpath.enriched_env())
    finally:
        handle.close()
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


def stop_all(slots=None):
    """Shut slot browsers down, for cloning or a clean restart.

    With no argument, every slot. One slot at a time exists for the case where
    a Chrome update has left exactly one browser behind and taking the rest down
    would cost a live run its form.
    """
    targets = list(SLOTS) if slots is None else [s for s in slots if s in SLOTS]
    stopped = []
    for slot in targets:
        if not running(slot):
            continue
        stop_slot(slot)
        stopped.append(slot)
    time.sleep(1)
    return {"stopped": stopped, "stillRunning": [s for s in targets if running(s)]}


def tabs(slot=None):
    if slot is None:
        out = []
        for name in SLOTS:
            for t in tabs(name):
                out.append({**t, "slot": name})
        return out
    return [t for t in (_cdp("/json/list", slot) or []) if t.get("type") == "page"]


def clear_slot(slot):
    """Remove page state before a slot is handed to another application.

    A stopped browser has no live form state, so it is already clear. When it
    is running, close every page and verify that no non-blank page survived.
    Callers keep the slot reserved unless ``cleared`` is true.
    """
    if slot not in SLOTS:
        return {"cleared": False, "slot": slot, "error": "unknown browser slot"}
    # Checking only ARTIFACTS/slot is not enough. If ARTIFACTS itself is a
    # symlink, resolving both parent and child makes the relationship appear
    # safe and the cleanup can delete a same-named directory outside Jobby.
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        parent_stat = ARTIFACTS.lstat()
        if (stat.S_ISLNK(parent_stat.st_mode)
                or not stat.S_ISDIR(parent_stat.st_mode)
                or parent_stat.st_uid != os.getuid()):
            raise OSError("unsafe artifact parent")
        ARTIFACTS.chmod(0o700)
    except OSError:
        return {"cleared": False, "slot": slot, "error": "artifact cleanup failed"}
    artifact_path = ARTIFACTS / slot
    if artifact_path.is_symlink():
        return {"cleared": False, "slot": slot, "error": "artifact cleanup failed"}
    artifact_root = artifact_path.resolve()
    try:
        artifacts_parent = ARTIFACTS.resolve()
        if artifact_root.parent != artifacts_parent:
            raise OSError("unsafe artifact directory")
    except OSError:
        return {"cleared": False, "slot": slot, "error": "artifact cleanup failed"}

    if not running(slot):
        try:
            if artifact_root.exists():
                shutil.rmtree(artifact_root, ignore_errors=False)
        except OSError:
            return {"cleared": False, "slot": slot, "error": "artifact cleanup failed"}
        return {"cleared": True, "slot": slot, "running": False, "closed": 0}

    pages = tabs(slot)
    for page in pages:
        target = page.get("id")
        if target:
            _cdp(f"/json/close/{target}", slot)

    remaining = []
    for _ in range(20):
        remaining = [
            page for page in tabs(slot)
            if (page.get("url") or "").strip().lower() not in {"", "about:blank"}
        ]
        if not remaining:
            break
        time.sleep(0.1)
    if remaining:
        return {
            "cleared": False,
            "slot": slot,
            "closed": len(pages) - len(remaining),
            "error": "one or more application pages could not be closed",
        }
    try:
        if artifact_root.exists():
            shutil.rmtree(artifact_root, ignore_errors=False)
    except OSError:
        return {"cleared": False, "slot": slot, "closed": len(pages),
                "error": "artifact cleanup failed"}
    return {"cleared": True, "slot": slot, "running": running(slot),
            "closed": len(pages)}


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


def open_application(url, slot, settle_timeout=20):
    """Put one slot on one posting, and leave that page as the only tab.

    The form agent has no navigation tool: the firewall removes it so a posting
    URL cannot be swapped for somewhere else mid-run. That makes opening the
    page the trusted runner's job, and nothing was doing it. Chrome came up on
    the about:blank tab that serve() uses to hold the window open, the agent
    looked at a blank page, and every run parked itself with "the current tab is
    not this application". That is what a queue of applications that never start
    looks like from outside.

    Reuse is deliberately narrow. A retry has to land back on the part-filled
    form, and an ATS rewrites its own path between steps, so a tab on the same
    origin counts as a match. A different origin never does, and the search
    never leaves this slot: a wrong match here would fill one company's answers
    into another company's form.
    """
    if slot not in SLOTS:
        return {"opened": False, "why": "unknown browser slot"}
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"opened": False, "why": "the posting is not an http address"}
    if not running(slot):
        return {"opened": False, "why": f"no browser is running for {slot}"}

    want = _key(url)
    pages = tabs(slot)
    hit = next((t for t in pages if _key(t.get("url", "")) == want), None)
    if hit is None:
        host = parsed.netloc.lower()
        hit = next((t for t in pages
                    if urllib.parse.urlparse(t.get("url", "")).netloc.lower() == host),
                   None)
    reused = hit is not None
    if hit is None:
        hit = _open_tab(url, slot)
        if hit is None or not hit.get("id"):
            return {"opened": False, "why": "Chrome would not open a tab for this posting"}

    target = hit["id"]
    # The slot is left holding this posting and nothing else. The agent is told
    # to work in the tab it is given, and a stray left over from a crashed run
    # or a cleanup that did not complete is the one thing that could put it on
    # another company's form. Closing happens only after the posting has a tab
    # of its own, so the window is never left with nothing in it.
    for page in pages:
        other = page.get("id")
        if other and other != target:
            _cdp(f"/json/close/{other}", slot)

    # No activate here, deliberately. Every apply run passes through this
    # function, and `/json/activate` raises the slot's window, so a working
    # afternoon of applications took the user's screen away from them again and
    # again -- eight slots now, so eight times as often as it needs to be.
    #
    # It was also doing nothing else. The loop above closes every other page, so
    # the posting is the only tab in the window and is therefore already the
    # selected one. The call's only remaining effect was the focus theft.
    #
    # A user-initiated focus is a different thing and stays: `focus_tab` fronts
    # Chrome because somebody clicked Open & finish and wants to be taken there.
    # The rule is that the agent works in the background and the user's own
    # clicks move the user.

    settled = None
    for _ in range(max(1, int(settle_timeout)) * 4):
        settled = next((t for t in tabs(slot) if t.get("id") == target), None)
        if settled is None:
            return {"opened": False, "why": "the posting tab closed while it was loading"}
        current = (settled.get("url") or "").strip().lower()
        if current and current != "about:blank":
            break
        time.sleep(0.25)
    else:
        return {"opened": False, "why": f"the posting did not load within {settle_timeout}s"}

    landed = urllib.parse.urlparse(settled.get("url", "")).netloc.lower()
    # Recorded, not enforced. An ATS redirect between hosts is ordinary here, so
    # refusing on a host change would stall real applications; the run log keeps
    # the address that was actually reached.
    return {"opened": True, "reused": reused, "slot": slot,
            "redirected": landed != parsed.netloc.lower(),
            "url": settled.get("url", ""), "title": (settled.get("title") or "")[:120]}


def focus(url, open_if_missing=True, slot=None):
    """Bring the tab already showing this posting to the front.

    Matching is on origin plus path, ignoring query and fragment, because an ATS
    rewrites its own URL as you move through the steps and the tab holding a
    part-filled form rarely still has the address the run started from.

    With nothing open for it, the posting is opened here rather than reported as
    a miss. The point of the button is to land the user in the browser that holds
    the profile and the logins, whether or not a run left something behind.
    """
    if slot is not None and slot not in SLOTS:
        return {"focused": False, "why": "unknown browser slot"}
    live = ([slot] if slot and running(slot)
            else [] if slot
            else [s for s in SLOTS if running(s)])
    if not live:
        return {"focused": False, "why": "no browser is running"}
    # A parked application supplies its slot, and must never drift into another
    # application's window. The all-slot search remains for interactive uses
    # that have no recorded owner.
    everything = ([{**page, "slot": slot} for page in tabs(slot)]
                  if slot else tabs())
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
    g.add_argument("--stop", action="store_true",
                   help="shut every slot's browser down, or just --slot's")
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
        print(json.dumps(stop_all([args.slot] if args.slot else None), indent=2))
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
        out = focus(args.focus, slot=args.slot)
        print(json.dumps(out, indent=2))
        return 0 if out["focused"] else 1
    if args.check:
        ok, why = available()
        out = {"ok": ok, "reason": why,
               "installedChrome": installed_version() or None,
               "slots": {slot: {"running": running(slot), "port": port_for(slot),
                                "profile": str(_profile_dir(slot)),
                                "chrome": running_version(slot) or None,
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
    out = focus(args.login, slot=slot)
    out["signInHere"] = (
        f"Sign in in the window that came up ({slot}). Then run "
        "`python3 scripts/browser.py --stop && python3 scripts/browser.py --clone-logins` "
        "to carry it to the other slots.")
    print(json.dumps(out, indent=2))
    return 0 if out["focused"] else 1


if __name__ == "__main__":
    sys.exit(main())
