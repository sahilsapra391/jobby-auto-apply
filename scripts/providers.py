#!/usr/bin/env python3
"""Local account registry and command builders for agent CLI providers.

Provider credentials belong to each vendor's CLI. This module records only
which CLIs the user connected to Jobby, the order of those connections, and a
small health result. It never reads or copies OAuth tokens.

Connection order is deliberate state. New work uses the first connected account
that passes a live health and exact-model check. An in-flight provider process
keeps its provider, while the next stage or retry can use the next healthy one.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402
import provider_secrets  # noqa: E402


REPO = Path(__file__).resolve().parents[1]
STATE_DIR = Path.home() / ".job-application"
STATE_FILE = STATE_DIR / "providers.json"

PROVIDER_IDS = ("openai", "anthropic", "gemini")
TASK_KINDS = ("documents", "apply", "scan")

# Which provider may do which task, and it is a capability matrix rather than a
# provider list because the two are not the same question. Document generation
# is provider-agnostic. Form filling and sourcing put an agent on a browser or
# the open web, and need a fence that has been shown to bind by negative test,
# not one that has merely been configured.
#
# Every entry here is a measurement from scripts/fence_probe.py, which tells a
# real agent to write outside its workspace and run a shell command and then
# asks the filesystem what happened. Re-run it before changing a row.
#
# gemini is documents-only. The Antigravity CLI advertises 57 built-in tools
# including run_command, write_to_file and execute_browser_javascript, with no
# allowlist, no per-run MCP scoping, and credentials in the Keychain so an
# isolated HOME authenticates as nobody. Its documents fence is the absence of
# --dangerously-skip-permissions, which works only because a documents run
# needs no tool at all. A form agent needs its browser tools, so it needs that
# flag, and the flag hands it a shell as well.
TASK_CAPABILITIES = {
    # documents is toolless on every provider (see DOCUMENTS_TOOLLESS), so it
    # needs no fence probe: there is nothing to escape with.
    #
    # apply and scan put an agent on a browser or the open web and need a fence
    # PROVEN to bind by scripts/fence_probe.py. Inconclusive means no.
    #
    # openai: probed bound on all three once its quota returned, 2026-08-26.
    # It was documents-only before that, not because of the quota error but
    # because a probe that never reached the model proves nothing: three clean
    # canaries against "You've hit your usage limit" is an absence of evidence.
    # Restored on a `bound` result, which is the only thing that restores one.
    "openai": frozenset(TASK_KINDS),
    # anthropic: probed bound on all three, 2026-08-25.
    "anthropic": frozenset(TASK_KINDS),
    # gemini: documents only, and not for want of probing. The Antigravity CLI
    # advertises 57 built-in tools with no allowlist, no per-run MCP scoping,
    # and credentials in the Keychain so an isolated HOME authenticates as
    # nobody. A form agent needs the flag that reaches its browser tools, and
    # that flag hands it run_command too.
    "gemini": frozenset(("documents",)),
}


# What "no tools" is spelled as, per CLI. Documents is one turn of prompt in
# and JSON out: it needs no tool on any provider, so it gets none on any
# provider, and the capability question disappears because there is nothing to
# escape with.
#
# This began as a Gemini fix. Dropping --dangerously-skip-permissions closed a
# real escape, a documents run had executed a shell command and left the token
# on disk. That was never a Gemini fact, it was a documents fact, and the other
# two are held to it here rather than by anyone remembering.
DOCUMENTS_TOOLLESS = {
    "anthropic": {
        "required_pairs": (("--tools", ""), ("--allowedTools", "")),
        "required_flags": ("--strict-mcp-config",),
        "forbidden_flags": (),
    },
    "openai": {
        "required_pairs": (("--sandbox", "read-only"),),
        "required_flags": ("--ignore-user-config", "--ignore-rules"),
        # Every executor Codex offers, off by name.
        "required_disabled": (
            "shell_tool", "unified_exec", "apply_patch_freeform", "code_mode",
            "computer_use", "browser_use", "multi_agent",
        ),
        "forbidden_flags": (),
    },
    "gemini": {
        "required_pairs": (),
        "required_flags": (),
        # The whole fence. agy has no allowlist to put beside it, so with this
        # present all 57 built-in tools are auto-approved, run_command included.
        "forbidden_flags": ("--dangerously-skip-permissions",),
    },
}


def assert_documents_toolless(provider_id: str, argv: list[str]) -> None:
    """Refuse to hand a documents run anything it could act on the world with.

    Structural rather than advisory: it runs on every build, so a future edit
    that reintroduces a tool to a documents invocation fails here instead of
    in production.
    """
    rules = DOCUMENTS_TOOLLESS.get(provider_id)
    if rules is None:
        raise ProviderError(
            f"{provider_id} has no toolless document contract")
    for flag, value in rules["required_pairs"]:
        if flag not in argv or argv[argv.index(flag) + 1] != value:
            raise ProviderError(
                f"{provider_id} document run is missing {flag} {value!r}")
    for flag in rules["required_flags"]:
        if flag not in argv:
            raise ProviderError(
                f"{provider_id} document run is missing {flag}")
    for flag in rules["forbidden_flags"]:
        if flag in argv:
            raise ProviderError(
                f"{provider_id} document run must not pass {flag}")
    disabled = {
        argv[index + 1]
        for index, value in enumerate(argv[:-1]) if value == "--disable"
    }
    for feature in rules.get("required_disabled", ()):
        if feature not in disabled:
            raise ProviderError(
                f"{provider_id} document run must disable {feature}")


def supports(provider_id: str, task_kind: str) -> bool:
    """Whether this provider is fence-proven for this task."""
    return task_kind in TASK_CAPABILITIES.get(provider_id, frozenset())


def capable_providers(task_kind: str) -> list[str]:
    """Connected providers allowed to do this task, in work order."""
    return [p for p in work_order() if supports(p, task_kind)]
MODEL_IDS = {
    "openai": "gpt-5.6-luna",
    "anthropic": "claude-sonnet-5",
    "gemini": "gemini-3.7-flash-medium",
}
STATE_VERSION = 1
PROBE_TIMEOUT_SECONDS = 30
# Above every caller's own bound, so agy never self-kills a turn the runner
# still considers live. agy's own default is five minutes.
GEMINI_PRINT_TIMEOUT = "180m"


def _gemini_stream_prompt(prompt: str) -> str:
    """One NDJSON turn for agy's stream-json input, so the prompt stays off argv."""
    return json.dumps({
        "event": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": prompt}],
        },
    }) + "\n"
MODEL_PROBE_TIMEOUT_SECONDS = 120
CONNECTION_KEY_PATTERN = re.compile(
    r"^(?:0-[0-9]{20}|1-[0-9]{13}-[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)

HEALTH_VALUES = {
    "healthy",
    "authentication_required",
    "unavailable",
    "not_installed",
    "unknown",
}

PROVIDER_INFO = {
    "openai": {
        "name": "OpenAI",
        "cli": "codex",
        "authHint": "Run `codex login` on Your Device and finish the ChatGPT sign-in in your browser.",
        "installHint": "Install the Codex CLI, then run `codex login`.",
    },
    "anthropic": {
        "name": "Anthropic",
        "cli": "claude",
        "authHint": "Create an Anthropic Console API key, then paste it into the private terminal prompt on Your Device.",
        "installHint": "Install Claude Code, then connect an Anthropic Console API key.",
    },
    "gemini": {
        "name": "Gemini",
        "cli": "gemini",
        "authHint": "Create a Gemini API key in Google AI Studio, then paste it into the private terminal prompt on Your Device.",
        "installHint": "Install Gemini CLI, then connect a Google AI Studio API key.",
    },
}

# These values can make a CLI bypass its cached account and use a raw secret.
# Remove them from agent children so account connection always uses the native
# CLI credential store. HOME remains available because the CLIs keep login data
# there.
NATIVE_CREDENTIAL_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "CF_ACCOUNT_ID",
    "CF_API_TOKEN",
    "CF_KV_NAMESPACE_ID",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CODEX_API_KEY",
    "DASHBOARD_PASSWORD",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HEALTHCHECKS_URL",
    "JOBBY_WORKER_KEY",
    "OPENAI_API_KEY",
    "GEMINI_CLI_HOME",
}

AUTH_MARKERS = (
    "authenticate",
    "authentication",
    "credential",
    "invalid api key",
    "login required",
    "log in",
    "not logged in",
    "oauth",
    "sign in",
    "unauthorized",
    "401",
)

CLAUDE_BUILTIN_TOOLS = {
    "documents": "",
    "apply": "",
    "scan": "WebSearch,WebFetch",
}
CLAUDE_ALLOWED_TOOLS = {
    "documents": "",
    "apply": "mcp__browser__*,mcp__jobby__*",
    "scan": "WebSearch,WebFetch",
}

CODEX_DISABLED_FEATURES = (
    "apply_patch_freeform",
    "apps",
    "artifact",
    "auth_elicitation",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "code_mode",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "multi_agent",
    "plugins",
    "remote_plugin",
    "shell_tool",
    "skill_mcp_dependency_install",
    "skill_search",
    "tool_call_mcp_elicitation",
    "tool_suggest",
    "unified_exec",
    "view_image",
    "workspace_dependencies",
)

NATIVE_ENV_ALLOWLIST = {
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "NO_PROXY",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TMPDIR",
    "TZ",
    "USER",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


class ProviderError(RuntimeError):
    """Base error for invalid provider operations."""


class ProviderStateError(ProviderError):
    """Raised when providers.json cannot be read safely."""


class GeminiModelMismatch(ProviderError):
    """Gemini CLI did not prove that every request used the pinned model."""


@dataclass(frozen=True)
class ProbeResult:
    provider_id: str
    installed: bool
    authenticated: bool
    health: str
    checked_at: str
    message: str = ""


@dataclass(frozen=True)
class RunInvocation:
    provider_id: str
    task_kind: str
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    stdin_payload: str | None = None
    credential_sync: tuple[tuple[Path, Path], ...] = ()


def _require_provider(provider_id: str) -> str:
    if provider_id not in PROVIDER_IDS:
        raise ProviderError(f"unknown provider: {provider_id}")
    return provider_id


def _require_task(task_kind: str) -> str:
    if task_kind not in TASK_KINDS:
        raise ProviderError(f"unknown agent task kind: {task_kind}")
    return task_kind


def _iso_now(clock: Callable[[], datetime] | None = None) -> str:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def native_cli_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment that can use cached CLI login data but not raw API secrets."""
    source = toolpath.agent_env(dict(base) if base is not None else None)
    env = {
        key: value
        for key, value in source.items()
        if key in NATIVE_ENV_ALLOWLIST and value
    }
    node_options = source.get("NODE_OPTIONS", "")
    if re.fullmatch(r"--max-old-space-size=[0-9]{2,5}", node_options):
        env["NODE_OPTIONS"] = node_options
    arena_limit = source.get("MALLOC_ARENA_MAX", "")
    if re.fullmatch(r"[1-9][0-9]{0,2}", arena_limit):
        env["MALLOC_ARENA_MAX"] = arena_limit
    for key in NATIVE_CREDENTIAL_ENV_KEYS:
        env.pop(key, None)
    return env


def provider_cli_env(
    provider_id: str, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Give a child only its selected provider's locally stored credential."""
    provider_id = _require_provider(provider_id)
    env = native_cli_env(base)
    key_names = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
    if provider_id not in key_names:
        return env
    try:
        secret = provider_secrets.load(provider_id)
    except provider_secrets.ProviderSecretError as exc:
        raise ProviderStateError("the local provider key is unsafe") from exc
    if secret:
        env[key_names[provider_id]] = secret
    return env


def has_provider_secret(provider_id: str) -> bool:
    if provider_id not in provider_secrets.PROVIDERS:
        return False
    try:
        return provider_secrets.load(provider_id) is not None
    except provider_secrets.ProviderSecretError:
        return False


def gemini_hardened_settings(task_kind: str) -> dict[str, Any]:
    """Highest-precedence Gemini settings shared by every task surface."""
    task_kind = _require_task(task_kind)
    core = ["google_web_search", "web_fetch"] if task_kind == "scan" else []
    model = MODEL_IDS["gemini"]
    return {
        "general": {
            "enableAutoUpdate": False,
            "enableAutoUpdateNotification": False,
        },
        "security": {"folderTrust": {"enabled": False}},
        "telemetry": {"enabled": False, "logPrompts": False},
        "privacy": {"usageStatisticsEnabled": False},
        "tools": {
            "core": core,
            "discoveryCommand": "",
            "callCommand": "",
            "exclude": [
                "run_shell_command", "read_file", "write_file", "replace",
                "glob", "grep_search", "list_directory", "save_memory",
            ],
        },
        "mcp": {"allowed": [], "excluded": []},
        "skills": {"enabled": False},
        "hooksConfig": {"enabled": False},
        "experimental": {
            "enableAgents": False,
            "extensionManagement": False,
            "extensionConfig": False,
            "extensionRegistry": False,
            "extensionReloading": False,
            "dynamicModelConfiguration": True,
        },
        "modelConfigs": {
            "customAliases": {
                model: {
                    "extends": "chat-base-3",
                    "modelConfig": {"model": model},
                },
            },
            "customOverrides": [{
                "match": {"overrideScope": "core"},
                "modelConfig": {"model": model},
            }],
            "modelDefinitions": {
                model: {
                    "displayName": "Gemini 3.7 Flash",
                    "tier": "flash",
                    "family": "gemini-3",
                    "isPreview": False,
                    "isVisible": False,
                    "features": {"thinking": True, "multimodalToolUse": True},
                },
            },
            "modelIdResolutions": {model: {"default": model}},
        },
        "admin": {
            "secureModeEnabled": True,
            "extensions": {"enabled": False},
            "skills": {"enabled": False},
            "mcp": {"enabled": False},
        },
        "context": {
            "fileName": [],
            "includeDirectoryTree": False,
            "discoveryMaxDirs": 0,
            "memoryBoundaryMarkers": [],
            "includeDirectories": [],
            "loadMemoryFromIncludeDirectories": False,
        },
    }


def verify_gemini_served_model(output: str, *, stream: bool) -> str:
    """Require Antigravity telemetry for exactly the pinned model.

    The shape changed with the CLI. The old Gemini CLI reported a stats.models
    map; agy announces the model once in an init event and closes with a result
    event, so both have to be present and agree. Still fails closed: no
    telemetry, a different model, or a run that did not finish successfully all
    raise rather than pass quietly, because the whole point is to catch a
    silent remap onto a model nobody chose.
    """
    if not isinstance(output, str) or not output.strip():
        raise GeminiModelMismatch("Gemini output has no model telemetry")
    expected = MODEL_IDS["gemini"]
    try:
        values = [json.loads(line) for line in output.splitlines() if line.strip()]
    except (json.JSONDecodeError, RecursionError) as exc:
        raise GeminiModelMismatch("Gemini model telemetry is not valid JSON") from exc
    if not values or not all(isinstance(value, dict) for value in values):
        raise GeminiModelMismatch("Gemini stream is invalid")

    # The model is announced once, at the top, and only in the stream format.
    inits = [v for v in values if v.get("event") == "init"]
    if len(inits) != 1:
        raise GeminiModelMismatch("Gemini stream has no unique init record")
    served = (inits[0].get("init") or {}).get("model")
    if served != expected:
        raise GeminiModelMismatch(
            f"Gemini served {served!r}, not the pinned {expected!r}")

    terminal = [v for v in values if v.get("event") == "result"]
    if len(terminal) != 1 or values[-1] is not terminal[0]:
        raise GeminiModelMismatch("Gemini stream has no unique terminal result")
    outcome = terminal[0].get("result")
    if not isinstance(outcome, dict) or outcome.get("status") != "SUCCESS":
        raise GeminiModelMismatch("Gemini stream did not finish successfully")

    usage = outcome.get("usage")
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    if isinstance(total, bool) or not isinstance(total, int) or total < 1:
        raise GeminiModelMismatch("Gemini model telemetry has no completed request")
    return expected


def auth_hint(provider_id: str) -> str:
    return str(PROVIDER_INFO[_require_provider(provider_id)]["authHint"])


def login_invocation(
    provider_id: str,
    *,
    binary_resolver: Callable[[str, bool], str | None] | None = None,
) -> list[str]:
    """Interactive login command for a local terminal or a remote worker."""
    provider_id = _require_provider(provider_id)
    resolver = binary_resolver or toolpath.agent_binary
    binary = resolver(provider_id, True)
    if not binary:
        raise ProviderError(f"{PROVIDER_INFO[provider_id]['cli']} is not installed")
    # Each CLI signs in against the account the user already has, so no API key
    # is handled here at all. The Anthropic CLI reports a claude.ai login and a
    # Max subscription through `auth status`, which is the same shape as
    # `codex login status`, so nothing about the probe path changes.
    #
    # Gemini has no auth subcommand: it authenticates through its interactive
    # session, where /auth opens the Google sign-in. Launching it bare is the
    # login flow.
    commands = {
        "openai": [str(binary), "login"],
        "anthropic": [str(binary), "auth", "login"],
        "gemini": [str(binary)],
    }
    return commands[provider_id]


def _probe_argv(provider_id: str, binary: str) -> list[str]:
    commands = {
        "openai": [binary, "login", "status"],
        "anthropic": [binary, "auth", "status"],
        # Antigravity has no status subcommand either. A minimal noninteractive
        # request proves the cached Google login reaches the service, and
        # stream-json is what carries the served model: its plain json output
        # reports the answer and the token usage but never says which model
        # produced them, so the pin could not be checked from it.
        "gemini": [
            binary, "-p", "Reply with exactly: OK",
            "--model", MODEL_IDS["gemini"],
            "--output-format", "stream-json",
        ],
    }
    return commands[provider_id]


def _model_probe_argv(provider_id: str, binary: str) -> list[str]:
    if provider_id == "openai":
        argv = [
            binary, "--strict-config", "--ask-for-approval", "never",
            "--model", MODEL_IDS[provider_id],
        ]
        for feature in CODEX_DISABLED_FEATURES:
            argv.extend(["--disable", feature])
        argv.extend([
            "exec", "--ignore-user-config", "--ignore-rules",
            "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "-",
        ])
        return argv
    if provider_id == "anthropic":
        return [
            binary, "-p", "--model", MODEL_IDS[provider_id],
            "--no-session-persistence", "--disable-slash-commands", "--no-chrome",
            "--setting-sources", "project", "--tools", "",
            "--permission-mode", "dontAsk", "--mcp-config", '{"mcpServers":{}}',
            "--strict-mcp-config",
        ]
    return [
        binary, "--model", MODEL_IDS[provider_id], "--output-format", "json",
        "--approval-mode", "yolo", "--extensions", "none",
    ]


def probe_model_access(
    provider_id: str,
    *,
    binary_resolver: Callable[[str, bool], str | None] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ProbeResult:
    """Prove that the connected account can invoke Jobby's exact pinned model."""
    provider_id = _require_provider(provider_id)
    checked_at = _iso_now(clock)
    resolver = binary_resolver or toolpath.agent_binary
    binary = resolver(provider_id, False)
    if not binary:
        return ProbeResult(
            provider_id, False, False, "not_installed", checked_at, "CLI not installed"
        )
    run = runner or subprocess.run
    try:
        with tempfile.TemporaryDirectory(prefix="jobby-model-check-") as directory:
            child_env = provider_cli_env(provider_id)
            if provider_id == "gemini":
                child_env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(
                    _gemini_system_settings(Path(directory), "documents")
                )
                child_env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
            result = run(
                _model_probe_argv(provider_id, str(binary)),
                cwd=directory,
                env=child_env,
                input="Reply with exactly: OK",
                capture_output=True,
                text=True,
                timeout=MODEL_PROBE_TIMEOUT_SECONDS,
            )
    except (subprocess.TimeoutExpired, TimeoutError):
        return ProbeResult(
            provider_id, True, False, "unavailable", checked_at,
            "Pinned model check did not respond",
        )
    except (FileNotFoundError, OSError):
        return ProbeResult(
            provider_id, False, False, "not_installed", checked_at, "CLI not installed"
        )
    if result.returncode == 0:
        if provider_id == "gemini":
            try:
                verify_gemini_served_model(result.stdout or "", stream=True)
            except GeminiModelMismatch:
                return ProbeResult(
                    provider_id,
                    True,
                    False,
                    "unavailable",
                    checked_at,
                    "Gemini CLI did not serve the pinned model",
                )
        return ProbeResult(provider_id, True, True, "healthy", checked_at, "")
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if provider_id == "gemini" and "unsupported_client" in output:
        return ProbeResult(
            provider_id,
            True,
            False,
            "authentication_required",
            checked_at,
            "Google consumer sign-in is unavailable. Connect a Gemini API key.",
        )
    health = (
        "authentication_required"
        if any(marker in output for marker in AUTH_MARKERS)
        else "unavailable"
    )
    return ProbeResult(
        provider_id, True, False, health, checked_at,
        "Pinned model is not available to this account",
    )


def probe_provider(
    provider_id: str,
    *,
    binary_resolver: Callable[[str, bool], str | None] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ProbeResult:
    """Check one native CLI without returning its output or credentials."""
    provider_id = _require_provider(provider_id)
    checked_at = _iso_now(clock)
    resolver = binary_resolver or toolpath.agent_binary
    binary = resolver(provider_id, False)
    if not binary:
        return ProbeResult(
            provider_id, False, False, "not_installed", checked_at, "CLI not installed"
        )

    run = runner or subprocess.run
    try:
        with tempfile.TemporaryDirectory(prefix="jobby-provider-check-") as directory:
            child_env = provider_cli_env(provider_id)
            if provider_id == "gemini":
                child_env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(
                    _gemini_system_settings(Path(directory), "documents")
                )
                child_env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
            model_checked = (
                provider_id == "anthropic" and has_provider_secret(provider_id)
            )
            command = (
                _model_probe_argv(provider_id, str(binary))
                if model_checked else _probe_argv(provider_id, str(binary))
            )
            result = run(
                command,
                cwd=directory,
                env=child_env,
                input="Reply with exactly: OK" if model_checked else None,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
    except (subprocess.TimeoutExpired, TimeoutError):
        return ProbeResult(
            provider_id, True, False, "unavailable", checked_at,
            "Provider CLI did not respond",
        )
    except (FileNotFoundError, OSError):
        return ProbeResult(
            provider_id, False, False, "not_installed", checked_at, "CLI not installed"
        )

    if result.returncode == 0:
        if provider_id == "anthropic" and not model_checked:
            try:
                auth = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                auth = {}
            if auth.get("loggedIn") is not True:
                return ProbeResult(
                    provider_id,
                    True,
                    False,
                    "authentication_required",
                    checked_at,
                    "Claude reported login success but did not save the session",
                )
        if provider_id == "gemini":
            try:
                verify_gemini_served_model(result.stdout or "", stream=True)
            except GeminiModelMismatch:
                return ProbeResult(
                    provider_id,
                    True,
                    False,
                    "unavailable",
                    checked_at,
                    "Gemini CLI did not serve the pinned model",
                )
        return ProbeResult(provider_id, True, True, "healthy", checked_at, "")

    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if provider_id == "gemini" and "unsupported_client" in output:
        return ProbeResult(
            provider_id,
            True,
            False,
            "authentication_required",
            checked_at,
            "Google consumer sign-in is unavailable. Connect a Gemini API key.",
        )
    if provider_id in ("openai", "anthropic") or any(
        marker in output for marker in AUTH_MARKERS
    ):
        return ProbeResult(
            provider_id, True, False, "authentication_required", checked_at,
            "Sign in with the provider CLI",
        )
    return ProbeResult(
        provider_id, True, False, "unavailable", checked_at,
        "Provider CLI health check failed",
    )


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_array(values: Sequence[str]) -> str:
    return "[" + ",".join(_toml_string(value) for value in values) + "]"


def _codex_browser_config(
    server: Mapping[str, Any], *, name: str = "browser"
) -> list[str]:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
        raise ProviderError("MCP server name is unsafe")
    command = str(server.get("command") or "")
    if not command:
        raise ProviderError("browser MCP server has no command")
    args = [str(value) for value in server.get("args", [])]
    out = [
        f"mcp_servers.{name}.command={_toml_string(command)}",
        f"mcp_servers.{name}.args={_toml_array(args)}",
    ]
    for key, value in sorted(dict(server.get("env") or {}).items()):
        out.append(f"mcp_servers.{name}.env.{key}={_toml_string(str(value))}")
    out.extend(
        [
            f"mcp_servers.{name}.required=true",
            f'mcp_servers.{name}.default_tools_approval_mode="approve"',
        ]
    )
    return out


def _default_browser_adapter():
    import browser  # Imported only for apply runs.  # noqa: PLC0415

    return browser


def _with_application_context(method: Callable[..., Any], *args, context=None):
    if context is None:
        return method(*args)
    return method(*args, application_context=context)


def _browser_server_configs(browser_api: Any, slot: str | None, context=None):
    method = getattr(browser_api, "server_configs", None)
    if method is not None:
        return _with_application_context(method, slot, context=context)
    return {"browser": browser_api.server_config(slot)}


def _write_private_json(path: Path, value: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return path


def _gemini_system_settings(work: Path, task_kind: str) -> Path:
    return _write_private_json(
        work / ".jobby-gemini-system.json",
        gemini_hardened_settings(task_kind),
    )


def build_run_invocation(
    provider_id: str,
    task_kind: str,
    prompt: str,
    *,
    workspace: Path | str = REPO,
    state_dir: Path | str = STATE_DIR,
    writable_dirs: Sequence[Path | str] = (),
    browser_slot: str | None = None,
    browser_adapter: Any | None = None,
    allowed_tools: str | None = None,
    application_context: Mapping[str, Any] | None = None,
    binary_resolver: Callable[[str, bool], str | None] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> RunInvocation:
    """Build a noninteractive native CLI command for one agent task."""
    provider_id = _require_provider(provider_id)
    task_kind = _require_task(task_kind)
    resolver = binary_resolver or toolpath.agent_binary
    binary = resolver(provider_id, True)
    if not binary:
        raise ProviderError(f"{PROVIDER_INFO[provider_id]['cli']} is not installed")

    work = Path(workspace).expanduser().resolve()
    state = Path(state_dir).expanduser().resolve()
    # Provider tools never receive repository or state-directory access. The
    # trusted runner passes task facts in the prompt and exposes only narrow MCP
    # actions. In particular, config.env and browser profiles are not agent
    # workspaces or add-dir mounts.
    _ = state, writable_dirs

    if not supports(provider_id, task_kind):
        # Structural, not advisory. Every runner builds its invocation here, so
        # a provider that is not fence-proven for a task cannot be handed that
        # task by any caller, prompt or configuration change.
        raise ProviderError(
            f"{provider_id} is not cleared for {task_kind} work: its tool fence "
            "has not been proven to bind by negative test"
        )

    env = provider_cli_env(provider_id, base_env)
    browser_api = browser_adapter
    if task_kind == "apply" and browser_api is None:
        browser_api = _default_browser_adapter()

    if provider_id == "anthropic":
        argv = [
            str(binary),
            "-p",
            "--model",
            MODEL_IDS[provider_id],
            "--no-session-persistence",
            "--disable-slash-commands",
            "--no-chrome",
            "--setting-sources",
            "project",
            "--tools",
            CLAUDE_BUILTIN_TOOLS[task_kind],
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            CLAUDE_ALLOWED_TOOLS[task_kind],
        ]
        if task_kind == "apply":
            # Machine-readable output, for the usage block and nothing else.
            # 820 form rows had been written and not one carried a token count,
            # because a text-mode print run reports no usage and
            # provider_usage.parse had nothing to find. The telemetry existed
            # and measured nothing.
            #
            # apply only. A form run never parses its own stdout: the outcome
            # comes from the store and the artifacts. documents and scan both
            # read the payload back out, and this wrapper breaks that parse.
            argv.extend(["--output-format", "json"])
            argv.extend([
                "--mcp-config",
                str(_with_application_context(
                    browser_api.config_path,
                    browser_slot,
                    context=application_context,
                )),
                "--strict-mcp-config",
            ])
        else:
            argv.extend([
                "--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config"
            ])
        if task_kind == "documents":
            assert_documents_toolless(provider_id, argv)
        return RunInvocation(provider_id, task_kind, argv, env, work, prompt)

    if provider_id == "openai":
        argv = [
            str(binary), "--strict-config", "--ask-for-approval", "never",
            "--model", MODEL_IDS[provider_id],
        ]
        for feature in CODEX_DISABLED_FEATURES:
            argv.extend(["--disable", feature])
        if task_kind == "scan":
            argv.append("--search")
        if task_kind == "apply":
            servers = _browser_server_configs(
                browser_api, browser_slot, context=application_context
            )
            for name, server in sorted(servers.items()):
                for config in _codex_browser_config(server, name=name):
                    argv.extend(["-c", config])
        argv.extend([
            "exec", "--ignore-user-config", "--ignore-rules",
            "--sandbox", "read-only", "-C", str(work),
            "--skip-git-repo-check", "--ephemeral", "--json", "-",
        ])
        if task_kind == "documents":
            assert_documents_toolless(provider_id, argv)
        return RunInvocation(provider_id, task_kind, argv, env, work, prompt)

    # The Antigravity CLI, `agy`, replaced the Gemini CLI for individual
    # accounts. The binary moved and the arguments did not, so every Gemini run
    # died before it started:
    #
    #   flags provided but not defined: -approval-mode
    #
    # agy rejects --approval-mode, --extensions and --allowed-mcp-server-names
    # outright. Measured against agy 1.1.20.
    #
    # The prompt stays on stdin. `-p` demands its value in argv, which would
    # put the resume, the identity block and the posting into the process list
    # for every local process to read. --input-format stream-json takes it as
    # one NDJSON line instead, and the shape is exact: the event field is
    # required, and message must be an object with content parts. A bare
    # string is refused with "cannot unmarshal string", and {"text": ...} with
    # "user message has no content".
    #
    # --print-timeout defaults to five minutes and self-kills the turn. The
    # caller already bounds the process, so this is set above that bound and
    # the caller stays the one authority on how long a run may take.
    argv = [
        str(binary),
        "--model",
        MODEL_IDS[provider_id],
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--disable-slash-commands",
        "--print-timeout",
        GEMINI_PRINT_TIMEOUT,
    ]
    # No --dangerously-skip-permissions, and that flag is the entire fence.
    #
    # agy has no allowlist, so the flag is all or nothing: with it, all 57
    # built-in tools are auto-approved, and a fence probe caught exactly that.
    # Asked to run `printf %s <token> > SHELL.txt` during a *documents* task it
    # ran it, and the token was on disk afterwards. Schema-checking the output
    # says nothing about what the agent does on the way to producing it.
    #
    # Without the flag the tools are unreachable and the same probe leaves the
    # canary untouched. Documents needs no tool: it is one turn of prompt in,
    # JSON out, and it returns the identical result either way. Measured on
    # agy 1.1.20.
    #
    # This is why apply and scan are refused above rather than run this way. A
    # form agent needs its browser tools, so it needs the flag, and the flag
    # hands it run_command with them.
    if task_kind in ("apply", "scan"):
        # MEASURED REFUSAL, not a placeholder. Three things have to hold for an
        # agent loose on a browser or the open web, and none of them can be
        # made to hold on agy 1.1.20:
        #
        # Its tool surface cannot be narrowed. It advertises 57 built-in tools
        # including run_command, write_to_file, replace_file_content, sed_file
        # and execute_browser_javascript. There is no allowlist flag, and
        # neither --sandbox nor --mode plan removes one of them: all three
        # invocations report the same 57.
        #
        # Its only permission control is all or nothing.
        # --dangerously-skip-permissions auto-approves every one of those 57;
        # without it a print-mode turn returns an empty response having used no
        # tools at all, so there is no middle setting that fills a form.
        #
        # Its MCP servers are global and cannot be scoped to a run. There is no
        # --mcp-config; `agy mcp add` writes one shared config. Redirecting HOME
        # does relocate that config, but agy keeps its credentials in the macOS
        # Keychain rather than under HOME, so an isolated home authenticates as
        # nobody. Five slots cannot each get their own browser endpoint, and
        # without per-slot scoping one application's agent can drive another's
        # browser.
        #
        # scan is refused for the same reason and one of its own. Its fence is
        # --allowed-tools plus a --policy file plus a core-tools check on the
        # system settings, and agy has neither flag and does not read that
        # file. The check still passes, so the restriction reads as enforced
        # and enforces nothing.
        #
        # The firewall in browser_mcp_proxy.py is what makes a form agent safe
        # to point at a real application under the user's name. Refusing here
        # is cheap: the runner walks to the next connected account and says so.
        # Fixing the flags without saying this would have quietly handed an
        # agent a shell on the user's Mac.
        raise ProviderError(
            f"gemini cannot run {task_kind} work: the Antigravity CLI exposes "
            "57 unrestricted built-in tools, has no per-run MCP scoping, and "
            "offers no allowlist to narrow them"
        )

    settings_path = _gemini_system_settings(work, task_kind)
    # KNOWN GAP, still true and now measured. These two variables fenced what
    # the old Gemini CLI could do. agy does not read them: the string
    # GEMINI_CLI_SYSTEM_SETTINGS_PATH occurs zero times in the binary. The file
    # is still written because run_scan checks it, and it binds nothing.
    #
    # It does not gate this path. `documents` is a bounded prompt-to-JSON task
    # whose output is schema-checked, claim-checked and rendered by trusted
    # Python before anything reaches a file, so the worker never depends on the
    # model having been fenced. `apply` is refused above, and `scan` still has
    # its own allowlist and policy file in run_scan.
    env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(settings_path)
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    env.pop("GEMINI_SANDBOX", None)

    if task_kind == "documents":
        assert_documents_toolless(provider_id, argv)
    return RunInvocation(
        provider_id, task_kind, argv, env, work, _gemini_stream_prompt(prompt))


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "nextConnectionOrder": 1,
        "connections": {},
    }


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 40:
        return None
    return value


def _legacy_connection_key(order: int) -> str:
    return f"0-{order:020d}"


def _validated_connection_key(value: Any) -> str:
    if not isinstance(value, str) or not CONNECTION_KEY_PATTERN.fullmatch(value):
        raise ProviderError("provider connection order key is invalid")
    return value


def _normalize_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProviderStateError("providers.json must contain an object")
    if raw.get("version") != STATE_VERSION:
        raise ProviderStateError("providers.json has an unsupported version")

    clean = _empty_state()
    connections = raw.get("connections")
    if not isinstance(connections, dict):
        raise ProviderStateError("providers.json has invalid connections")

    max_order = 0
    for provider_id in PROVIDER_IDS:
        record = connections.get(provider_id)
        if not isinstance(record, dict) or record.get("connected") is not True:
            continue
        order = record.get("connectionOrder")
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            raise ProviderStateError(f"providers.json has an invalid order for {provider_id}")
        health = record.get("health")
        if health not in HEALTH_VALUES:
            health = "unknown"
        clean_record = {
            "connected": True,
            "connectionOrder": order,
            "connectionKey": (
                record.get("connectionKey")
                if isinstance(record.get("connectionKey"), str)
                and CONNECTION_KEY_PATTERN.fullmatch(record["connectionKey"])
                else _legacy_connection_key(order)
            ),
            "connectedAt": _safe_timestamp(record.get("connectedAt")),
            "health": health,
            "lastCheckedAt": _safe_timestamp(record.get("lastCheckedAt")),
            "modelVerifiedAt": _safe_timestamp(record.get("modelVerifiedAt")),
            "detail": str(record.get("detail") or "")[:300],
        }
        clean["connections"][provider_id] = clean_record
        max_order = max(max_order, order)

    next_order = raw.get("nextConnectionOrder")
    if not isinstance(next_order, int) or isinstance(next_order, bool):
        next_order = max_order + 1
    clean["nextConnectionOrder"] = max(next_order, max_order + 1, 1)

    # Carried through deliberately. _normalize_state rebuilds a clean object
    # and copies only what it knows, so a field added elsewhere is silently
    # dropped on the next load. The preferred provider was written to disk,
    # read back correctly by whoever wrote it, and then erased by the first
    # component to reload the registry: it looked persisted right up until
    # something else read it.
    preferred = raw.get("preferredProvider")
    if preferred in PROVIDER_IDS:
        clean["preferredProvider"] = preferred
    return clean


class ProviderRegistry:
    def __init__(
        self,
        state_file: Path | str = STATE_FILE,
        *,
        probe_fn: Callable[[str], ProbeResult] | None = None,
        model_probe_fn: Callable[[str], ProbeResult] | None = None,
        binary_resolver: Callable[[str, bool], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.state_file = Path(state_file).expanduser()
        self.lock_file = self.state_file.with_name(f"{self.state_file.name}.lock")
        self.probe_fn = probe_fn or probe_provider
        self.model_probe_fn = model_probe_fn or (probe_fn if probe_fn else probe_model_access)
        self.binary_resolver = binary_resolver or toolpath.agent_binary
        self.clock = clock

    def _prepare_directory(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_file.parent, 0o700)

    @contextmanager
    def _locked(self):
        self._prepare_directory()
        descriptor = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _write_locked(self, state: Mapping[str, Any]) -> None:
        self._prepare_directory()
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.state_file.name}.", dir=self.state_file.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            descriptor = -1
            os.replace(temporary, self.state_file)
            os.chmod(self.state_file, 0o600)
            directory_fd = os.open(
                self.state_file.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _safe_probe(self, provider_id: str) -> ProbeResult:
        try:
            result = self.probe_fn(provider_id)
        except Exception:
            return ProbeResult(
                provider_id,
                bool(self.binary_resolver(provider_id, False)),
                False,
                "unavailable",
                _iso_now(self.clock),
                "Provider CLI health check failed",
            )
        if result.provider_id != provider_id or result.health not in HEALTH_VALUES:
            raise ProviderError(f"invalid health result for {provider_id}")
        return result

    def _safe_model_probe(self, provider_id: str) -> ProbeResult:
        try:
            result = self.model_probe_fn(provider_id)
        except Exception:
            return ProbeResult(
                provider_id,
                bool(self.binary_resolver(provider_id, False)),
                False,
                "unavailable",
                _iso_now(self.clock),
                "Pinned model check failed",
            )
        if result.provider_id != provider_id or result.health not in HEALTH_VALUES:
            raise ProviderError(f"invalid model health result for {provider_id}")
        return result

    def _load_locked(self) -> dict[str, Any]:
        if self.state_file.exists():
            try:
                raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProviderStateError(f"cannot read providers.json: {exc}") from exc
            os.chmod(self.state_file, 0o600)
            return _normalize_state(raw)

        state = _empty_state()
        # An existing native CLI login is not a Jobby connection. Starting
        # empty makes the first explicit Settings action the only authority for
        # provider order on a new or upgraded installation.
        self._write_locked(state)
        return state

    @staticmethod
    def _ordered_from_state(state: Mapping[str, Any]) -> list[str]:
        """Providers newest connection first, then back through the older ones.

        Connecting an account is how you say which one to use. It used to run
        oldest first, so connecting a second provider changed nothing until the
        first became unhealthy, which reads as the new connection having failed.

        Order is a preference, not a restriction: an unhealthy provider is
        skipped and the next connected one takes the work, so a single
        connection still behaves the obvious way.
        """
        connections = state.get("connections") or {}
        return sorted(
            connections,
            key=lambda provider_id: (
                connections[provider_id].get("connectionKey")
                or _legacy_connection_key(
                    connections[provider_id]["connectionOrder"]
                ),
                provider_id,
            ),
            reverse=True,
        )

    @classmethod
    def _active_from_state(cls, state: Mapping[str, Any]) -> str | None:
        connections = state.get("connections") or {}

        # A preferred provider wins whenever it is healthy, whatever order it
        # was connected in. Connection order is the order accounts were added,
        # which is not a statement about which model the work should run on;
        # this is. Set with `providers.py prefer <id>`.
        #
        # It falls back rather than stalling. A preference that halts every
        # application the moment one API key expires would be a worse failure
        # than running on the next healthy provider and saying so.
        preferred = state.get("preferredProvider")
        if preferred in connections and \
                connections[preferred].get("health") == "healthy":
            return preferred

        return next(
            (
                provider_id
                for provider_id in cls._ordered_from_state(state)
                if connections[provider_id].get("health") == "healthy"
            ),
            None,
        )

    def _public_status_locked(self, state: Mapping[str, Any]) -> dict[str, Any]:
        active = self._active_from_state(state)
        rows = []
        for provider_id in PROVIDER_IDS:
            info = PROVIDER_INFO[provider_id]
            record = (state.get("connections") or {}).get(provider_id)
            installed = bool(self.binary_resolver(provider_id, False))
            connected = record is not None
            health = record.get("health", "unknown") if record else "unknown"
            status = health if connected else ("not_connected" if installed else "not_installed")
            rows.append(
                {
                    "id": provider_id,
                    "name": info["name"],
                    "cli": info["cli"],
                    "model": MODEL_IDS[provider_id],
                    "installed": installed,
                    "connected": connected,
                    "active": provider_id == active,
                    "healthy": connected and health == "healthy",
                    "status": status,
                    "connectionOrder": record.get("connectionOrder") if record else None,
                    "connectedAt": record.get("connectedAt") if record else None,
                    "lastCheckedAt": record.get("lastCheckedAt") if record else None,
                    "authHint": info["authHint"],
                    "installHint": info["installHint"],
                    "detail": record.get("detail", "") if record else "",
                }
            )
        active_row = next((row for row in rows if row["active"]), None)
        return {
            "version": STATE_VERSION,
            "activeProvider": active,
            "ready": bool(active_row and active_row["healthy"]),
            "providers": rows,
        }

    def public_status(self) -> dict[str, Any]:
        with self._locked():
            state = self._load_locked()
            return self._public_status_locked(state)

    def preferred(self) -> str | None:
        with self._locked():
            return self._load_locked().get("preferredProvider")

    def set_preferred(self, provider_id: str | None) -> dict[str, Any]:
        with self._locked():
            state = self._load_locked()
            if provider_id is None:
                state.pop("preferredProvider", None)
            else:
                state["preferredProvider"] = provider_id
            self._write_locked(state)
            return {"preferredProvider": state.get("preferredProvider"),
                    "active": self._active_from_state(state),
                    "model": MODEL_IDS.get(provider_id) if provider_id else None}

    def active_provider(self) -> str | None:
        with self._locked():
            state = self._load_locked()
            return self._active_from_state(state)

    def connect(
        self, provider_id: str, *, connection_key: str | None = None
    ) -> dict[str, Any]:
        provider_id = _require_provider(provider_id)
        requested_key = (
            _validated_connection_key(connection_key)
            if connection_key is not None
            else None
        )
        with self._locked():
            state = self._load_locked()
            result = self._safe_probe(provider_id)
            if result.authenticated and provider_id != "gemini" and not (
                provider_id == "anthropic" and has_provider_secret(provider_id)
            ):
                result = self._safe_model_probe(provider_id)
            record = state["connections"].get(provider_id)
            if record is not None:
                record["health"] = result.health
                record["lastCheckedAt"] = result.checked_at
                record["detail"] = result.message
                if (
                    requested_key is not None
                    and str(record.get("connectionKey") or "").startswith("0-")
                ):
                    # A Settings event carries one shared ordering key for the
                    # Mac and document worker. Replace only a legacy local key,
                    # never an earlier shared Settings choice.
                    record["connectionKey"] = requested_key
                if result.authenticated:
                    record["modelVerifiedAt"] = result.checked_at
                else:
                    record["modelVerifiedAt"] = None
                self._write_locked(state)
                return self._public_status_locked(state)

            # Record the user's Connect action even when the native CLI still
            # needs its one-time browser login. This keeps connection order
            # stable across that handoff and means the worker can become ready
            # on its next refresh without consuming a second Settings click.
            order = state["nextConnectionOrder"]
            state["connections"][provider_id] = {
                "connected": True,
                "connectionOrder": order,
                "connectionKey": requested_key or _legacy_connection_key(order),
                "connectedAt": result.checked_at,
                "health": result.health,
                "lastCheckedAt": result.checked_at,
                "modelVerifiedAt": result.checked_at if result.authenticated else None,
                "detail": result.message,
            }
            state["nextConnectionOrder"] = order + 1
            self._write_locked(state)
            return self._public_status_locked(state)

    def begin_connect(
        self, provider_id: str, *, connection_key: str | None = None
    ) -> dict[str, Any]:
        """Record a Settings click without putting login behind a live probe."""
        provider_id = _require_provider(provider_id)
        requested_key = (
            _validated_connection_key(connection_key)
            if connection_key is not None
            else None
        )
        with self._locked():
            state = self._load_locked()
            record = state["connections"].get(provider_id)
            checked_at = _iso_now(self.clock)
            if record is None:
                order = state["nextConnectionOrder"]
                state["connections"][provider_id] = {
                    "connected": True,
                    "connectionOrder": order,
                    "connectionKey": requested_key or _legacy_connection_key(order),
                    "connectedAt": checked_at,
                    "health": "unknown",
                    "lastCheckedAt": checked_at,
                    "modelVerifiedAt": None,
                    "detail": (
                        "Waiting for browser sign-in" if provider_id == "openai"
                        else "Waiting for local API key"
                    ),
                }
                state["nextConnectionOrder"] = order + 1
            else:
                if (
                    requested_key is not None
                    and str(record.get("connectionKey") or "").startswith("0-")
                ):
                    record["connectionKey"] = requested_key
                record["health"] = "unknown"
                record["lastCheckedAt"] = checked_at
                record["modelVerifiedAt"] = None
                record["detail"] = (
                    "Waiting for browser sign-in" if provider_id == "openai"
                    else "Waiting for local API key"
                )
            self._write_locked(state)
            return self._public_status_locked(state)

    def work_order(self) -> list[str]:
        """Connected providers in the order work should try them.

        The same sequence select_for_work probes, exposed so a stage that can
        fall through on failure walks the identical one rather than inventing a
        second that drifts from it.
        """
        with self._locked():
            return list(self._ordered_from_state(self._load_locked()))

    def select_for_work(self) -> dict[str, Any]:
        """Probe connections in order and return the first account that can work."""
        with self._locked():
            state = self._load_locked()
            changed = False
            for provider_id in self._ordered_from_state(state):
                result = self._safe_probe(provider_id)
                if result.authenticated and provider_id != "gemini" and not (
                    provider_id == "anthropic" and has_provider_secret(provider_id)
                ):
                    result = self._safe_model_probe(provider_id)
                record = state["connections"][provider_id]
                record["health"] = result.health
                record["lastCheckedAt"] = result.checked_at
                record["detail"] = result.message
                record["modelVerifiedAt"] = (
                    result.checked_at if result.authenticated else None
                )
                changed = True
                if result.authenticated:
                    break
            if changed:
                self._write_locked(state)
            return self._public_status_locked(state)

    def disconnect(
        self, provider_id: str, *, forget_secret: bool = False
    ) -> dict[str, Any]:
        provider_id = _require_provider(provider_id)
        with self._locked():
            state = self._load_locked()
            if provider_id in state["connections"]:
                del state["connections"][provider_id]
                self._write_locked(state)
            if forget_secret and provider_id in provider_secrets.PROVIDERS:
                try:
                    provider_secrets.delete(provider_id)
                except provider_secrets.ProviderSecretError as exc:
                    raise ProviderStateError("the local provider key is unsafe") from exc
            return self._public_status_locked(state)

    def refresh(self, provider_id: str | None = None) -> dict[str, Any]:
        if provider_id is not None:
            _require_provider(provider_id)
        with self._locked():
            state = self._load_locked()
            targets = [provider_id] if provider_id else list(state["connections"])
            changed = False
            for target in targets:
                if target not in state["connections"]:
                    continue
                result = self._safe_probe(target)
                record = state["connections"][target]
                verified_at = record.get("modelVerifiedAt")
                verification_due = not verified_at
                if verified_at:
                    try:
                        verified = datetime.fromisoformat(
                            str(verified_at).replace("Z", "+00:00")
                        )
                        verification_due = (
                            (self.clock or (lambda: datetime.now(timezone.utc)))()
                            .astimezone(timezone.utc) - verified.astimezone(timezone.utc)
                        ).total_seconds() >= 24 * 60 * 60
                    except (TypeError, ValueError):
                        verification_due = True
                model_verified = False
                if result.authenticated:
                    if target == "gemini" or (
                        target == "anthropic" and has_provider_secret(target)
                    ):
                        # Gemini's health probe invokes the pinned model because
                        # its CLI has no account-status command.
                        model_verified = True
                    elif verification_due:
                        result = self._safe_model_probe(target)
                        model_verified = result.authenticated
                record["health"] = result.health
                record["lastCheckedAt"] = result.checked_at
                record["detail"] = result.message
                if model_verified:
                    record["modelVerifiedAt"] = result.checked_at
                elif not result.authenticated:
                    # Retry the exact model check after any failed probe. A
                    # cheap successful auth probe before the deadline keeps the
                    # last real model-verification time unchanged.
                    record["modelVerifiedAt"] = None
                changed = True
            if changed:
                self._write_locked(state)
            return self._public_status_locked(state)

    def mark_unhealthy(
        self, provider_id: str, health: str = "authentication_required"
    ) -> dict[str, Any]:
        """Persist a failure observed during real work without re-probing it away."""
        provider_id = _require_provider(provider_id)
        if health not in {"authentication_required", "unavailable"}:
            raise ProviderError("invalid unhealthy provider state")
        with self._locked():
            state = self._load_locked()
            record = state["connections"].get(provider_id)
            if record is not None:
                record["health"] = health
                record["lastCheckedAt"] = _iso_now(self.clock)
                record["modelVerifiedAt"] = None
                record["detail"] = (
                    "The provider rejected the saved login"
                    if health == "authentication_required"
                    else "The provider could not complete the request"
                )
                self._write_locked(state)
            return self._public_status_locked(state)


def _registry() -> ProviderRegistry:
    return ProviderRegistry(STATE_FILE)


def public_status() -> dict[str, Any]:
    return _registry().public_status()


def active_provider() -> str | None:
    return _registry().active_provider()


def connect(
    provider_id: str, *, connection_key: str | None = None
) -> dict[str, Any]:
    return _registry().connect(provider_id, connection_key=connection_key)


def begin_connect(
    provider_id: str, *, connection_key: str | None = None
) -> dict[str, Any]:
    return _registry().begin_connect(provider_id, connection_key=connection_key)


def disconnect(provider_id: str) -> dict[str, Any]:
    return _registry().disconnect(provider_id, forget_secret=True)


def refresh(provider_id: str | None = None) -> dict[str, Any]:
    return _registry().refresh(provider_id)


def select_for_work() -> dict[str, Any]:
    return _registry().select_for_work()


def work_order() -> list[str]:
    """Connected providers in the order work should try them."""
    return _registry().work_order()


def mark_unhealthy(
    provider_id: str, health: str = "authentication_required"
) -> dict[str, Any]:
    return _registry().mark_unhealthy(provider_id, health)


def preferred() -> str | None:
    """Which provider new work runs on when it is healthy, or None."""
    return _registry().preferred()


def set_preferred(provider_id: str | None) -> dict[str, Any]:
    """Pin which model new work runs on, independently of connection order.

    Connection order records when an account was added, which says nothing
    about which model the work should use. This does.
    """
    if provider_id is not None:
        provider_id = _require_provider(provider_id)
    return _registry().set_preferred(provider_id)


if __name__ == "__main__":
    import argparse as _argparse

    _ap = _argparse.ArgumentParser(description="Local provider registry.")
    _sub = _ap.add_subparsers(dest="cmd", required=True)
    _sub.add_parser("status", help="what is connected, healthy and preferred")
    _pref = _sub.add_parser("prefer", help="run new work on this provider when healthy")
    _pref.add_argument("provider", nargs="?", choices=sorted(PROVIDER_IDS),
                       help="omit to clear the preference")
    _args = _ap.parse_args()

    if _args.cmd == "prefer":
        print(json.dumps(set_preferred(_args.provider), indent=2))
    else:
        out = status()
        out["preferred"] = preferred()
        print(json.dumps(out, indent=2))
