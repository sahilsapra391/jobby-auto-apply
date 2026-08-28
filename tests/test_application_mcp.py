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

    def test_exactly_nine_narrow_tools_are_exposed(self):
        names = {tool["name"] for tool in application_mcp.TOOLS}
        self.assertEqual(names, {
            "set_phase",
            "resolve_answers",
            "story_facts",
            "experience_plan",
            "salary_midpoint",
            "park_application",
            "record_answers",
            "pre_submit",
            "record_completed",
        })
        for tool in application_mcp.TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        rendered = json.dumps(application_mcp.TOOLS).lower()
        for forbidden in ("bash", "shell", "read_file", "write_file", "command"):
            self.assertNotIn(forbidden, rendered)

    def test_story_facts_also_carries_the_answers_given_on_other_applications(self):
        # The file and the store overlap and neither contains the other, so an
        # essay composed from the file alone loses six answers written in the
        # user's own words that exist nowhere else.
        (self.store_root / "story-facts.txt").write_text("## Story: a migration\n")
        (self.store_root / "answers.json").write_text(json.dumps({
            "story-motivation": {"question": "What motivates you?",
                                 "value": "The impact of the work.",
                                 "tags": ["free-text", "story-motivation"]},
            "notice": {"question": "Notice period", "value": "15 days", "tags": []},
        }))
        out = self.tools.call("story_facts", {})
        questions = [item["question"] for item in out["previousAnswers"]]
        self.assertEqual(questions, ["What motivates you?"])
        self.assertNotIn("Notice period", questions, "only the free-text bank travels")
        self.assertIn("never paste a prefix", out["note"])

    def test_story_facts_serves_the_bank_the_prompt_already_names(self):
        # The prompt has always listed story facts as a source for a free-text
        # answer, and the brief carried the PATH to a file the agent cannot
        # open. Nothing served the text until this tool.
        (self.store_root / "story-facts.txt").write_text(
            "## Story: a migration\nMoved 75 customers with zero downtime.\n")
        out = self.tools.call("story_facts", {})
        self.assertIn("zero downtime", out["storyFacts"])
        self.assertIn("Invent no number", out["note"])

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


class ParkGateTests(unittest.TestCase):
    """A park may not carry a question the store already answers.

    The prompt has forbidden it since the Robinhood run parked on seven
    stored answers (2026-08-28), and the TELUS run parked "Where did you
    hear about us?" hours later with the answer confirmed since 2026-08-19.
    Saying it is not a guard; the tool refusing is. Disable the gate and
    the first test here fails, which is the point of it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.store_root = root / "state"
        self.store_root.mkdir()
        (self.store_root / "answers.json").write_text(json.dumps({
            "how-did-hear-about-this-job": {
                "question": "How did you hear about this job?",
                "value": "Company website",
                "tags": ["sourcing", "source-how-heard"],
            },
            "expected-compensation": {
                "question": "What is your expected compensation?",
                "value": "$150,000", "state": "sensitive",
                "tags": ["compensation"],
            },
        }))
        (self.store_root / "profile.json").write_text(json.dumps({
            "preferences": {"standingConsents": {
                "recruitmentPrivacyNotice": "confirm on every application",
            }},
        }))
        for name in ("resume.pdf", "cover.pdf"):
            (root / name).write_bytes(b"%PDF-1.4\nx")
        (root / ".resume.html").write_text("<html><body></body></html>")
        self.context = application_mcp.ApplicationContext.create(
            "acme-pm", str(root / "resume.pdf"), str(root / "cover.pdf"),
            str(root / ".resume.html"), str(self.store_root))
        self.tools = application_mcp.ApplicationTools(self.context)

    def tearDown(self):
        self.temp.cleanup()

    def park(self, pending):
        with mock.patch.object(
            application_mcp.finish, "park", return_value={"parked": True}
        ) as parked:
            result = self.tools.call("park_application", {
                "pending": pending, "detail": "needs the user"})
        return parked, result

    def test_a_question_the_bank_answers_is_refused_with_its_value(self):
        with mock.patch.object(application_mcp.finish, "park") as parked:
            with self.assertRaises(application_mcp.ToolInputError) as caught:
                self.tools.call("park_application", {
                    "pending": ["Where did you hear about us?"],
                    "detail": "needs the user"})
        parked.assert_not_called()
        self.assertIn("Company website", str(caught.exception))

    def test_a_question_the_profile_answers_is_refused(self):
        with mock.patch.object(application_mcp.finish, "park") as parked:
            with self.assertRaises(application_mcp.ToolInputError):
                self.tools.call("park_application", {
                    "pending": ["Do you agree to the Recruiting Privacy Policy?"],
                    "detail": "needs the user"})
        parked.assert_not_called()

    def test_a_sensitive_answer_is_refused_without_its_value(self):
        with mock.patch.object(application_mcp.finish, "park"):
            with self.assertRaises(application_mcp.ToolInputError) as caught:
                self.tools.call("park_application", {
                    "pending": ["What is your expected compensation?"],
                    "detail": "needs the user"})
        message = str(caught.exception)
        self.assertNotIn("150,000", message)
        self.assertIn("resolve_answers", message)

    def test_actions_and_genuine_unknowns_still_park(self):
        parked, _ = self.park([
            "Solve the visible CAPTCHA",
            "Attach the resume manually in the embedded frame",
            "What is your favourite colour?",
        ])
        parked.assert_called_once()

    def test_prose_blockers_are_not_resolved_against_the_bank(self):
        # 0 of 29 real blockers were question-shaped (measured 2026-08-27);
        # fuzzy-matching prose would refuse legitimate parks by accident.
        parked, _ = self.park([
            "Email verification code (8 characters) sent to the user",
        ])
        parked.assert_called_once()


class RecordAnswersToolTests(unittest.TestCase):
    """The manifest tool: bounded, checked, and unable to store a secret."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.store_root = root / "state"
        (self.store_root / "sessions").mkdir(parents=True)
        (self.store_root / "answers.json").write_text(json.dumps({
            "expected-compensation": {
                "key": "expected-compensation", "question": "Expected pay?",
                "state": "sensitive", "value": "165000", "tags": ["compensation"]},
        }))
        (self.store_root / "sessions" / "acme-pm.json").write_text(json.dumps({
            "applicationId": "acme-pm", "company": "Acme", "role": "Product Manager"}))
        self.resume = root / "Sahil_Acme_PM_Resume.pdf"
        self.cover = root / "Sahil_Acme_PM_CoverLetter.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.apps = root / "applications"
        self.apps.mkdir()
        self._real_apps = application_mcp.answer_record.APPS
        application_mcp.answer_record.APPS = self.apps
        self.tools = application_mcp.ApplicationTools(
            application_mcp.ApplicationContext.create(
                "acme-pm", str(self.resume), str(self.cover), None,
                str(self.store_root)))

    def tearDown(self):
        application_mcp.answer_record.APPS = self._real_apps
        self.temp.cleanup()

    def record(self):
        return json.loads((self.apps / "Acme" / "answers-acme-pm.json").read_text())

    def test_the_tool_files_the_record_for_its_own_bound_application(self):
        # The application id is bound when the server starts and is not an
        # argument, so a run cannot file a manifest against a different job.
        result = self.tools.call("record_answers", {"fields": [
            {"question": "First name", "answer": "Sahil", "source": "profile",
             "readBack": "match"},
        ]})
        self.assertEqual(result["fields"], 1)
        self.assertEqual(self.record()["applicationId"], "acme-pm")

    def test_the_tool_takes_no_application_id_and_no_sensitive_flag(self):
        schema = next(t["inputSchema"] for t in application_mcp.TOOLS
                      if t["name"] == "record_answers")
        self.assertEqual(set(schema["properties"]), {"fields"})
        self.assertFalse(schema["additionalProperties"])
        field = schema["properties"]["fields"]["items"]
        self.assertFalse(field["additionalProperties"])
        for absent in ("sensitive", "applicationId", "withheld", "path"):
            self.assertNotIn(absent, field["properties"])

    def test_a_verification_code_is_withheld_even_though_the_caller_sent_it(self):
        self.tools.call("record_answers", {"fields": [
            {"question": "Enter the verification code", "answer": "884213",
             "source": "provided", "readBack": "match"},
        ]})
        raw = (self.apps / "Acme" / "answers-acme-pm.json").read_text()
        self.assertNotIn("884213", raw)
        self.assertEqual(json.loads(raw)["fields"][0]["withheld"],
                         "one-time or verification code")

    def test_pay_is_flagged_sensitive_without_the_caller_saying_so(self):
        self.tools.call("record_answers", {"fields": [
            {"question": "Expected pay?", "answer": "165000", "source": "stored",
             "sourceKey": "expected-compensation", "readBack": "match"},
        ]})
        self.assertTrue(self.record()["fields"][0]["sensitive"])

    def test_a_bad_manifest_is_refused_with_a_reason_the_caller_can_act_on(self):
        with self.assertRaises(application_mcp.ToolInputError) as caught:
            self.tools.call("record_answers", {"fields": [
                {"question": "Authorized?", "answer": "Yes", "source": "stored",
                 "sourceKey": "not-a-real-key"},
            ]})
        self.assertIn("not in the answer store", str(caught.exception))
        self.assertFalse(list(self.apps.rglob("answers-*.json")))

    def test_an_extra_argument_is_refused(self):
        with self.assertRaises(application_mcp.ToolInputError):
            self.tools.call("record_answers", {"fields": [], "applicationId": "other"})

    def test_the_schema_is_an_object_schema_like_every_other_tool(self):
        # A bare top-level oneOf makes a client drop the whole server's tool
        # list rather than the one tool.
        for tool in application_mcp.TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertNotIn("oneOf", tool["inputSchema"])


class CaptureContractAgreesInBothPlacesTests(unittest.TestCase):
    """One requirement, two readers, and they must not drift apart.

    The Claude Code skill reads SKILL.md. The spawned provider reads the task
    contract inside run_apply. Both have to tell the agent to build the manifest
    from the read-back rather than from memory, because that is what makes a
    short record unlikely -- and a short record is the failure nothing
    downstream can catch. The server never sees the form, only the manifest it
    is handed, so no gate here can notice a question that was left out.

    This is the same shape as the ATS upload instruction, which also lives in
    both: two contracts to two readers rather than two descriptions of one
    behaviour. What makes that safe rather than the duplication the repo warns
    about is a test comparing them, which is why this exists.
    """

    def setUp(self):
        # Both documents are hand-wrapped prose, so a phrase that matters can
        # straddle a line break. Compare on collapsed whitespace or the test
        # fails on where somebody happened to wrap, which is not the contract.
        root = pathlib.Path(__file__).resolve().parents[1]
        flat = lambda text: " ".join(text.split())
        self.skill = flat((root / "SKILL.md").read_text(encoding="utf-8"))
        self.contract = flat(
            (root / "scripts" / "run_apply.py").read_text(encoding="utf-8"))

    def test_both_require_the_manifest_to_come_from_the_read_back(self):
        for name, text in (("SKILL.md", self.skill), ("run_apply", self.contract)):
            with self.subTest(document=name):
                self.assertIn("from the read-back, not from memory", text)
                self.assertIn("recollection", text)

    def test_both_require_blank_and_prefilled_questions_to_be_recorded(self):
        # The two easiest rows to leave out, and the two that make a record
        # look complete while describing a different form than the one sent.
        for name, text in (("SKILL.md", self.skill), ("run_apply", self.contract)):
            with self.subTest(document=name):
                self.assertIn("left blank", text)
                self.assertIn("prefilled", text)

    def test_both_say_the_omission_cannot_be_caught_downstream(self):
        # The reason the instruction has to carry the weight: there is no
        # number in the system that says how many questions the form had.
        for name, text in (("SKILL.md", self.skill), ("run_apply", self.contract)):
            with self.subTest(document=name):
                self.assertIn("never sees the form", text)


if __name__ == "__main__":
    unittest.main()
