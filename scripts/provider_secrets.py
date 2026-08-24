#!/usr/bin/env python3
"""Owner-only local API keys for provider CLIs.

These files never enter the provider registry, dashboard payload, Cloudflare KV,
or Vercel. A key is added only to the environment of its matching local CLI.
"""

import os
import stat
import tempfile
from pathlib import Path


SECRET_DIR = Path.home() / ".job-application" / "provider-secrets"
PROVIDERS = {"anthropic", "gemini"}
MAX_SECRET_LENGTH = 4096


class ProviderSecretError(ValueError):
    """A provider key or its storage location is unsafe."""


def _provider(provider_id):
    if provider_id not in PROVIDERS:
        raise ProviderSecretError("provider does not use a Jobby-managed API key")
    return provider_id


def _path(provider_id, root):
    return Path(root).expanduser() / _provider(provider_id)


def _validate(secret):
    value = str(secret or "").strip()
    if not value or len(value) > MAX_SECRET_LENGTH or any(ch.isspace() for ch in value):
        raise ProviderSecretError("API key is empty or malformed")
    return value


def store(provider_id, secret, root=SECRET_DIR):
    """Atomically save one API key without ever returning or printing it."""
    value = _validate(secret)
    target = _path(provider_id, root)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.is_symlink():
        raise ProviderSecretError("provider secret directory cannot be a symlink")
    os.chmod(target.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)


def load(provider_id, root=SECRET_DIR):
    """Read one private key, or return None when it has not been connected."""
    target = _path(provider_id, root)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProviderSecretError("provider key must be a regular file")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ProviderSecretError("provider key permissions must be 0600")
    return _validate(target.read_text(encoding="utf-8"))


def delete(provider_id, root=SECRET_DIR):
    target = _path(provider_id, root)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProviderSecretError("refusing to remove an unsafe provider key path")
    target.unlink()
    return True
