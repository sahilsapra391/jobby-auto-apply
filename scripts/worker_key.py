#!/usr/bin/env python3
"""Manage private settings used by Jobby's local workers."""

import argparse
import base64
import binascii
import contextlib
import errno
import fcntl
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import urllib.parse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import toolpath  # noqa: E402


KEY_NAME = "JOBBY_WORKER_KEY"
HEALTHCHECKS_NAME = "HEALTHCHECKS_URL"
WORKER_VARIABLES = (
    "CF_ACCOUNT_ID",
    "CF_KV_NAMESPACE_ID",
    "CF_API_TOKEN",
    KEY_NAME,
)
KEY_ASSIGNMENT = re.compile(r"^[ \t]*JOBBY_WORKER_KEY[ \t]*=(.*)$")
HEALTHCHECKS_ASSIGNMENT = re.compile(r"^[ \t]*HEALTHCHECKS_URL[ \t]*=(.*)$")
HEALTHCHECKS_PATH = re.compile(
    r"^/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class WorkerKeyError(RuntimeError):
    """A safe, fixed-code failure suitable for CLI JSON output."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _decoded_key(value):
    if not isinstance(value, str):
        return None
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None
    canonical = base64.b64encode(decoded).decode("ascii")
    return decoded if len(decoded) == 32 and value == canonical else None


def _new_key():
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _secure_directory(directory):
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise WorkerKeyError("config_directory_unavailable") from exc

    try:
        info = os.lstat(directory)
    except OSError as exc:
        raise WorkerKeyError("config_directory_unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise WorkerKeyError("unsafe_config_directory")
    try:
        os.chmod(directory, 0o700, follow_symlinks=False)
    except OSError as exc:
        raise WorkerKeyError("config_directory_permissions_failed") from exc


def _open_regular(path, flags, mode=0o600):
    safe_flags = flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, safe_flags, mode)
    except OSError as exc:
        raise WorkerKeyError("unsafe_file_target") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise WorkerKeyError("unsafe_file_target")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


@contextlib.contextmanager
def _config_lock(config_path):
    lock_path = config_path.with_name(f".{config_path.name}.lock")
    descriptor = _open_regular(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise WorkerKeyError("config_lock_failed") from exc
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_config(path):
    if not path.exists():
        return ""
    descriptor = _open_regular(path, os.O_RDONLY)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as stream:
            descriptor = None
            return stream.read()
    except (OSError, UnicodeError) as exc:
        raise WorkerKeyError("config_read_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _split_ending(line):
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1:]
    return line, ""


def _unquoted_value(raw):
    return raw.strip().strip('"').strip("'")


def _render_config(content):
    lines = content.splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        body, ending = _split_ending(line)
        match = KEY_ASSIGNMENT.match(body)
        if match:
            matches.append((index, _unquoted_value(match.group(1)), ending))

    retained = matches[-1][1] if matches else ""
    valid = _decoded_key(retained) is not None
    key = retained if valid else _new_key()

    if matches:
        first_index = matches[0][0]
        matched_indexes = {item[0] for item in matches}
        output = []
        for index, line in enumerate(lines):
            if index == first_index:
                output.append(f"{KEY_NAME}={key}{matches[0][2]}")
            elif index not in matched_indexes:
                output.append(line)
        rendered = "".join(output)
        state = "retained" if valid and len(matches) == 1 else (
            "normalized" if valid else "replaced"
        )
    else:
        separator = "" if not content or content.endswith(("\n", "\r")) else "\n"
        rendered = f"{content}{separator}{KEY_NAME}={key}\n"
        state = "created"
    return rendered, state


def _fsync_directory(directory):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)):
            raise
    finally:
        os.close(descriptor)


def _atomic_write(path, content):
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=str(path.parent)
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600, follow_symlinks=False)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise WorkerKeyError("atomic_write_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def ensure_worker_key(config_path=None):
    """Ensure the local config contains one valid shared key assignment."""
    path = Path(config_path) if config_path is not None else toolpath.LOCAL_CONFIG
    if not path.is_absolute():
        raise WorkerKeyError("config_path_not_absolute")
    _secure_directory(path.parent)

    with _config_lock(path):
        if path.is_symlink():
            raise WorkerKeyError("unsafe_file_target")
        content = _read_config(path)
        rendered, state = _render_config(content)
        if rendered != content or not path.exists():
            _atomic_write(path, rendered)
        else:
            try:
                os.chmod(path, 0o600, follow_symlinks=False)
            except OSError as exc:
                raise WorkerKeyError("config_permissions_failed") from exc

    return {
        "ok": True,
        "action": "ensure",
        "path": str(path),
        "mode": "0600",
        "state": state,
    }


def _validated_healthchecks_url(value):
    if not isinstance(value, str) or any(char in value for char in ("\0", "\r", "\n")):
        raise WorkerKeyError("invalid_healthchecks_url")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise WorkerKeyError("invalid_healthchecks_url") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "hc-ping.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or HEALTHCHECKS_PATH.fullmatch(parsed.path) is None
    ):
        raise WorkerKeyError("invalid_healthchecks_url")
    return value


def _render_healthchecks(content, value):
    lines = content.splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        body, ending = _split_ending(line)
        if HEALTHCHECKS_ASSIGNMENT.match(body):
            matches.append((index, ending))

    if matches:
        first_index = matches[0][0]
        matched_indexes = {item[0] for item in matches}
        output = []
        for index, line in enumerate(lines):
            if index == first_index:
                output.append(f"{HEALTHCHECKS_NAME}={value}{matches[0][1]}")
            elif index not in matched_indexes:
                output.append(line)
        return "".join(output), "updated"

    separator = "" if not content or content.endswith(("\n", "\r")) else "\n"
    return f"{content}{separator}{HEALTHCHECKS_NAME}={value}\n", "created"


def set_healthchecks_url(value, config_path=None):
    """Atomically replace the private Healthchecks URL without printing it."""
    url = _validated_healthchecks_url(value)
    path = Path(config_path) if config_path is not None else toolpath.LOCAL_CONFIG
    if not path.is_absolute():
        raise WorkerKeyError("config_path_not_absolute")
    _secure_directory(path.parent)

    with _config_lock(path):
        if path.is_symlink():
            raise WorkerKeyError("unsafe_file_target")
        content = _read_config(path)
        rendered, state = _render_healthchecks(content, url)
        if rendered != content or not path.exists():
            _atomic_write(path, rendered)
        else:
            try:
                os.chmod(path, 0o600, follow_symlinks=False)
            except OSError as exc:
                raise WorkerKeyError("config_permissions_failed") from exc

    return {
        "ok": True,
        "action": "set-healthchecks",
        "path": str(path),
        "mode": "0600",
        "state": state,
    }


def _validate_stage_settings(values):
    missing = [name for name in WORKER_VARIABLES if not values.get(name)]
    if missing:
        raise WorkerKeyError("missing_worker_settings", missing)
    for name in WORKER_VARIABLES:
        value = values[name]
        if not isinstance(value, str) or any(char in value for char in ("\0", "\r", "\n")):
            raise WorkerKeyError("unsafe_worker_setting", [name])
    if _decoded_key(values[KEY_NAME]) is None:
        raise WorkerKeyError("invalid_worker_key")


def stage_worker_env(output_path, config_path=None):
    """Stage the four worker settings in a private, atomic environment file."""
    output = Path(output_path)
    if not output.is_absolute():
        raise WorkerKeyError("output_path_not_absolute")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise WorkerKeyError("output_directory_unavailable")

    config = Path(config_path) if config_path is not None else toolpath.LOCAL_CONFIG
    if os.path.abspath(output) == os.path.abspath(config):
        raise WorkerKeyError("output_overlaps_local_config")
    if output.is_symlink():
        raise WorkerKeyError("unsafe_file_target")

    ensure_worker_key(config)
    values = toolpath.settings()
    _validate_stage_settings(values)
    content = "".join(f"{name}={values[name]}\n" for name in WORKER_VARIABLES)
    _atomic_write(output, content)

    return {
        "ok": True,
        "action": "stage",
        "path": str(output),
        "mode": "0600",
        "variables": list(WORKER_VARIABLES),
    }


def _safe_error(action, exc):
    result = {"ok": False, "action": action, "error": exc.code}
    if exc.detail:
        result["fields"] = list(exc.detail)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ensure", help="ensure the local shared key exists")
    stage = commands.add_parser("stage", help="stage the retired remote worker environment")
    stage.add_argument("output", help="absolute path for the staged environment file")
    healthchecks = commands.add_parser(
        "set-healthchecks", help="set the private Healthchecks ping URL"
    )
    healthchecks.add_argument("url", help="https://hc-ping.com/<uuid>")
    args = parser.parse_args(argv)

    try:
        if args.command == "ensure":
            result = ensure_worker_key()
        elif args.command == "stage":
            result = stage_worker_env(args.output)
        else:
            result = set_healthchecks_url(args.url)
    except WorkerKeyError as exc:
        print(json.dumps(_safe_error(args.command, exc), sort_keys=True))
        return 2
    except OSError as exc:
        result = {
            "ok": False,
            "action": args.command,
            "error": "filesystem_error",
            "errno": exc.errno,
        }
        print(json.dumps(result, sort_keys=True))
        return 2

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
