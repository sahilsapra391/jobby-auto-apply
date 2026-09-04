"""Provider configs expose one browser and one application-scoped state API."""

import hashlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))
import browser


class BrowserMcpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.state = root / "state"
        self.resume = root / "Resume.pdf"
        self.cover = root / "Cover.pdf"
        self.resume_html = root / ".resume.html"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.resume_html.write_text("<!doctype html><html></html>")
        self.context = {
            "applicationId": "acme-pm",
            "resume": str(self.resume),
            "cover": str(self.cover),
            "resumeHtml": str(self.resume_html),
            "uploadResume": str(self.resume.resolve()),
            "uploadCover": str(self.cover.resolve()),
        }
        self.patches = [
            mock.patch.object(browser, "STATE", self.state),
            mock.patch.object(browser, "PROFILE", self.state / "profile"),
            mock.patch.object(browser, "ARTIFACTS", self.state / "artifacts"),
            mock.patch.object(browser.toolpath, "find", return_value="/usr/local/bin/npx"),
            mock.patch.object(
                browser.toolpath, "search_path", return_value="/usr/local/bin:/usr/bin"
            ),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def test_absent_context_preserves_browser_only_configs(self):
        self.assertEqual(set(browser.server_configs("apply-2")), {"browser"})
        self.assertEqual(browser.mcp_servers("apply-2"), browser.server_configs("apply-2"))
        claude = json.loads(browser.write_config("apply-2").read_text())
        gemini = json.loads(browser.write_gemini_settings("apply-2").read_text())
        self.assertEqual(set(claude["mcpServers"]), {"browser"})
        self.assertEqual(set(gemini["mcpServers"]), {"browser"})

    def test_focus_cli_keeps_the_requested_browser_slot(self):
        with mock.patch.object(
            sys, "argv", ["browser.py", "--focus", "https://example.test/job", "--slot", "apply-5"]
        ), mock.patch.object(
            browser, "focus", return_value={"focused": True, "slot": "apply-5"}
        ) as focus, mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(browser.main(), 0)
        focus.assert_called_once_with("https://example.test/job", slot="apply-5")

    def test_claude_and_codex_data_include_the_bound_jobby_server(self):
        codex_data = browser.server_configs("apply-3", self.context)
        self.assertEqual(set(codex_data), {"browser", "jobby"})
        jobby = codex_data["jobby"]
        self.assertEqual(jobby["command"], str(pathlib.Path(sys.executable).resolve()))
        self.assertEqual(
            jobby["args"][jobby["args"].index("--application-id") + 1], "acme-pm"
        )
        self.assertEqual(jobby["args"][jobby["args"].index("--resume") + 1],
                         str(self.resume))
        self.assertEqual(jobby["args"][jobby["args"].index("--cover") + 1],
                         str(self.cover))
        rendered = " ".join(jobby["args"]).lower()
        self.assertNotIn("bash", rendered)
        self.assertNotIn("--root", rendered)

        claude = json.loads(browser.write_config("apply-3", self.context).read_text())
        self.assertEqual(set(claude["mcpServers"]), {"browser", "jobby"})
        browser_server = claude["mcpServers"]["browser"]
        self.assertEqual(
            browser_server["command"], str(pathlib.Path(sys.executable).resolve())
        )
        self.assertIn(str(browser.BROWSER_MCP_PROXY), browser_server["args"])
        self.assertEqual(browser_server["args"].count("--upload"), 2)
        self.assertIn(str(self.resume.resolve()), browser_server["args"])
        self.assertIn(str(self.cover.resolve()), browser_server["args"])

    def test_gemini_has_only_mcp_tools_and_blocks_user_discovery(self):
        settings = json.loads(
            browser.write_gemini_settings("apply-4", self.context).read_text()
        )
        self.assertEqual(settings["tools"]["core"], [])
        self.assertEqual(settings["tools"]["discoveryCommand"], "")
        self.assertEqual(settings["tools"]["callCommand"], "")
        self.assertEqual(settings["mcp"]["allowed"], ["browser", "jobby"])
        self.assertFalse(settings["skills"]["enabled"])
        self.assertFalse(settings["hooksConfig"]["enabled"])
        self.assertFalse(settings["admin"]["extensions"]["enabled"])
        self.assertFalse(settings["admin"]["skills"]["enabled"])
        self.assertFalse(settings["experimental"]["enableAgents"])
        self.assertFalse(settings["experimental"]["extensionManagement"])
        self.assertFalse(settings["telemetry"]["enabled"])
        self.assertFalse(settings["privacy"]["usageStatisticsEnabled"])
        self.assertEqual(settings["context"]["fileName"], [])
        self.assertFalse(settings["context"]["includeDirectoryTree"])
        self.assertEqual(set(settings["mcpServers"]), {"browser", "jobby"})
        self.assertTrue(settings["mcpServers"]["browser"]["trust"])
        self.assertTrue(settings["mcpServers"]["jobby"]["trust"])

    def test_context_validation_rejects_path_and_id_injection(self):
        bad = dict(self.context, applicationId="../other")
        with self.assertRaises(ValueError):
            browser.mcp_servers("apply-1", bad)

    def test_upload_staging_copies_only_the_two_verified_pdfs(self):
        artifacts = {
            "resume": {
                "path": str(self.resume.resolve()),
                "size": self.resume.stat().st_size,
                "sha256": hashlib.sha256(self.resume.read_bytes()).hexdigest(),
            },
            "coverLetter": {
                "path": str(self.cover.resolve()),
                "size": self.cover.stat().st_size,
                "sha256": hashlib.sha256(self.cover.read_bytes()).hexdigest(),
            },
        }
        staged = browser.stage_application_uploads("apply-2", "acme-pm", artifacts)
        output_root = browser.artifact_root_for("apply-2")

        self.assertEqual(pathlib.Path(staged["resume"]["path"]).read_bytes(),
                         self.resume.read_bytes())
        self.assertEqual(pathlib.Path(staged["coverLetter"]["path"]).read_bytes(),
                         self.cover.read_bytes())
        self.assertEqual(
            set(pathlib.Path(staged["resume"]["path"]).parent.iterdir()),
            {
                pathlib.Path(staged["resume"]["path"]),
                pathlib.Path(staged["coverLetter"]["path"]),
            },
        )
        for item in staged.values():
            self.assertTrue(pathlib.Path(item["path"]).is_relative_to(output_root))

        configs = [
            browser.server_config("apply-2"),
            json.loads(browser.write_config("apply-2").read_text())["mcpServers"]["browser"],
            json.loads(browser.write_gemini_settings("apply-2").read_text())[
                "mcpServers"
            ]["browser"],
        ]
        for config in configs:
            with self.subTest(config=config):
                self.assertEqual(
                    config["args"][config["args"].index("--output-dir") + 1],
                    str(output_root),
                )
                self.assertNotIn("--allow-unrestricted-file-access", config["args"])

    def test_upload_staging_rejects_a_symlink_escape(self):
        root = browser.artifact_root_for("apply-2")
        external = pathlib.Path(self.temp.name) / "outside"
        external.mkdir()
        (root / "uploads").symlink_to(external, target_is_directory=True)
        digest = hashlib.sha256(self.resume.read_bytes()).hexdigest()
        artifacts = {
            "resume": {
                "path": str(self.resume), "size": self.resume.stat().st_size,
                "sha256": digest,
            },
            "coverLetter": {
                "path": str(self.resume), "size": self.resume.stat().st_size,
                "sha256": digest,
            },
        }
        with self.assertRaises(OSError):
            browser.stage_application_uploads("apply-2", "acme-pm", artifacts)
        self.assertEqual(list(external.iterdir()), [])
        bad = dict(self.context, resume="relative.pdf")
        with self.assertRaises(ValueError):
            browser.mcp_servers("apply-1", bad)

    def test_clear_slot_refuses_a_symlinked_artifact_parent(self):
        external = pathlib.Path(self.temp.name) / "external"
        private = external / "apply-2"
        private.mkdir(parents=True)
        marker = private / "private-form.txt"
        marker.write_text("must survive")
        browser.ARTIFACTS.parent.mkdir(parents=True, exist_ok=True)
        browser.ARTIFACTS.symlink_to(external, target_is_directory=True)

        with mock.patch.object(browser, "running", return_value=False):
            result = browser.clear_slot("apply-2")

        self.assertFalse(result["cleared"])
        self.assertTrue(marker.exists())
