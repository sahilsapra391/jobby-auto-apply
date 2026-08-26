"""The local apply agent gets bounded state tools, not a shell or file API."""

import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))
import application_mcp


class ApplicationMcpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.store_root = root / "state"
        self.store_root.mkdir()
        (self.store_root / "answers.json").write_text("{}")
        self.resume = root / "Sahil_Acme_PM_Resume.pdf"
        self.cover = root / "Sahil_Acme_PM_CoverLetter.pdf"
        self.resume_html = root / ".Sahil_Acme_PM.resume.html"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.resume_html.write_text(
            "<!doctype html><html><body>"
            '<div class="org">Acme<span class="when">2020 - 2024</span></div>'
            '<div class="jobtitle">Product Manager</div>'
            '<div class="body lede">Built factual products.</div>'
            "</body></html>"
        )
        self.context = application_mcp.ApplicationContext.create(
            "acme-pm",
            str(self.resume),
            str(self.cover),
            str(self.resume_html),
            str(self.store_root),
        )
        self.tools = application_mcp.ApplicationTools(self.context)

    def tearDown(self):
        self.temp.cleanup()

    def test_exactly_eight_narrow_tools_are_exposed(self):
        names = {tool["name"] for tool in application_mcp.TOOLS}
        self.assertEqual(names, {
            "set_phase",
            "resolve_answers",
            "story_facts",
            "experience_plan",
            "salary_midpoint",
            "park_application",
            "pre_submit",
            "record_completed",
        })
        for tool in application_mcp.TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        rendered = json.dumps(application_mcp.TOOLS).lower()
        for forbidden in ("bash", "shell", "read_file", "write_file", "command"):
            self.assertNotIn(forbidden, rendered)

    def test_story_facts_serves_the_bank_the_prompt_already_names(self):
        # The prompt has always listed story facts as a source for a free-text
        # answer, and the brief carried the PATH to a file the agent cannot
        # open. Nothing served the text until this tool.
        (self.store_root / "story-facts.txt").write_text(
            "## Story: a migration\nMoved 75 customers with zero downtime.\n")
        out = self.tools.call("story_facts", {})
        self.assertIn("zero downtime", out["storyFacts"])
        self.assertIn("Do not invent", out["note"])

    def test_story_facts_takes_no_arguments_and_reads_no_other_file(self):
        (self.store_root / "story-facts.txt").write_text("## Story: one\n")
        with self.assertRaises(application_mcp.ToolInputError):
            self.tools.call("story_facts", {"path": "/etc/passwd"})

    def test_story_facts_says_so_when_there_are_none(self):
        with self.assertRaises(application_mcp.ToolExecutionError):
            self.tools.call("story_facts", {})
        (self.store_root / "story-facts.txt").write_text("   \n")
        with self.assertRaises(application_mcp.ToolExecutionError):
            self.tools.call("story_facts", {})

    def test_context_requires_safe_id_absolute_real_pdfs(self):
        with self.assertRaises(application_mcp.ToolInputError):
            application_mcp.ApplicationContext.create(
                "../other", str(self.resume), str(self.cover)
            )
        with self.assertRaises(application_mcp.ToolInputError):
            application_mcp.ApplicationContext.create(
                "safe", "relative.pdf", str(self.cover)
            )
        link = self.resume.parent / "linked.pdf"
        link.symlink_to(self.resume)
        with self.assertRaises(application_mcp.ToolInputError):
            application_mcp.ApplicationContext.create("safe", str(link), str(self.cover))

    def test_resume_html_is_derived_by_exact_worker_filename(self):
        context = application_mcp.ApplicationContext.create(
            "acme-pm", str(self.resume), str(self.cover)
        )
        self.assertEqual(context.resume_html, self.resume_html.resolve())

    def test_completed_uses_fixed_id_and_artifacts(self):
        with mock.patch.object(
            application_mcp.finish, "completed", return_value={"logged": True}
        ) as completed:
            result = self.tools.call(
                "record_completed", {
                    "confirmation": "Application received",
                    "submissionNonce": "n" * 32,
                }
            )
        self.assertTrue(result["logged"])
        completed.assert_called_once_with(
            "acme-pm",
            "Application received",
            str(self.resume.resolve()),
            str(self.cover.resolve()),
            "n" * 32,
        )
        with self.assertRaises(application_mcp.ToolInputError):
            self.tools.call("record_completed", {
                "confirmation": "received", "submissionNonce": "n" * 32,
                "resume": "/tmp/other.pdf"
            })

    def test_phase_uses_fixed_id_and_rejects_other_phases(self):
        done = mock.Mock(returncode=0, stdout=json.dumps({
            "id": "acme-pm", "status": "inflight", "phase": "Filling the form"
        }), stderr="")
        with mock.patch.object(application_mcp.subprocess, "run", return_value=done) as run:
            result = self.tools.call("set_phase", {
                "phase": "Filling the form", "detail": "8 of 12 fields"
            })
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--id") + 1], "acme-pm")
        self.assertIn(str(self.store_root.resolve()), command)
        self.assertEqual(result["phase"], "Filling the form")
        with self.assertRaises(application_mcp.ToolInputError):
            self.tools.call("set_phase", {"phase": "Delete everything"})

    def test_answers_are_read_only_and_question_input_is_bounded(self):
        (self.store_root / "answers.json").write_text(json.dumps({
            "notice-period": {
                "question": "Notice period?", "value": "Two weeks", "state": "confirmed"
            }
        }))
        result = self.tools.call("resolve_answers", {"questions": ["Notice period?"]})
        self.assertEqual(result["Notice period?"]["value"], "Two weeks")
        with self.assertRaises(application_mcp.ToolInputError):
            self.tools.call("resolve_answers", {"questions": ["same", "same"]})
        with self.assertRaises(application_mcp.ToolInputError):
            self.tools.call("resolve_answers", {"questions": [], "write": True})

    def test_experience_plan_reads_only_the_fixed_html(self):
        planned = [{"title": "Product Manager", "order": 1}]
        with mock.patch.object(
            application_mcp.experience, "plan", return_value=planned
        ) as plan:
            result = self.tools.call(
                "experience_plan", {"style": "prose", "maxChars": 900}
            )
        supplied_html, supplied_root, maximum, style = plan.call_args.args
        self.assertIn("Built factual products", supplied_html)
        self.assertEqual(supplied_root, self.store_root.resolve())
        self.assertEqual((maximum, style), (900, "prose"))
        self.assertEqual(result["positions"], planned)

    def test_salary_park_and_pre_submit_are_the_only_other_actions(self):
        salary_result = self.tools.call("salary_midpoint", {"band": "$70,000 - $90,000"})
        self.assertEqual(salary_result["answer"], "$80,000")
        with mock.patch.object(
            application_mcp.finish, "park", return_value={"parked": True}
        ) as park:
            self.tools.call("park_application", {
                "pending": ["CAPTCHA"], "detail": "Solve the visible CAPTCHA"
            })
        # A CAPTCHA is a blocker, not a question, so nothing is tagged for the
        # Memory Bank and the fourth argument comes through empty.
        park.assert_called_once_with("acme-pm", ["CAPTCHA"], "Solve the visible CAPTCHA", [])
        with mock.patch.object(
            application_mcp.finish, "pre_submit", return_value={"proceed": False}
        ) as guard:
            self.tools.call("pre_submit", {})
        guard.assert_called_once_with("acme-pm")
        with self.assertRaises(application_mcp.ToolInputError):
            self.tools.call("pre_submit", {"force": True})

    def test_stdio_initialize_list_and_rejected_call_stay_valid_json_rpc(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "run_command", "arguments": {"command": "id"}}},
        ]
        source = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
        sink = io.StringIO()
        application_mcp.serve(self.tools, source, sink)
        replies = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [1, 2, 3])
        self.assertEqual({tool["name"] for tool in replies[1]["result"]["tools"]},
                         application_mcp.TOOL_NAMES)
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertNotIn("command", replies[2]["result"]["structuredContent"]["error"])

    def test_modern_mcp_discovery_and_per_request_metadata_are_supported(self):
        meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        discovered = application_mcp.handle_message({
            "jsonrpc": "2.0", "id": "d", "method": "server/discover",
            "params": {"_meta": meta},
        }, self.tools)
        self.assertEqual(
            discovered["result"]["supportedVersions"], ["2026-07-28"]
        )
        self.assertEqual(discovered["result"]["resultType"], "complete")
        self.assertEqual(discovered["result"]["cacheScope"], "private")
        self.assertEqual(
            discovered["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            "jobby-application",
        )

        listed = application_mcp.handle_message({
            "jsonrpc": "2.0", "id": "l", "method": "tools/list",
            "params": {"_meta": meta},
        }, self.tools)
        self.assertEqual(listed["result"]["resultType"], "complete")
        self.assertEqual({tool["name"] for tool in listed["result"]["tools"]},
                         application_mcp.TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
