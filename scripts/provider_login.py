#!/usr/bin/env python3
"""Run one vendor CLI login in a terminal, then connect it to Jobby."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parent
DEPLOY = REPO / "deploy"
sys.path.insert(0, str(SCRIPT_DIR))

import providers  # noqa: E402
import settings_panel  # noqa: E402
import toolpath  # noqa: E402


def publish_status(status):
    """Best-effort live settings update. Authentication does not depend on it."""
    payload = settings_panel.public_payload(status)
    descriptor, temporary = tempfile.mkstemp(prefix="jobby-providers-", suffix=".json")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        result = subprocess.run(
            [toolpath.node(), "local-status.mjs", "write", temporary],
            cwd=DEPLOY,
            env=toolpath.queue_env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, SystemExit):
        return False
    finally:
        Path(temporary).unlink(missing_ok=True)


def login(provider_id, registry=None, runner=None):
    registry = registry or providers.ProviderRegistry()
    run = runner or subprocess.run
    command = providers.login_invocation(provider_id)
    result = run(
        command,
        cwd=REPO,
        env=providers.native_cli_env(),
        check=False,
    )
    status = registry.connect(provider_id) if result.returncode == 0 \
        else registry.public_status()
    publish_status(status)
    row = next(item for item in status["providers"] if item["id"] == provider_id)
    return {
        "ok": bool(row.get("connected") and row.get("healthy")),
        "provider": provider_id,
        "status": row.get("status"),
        "activeProvider": status.get("activeProvider"),
        "loginExit": result.returncode,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=providers.PROVIDER_IDS, required=True)
    args = parser.parse_args(argv)
    try:
        result = login(args.provider)
    except providers.ProviderError as exc:
        result = {
            "ok": False,
            "provider": args.provider,
            "status": "not_installed",
            "error": str(exc),
        }
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        print("Sign-in did not complete. You can close this terminal and try again.")
        return 2
    print("Provider connected to Jobby. You can close this terminal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
