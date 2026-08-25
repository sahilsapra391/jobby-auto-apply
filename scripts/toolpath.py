#!/usr/bin/env python3
"""Absolute paths to the external binaries this project shells out to.

launchd starts agents with PATH=/usr/bin:/bin:/usr/sbin:/sbin and nothing else.
node and several agent CLIs live outside launchd's PATH on this machine, so every
bare-name subprocess call in a launchd-run script dies with FileNotFoundError.
That is not hypothetical: the watcher ran for hours reporting healthy cycles
while every queue read and every publish failed exactly this way.

Resolving to an absolute path once, here, means a script behaves the same
whether it is run from a shell, from launchd, or from a spawned agent.

  python3 scripts/toolpath.py        # show what resolves, and what is missing
"""

import json
import os
import shutil
import sys
from pathlib import Path

# Homebrew on Apple silicon, then Intel, then nvm's current, then the system.
# Ordered by how likely it is to be the one the user actually installed.
EXTRA_DIRS = [
    # Codex is bundled with the ChatGPT desktop app on this machine. Keep this
    # ahead of package-manager paths so the app and its CLI stay on one build.
    "/Applications/ChatGPT.app/Contents/Resources",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/snap/bin",
    str(Path.home() / ".nvm" / "current" / "bin"),
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".bun" / "bin"),
    "/usr/bin",
    "/bin",
]

_cache = {}


def search_path():
    """PATH plus the directories launchd omits, without duplicates."""
    seen, out = set(), []
    for d in os.environ.get("PATH", "").split(os.pathsep) + EXTRA_DIRS:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return os.pathsep.join(out)


def find(name, required=True):
    """Absolute path to `name`, or None when it is genuinely not installed."""
    if name in _cache:
        return _cache[name]
    found = shutil.which(name, path=search_path())
    if found is None:
        # Deliberately not cached. Caching a miss would make a later required
        # lookup return None instead of raising, and would hide a tool the user
        # installed after the process started.
        if required:
            raise SystemExit(
                f"job-application: cannot find `{name}`. Looked on PATH and in "
                f"{', '.join(EXTRA_DIRS)}. Install it, or add its directory to EXTRA_DIRS "
                f"in scripts/toolpath.py."
            )
        return None
    _cache[name] = found
    return found


def node(required=True):
    return find("node", required)


def vercel(required=True):
    return find("vercel", required)


def claude(required=True):
    return find("claude", required)


def codex(required=True):
    return find("codex", required)


def gemini(required=True):
    """The Antigravity CLI, which replaced the Gemini CLI for individuals.

    Google stopped serving Gemini CLI requests for individual accounts on
    2026-06-18 — free, AI Pro and AI Ultra alike. Only Code Assist Standard,
    Enterprise and Google Cloud licences still work through it. Signing in
    succeeds and then every request comes back UNSUPPORTED_CLIENT, so the old
    binary is not a fallback; it is a guaranteed failure with a valid token.
    """
    return find("agy", required)


def agent_binary(provider, required=True):
    """Resolve the CLI used by a provider without accepting arbitrary names."""
    resolvers = {"anthropic": claude, "openai": codex, "gemini": gemini}
    if provider not in resolvers:
        raise ValueError(f"unknown agent provider: {provider}")
    return resolvers[provider](required)


def python(required=True):
    """This interpreter, always. Never a different python3 that happens to be on PATH."""
    return sys.executable


def agent_env(env=None):
    """Environment for a spawned agent, with `python3` pinned to this interpreter.

    Prompts tell agents to run `python3`, because --allowedTools grants
    Bash(python3:*) and that is a prefix match on the literal command an
    absolute path cannot satisfy. Which interpreter that name finds then decides
    whether pymupdf imports, so this puts sys.executable's own directory first
    and makes it independent of the plist and of whatever sits in /usr/bin.
    """
    out = enriched_env(env)
    own = str(Path(sys.executable).parent)
    parts = [p for p in out.get("PATH", "").split(os.pathsep) if p and p != own]
    out["PATH"] = os.pathsep.join([own] + parts)
    return out


def enriched_env(env=None):
    """An environment whose PATH can see the tools, for children we do not exec directly."""
    out = dict(env if env is not None else os.environ)
    out["PATH"] = search_path()
    return out


REPO = Path(__file__).resolve().parents[1]
QUEUE_VARS = ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN")


LOCAL_CONFIG = Path.home() / ".job-application" / "config.env"


def _read_env_file(path):
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def settings():
    """Config from both files, with the machine-local one winning.

    deploy/.env.local is REGENERATED by `vercel env pull`, which silently
    discards anything added by hand. A healthchecks ping URL was lost to exactly
    that. So anything that belongs to this machine rather than to the Vercel
    project lives in ~/.job-application/config.env, which nothing overwrites.
    """
    merged = _read_env_file(REPO / "deploy" / ".env.local")
    merged.update(_read_env_file(LOCAL_CONFIG))
    return merged


def queue_env(env=None):
    """Environment for talking to the board queue.

    The queue moved from Vercel Blob to Cloudflare Workers KV after the Blob
    store was suspended for exceeding its free operation limit. Everything that
    reads or writes the queue needs these three, and having each caller load
    them itself is how half of them ended up out of step.
    """
    out = enriched_env(env)
    wanted = set(QUEUE_VARS) | {
        "HEALTHCHECKS_URL", "DASHBOARD_PASSWORD", "JOBBY_WORKER_KEY",
    }
    for k, v in settings().items():
        if k in wanted and v:
            out[k] = v
    missing = [k for k in QUEUE_VARS if not out.get(k)]
    if missing:
        raise SystemExit(
            "job-application: the board queue is not configured. Missing "
            + ", ".join(missing)
            + " in deploy/.env.local. See references/hosting.md."
        )
    return out


def report():
    out = {"searchPath": search_path().split(os.pathsep), "tools": {}}
    for name in ("node", "vercel", "claude", "codex", "gemini"):
        out["tools"][name] = find(name, required=False) or "MISSING"
    out["tools"]["python"] = python()
    core_ok = all(out["tools"][name] != "MISSING" for name in ("node", "vercel"))
    provider_ok = any(out["tools"][name] != "MISSING"
                      for name in ("claude", "codex", "gemini"))
    out["ok"] = core_ok and provider_ok
    return out


if __name__ == "__main__":
    r = report()
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)
