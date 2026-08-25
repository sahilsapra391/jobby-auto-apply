"""Tests for provider-neutral local agent runners."""

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import agent
import providers
import run_apply
import run_scan


def completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class AgentFacadeTests(unittest.TestCase):
    def test_explicit_provider_does_not_depend_on_connection_order(self):
        with mock.patch.object(agent.providers, "active_provider") as active:
            self.assertEqual(agent.resolve_provider("gemini"), "gemini")
        active.assert_not_called()

    def test_default_provider_comes_from_the_registry(self):
        with mock.patch.object(agent.providers, "active_provider", return_value="openai"):
            self.assertEqual(agent.resolve_provider(), "openai")

    def test_cached_health_uses_public_registry_status(self):
        status = {"providers": [{
            "id": "openai", "connected": True, "healthy": True, "status": "healthy",
            "lastCheckedAt": datetime.now(timezone.utc).isoformat(),
        }]}
        with mock.patch.object(agent.providers, "public_status", return_value=status), \
                mock.patch.object(agent.providers, "refresh") as refresh:
            self.assertEqual(agent.authenticated(provider="openai"), (True, ""))
        refresh.assert_not_called()

    def test_forced_health_refreshes_only_the_selected_provider(self):
        status = {"providers": [{
            "id": "gemini", "connected": True, "healthy": False,
            "status": "authentication_required",
        }]}
        with mock.patch.object(agent.providers, "public_status", return_value=status), \
                mock.patch.object(agent.providers, "refresh", return_value=status) as refresh:
            ok, reason = agent.authenticated(force=True, provider="gemini")
        self.assertFalse(ok)
        self.assertIn("authentication", reason)
        refresh.assert_called_once_with("gemini")

    def test_real_child_auth_failure_is_persisted_without_a_probe(self):
        with mock.patch.object(agent.providers, "mark_unhealthy") as mark, \
                mock.patch.object(agent.providers, "refresh") as refresh:
            agent.invalidate("anthropic")
        mark.assert_called_once_with("anthropic", "authentication_required")
        refresh.assert_not_called()

    def test_generic_blocked_message_covers_each_supported_cli(self):
        message = agent.blocked_reason("401")
        self.assertIn("codex login", message)
        self.assertIn("API key", message)
        self.assertIn("Gemini", message)


class BrowserStub:
    def config_path(self, slot):
        return pathlib.Path("/private/tmp/browser-mcp.json")

    def server_config(self, slot):
        return {
            "command": "/usr/local/bin/npx",
            "args": ["-y", "browser-package", "--slot", slot],
            "env": {"PATH": "/usr/local/bin:/usr/bin"},
        }

    def write_gemini_settings(self, slot):
        return pathlib.Path("/private/tmp/gemini-settings.json")


class ProviderInvocationTests(unittest.TestCase):
    def build(self, provider):
        self.temp = tempfile.TemporaryDirectory()
        work = pathlib.Path(self.temp.name, "work")
        state = pathlib.Path(self.temp.name, "state")
        work.mkdir()
        return providers.build_run_invocation(
            provider,
            "apply",
            "fill one form",
            workspace=work,
            state_dir=state,
            browser_slot="apply-3",
            browser_adapter=BrowserStub(),
            binary_resolver=lambda selected, required: f"/cli/{selected}",
            base_env={"PATH": "/usr/bin"},
        )

    def tearDown(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def test_anthropic_receives_its_mcp_config_file(self):
        invocation = self.build("anthropic")
        self.assertIn("--mcp-config", invocation.argv)
        self.assertIn("/private/tmp/browser-mcp.json", invocation.argv)

    def test_openai_receives_inline_browser_mcp_settings(self):
        invocation = self.build("openai")
        command = " ".join(invocation.argv)
        self.assertIn("mcp_servers.browser.command", command)
        self.assertIn("mcp_servers.browser.required=true", command)
        self.assertIn("exec", invocation.argv)

    def test_gemini_receives_scoped_system_settings(self):
        invocation = self.build("gemini")
        self.assertEqual(
            invocation.env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"],
            "/private/tmp/gemini-settings.json",
        )
        self.assertIn("--allowed-mcp-server-names", invocation.argv)
        self.assertEqual(
            invocation.argv[
                invocation.argv.index("--allowed-mcp-server-names") + 1
            ],
            "browser,jobby",
        )


class ApplyFixtureMixin:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.resume = root / "Candidate_Acme_Resume.pdf"
        self.cover = root / "Candidate_Acme_CoverLetter.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.summary = {
            "applicationId": "acme-pm",
            "company": "Acme",
            "role": "Product Manager",
            "url": "https://example.test/jobs/1",
            "ats": "greenhouse",
            "region": "US",
            "documentsReady": True,
            "documents": {
                "resume": str(self.resume),
                "coverLetter": str(self.cover),
            },
            "identity": {"location": "New York, NY"},
            "pendingFields": [],
        }

    def tearDown(self):
        self.temp.cleanup()


class ApplyArtifactTests(ApplyFixtureMixin, unittest.TestCase):
    def test_both_exact_pdfs_are_required(self):
        artifacts = run_apply.artifact_snapshot(self.summary)
        self.assertEqual(artifacts["resume"]["path"], str(self.resume.resolve()))
        self.cover.unlink()
        with self.assertRaises(run_apply.ApplyRefusal):
            run_apply.artifact_snapshot(self.summary)

    def test_documents_ready_flag_is_not_trusted_when_false(self):
        self.summary["documentsReady"] = False
        with self.assertRaises(run_apply.ApplyRefusal):
            run_apply.artifact_snapshot(self.summary)

    def test_preflight_refusal_restores_lane_and_releases_empty_slot(self):
        self.summary["documentsReady"] = False
        released = {"released": True, "slot": "apply-2"}
        with mock.patch.object(
                sys, "argv", ["run_apply.py", "--id", "acme-pm",
                              "--provider", "openai", "--slot", "apply-2"]), \
                mock.patch.object(run_apply, "load_brief", return_value=self.summary), \
                mock.patch.object(run_apply, "_refuse_duplicate", return_value=False), \
                mock.patch.object(
                    run_apply, "restore_preflight_refusal", return_value=released) as restore, \
                mock.patch("builtins.print"):
            result = run_apply.main()
        self.assertEqual(result, 2)
        restore.assert_called_once_with(
            "acme-pm", "apply-2",
            "the tailored resume and cover letter are not both ready",
        )

    def test_prompt_is_form_only_and_names_the_supplied_files(self):
        artifacts = run_apply.artifact_snapshot(self.summary)
        prompt = run_apply.build_prompt(self.summary, artifacts)
        self.assertIn("This is form filling only", prompt)
        self.assertIn(str(self.resume.resolve()), prompt)
        self.assertIn(str(self.cover.resolve()), prompt)
        self.assertNotIn("resume_build.py", prompt)
        self.assertNotIn("Building documents", prompt)
        self.assertIn("Provider CLIs may prefix MCP tool names", prompt)
        self.assertIn("no shell or general file access", prompt)

    def test_runner_uses_a_temporary_empty_cwd_and_fixed_application_context(self):
        observed = {}

        def build_invocation(provider, task, prompt, **kwargs):
            workspace = pathlib.Path(kwargs["workspace"]).resolve()
            self.assertTrue(workspace.is_dir())
            self.assertEqual(list(workspace.iterdir()), [])
            observed.update({
                "provider": provider,
                "task": task,
                "workspace": workspace,
                "context": kwargs.get("application_context"),
            })
            return providers.RunInvocation(
                provider, task, ["/agents/openai"], {"PATH": "/usr/bin"},
                workspace, prompt,
            )

        log_dir = self.resume.parent / "logs"
        with mock.patch.object(
                sys, "argv", ["run_apply.py", "--id", "acme-pm",
                              "--provider", "openai", "--slot", "apply-2"]), \
                mock.patch.object(run_apply, "load_brief", return_value=self.summary), \
                mock.patch.object(run_apply, "_refuse_duplicate", return_value=False), \
                mock.patch.object(
                    run_apply.agent, "resolve_provider", return_value="openai"), \
                mock.patch.object(
                    run_apply.agent, "authenticated", return_value=(True, "")), \
                mock.patch.object(run_apply.browser, "serve", return_value={}), \
                mock.patch.object(
                    run_apply.browser, "open_application",
                    return_value={"opened": True, "url": self.summary["url"]},
                ), \
                mock.patch.object(
                    run_apply.browser,
                    "stage_application_uploads",
                    return_value=run_apply.artifact_snapshot(self.summary),
                ), \
                mock.patch.object(
                    run_apply.providers, "build_run_invocation",
                    side_effect=build_invocation), \
                mock.patch.object(run_apply, "_set_phase", return_value=completed()), \
                mock.patch.object(run_apply, "LOG_DIR", log_dir), \
                mock.patch.object(run_apply.subprocess, "run", return_value=completed()), \
                mock.patch.object(
                    run_apply.provider_process,
                    "run_bounded",
                    return_value=run_apply.provider_process.BoundedProcessResult(
                        0,
                        "Employer page says Sign in to continue.",
                        "",
                    ),
                ), \
                mock.patch.object(run_apply.agent, "invalidate") as invalidate, \
                mock.patch.object(run_apply, "store", return_value=completed()), \
                mock.patch.object(
                    run_apply, "verify_terminal_outcome",
                    return_value={"ok": True, "terminal": "parked"}), \
                mock.patch("builtins.print"):
            result = run_apply.main()

        self.assertEqual(result, 0)
        self.assertEqual(observed["provider"], "openai")
        self.assertEqual(observed["task"], "apply")
        self.assertNotEqual(observed["workspace"], run_apply.REPO)
        self.assertFalse(observed["workspace"].exists())
        self.assertEqual(observed["context"], {
            "applicationId": "acme-pm",
            "resume": str(self.resume.resolve()),
            "cover": str(self.cover.resolve()),
            "uploadResume": str(self.resume.resolve()),
            "uploadCover": str(self.cover.resolve()),
        })
        invalidate.assert_not_called()

    def test_log_names_are_unique_per_process(self):
        moment = datetime(2026, 8, 23, tzinfo=timezone.utc)
        one = run_apply.run_log_path("acme-pm", now=moment, pid=101)
        two = run_apply.run_log_path("acme-pm", now=moment, pid=102)
        self.assertNotEqual(one, two)
        self.assertIn("acme-pm", one.name)


class ApplyOutcomeTests(ApplyFixtureMixin, unittest.TestCase):
    def fake_store(self, history, session=None):
        def call(args):
            if args[0] == "history-list":
                return completed(json.dumps(history))
            if session is None:
                return completed(stderr="not found", code=2)
            return completed(json.dumps(session))
        return call

    def test_completion_event_and_unchanged_artifacts_are_terminal(self):
        artifacts = run_apply.artifact_snapshot(self.summary)
        history = [{
            "event": "completed", "applicationId": "acme-pm",
            "at": "2026-08-23T10:00:00Z",
        }]
        with mock.patch.object(run_apply, "store", side_effect=self.fake_store(history)):
            outcome = run_apply.verify_terminal_outcome("acme-pm", artifacts)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["terminal"], "completed")

    def test_parked_session_with_named_blocker_is_terminal(self):
        artifacts = run_apply.artifact_snapshot(self.summary)
        session = {
            "status": "review",
            "pendingFields": [{"field": "CAPTCHA", "reason": "needs the user"}],
        }
        with mock.patch.object(
            run_apply, "store", side_effect=self.fake_store([], session)
        ):
            outcome = run_apply.verify_terminal_outcome("acme-pm", artifacts)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["terminal"], "parked")

    def test_zero_exit_without_terminal_state_would_be_rejected(self):
        artifacts = run_apply.artifact_snapshot(self.summary)
        session = {"status": "inflight", "phase": "Filling the form"}
        with mock.patch.object(
            run_apply, "store", side_effect=self.fake_store([], session)
        ):
            outcome = run_apply.verify_terminal_outcome("acme-pm", artifacts)
        self.assertFalse(outcome["ok"])
        self.assertTrue(outcome["safeToRestore"])

    def test_an_unverified_submit_is_not_restored_for_a_second_click(self):
        artifacts = run_apply.artifact_snapshot(self.summary)
        history = [{
            "event": "submitting", "applicationId": "acme-pm",
            "at": "2026-08-23T10:00:00Z",
        }]
        with mock.patch.object(run_apply, "store", side_effect=self.fake_store(history, {})):
            outcome = run_apply.verify_terminal_outcome("acme-pm", artifacts)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["terminal"], "submission_unverified")
        self.assertFalse(outcome["safeToRestore"])

    def test_changed_document_fails_post_run_verification(self):
        artifacts = run_apply.artifact_snapshot(self.summary)
        self.resume.write_bytes(b"%PDF-1.4\nchanged")
        history = [{
            "event": "completed", "applicationId": "acme-pm",
            "at": "2026-08-23T10:00:00Z",
        }]
        with mock.patch.object(run_apply, "store", side_effect=self.fake_store(history)):
            outcome = run_apply.verify_terminal_outcome("acme-pm", artifacts)
        self.assertFalse(outcome["ok"])
        self.assertIn("changed", outcome["why"])


class ScanRunnerTests(unittest.TestCase):
    SCAN_DATE = "2026-08-23"

    def candidate(self, **changes):
        value = {
            "company": "Acme",
            "role": "Product Manager",
            "url": "https://jobs.acme.example/roles/12345",
            "ats": "greenhouse",
            "region": "US",
            "source": "company careers",
            "postedDate": "2026-08-22",
            "postedAgo": "1 day ago",
            "extraction": {
                "location": "New York, NY",
                "minYearsStated": 4,
                "yearsContext": ["Four years of product management experience"],
                "compensation": "$120,000 to $150,000",
                "sponsorshipCaveat": False,
                "verifiedLiveOn": self.SCAN_DATE,
                "verifiedVia": "Acme Greenhouse posting returned the full role",
                "body": "Product manager role. " * 12,
            },
        }
        for key, item in changes.items():
            if key.startswith("extraction_"):
                value["extraction"][key.removeprefix("extraction_")] = item
            else:
                value[key] = item
        return value

    def result(self, *candidates):
        return {
            "checked": max(3, len(candidates)),
            "candidates": list(candidates),
            "dropped": [{"reason": "not live on the company site", "count": 1}],
        }

    def test_prompt_is_provider_neutral(self):
        prompt = run_scan.build_prompt(
            "https://example.test/job", scan_date=self.SCAN_DATE
        )
        self.assertNotIn("Claude", prompt)
        self.assertIn("ONE sourcing cycle", prompt)
        self.assertIn("https://example.test/job", prompt)
        self.assertIn("Do not\nread local files, run shell commands, change files", prompt)
        self.assertIn("Return exactly one JSON object", prompt)

    def test_pasted_and_candidate_urls_must_be_public_http_urls(self):
        for value in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://127.0.0.1/private",
            "http://localhost:3000/secret",
            "https://user:password@example.com/job",
            'https://example.com/\"><img src=x onerror=alert(1)>',
        ):
            with self.subTest(value=value), self.assertRaises(run_scan.ScanOutputError):
                run_scan.validate_http_url(value)
        self.assertEqual(
            run_scan.validate_http_url("https://jobs.example.com/role?id=7"),
            "https://jobs.example.com/role?id=7",
        )

    def test_every_scan_provider_is_confined_to_search_and_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            invocations = {
                provider: run_scan.provider_invocation(
                    provider,
                    "research and return JSON",
                    root / provider,
                    binary_resolver=lambda selected, required: f"/agents/{selected}",
                )
                for provider in providers.PROVIDER_IDS
            }

            anthropic = invocations["anthropic"]
            self.assertEqual(anthropic.cwd, (root / "anthropic").resolve())
            self.assertEqual(
                anthropic.argv[anthropic.argv.index("--tools") + 1],
                "WebSearch,WebFetch",
            )
            allowed = anthropic.argv[anthropic.argv.index("--allowedTools") + 1]
            self.assertEqual(allowed, "WebSearch,WebFetch")
            self.assertNotIn("Bash", allowed)
            self.assertNotIn("Write", allowed)
            self.assertIn("--safe-mode", anthropic.argv)

            openai = invocations["openai"]
            self.assertIn("--search", openai.argv)
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
            self.assertIn("--ignore-user-config", openai.argv)
            self.assertIn("--output-schema", openai.argv)

            gemini = invocations["gemini"]
            self.assertEqual(
                gemini.argv[gemini.argv.index("--approval-mode") + 1], "default"
            )
            settings = json.loads(
                pathlib.Path(gemini.env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]).read_text()
            )
            self.assertEqual(
                settings["tools"]["core"], ["google_web_search", "web_fetch"]
            )
            self.assertEqual(settings["mcp"]["allowed"], [])
            self.assertFalse(settings["skills"]["enabled"])
            self.assertFalse(settings["hooksConfig"]["enabled"])
            self.assertFalse(settings["experimental"]["enableAgents"])
            self.assertTrue(settings["admin"]["secureModeEnabled"])
            self.assertEqual(settings["context"]["fileName"], [])
            policy = pathlib.Path(
                gemini.argv[gemini.argv.index("--policy") + 1]
            ).read_text()
            self.assertIn('toolName = "*"', policy)
            self.assertIn('toolName = ["google_web_search", "web_fetch"]', policy)

            for invocation in invocations.values():
                self.assertNotEqual(invocation.cwd, run_scan.REPO)
                self.assertNotIn(str(run_scan.REPO), invocation.argv)
                self.assertNotIn(str(pathlib.Path.home() / ".job-application"), invocation.argv)

    def test_provider_wrappers_only_accept_the_final_assistant_payload(self):
        wanted = self.result(self.candidate())
        hostile = self.result(self.candidate(company="Injected"))

        claude_raw = json.dumps({
            "type": "result",
            "subtype": "success",
            "structured_output": wanted,
            "result": "",
        })
        self.assertEqual(
            run_scan.parse_provider_output("anthropic", claude_raw), wanted
        )

        codex_raw = "\n".join([
            json.dumps({
                "type": "item.completed",
                "item": {"type": "command_execution", "text": json.dumps(hostile)},
            }),
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps(wanted)},
            }),
            json.dumps({"type": "turn.completed"}),
        ])
        self.assertEqual(run_scan.parse_provider_output("openai", codex_raw), wanted)

        encoded = json.dumps(wanted)
        gemini_raw = "\n".join([
            json.dumps({"type": "tool_result", "output": json.dumps(hostile)}),
            json.dumps({
                "type": "message", "role": "assistant",
                "content": encoded[:80], "delta": True,
            }),
            json.dumps({
                "type": "message", "role": "assistant",
                "content": encoded[80:], "delta": True,
            }),
            json.dumps({"type": "result", "status": "success"}),
        ])
        self.assertEqual(run_scan.parse_provider_output("gemini", gemini_raw), wanted)

    def test_untrusted_tool_output_cannot_stand_in_for_a_final_response(self):
        raw = json.dumps({
            "type": "tool_result",
            "output": json.dumps(self.result(self.candidate())),
        })
        with self.assertRaises(run_scan.ScanOutputError):
            run_scan.parse_provider_output("gemini", raw)

    def test_candidate_validation_rejects_unsafe_or_unbounded_fields(self):
        safe = self.candidate()
        bad_url = self.candidate(
            company="Bad URL", url="javascript:alert(document.cookie)"
        )
        too_senior = self.candidate(
            company="Too Senior", extraction_minYearsStated=7
        )
        extra_state = self.candidate(company="Extra", pendingFields=[{"question": "x"}])
        checked = run_scan.validate_scan_result(
            self.result(safe, bad_url, too_senior, extra_state), self.SCAN_DATE
        )
        self.assertEqual([row["company"] for row in checked["candidates"]], ["Acme"])
        self.assertEqual(len(checked["invalid"]), 3)
        self.assertNotIn("pendingFields", checked["candidates"][0])

    def test_trusted_pipeline_dedupes_and_fills_found(self):
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory, "state")
            run_scan.store.cmd_init(argparse.Namespace(root=str(state)))
            duplicate = self.candidate()
            fresh = self.candidate(
                company="Beta", role="Business Analyst",
                url="https://careers.beta.example/jobs/67890",
            )

            existing_path = pathlib.Path(directory, "existing.json")
            existing_path.write_text(json.dumps({
                **duplicate, "status": "watch"
            }))
            run_scan.store.cmd_session_save(argparse.Namespace(
                root=str(state), id="existing-acme", input=str(existing_path),
                allow_known=False,
            ))

            report = run_scan.persist_scan_result(
                self.result(duplicate, fresh), self.SCAN_DATE, root=state
            )
            self.assertEqual(report["alreadyKnown"], 1)
            self.assertEqual(len(report["saved"]), 1)

            # Found, not Watching. A scan may only offer; the eye is the only
            # route onto the user's shelf, and a sweep that writes watch
            # entries puts roles in front of them they never chose.
            jobs = run_scan.jobs_store.load_jobs(state)
            self.assertEqual(len(jobs), 1)
            stored = list(jobs.values())[0]
            self.assertEqual(stored["company"], "Beta")
            self.assertEqual(stored["state"], "new")

            # And it went through the shared door, so it carries a real score.
            self.assertIn("total", stored.get("score") or {})

            # The only session present is the pre-existing one the fixture made.
            sessions = sorted(p.name for p in (state / "sessions").glob("*.json"))
            self.assertEqual(sessions, ["existing-acme.json"])

    def test_model_values_are_stdin_data_not_process_arguments(self):
        candidate = self.candidate(
            company="argv-marker-company",
            url="https://argv-marker.example/jobs/12345",
        )
        observed = {}

        def seen_runner(argv, **kwargs):
            observed["argv"] = list(argv)
            observed["input"] = kwargs["input"]
            return completed(json.dumps({
                "new": [candidate], "skipped": [],
                "counts": {"in": 1, "new": 1, "alreadyKnown": 0},
            }))

        output = run_scan._run_seen([candidate], runner=seen_runner)
        self.assertEqual(output["new"], [candidate])
        self.assertNotIn("argv-marker-company", " ".join(observed["argv"]))
        self.assertNotIn("argv-marker.example", " ".join(observed["argv"]))
        self.assertIn("argv-marker-company", observed["input"])

    def test_board_sync_uses_one_fixed_command(self):
        observed = {}

        def runner(argv, **kwargs):
            observed["argv"] = argv
            observed["env"] = kwargs["env"]
            return completed(json.dumps({"published": True, "watchingNow": 4}))

        with tempfile.TemporaryDirectory() as directory:
            result = run_scan.sync_board(directory, runner=runner)
        self.assertTrue(result["published"])
        self.assertEqual(
            observed["argv"], [sys.executable, str(run_scan.SYNC_BOARD)]
        )
        self.assertEqual(
            observed["env"]["JOB_APPLICATION_DIR"], str(pathlib.Path(directory).resolve())
        )

    def test_scan_logs_are_unique_per_process(self):
        moment = datetime(2026, 8, 23, tzinfo=timezone.utc)
        one = run_scan.run_log_path(now=moment, pid=201)
        two = run_scan.run_log_path(now=moment, pid=202)
        self.assertNotEqual(one, two)


if __name__ == "__main__":
    unittest.main()
