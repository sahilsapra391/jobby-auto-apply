#!/usr/bin/env python3
"""What the device publishes about its provider connections, and nothing else.

`provider_login.publish_status` calls `public_payload` on every watcher tick
that publishes `jobby:worker:local:status`. The payload is a device-side
contract read by v2's `/api/providers` GET, so it lives with the device rather
than with a board. `PROVIDERS` also feeds v2's display names
(`apps/web/app/providers.generated.ts`, through `scripts/sync_v2_styles.py`).
"""

PROVIDERS = (
    ("openai", "OpenAI", "Codex", "gpt-5.6-luna"),
    ("anthropic", "Anthropic", "Claude Code", "claude-sonnet-5"),
    # **Antigravity, not "Gemini CLI".** The provider is Gemini and the
    # product Jobby drives is Antigravity, whose binary is `agy` and whose logs
    # live under `~/.gemini/antigravity-cli/`. `_gemini_reason` reads that
    # directory by name, so the runner has always known which tool this is; the
    # card was the only place saying otherwise.
    ("gemini", "Gemini", "Antigravity", "gemini-3.7-flash"),
)


def _cards(status):
    if not isinstance(status, dict):
        return {}
    connections = status.get("connections")
    if isinstance(connections, dict):
        return connections
    providers = status.get("providers")
    if isinstance(providers, list):
        return {item.get("id"): item for item in providers if isinstance(item, dict)}
    if isinstance(providers, dict):
        return providers
    return {}


def public_payload(status):
    """Return only fields that are safe to publish behind the board password."""
    source = _cards(status)
    active = status.get("activeProvider") if isinstance(status, dict) else None
    has_active_field = isinstance(status, dict) and "activeProvider" in status
    ordered_source = None
    if isinstance(status, dict):
        if isinstance(status.get("connections"), dict):
            ordered_source = status["connections"]
        elif isinstance(status.get("providers"), dict):
            ordered_source = status["providers"]
    if not has_active_field and not active and ordered_source is not None:
        ordered = [
            (item.get("connectionOrder"), provider)
            for provider, item in ordered_source.items()
            if isinstance(item, dict)
            and (item.get("connected") is True or item.get("enabled") is True)
            and item.get("connectionOrder")
        ]
        active = min(ordered)[1] if ordered else None
    cards = []
    for provider, name, agent, model in PROVIDERS:
        item = source.get(provider) or {}
        enabled = bool(item.get("enabled", item.get("connected", False)))
        healthy = item.get("healthy")
        raw_state = item.get("status") or item.get("health")
        state = {
            "healthy": "connected",
            "authentication_required": "needs_auth",
            "not_installed": "missing_cli",
            "unavailable": "error",
            "unknown": "checking" if enabled else "disconnected",
            "not_connected": "disconnected",
        }.get(raw_state, raw_state) or (
            "connected" if enabled and healthy is not False else
            "needs_auth" if enabled else "disconnected"
        )
        detail = str(item.get("detail") or item.get("message") or "")[:180]
        if not detail and state == "missing_cli":
            detail = str(item.get("installHint") or "Install this provider CLI on Your Device.")[:180]
        elif not detail and state == "needs_auth":
            detail = str(item.get("authHint") or "Finish provider sign-in on Your Device.")[:180]
        cards.append({
            "id": provider,
            "name": name,
            "agent": agent,
            "model": model,
            "installed": item.get("installed"),
            "enabled": enabled,
            "connected": bool(enabled and state == "connected"),
            "active": bool(item.get("active", provider == active)),
            "status": state,
            "checkedAt": item.get("checkedAt") or item.get("lastCheckedAt"),
            "detail": detail,
        })
    checked = sorted(
        str(item.get("checkedAt") or "") for item in cards
        if item.get("checkedAt")
    )
    return {
        "activeProvider": active,
        "checkedAt": checked[-1] if checked else None,
        "providers": cards,
    }
