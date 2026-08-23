"""Interactive provider login wrapper tests."""

import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "provider_login", ROOT / "scripts" / "provider_login.py"
)
provider_login = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = provider_login
spec.loader.exec_module(provider_login)


class Registry:
    def __init__(self, connected):
        self.connected = connected
        self.connected_provider = None

    def connect(self, provider):
        self.connected_provider = provider
        return self.public_status()

    def public_status(self):
        return {
            "activeProvider": "openai" if self.connected else None,
            "providers": [{
                "id": "openai",
                "connected": self.connected,
                "healthy": self.connected,
                "status": "healthy" if self.connected else "authentication_required",
            }],
        }


class LoginTests(unittest.TestCase):
    @mock.patch.object(provider_login, "publish_status", return_value=True)
    @mock.patch.object(provider_login.providers, "native_cli_env", return_value={"PATH": "/safe"})
    @mock.patch.object(provider_login.providers, "login_invocation",
                       return_value=["/agents/codex", "login"])
    def test_successful_login_is_probed_and_connected(self, _invocation, _env, _publish):
        registry = Registry(True)
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        result = provider_login.login("openai", registry=registry, runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(registry.connected_provider, "openai")
        self.assertEqual(calls[0][0], ["/agents/codex", "login"])
        self.assertFalse(calls[0][1]["check"])

    @mock.patch.object(provider_login, "publish_status", return_value=True)
    @mock.patch.object(provider_login.providers, "native_cli_env", return_value={})
    @mock.patch.object(provider_login.providers, "login_invocation", return_value=["/agents/codex"])
    def test_failed_login_is_not_recorded_as_connected(self, _invocation, _env, _publish):
        registry = Registry(False)
        result = provider_login.login(
            "openai",
            registry=registry,
            runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 1),
        )
        self.assertFalse(result["ok"])
        self.assertIsNone(registry.connected_provider)


if __name__ == "__main__":
    unittest.main()
