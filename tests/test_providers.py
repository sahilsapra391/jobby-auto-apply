"""Provider registry, connection ordering, and native CLI command tests."""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "providers", ROOT / "scripts" / "providers.py"
)
providers = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = providers
spec.loader.exec_module(providers)


NOW = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)


def probe(provider, healthy=True):
    return providers.ProbeResult(
        provider,
        True,
        healthy,
        "healthy" if healthy else "authentication_required",
        "2026-08-23T15:00:00Z",
        "",
    )


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "providers.json"
        self.health = {name: False for name in providers.PROVIDER_IDS}

        def check(provider):
            return probe(provider, self.health[provider])

        self.registry = providers.ProviderRegistry(
            self.state,
            probe_fn=check,
            binary_resolver=lambda _provider, _required: "/usr/local/bin/agent",
            clock=lambda: NOW,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_provider_signs_in_through_its_own_cli(self):
        """All three authenticate against the account the user already has.

        Anthropic and Gemini used to raise here, because they connected with a
        pasted API key instead. That was the wrong shape for anyone but the
        person whose key it was, and it left this program holding a secret it
        did not need. `claude auth login` reports a claude.ai login through the
        same `auth status` the probe already uses; Gemini has no auth
        subcommand and signs in inside its interactive session, so launching it
        bare is the login.
        """
        resolver = lambda provider, required: f"/agents/{provider}"
        self.assertEqual(
            providers.login_invocation("openai", binary_resolver=resolver),
            ["/agents/openai", "login"],
        )
        self.assertEqual(
            providers.login_invocation("anthropic", binary_resolver=resolver),
            ["/agents/anthropic", "auth", "login"],
        )
        self.assertEqual(
            providers.login_invocation("gemini", binary_resolver=resolver),
            ["/agents/gemini"],
        )

    def test_a_missing_cli_still_raises_rather_than_guessing(self):
        with self.assertRaises(providers.ProviderError):
            providers.login_invocation(
                "anthropic", binary_resolver=lambda provider, required: None)

    def test_the_newest_successful_connection_becomes_active(self):
        """Connecting an account is how you say which one to use.

        This used to assert the opposite: the first connection stayed active
        and a second one changed nothing until the first broke, which reads as
        the new connection having failed. The connection ORDER below is
        unchanged; what flipped is which end of it gets picked.
        """
        self.health["openai"] = True
        self.registry.connect("openai")
        self.health["gemini"] = True
        status = self.registry.connect("gemini")
        self.assertEqual(status["activeProvider"], "gemini")
        rows = {item["id"]: item for item in status["providers"]}
        self.assertEqual(rows["openai"]["connectionOrder"], 1)
        self.assertEqual(rows["gemini"]["connectionOrder"], 2)

    def test_begin_connect_opens_login_without_running_a_probe(self):
        calls = []
        registry = providers.ProviderRegistry(
            pathlib.Path(self.tmp.name) / "fast" / "providers.json",
            probe_fn=lambda provider: calls.append(provider) or probe(provider, True),
            binary_resolver=lambda _provider, _required: "/usr/local/bin/agent",
            clock=lambda: NOW,
        )
        status = registry.begin_connect("anthropic")
        self.assertEqual(calls, [])
        row = next(item for item in status["providers"] if item["id"] == "anthropic")
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["detail"], "Waiting for local API key")

    def test_reconnect_returns_an_existing_connection_to_checking(self):
        self.health["openai"] = True
        self.registry.connect("openai")
        status = self.registry.begin_connect("openai")
        row = next(item for item in status["providers"] if item["id"] == "openai")
        self.assertEqual(status["activeProvider"], None)
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["detail"], "Waiting for browser sign-in")

    def test_live_selection_checks_connections_in_order_and_stops_on_success(self):
        self.health.update(openai=True, anthropic=True, gemini=True)
        self.registry.connect("openai")
        self.registry.connect("anthropic")
        self.registry.connect("gemini")
        self.health["openai"] = False
        checked = []
        self.registry.probe_fn = lambda provider: (
            checked.append(("auth", provider)) or probe(provider, self.health[provider])
        )
        self.registry.model_probe_fn = lambda provider: (
            checked.append(("model", provider)) or probe(provider, self.health[provider])
        )
        status = self.registry.select_for_work()
        # openai is unhealthy, so the next newest connection takes the work.
        self.assertEqual(status["activeProvider"], "gemini")
        # Newest first, and it stops as soon as one answers. gemini was the
        # last connected and is healthy, so openai and anthropic are never
        # probed at all: under the old oldest-first order this walked openai,
        # found it broken, and then checked anthropic.
        self.assertEqual(checked, [("auth", "gemini")])

    def test_claude_exit_zero_without_saved_login_is_not_healthy(self):
        result = providers.probe_provider(
            "anthropic",
            binary_resolver=lambda _provider, _required: "/usr/bin/claude",
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, '{"loggedIn":false,"authMethod":"none"}', ""
            ),
            clock=lambda: NOW,
        )
        self.assertEqual(result.health, "authentication_required")
        self.assertIn("did not save", result.message)

    def test_retired_gemini_consumer_cli_returns_actionable_detail(self):
        result = providers.probe_provider(
            "gemini",
            binary_resolver=lambda _provider, _required: "/usr/bin/gemini",
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 1, "", "IneligibleTierError: UNSUPPORTED_CLIENT"
            ),
            clock=lambda: NOW,
        )
        self.assertEqual(result.health, "authentication_required")
        self.assertIn("Gemini API key", result.message)

    def test_existing_native_login_is_not_silently_connected(self):
        self.health["anthropic"] = True
        status = self.registry.public_status()
        self.assertIsNone(status["activeProvider"])
        self.assertFalse(status["ready"])
        self.assertFalse(any(row["connected"] for row in status["providers"]))

    def test_a_health_failure_selects_the_next_healthy_provider(self):
        self.health["openai"] = True
        self.registry.connect("openai")
        self.health["gemini"] = True
        self.registry.connect("gemini")
        self.health["openai"] = False
        status = self.registry.refresh("openai")
        self.assertEqual(status["activeProvider"], "gemini")
        self.assertTrue(status["ready"])

    def test_a_real_child_auth_failure_is_persisted_without_a_probe(self):
        self.health["openai"] = True
        self.registry.connect("openai")
        status = self.registry.mark_unhealthy("openai")
        row = next(item for item in status["providers"] if item["id"] == "openai")
        self.assertEqual(row["status"], "authentication_required")
        self.assertFalse(status["ready"])

    def test_disconnect_promotes_the_next_connection(self):
        self.health.update(openai=True, anthropic=True)
        self.registry.connect("openai")
        self.registry.connect("anthropic")
        status = self.registry.disconnect("openai")
        self.assertEqual(status["activeProvider"], "anthropic")

    def test_reconnecting_moves_provider_to_the_end_and_makes_it_active(self):
        self.health.update(openai=True, anthropic=True)
        self.registry.connect("openai")
        self.registry.connect("anthropic")
        self.registry.disconnect("openai")
        status = self.registry.connect("openai")
        rows = {item["id"]: item for item in status["providers"]}
        self.assertGreater(
            rows["openai"]["connectionOrder"], rows["anthropic"]["connectionOrder"]
        )
        # Reconnecting still moves you to the end of the order. The end is now
        # the front of the queue, which is the point: reconnecting a provider
        # is how you choose it.
        self.assertEqual(status["activeProvider"], "openai")

    def test_pending_login_keeps_the_first_connection_order(self):
        status = self.registry.connect("gemini")
        row = next(item for item in status["providers"] if item["id"] == "gemini")
        self.assertTrue(row["connected"])
        self.assertFalse(row["healthy"])
        self.assertEqual(row["status"], "authentication_required")
        self.assertEqual(row["connectionOrder"], 1)
        self.assertIsNone(status["activeProvider"])
        self.assertFalse(status["ready"])

        self.health["openai"] = True
        status = self.registry.connect("openai")
        rows = {item["id"]: item for item in status["providers"]}
        self.assertEqual(rows["gemini"]["connectionOrder"], 1)
        self.assertEqual(rows["openai"]["connectionOrder"], 2)
        self.assertEqual(status["activeProvider"], "openai")

        self.health["gemini"] = True
        status = self.registry.refresh("gemini")
        # gemini becoming healthy does not take the work back: openai was
        # connected more recently. Reconnecting gemini would.
        self.assertEqual(status["activeProvider"], "openai")
        self.assertTrue(status["ready"])

    def test_shared_connection_keys_converge_across_opposite_queue_order(self):
        self.health.update(openai=True, gemini=True)
        local = providers.ProviderRegistry(
            pathlib.Path(self.tmp.name) / "local" / "providers.json",
            probe_fn=self.registry.probe_fn,
            binary_resolver=lambda _provider, _required: "/usr/local/bin/agent",
            clock=lambda: NOW,
        )
        documents = providers.ProviderRegistry(
            pathlib.Path(self.tmp.name) / "documents" / "providers.json",
            probe_fn=self.registry.probe_fn,
            binary_resolver=lambda _provider, _required: "/usr/local/bin/agent",
            clock=lambda: NOW,
        )
        first_key = (
            "1-1787497200000-00000000-0000-4000-8000-000000000001"
        )
        second_key = (
            "1-1787497201000-00000000-0000-4000-8000-000000000002"
        )

        local.connect("openai", connection_key=first_key)
        local.connect("gemini", connection_key=second_key)
        documents.connect("gemini", connection_key=second_key)
        documents.connect("openai", connection_key=first_key)

        # The point is convergence: both agree regardless of the order they
        # saw the connections in. The winner is the later key, gemini.
        self.assertEqual(local.active_provider(), "gemini")
        self.assertEqual(documents.active_provider(), "gemini")

    def test_settings_key_replaces_only_a_legacy_local_order_key(self):
        self.health.update(openai=True, gemini=True)
        self.registry.connect("gemini")
        shared_key = (
            "1-1787497200000-00000000-0000-4000-8000-000000000001"
        )
        self.registry.connect("gemini", connection_key=shared_key)
        self.registry.connect(
            "openai",
            connection_key=(
                "1-1787497201000-00000000-0000-4000-8000-000000000002"
            ),
        )
        self.assertEqual(self.registry.active_provider(), "openai")

    def test_frequent_auth_refreshes_do_not_postpone_exact_model_check(self):
        current = [NOW]
        auth_calls = []
        model_calls = []

        def result(provider, bucket):
            bucket.append(current[0])
            checked = current[0].isoformat().replace("+00:00", "Z")
            return providers.ProbeResult(
                provider, True, True, "healthy", checked, ""
            )

        registry = providers.ProviderRegistry(
            pathlib.Path(self.tmp.name) / "aging" / "providers.json",
            probe_fn=lambda provider: result(provider, auth_calls),
            model_probe_fn=lambda provider: result(provider, model_calls),
            binary_resolver=lambda _provider, _required: "/usr/local/bin/agent",
            clock=lambda: current[0],
        )
        registry.connect("openai")
        self.assertEqual(len(model_calls), 1)

        for half_hour in range(1, 48):
            current[0] = NOW + timedelta(minutes=30 * half_hour)
            registry.refresh("openai")
        self.assertEqual(len(model_calls), 1)

        current[0] = NOW + timedelta(hours=24)
        registry.refresh("openai")
        self.assertEqual(len(model_calls), 2)

    def test_state_files_are_private(self):
        self.health["openai"] = True
        self.registry.connect("openai")
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.state.parent).st_mode & 0o777, 0o700)

    def test_corrupt_state_is_refused(self):
        self.state.write_text("not-json", encoding="utf-8")
        with self.assertRaises(providers.ProviderStateError):
            self.registry.public_status()


class ProbeTests(unittest.TestCase):
    def test_missing_cli_is_reported_without_running_anything(self):
        result = providers.probe_provider(
            "gemini", binary_resolver=lambda _provider, _required: None
        )
        self.assertEqual(result.health, "not_installed")
        self.assertFalse(result.authenticated)

    def test_native_cli_environment_drops_raw_api_secrets(self):
        env = providers.native_cli_env({
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "GEMINI_API_KEY": "gemini-secret",
            "CF_ACCOUNT_ID": "account-secret",
            "CF_KV_NAMESPACE_ID": "namespace-secret",
            "CF_API_TOKEN": "queue-secret",
            "JOBBY_WORKER_KEY": "worker-secret",
            "DASHBOARD_PASSWORD": "dashboard-secret",
            "HEALTHCHECKS_URL": "https://secret.example.test",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "DATABASE_URL": "postgres://private",
            "GH_TOKEN": "github-secret",
            "SSH_AUTH_SOCK": "/private/tmp/agent.sock",
            "KEEP_ME": "safe",
        })
        self.assertTrue(env["PATH"])
        self.assertNotIn("KEEP_ME", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("GEMINI_API_KEY", env)
        for key in (
            "CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN",
            "JOBBY_WORKER_KEY", "DASHBOARD_PASSWORD", "HEALTHCHECKS_URL",
        ):
            self.assertNotIn(key, env)
        for key in (
            "AWS_SECRET_ACCESS_KEY", "DATABASE_URL", "GH_TOKEN", "SSH_AUTH_SOCK",
        ):
            self.assertNotIn(key, env)

    def test_selected_provider_receives_only_its_stored_key(self):
        with mock.patch.object(
            providers.provider_secrets, "load", return_value="selected-secret"
        ):
            anthropic = providers.provider_cli_env(
                "anthropic", {"PATH": "/usr/bin", "GEMINI_API_KEY": "wrong"}
            )
            gemini = providers.provider_cli_env(
                "gemini", {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "wrong"}
            )
        self.assertEqual(anthropic["ANTHROPIC_API_KEY"], "selected-secret")
        self.assertNotIn("GEMINI_API_KEY", anthropic)
        self.assertEqual(gemini["GEMINI_API_KEY"], "selected-secret")
        self.assertNotIn("ANTHROPIC_API_KEY", gemini)

    def test_authentication_marker_becomes_needs_login(self):
        result = providers.probe_provider(
            "gemini",
            binary_resolver=lambda _provider, _required: "/usr/bin/gemini",
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 1, "", "Please sign in"
            ),
            clock=lambda: NOW,
        )
        self.assertEqual(result.health, "authentication_required")

    def test_gemini_health_probe_uses_the_pinned_model(self):
        observed = {}

        def runner(command, **kwargs):
            observed["command"] = command
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "response": "OK",
                "stats": {"models": {
                    providers.MODEL_IDS["gemini"]: {
                        "api": {"totalRequests": 1},
                    },
                }},
            }), "")

        providers.probe_provider(
            "gemini",
            binary_resolver=lambda _provider, _required: "/usr/bin/gemini",
            runner=runner,
        )
        self.assertEqual(
            observed["command"][observed["command"].index("--model") + 1],
            "gemini-3.7-flash-medium",
        )

    def test_gemini_probe_rejects_a_silently_remapped_model(self):
        result = providers.probe_provider(
            "gemini",
            binary_resolver=lambda _provider, _required: "/usr/bin/gemini",
            runner=lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                0,
                json.dumps({
                    "response": "OK",
                    "stats": {"models": {
                        "gemini-3.5-flash": {"api": {"totalRequests": 1}},
                    }},
                }),
                "",
            ),
        )
        self.assertEqual(result.health, "unavailable")
        self.assertFalse(result.authenticated)

    def test_gemini_stream_requires_the_pinned_model_and_a_successful_end(self):
        """Antigravity announces the model once, in the init record.

        This is weaker than what it replaced, and the weakening is worth
        stating. The old Gemini CLI put a model label in its init record too,
        and this test used to feed it "untrusted-request-label" on purpose,
        because verification came from the terminal stats.models instead. The
        agy terminal record carries a status and a token count and no model at
        all, so the init record is the only model signal there is.

        What still holds: a different model, a run that did not finish
        successfully, a missing record at either end, and a run with no tokens
        are all refused.
        """
        exact = providers.MODEL_IDS["gemini"]
        output = "\n".join([
            json.dumps({"event": "init", "init": {"model": exact}}),
            json.dumps({"event": "result", "result": {
                "status": "SUCCESS", "usage": {"total_tokens": 12}}}),
        ])
        self.assertEqual(
            providers.verify_gemini_served_model(output, stream=True), exact
        )

        remapped = output.replace(exact, "gemini-3.5-flash-low", 1)
        with self.assertRaises(providers.GeminiModelMismatch):
            providers.verify_gemini_served_model(remapped, stream=True)

        failed = output.replace('"SUCCESS"', '"FAILURE"')
        with self.assertRaises(providers.GeminiModelMismatch):
            providers.verify_gemini_served_model(failed, stream=True)

        no_init = json.dumps({"event": "result", "result": {
            "status": "SUCCESS", "usage": {"total_tokens": 12}}})
        with self.assertRaises(providers.GeminiModelMismatch):
            providers.verify_gemini_served_model(no_init, stream=True)

        for empty in ("", "   ", "not json at all"):
            with self.assertRaises(providers.GeminiModelMismatch):
                providers.verify_gemini_served_model(empty, stream=True)

    def test_gemini_settings_pin_every_core_model_call(self):
        settings = providers.gemini_hardened_settings("scan")
        self.assertTrue(settings["experimental"]["dynamicModelConfiguration"])
        override = settings["modelConfigs"]["customOverrides"][0]
        self.assertEqual(override["match"], {"overrideScope": "core"})
        self.assertEqual(
            override["modelConfig"]["model"], providers.MODEL_IDS["gemini"]
        )


class BrowserStub:
    def config_path(self, _slot):
        return pathlib.Path("/tmp/browser.json")

    def server_config(self, _slot):
        return {
            "command": "/usr/bin/browser-mcp",
            "args": ["--slot", "one"],
            "env": {"SAFE_NAME": "slot-one"},
        }

    def write_gemini_settings(self, _slot):
        return pathlib.Path("/tmp/gemini-settings.json")


class InvocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self.tmp.name) / "agent-work"
        self.state_dir = pathlib.Path(self.tmp.name) / "private-state"
        self.workspace.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def invocation(self, provider, task="apply"):
        return providers.build_run_invocation(
            provider,
            task,
            "Do one bounded task.",
            workspace=self.workspace,
            state_dir=self.state_dir,
            browser_slot="one",
            browser_adapter=BrowserStub(),
            binary_resolver=lambda name, _required: f"/agents/{name}",
            base_env={"PATH": "/usr/bin"},
        )

    def test_openai_uses_ephemeral_read_only_and_browser_mcp(self):
        invocation = self.invocation("openai")
        joined = " ".join(invocation.argv)
        self.assertIn("exec", invocation.argv)
        self.assertIn("--ephemeral", invocation.argv)
        self.assertEqual(
            invocation.argv[invocation.argv.index("--sandbox") + 1], "read-only"
        )
        self.assertNotIn("workspace-write", invocation.argv)
        self.assertIn("mcp_servers.browser.command", joined)
        self.assertEqual(
            invocation.argv[invocation.argv.index("--model") + 1], "gpt-5.6-luna"
        )
        self.assertNotIn("dangerously-bypass", joined)

    def test_anthropic_uses_the_browser_configuration(self):
        invocation = self.invocation("anthropic")
        self.assertIn("--mcp-config", invocation.argv)
        self.assertIn("/tmp/browser.json", invocation.argv)
        self.assertIn("--permission-mode", invocation.argv)
        self.assertIn("--no-session-persistence", invocation.argv)
        self.assertEqual(
            invocation.argv[invocation.argv.index("--model") + 1], "claude-sonnet-5"
        )

    def test_gemini_uses_an_isolated_system_settings_file(self):
        invocation = self.invocation("gemini", "documents")
        # Written into the run's own workspace, never a shared location. It
        # binds nothing on the Antigravity CLI (see the note in
        # build_run_invocation) and is kept because run_scan still reads it;
        # what this pins is that it stays scoped to the run.
        settings = pathlib.Path(invocation.env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
        self.assertTrue(
            settings.is_relative_to(self.workspace.resolve()), settings)
        self.assertEqual(
            invocation.argv[invocation.argv.index("--model") + 1],
            "gemini-3.7-flash-medium",
        )
        self.assertNotIn("GEMINI_SANDBOX", invocation.env)

    def test_gemini_document_settings_disable_builtin_and_discovery_tools(self):
        invocation = self.invocation("gemini", "documents")
        settings = json.loads(
            pathlib.Path(
                invocation.env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(settings["tools"]["core"], [])
        self.assertEqual(settings["tools"]["discoveryCommand"], "")
        self.assertEqual(settings["tools"]["callCommand"], "")
        self.assertEqual(settings["mcp"]["allowed"], [])
        self.assertFalse(settings["skills"]["enabled"])
        self.assertFalse(settings["hooksConfig"]["enabled"])
        self.assertFalse(settings["experimental"]["enableAgents"])
        self.assertFalse(settings["admin"]["extensions"]["enabled"])
        self.assertEqual(settings["context"]["fileName"], [])
        self.assertTrue({
            "run_shell_command", "read_file", "write_file", "glob",
            "grep_search", "list_directory",
        }.issubset(settings["tools"]["exclude"]))

    def test_private_prompt_is_supplied_only_on_stdin(self):
        private_prompt = "private candidate prompt token"
        for provider in providers.PROVIDER_IDS:
            with self.subTest(provider=provider):
                invocation = providers.build_run_invocation(
                    provider,
                    "documents",
                    private_prompt,
                    workspace=self.workspace,
                    state_dir=self.state_dir,
                    binary_resolver=lambda name, _required: f"/agents/{name}",
                    base_env={"PATH": "/usr/bin"},
                )
                # The rule is where the prompt goes, not what it is wrapped
                # in. agy takes its turn as one NDJSON line on stdin, so the
                # payload carries the prompt rather than equalling it; what
                # must never change is that argv does not see it, because argv
                # is readable by every local process.
                self.assertIn(private_prompt, invocation.stdin_payload or "")
                self.assertNotIn(private_prompt, invocation.argv)
                self.assertFalse(
                    [a for a in invocation.argv if private_prompt in a],
                    "the prompt reached argv")

    def test_codex_web_search_is_scan_only(self):
        self.assertNotIn("--search", self.invocation("openai", "apply").argv)
        self.assertIn("--search", self.invocation("openai", "scan").argv)

    def test_every_provider_has_one_exact_model_pin(self):
        for provider, expected in providers.MODEL_IDS.items():
            for task in providers.TASK_KINDS:
                with self.subTest(provider=provider, task=task):
                    if provider == "gemini" and task in ("apply", "scan"):
                        # Refused outright, so there is no argv to pin. The
                        # refusal itself is asserted in GeminiAntigravityTests.
                        continue
                    invocation = self.invocation(provider, task)
                    self.assertEqual(invocation.argv.count("--model"), 1)
                    self.assertEqual(
                        invocation.argv[invocation.argv.index("--model") + 1],
                        expected,
                    )

    def test_writable_dir_requests_never_mount_external_tools(self):
        for provider in providers.PROVIDER_IDS:
            with self.subTest(provider=provider):
                invocation = providers.build_run_invocation(
                    provider,
                    "documents",
                    "Create the documents.",
                    workspace=self.workspace,
                    state_dir=self.state_dir,
                    writable_dirs=("/opt/jobby-tools",),
                    binary_resolver=lambda name, _required: f"/agents/{name}",
                    base_env={"PATH": "/usr/bin"},
                )
                joined = " ".join(invocation.argv)
                self.assertNotIn("/opt/jobby-tools", joined)
                self.assertNotIn("--add-dir", invocation.argv)
                self.assertEqual(invocation.cwd, self.workspace.resolve())

    def test_application_invocations_have_no_shell_or_general_filesystem(self):
        invocations = {
            provider: self.invocation(provider, "apply")
            for provider in providers.PROVIDER_IDS
            if provider != "gemini"
        }
        # gemini has no application invocation to inspect: it is refused before
        # one is built, which is the strongest form of this assertion.
        with self.assertRaises(providers.ProviderError):
            self.invocation("gemini", "apply")

        anthropic = invocations["anthropic"]
        self.assertEqual(
            anthropic.argv[anthropic.argv.index("--tools") + 1], ""
        )
        self.assertEqual(
            anthropic.argv[anthropic.argv.index("--allowedTools") + 1],
            "mcp__browser__*,mcp__jobby__*",
        )
        self.assertNotIn("Bash", " ".join(anthropic.argv))

        openai = invocations["openai"]
        self.assertEqual(
            openai.argv[openai.argv.index("--sandbox") + 1], "read-only"
        )
        disabled = {
            openai.argv[index + 1]
            for index, value in enumerate(openai.argv[:-1])
            if value == "--disable"
        }
        for feature in (
            "shell_tool",
            "unified_exec",
            "apply_patch_freeform",
            "code_mode",
            "artifact",
        ):
            self.assertIn(feature, disabled)

        # gemini is covered by the refusal above rather than by argv, because
        # there is no argv it could be given that would satisfy this test: the
        # Antigravity CLI has no allowlist and advertises run_command,
        # write_to_file and execute_browser_javascript on every invocation.

        for provider, invocation in invocations.items():
            with self.subTest(provider=provider):
                self.assertEqual(invocation.cwd, self.workspace.resolve())
                command = " ".join(invocation.argv)
                self.assertNotIn(str(ROOT), command)
                self.assertNotIn(str(self.state_dir), command)

    def test_document_invocation_has_no_browser_configuration(self):
        invocation = self.invocation("openai", "documents")
        joined = " ".join(invocation.argv)
        self.assertNotIn("mcp_servers.browser", joined)
        self.assertNotIn("--search", invocation.argv)




class PreferredProviderTests(unittest.TestCase):
    """The preference has to survive a reload, not just a write.

    _normalize_state rebuilds a clean object and copies only the fields it
    knows, so anything added elsewhere is dropped on the next load. The
    preferred provider was written to disk, read back correctly by whoever
    wrote it, and erased by the first component to reload the registry. It
    looked persisted right up until something else read it, which is why
    checking the file straight after writing it proved nothing.
    """

    def test_a_preference_survives_normalisation(self):
        raw = {"version": providers.STATE_VERSION, "connections": {},
               "nextConnectionOrder": 1, "preferredProvider": "anthropic"}
        self.assertEqual(
            providers._normalize_state(raw).get("preferredProvider"), "anthropic")

    def test_an_unknown_preference_is_dropped_rather_than_carried(self):
        raw = {"version": providers.STATE_VERSION, "connections": {},
               "nextConnectionOrder": 1, "preferredProvider": "not-a-provider"}
        self.assertIsNone(
            providers._normalize_state(raw).get("preferredProvider"))

    def test_no_preference_stays_absent(self):
        raw = {"version": providers.STATE_VERSION, "connections": {},
               "nextConnectionOrder": 1}
        self.assertIsNone(
            providers._normalize_state(raw).get("preferredProvider"))




class NewestConnectionWinsTests(unittest.TestCase):
    """The provider you connected most recently is the one that gets the work.

    Connecting an account is how you say which one to use. Ordering ran oldest
    first, so connecting a second provider changed nothing until the first went
    unhealthy — which reads as the new connection having failed.

    It is a preference, not a restriction: an unhealthy newest is skipped and
    the next connected one takes over, and a single connection still behaves
    the obvious way.
    """

    def state(self, **health):
        order = {"openai": 6, "anthropic": 14, "gemini": 9}
        return {"connections": {
            name: {"health": health[name], "connectionOrder": order[name]}
            for name in health}}

    def test_the_newest_connection_is_tried_first(self):
        state = self.state(openai="healthy", anthropic="healthy", gemini="healthy")
        self.assertEqual(
            providers.ProviderRegistry._ordered_from_state(state)[0], "anthropic")
        self.assertEqual(
            providers.ProviderRegistry._active_from_state(state), "anthropic")

    def test_an_unhealthy_newest_hands_over_to_the_next(self):
        state = self.state(openai="healthy", anthropic="authentication_required",
                           gemini="healthy")
        self.assertEqual(
            providers.ProviderRegistry._active_from_state(state), "gemini")

    def test_it_falls_all_the_way_back(self):
        state = self.state(openai="healthy", anthropic="unavailable",
                           gemini="unavailable")
        self.assertEqual(
            providers.ProviderRegistry._active_from_state(state), "openai")

    def test_one_connection_is_simply_used(self):
        state = self.state(openai="healthy")
        self.assertEqual(
            providers.ProviderRegistry._active_from_state(state), "openai")

    def test_nothing_healthy_selects_nothing(self):
        state = self.state(openai="unavailable", anthropic="unavailable")
        self.assertIsNone(providers.ProviderRegistry._active_from_state(state))


class GeminiAntigravityTests(unittest.TestCase):
    """The flags agy actually has, and the three tasks it may and may not run.

    Every Gemini run died on `flags provided but not defined: -approval-mode`
    before doing anything, because the binary moved from the Gemini CLI to the
    Antigravity CLI and the arguments did not move with it. Measured against
    agy 1.1.20 on 2026-08-25.
    """

    def setUp(self):
        self.workspace = pathlib.Path(tempfile.mkdtemp())

    def build(self, task):
        return providers.build_run_invocation(
            "gemini",
            task,
            "a bounded private prompt",
            workspace=self.workspace,
            binary_resolver=lambda name, _required: f"/agents/{name}",
            base_env={"PATH": "/usr/bin"},
        )

    def test_no_flag_the_antigravity_cli_rejects_survives(self):
        argv = self.build("documents").argv
        for gone in ("--approval-mode", "--extensions",
                     "--allowed-mcp-server-names", "--allowed-tools", "--policy"):
            self.assertNotIn(gone, argv, f"{gone} does not exist on agy")

    def test_it_uses_the_flags_agy_does_have(self):
        argv = self.build("documents").argv
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--input-format") + 1], "stream-json")

    def test_documents_never_auto_approves_the_57_built_in_tools(self):
        """That flag is the whole fence, and agy has no allowlist beside it.

        A fence probe caught this: with --dangerously-skip-permissions, a
        *documents* run asked to `printf %s <token> > SHELL.txt` ran it, and
        the token was on disk afterwards. Schema-checking the output says
        nothing about what the agent does on the way to producing it.

        Without the flag the same probe leaves every canary untouched, and the
        document result is byte-identical: one turn of prompt in, JSON out,
        needing no tool at all.
        """
        self.assertNotIn(
            "--dangerously-skip-permissions", self.build("documents").argv)

    def test_the_print_timeout_outlasts_the_callers_own_bound(self):
        # agy defaults to five minutes and kills its own turn. run_apply bounds
        # a form run at 45 minutes, so the default would end every long run
        # early and blame the model.
        argv = self.build("documents").argv
        self.assertIn("--print-timeout", argv)
        value = argv[argv.index("--print-timeout") + 1]
        self.assertTrue(value.endswith("m"), value)
        self.assertGreaterEqual(int(value[:-1]), 60)

    def test_the_prompt_travels_on_stdin_as_one_ndjson_turn(self):
        invocation = self.build("documents")
        self.assertNotIn("-p", invocation.argv)
        self.assertNotIn("a bounded private prompt", invocation.argv)
        message = json.loads(invocation.stdin_payload)
        self.assertEqual(message["event"], "user")
        self.assertEqual(
            message["message"]["content"][0]["text"], "a bounded private prompt")

    def test_apply_and_scan_are_refused_and_documents_is_not(self):
        for task in ("apply", "scan"):
            with self.subTest(task=task):
                with self.assertRaises(providers.ProviderError) as caught:
                    self.build(task)
                self.assertIn("57 unrestricted built-in tools",
                              str(caught.exception))
        self.assertTrue(self.build("documents").argv)



if __name__ == "__main__":
    unittest.main()
