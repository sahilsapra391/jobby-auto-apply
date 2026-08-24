#!/usr/bin/env python3
"""Compatibility helpers for the configured local agent provider.

Provider connection order, health, login hints, and native CLI commands live in
providers.py. Older watcher and runner callers still import this module for a
small set of auth helpers, so this file keeps that surface while delegating all
provider decisions to the registry.

  python3 scripts/agent.py --check
  python3 scripts/agent.py --check --provider openai
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers  # noqa: E402


AUTH_MARKERS = providers.AUTH_MARKERS
CACHE_SECONDS = 600


def resolve_provider(provider=None):
    """Return an explicit provider or the first healthy connected provider."""
    if provider is not None:
        if provider not in providers.PROVIDER_IDS:
            raise providers.ProviderError(f"unknown provider: {provider}")
        return provider
    selected = providers.active_provider()
    if not selected:
        raise providers.ProviderError("no connected agent provider is healthy")
    return selected


def _provider_row(status, provider):
    return next(
        (row for row in status.get("providers", []) if row.get("id") == provider),
        None,
    )


def _fresh(row, now=None):
    checked = (row or {}).get("lastCheckedAt")
    if not checked:
        return False
    try:
        value = datetime.fromisoformat(str(checked).replace("Z", "+00:00"))
    except ValueError:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return 0 <= (current - value.astimezone(timezone.utc)).total_seconds() < CACHE_SECONDS


def authenticated(force=False, provider=None):
    """Return ``(ok, reason)`` for one connected native provider CLI.

    ``force`` refreshes the native CLI health result. Without it, callers get
    the registry's last result, which keeps the watcher's frequent idle checks
    cheap. Runners use a forced check immediately before starting paid work.
    """
    try:
        selected = resolve_provider(provider)
        status = providers.public_status()
        row = _provider_row(status, selected)
        if force or not _fresh(row):
            status = providers.refresh(selected)
    except providers.ProviderError as exc:
        return False, str(exc)

    row = _provider_row(status, selected)
    if not row or not row.get("connected"):
        return False, f"{selected} is not connected"
    if row.get("healthy"):
        return True, ""

    reasons = {
        "authentication_required": "authentication is required",
        "not_installed": "the provider CLI is not installed",
        "unavailable": "the provider CLI is unavailable",
        "unknown": "the provider has not passed a health check",
    }
    return False, reasons.get(row.get("status"), "the provider is not ready")


def invalidate(provider=None):
    """Persist an authentication failure observed during real provider work.

    Kept for compatibility with existing runner callers. The registry owns the
    durable health value, so there is no separate cache file to delete or probe
    that can overwrite the stronger evidence from the failed request.
    """
    try:
        selected = resolve_provider(provider)
        return providers.mark_unhealthy(selected, "authentication_required")
    except providers.ProviderError:
        return None


def looks_like_auth_failure(text):
    low = (text or "").lower()
    return any(marker in low for marker in AUTH_MARKERS)


def blocked_reason(reason, provider=None):
    """One line for the board, naming a concrete sign-in action."""
    detail = str(reason or "authentication failed").strip()[:200]
    if provider in providers.PROVIDER_IDS:
        name = providers.PROVIDER_INFO[provider]["name"]
        return f"{name} agent cannot authenticate ({detail}). {providers.auth_hint(provider)}"
    return (
        f"No connected agent provider can run ({detail}). Open Settings to reconnect "
        "an account or restore its usage. Jobby will use the first healthy connection "
        "automatically. OpenAI uses `codex login`; Anthropic and Gemini use "
        "local API keys entered through Settings."
    )


def build_run_invocation(task_kind, prompt, provider=None, **kwargs):
    """Compatibility facade for the provider-neutral command builder."""
    selected = resolve_provider(provider)
    return providers.build_run_invocation(selected, task_kind, prompt, **kwargs)


def main():
    ap = argparse.ArgumentParser(description="Check whether a local agent provider can run.")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="run a fresh provider health check")
    ap.add_argument("--provider", choices=providers.PROVIDER_IDS)
    args = ap.parse_args()

    try:
        selected = resolve_provider(args.provider)
    except providers.ProviderError as exc:
        selected = args.provider
        ok, reason = False, str(exc)
    else:
        ok, reason = authenticated(force=args.check or args.force, provider=selected)
    print(json.dumps({
        "ok": ok,
        "provider": selected,
        "reason": reason,
        "hint": None if ok else blocked_reason(reason, selected),
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
