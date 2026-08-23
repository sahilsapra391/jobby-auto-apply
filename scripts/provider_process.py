#!/usr/bin/env python3
"""Bounded subprocess handling for local provider CLIs."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    overflow_stream: str | None = None


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | Path,
    env: Mapping[str, str],
    stdin_payload: str | None,
    timeout_seconds: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> BoundedProcessResult:
    """Run one CLI while capping both output streams during execution."""
    if timeout_seconds <= 0 or max_stdout_bytes < 1 or max_stderr_bytes < 1:
        raise ValueError("provider process limits must be positive")

    process = subprocess.Popen(
        [str(value) for value in argv],
        cwd=str(cwd),
        env={str(key): str(value) for key, value in env.items()},
        stdin=subprocess.PIPE if stdin_payload is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    overflow_stream: list[str | None] = [None]
    read_failed = threading.Event()

    def drain(stream, target: bytearray, limit: int, label: str) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024) if stream is not None else b""
                if not chunk:
                    break
                remaining = limit - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    if overflow_stream[0] is None:
                        overflow_stream[0] = label
                    overflow.set()
        except OSError:
            read_failed.set()
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    readers = [
        threading.Thread(
            target=drain,
            args=(process.stdout, stdout, max_stdout_bytes, "stdout"),
            name="jobby-provider-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, stderr, max_stderr_bytes, "stderr"),
            name="jobby-provider-stderr",
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    def feed() -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.write((stdin_payload or "").encode("utf-8"))
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    writer = threading.Thread(target=feed, name="jobby-provider-stdin", daemon=True)
    writer.start()

    timed_out = False
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        if overflow.is_set() or read_failed.is_set():
            _terminate_group(process)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _terminate_group(process)
            break
        overflow.wait(min(0.1, remaining))

    for reader in readers:
        reader.join(timeout=3)
    writer.join(timeout=3)
    if any(reader.is_alive() for reader in readers) or read_failed.is_set():
        _terminate_group(process)
        raise OSError("provider output streams could not be read")

    if process.returncode is None:
        _terminate_group(process)
    return BoundedProcessResult(
        returncode=int(process.returncode if process.returncode is not None else 127),
        stdout=bytes(stdout).decode("utf-8", errors="replace"),
        stderr=bytes(stderr).decode("utf-8", errors="replace"),
        timed_out=timed_out,
        overflow_stream=overflow_stream[0],
    )


def looks_like_provider_auth_failure(provider_id: str, stderr: str) -> bool:
    """Recognize provider-account failures without trusting page text."""
    low = (stderr or "").lower()
    if not low:
        return False
    strong = (
        "authentication_required",
        "authentication required for this account",
        "invalid api key",
        "incorrect api key",
        "api key not valid",
        "invalid_grant",
        "oauth token has expired",
        "access token has expired",
        "refresh token has expired",
    )
    if any(marker in low for marker in strong):
        return True

    provider_markers = {
        "openai": (
            "codex login",
            "run `codex login`",
            "run 'codex login'",
            "not logged in to codex",
        ),
        "anthropic": (
            "claude auth login --claudeai",
            "claude auth login",
            "run /login to authenticate",
            "not logged in to claude",
        ),
        "gemini": (
            "choose sign in with google",
            "gemini authentication required",
            "not logged in to gemini",
            "select an authentication method",
        ),
    }
    if any(marker in low for marker in provider_markers.get(provider_id, ())):
        return True

    service_markers = {
        "openai": ("api.openai.com", "chatgpt.com/backend-api"),
        "anthropic": ("api.anthropic.com",),
        "gemini": (
            "generativelanguage.googleapis.com",
            "cloudcode-pa.googleapis.com",
        ),
    }
    return "401" in low and any(
        service in low for service in service_markers.get(provider_id, ())
    )
