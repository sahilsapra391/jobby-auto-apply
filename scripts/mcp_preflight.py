#!/usr/bin/env python3
"""Ask each MCP server for its tools before an agent is spawned.

An agent that connects to a server advertising nothing does not fail. It
improvises: it reports that "only jobby tools were exposed", parks the
application on a blocker the user cannot act on, and holds one of five browser
slots while it waits. Four of the five slots were held that way on 2026-08-25,
which stops the pipeline outright, and none of the four said anything a user
could act on.

The cause is still not reproduced. The proxy answers correctly every time it is
probed by hand. This does not wait for the cause: it performs the same
handshake the agent's own client performs, against the same configuration, and
refuses to start a run whose tools are missing. A named failure with the
handshake captured beats an agent floundering at a form, and several captured
handshakes are the likeliest route to the cause.

Standard library only, and it starts nothing the run would not have started.
"""

import json
import os
import subprocess
import threading
import time

# The proxy's whole advertised surface. Requiring all thirteen rather than a
# couple is deliberate: a partial list is the same fault as an empty one, and
# an agent that discovers the gap mid-form has already opened the posting.
REQUIRED = {
    "browser": (
        "browser_snapshot", "browser_find", "browser_click", "browser_hover",
        "browser_fill_form", "browser_type", "browser_press_key",
        "browser_select_option", "browser_handle_dialog", "browser_file_upload",
        "browser_attach_file", "browser_tabs", "browser_wait_for",
    ),
    "jobby": (
        "set_phase", "resolve_answers", "experience_plan", "salary_midpoint",
        "park_application", "pre_submit", "record_completed",
    ),
}

HANDSHAKE_TIMEOUT_SECONDS = 45
MAX_CAPTURE = 2000
PROTOCOL_VERSION = "2025-06-18"


class PreflightError(RuntimeError):
    """One server could not be shown to advertise the tools a run needs."""

    def __init__(self, server, reason, capture=""):
        self.server = server
        self.reason = reason
        self.capture = capture[:MAX_CAPTURE]
        super().__init__(f"{server}: {reason}")


def _read_lines(stream, sink, stop):
    try:
        for line in stream:
            sink.append(line.rstrip("\n"))
            if stop.is_set():
                break
    except (OSError, ValueError):
        pass


def tool_names(name, config, timeout=HANDSHAKE_TIMEOUT_SECONDS):
    """Spawn one configured server and return the tool names it advertises.

    Speaks the same three messages a client speaks, in the same order. The
    server is killed as soon as it has answered; nothing here is left running
    for the agent to inherit.
    """
    argv = [config.get("command") or ""] + list(config.get("args") or [])
    if not argv[0]:
        raise PreflightError(name, "the server has no command configured")
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (config.get("env") or {}).items()})

    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
            start_new_session=True,
        )
    except OSError as exc:
        raise PreflightError(name, "the server could not be started", str(exc))

    out, err, stop = [], [], threading.Event()
    threading.Thread(target=_read_lines, args=(process.stdout, out, stop),
                     daemon=True).start()
    threading.Thread(target=_read_lines, args=(process.stderr, err, stop),
                     daemon=True).start()

    def send(message):
        try:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise PreflightError(
                name, "the server closed its input during the handshake",
                "\n".join(err[-8:]) or str(exc))

    def answer(request_id, deadline):
        while time.monotonic() < deadline:
            for line in list(out):
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message
            if process.poll() is not None and not out:
                raise PreflightError(
                    name, f"the server exited {process.returncode} without answering",
                    "\n".join(err[-8:]))
            time.sleep(0.1)
        return None

    try:
        deadline = time.monotonic() + timeout
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "jobby-preflight", "version": "1"}}})
        if answer(1, deadline) is None:
            raise PreflightError(name, "the server did not answer initialize",
                                 "\n".join((out + err)[-8:]))
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = answer(2, deadline)
        if listed is None:
            raise PreflightError(name, "the server did not answer tools/list",
                                 "\n".join((out + err)[-8:]))
        tools = (listed.get("result") or {}).get("tools")
        if not isinstance(tools, list):
            raise PreflightError(
                name, "the server answered tools/list without a tool list",
                json.dumps(listed)[:MAX_CAPTURE])
        names = [
            tool.get("name") for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        ]
        if not names:
            # The exact shape the agents hit. Connected, answering, advertising
            # nothing, and every client reports it as a healthy server.
            raise PreflightError(
                name, "the server advertised no tools",
                json.dumps(listed)[:MAX_CAPTURE])
        return names
    finally:
        stop.set()
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except (OSError, ValueError):
                pass


def check(servers, required=None, timeout=HANDSHAKE_TIMEOUT_SECONDS):
    """Raise unless every configured server advertises the tools a run needs."""
    wanted = REQUIRED if required is None else required
    observed = {}
    for name, config in sorted((servers or {}).items()):
        names = tool_names(name, config, timeout=timeout)
        observed[name] = sorted(names)
        missing = sorted(set(wanted.get(name) or ()) - set(names))
        if missing:
            raise PreflightError(
                name,
                f"the server is missing {len(missing)} required tools",
                "missing: " + ", ".join(missing)
                + " | advertised: " + ", ".join(sorted(names)),
            )
    for name in sorted(wanted):
        if name not in observed:
            raise PreflightError(name, "the server was not configured for this run")
    return observed
