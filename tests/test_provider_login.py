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

    @mock.patch.object(provider_login, "publish_status", return_value=True)
    @mock.patch.object(provider_login.providers, "native_cli_env", return_value={})
    def test_every_provider_signs_in_through_its_own_cli(self, _env, _publish):
        """No API key is asked for, and none is stored.

        Anthropic and Gemini used to open a key page and prompt for a paste.
        That made the connector hold a secret it did not need, and it is the
        wrong shape for anyone but the person whose key it was: each user signs
        in to their own account through their own CLI instead.
        """
        seen = {}

        class CliRegistry:
            connected_provider = None

            def connect(self, provider):
                self.connected_provider = provider
                return {"activeProvider": provider, "providers": [{
                    "id": provider, "connected": True, "healthy": True,
                    "status": "healthy", "detail": "",
                }]}

            def public_status(self):
                return {"activeProvider": None, "providers": []}

        for provider, expected in (("openai", "login"),
                                   ("anthropic", "auth"),
                                   ("gemini", None)):
            with self.subTest(provider=provider):
                registry = CliRegistry()
                def runner(command, **kwargs):
                    seen[provider] = command
                    return subprocess.CompletedProcess(command, 0)
                with mock.patch.object(
                        provider_login.providers, "login_invocation",
                        return_value=[f"/agents/{provider}"]
                        + ([expected] if expected else [])):
                    result = provider_login.login(
                        provider, registry=registry, runner=runner)
                self.assertTrue(result["ok"])
                self.assertEqual(registry.connected_provider, provider)
                self.assertEqual(seen[provider][0], f"/agents/{provider}")

    def test_no_api_key_is_ever_prompted_for_or_stored(self):
        source = (ROOT / "scripts" / "provider_login.py").read_text()
        for banned in ("getpass", "webbrowser", "provider_secrets.store"):
            self.assertNotIn(banned, source, f"{banned} is back in the login path")


if __name__ == "__main__":
    unittest.main()
