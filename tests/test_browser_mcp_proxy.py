"""The Playwright MCP firewall exposes only fixed application form actions."""

import io
import json
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import browser_mcp_proxy  # noqa: E402


def request(request_id, name, arguments):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


class BrowserMcpPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name).resolve()
        self.resume = root / "resume.pdf"
        self.cover = root / "cover.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.policy = browser_mcp_proxy.BrowserPolicy(
            [str(self.resume), str(self.cover)]
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_tool_list_is_a_static_allowlist_with_tight_schemas(self):
        child_tools = [
            {"name": name, "inputSchema": {"type": "object"}}
            for name in self.policy.tool_names
        ] + [
            {"name": "browser_evaluate"},
            {"name": "browser_run_code_unsafe"},
            {"name": "browser_network_requests"},
        ]
        filtered = self.policy.filter_tools_result({"tools": child_tools})["tools"]
        by_name = {tool["name"]: tool for tool in filtered}
        self.assertEqual(set(by_name), self.policy.tool_names)
        self.assertNotIn("filename", by_name["browser_snapshot"]["inputSchema"]["properties"])
        self.assertEqual(
            by_name["browser_file_upload"]["inputSchema"]["properties"]["paths"]
            ["items"]["enum"],
            [str(self.resume), str(self.cover)],
        )
        self.assertEqual(
            set(by_name["browser_tabs"]["inputSchema"]["properties"]["action"]["enum"]),
            {"list", "select"},
        )
        # Claude Code drops the whole server's tool list, not just the offending
        # tool, when one advertised schema is not an object schema. Two tools
        # used a bare top-level oneOf, so the form agent got no page tools at all
        # and parked every application. The pairing rules those schemas expressed
        # are enforced in the validators, which is the boundary that binds.
        for name, tool in sorted(by_name.items()):
            with self.subTest(tool=name):
                schema = tool["inputSchema"]
                self.assertEqual(schema.get("type"), "object")
                self.assertIsInstance(schema.get("properties"), dict)
                self.assertFalse(set(schema) & {"oneOf", "anyOf", "allOf", "not"})

    def test_direct_code_console_network_storage_route_and_drop_tools_are_rejected(self):
        denied = [
            "browser_run_code_unsafe",
            "browser_evaluate",
            "browser_console_messages",
            "browser_network_requests",
            "browser_storage_state",
            "browser_route",
            "browser_drop",
            "run_command",
        ]
        for index, name in enumerate(denied):
            with self.subTest(name=name):
                decision = self.policy.prepare_client_message(request(index, name, {}))
                self.assertIsNone(decision.forward)
                self.assertTrue(decision.response["result"]["isError"])

    def test_snapshot_filename_and_submit_shortcuts_are_rejected(self):
        rejected = [
            request(1, "browser_snapshot", {"filename": "private.md"}),
            request(2, "browser_type", {
                "target": "field", "text": "value", "submit": True,
            }),
            request(3, "browser_press_key", {"key": "Enter"}),
            request(4, "browser_press_key", {"key": "Control+Enter"}),
        ]
        for message in rejected:
            decision = self.policy.prepare_client_message(message)
            self.assertIsNone(decision.forward)
            self.assertTrue(decision.response["result"]["isError"])
        allowed = self.policy.prepare_client_message(
            request(5, "browser_press_key", {"key": "Tab"})
        )
        self.assertIsNotNone(allowed.forward)

    def test_tabs_can_only_list_or_select_existing_tabs(self):
        for arguments in ({"action": "new", "url": "https://example.com"},
                          {"action": "close", "index": 0},
                          {"action": "select", "index": 0, "url": "https://example.com"}):
            with self.subTest(arguments=arguments):
                decision = self.policy.prepare_client_message(
                    request(1, "browser_tabs", arguments)
                )
                self.assertIsNone(decision.forward)
        self.assertIsNotNone(self.policy.prepare_client_message(
            request(2, "browser_tabs", {"action": "list"})
        ).forward)
        self.assertIsNotNone(self.policy.prepare_client_message(
            request(3, "browser_tabs", {"action": "select", "index": 1})
        ).forward)

    def test_uploads_are_exact_and_reverified_on_every_call(self):
        allowed = self.policy.prepare_client_message(request(
            1, "browser_file_upload", {"paths": [str(self.resume), str(self.cover)]}
        ))
        self.assertIsNotNone(allowed.forward)

        other = pathlib.Path(self.temp.name) / "other.pdf"
        other.write_bytes(b"%PDF-1.4\nother")
        denied = self.policy.prepare_client_message(request(
            2, "browser_file_upload", {"paths": [str(other.resolve())]}
        ))
        self.assertIsNone(denied.forward)

        self.resume.write_bytes(b"%PDF-1.4\nchanged")
        changed = self.policy.prepare_client_message(request(
            3, "browser_file_upload", {"paths": [str(self.resume)]}
        ))
        self.assertIsNone(changed.forward)

    def test_startup_requires_two_distinct_regular_pdfs(self):
        with self.assertRaises(browser_mcp_proxy.PolicyError):
            browser_mcp_proxy.BrowserPolicy([str(self.resume)])
        with self.assertRaises(browser_mcp_proxy.PolicyError):
            browser_mcp_proxy.BrowserPolicy([str(self.resume), str(self.resume)])
        text_file = pathlib.Path(self.temp.name) / "not.pdf"
        text_file.write_text("not a PDF")
        with self.assertRaises(browser_mcp_proxy.PolicyError):
            browser_mcp_proxy.BrowserPolicy([str(self.resume), str(text_file.resolve())])

    def test_every_direct_navigation_url_is_rejected(self):
        for url in (
            "file:///etc/passwd",
            "http://127.0.0.1:9222/json",
            "http://169.254.169.254/latest/meta-data/",
            "http://2130706433/json",
            "http://0177.0.0.1/json",
            "http://[::1]:9222/json",
            "http://internal.invalid/redirect-to-metadata",
            "javascript:alert(1)",
            "https://user:password@example.com/apply",
            "https://jobs.example.com/apply",
        ):
            with self.subTest(url=url):
                decision = self.policy.prepare_client_message(
                    request(1, "browser_navigate", {"url": url})
                )
                self.assertIsNone(decision.forward)
                self.assertTrue(decision.response["result"]["isError"])

    def test_child_requests_and_notifications_are_never_forwarded(self):
        child_request = {
            "jsonrpc": "2.0", "id": "child-1",
            "method": "sampling/createMessage", "params": {},
        }
        decision = self.policy.filter_child_message(child_request, None)
        self.assertIsNone(decision.to_client)
        self.assertEqual(decision.to_child["id"], "child-1")
        self.assertEqual(decision.to_child["error"]["code"], -32601)

        notification = {
            "jsonrpc": "2.0", "method": "notifications/message", "params": {},
        }
        decision = self.policy.filter_child_message(notification, None)
        self.assertIsNone(decision.to_client)
        self.assertIsNone(decision.to_child)

    def test_bounded_line_reader_drains_one_oversized_message(self):
        source = io.BytesIO(b"a" * 40 + b"\n{}\n")
        raw, oversized = browser_mcp_proxy.read_bounded_line(source, 16)
        self.assertTrue(oversized)
        self.assertEqual(raw, b"")
        raw, oversized = browser_mcp_proxy.read_bounded_line(source, 16)
        self.assertFalse(oversized)
        self.assertEqual(raw, b"{}\n")

    def test_proxy_launches_child_and_filters_messages_end_to_end(self):
        fake = pathlib.Path(self.temp.name) / "fake_child.py"
        fake.write_text(textwrap.dedent("""
            import json
            import sys

            for raw in sys.stdin:
                message = json.loads(raw)
                method = message.get("method")
                if "id" not in message:
                    continue
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": {"name": "playwright", "version": "test"},
                    }
                elif method == "tools/list":
                    result = {"tools": [
                        {"name": "browser_snapshot", "inputSchema": {}},
                        {"name": "browser_evaluate", "inputSchema": {}},
                    ]}
                else:
                    result = {"content": [{"type": "text", "text": method}]}
                print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                  "result": result}), flush=True)
        """))
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            request(3, "browser_evaluate", {"function": "() => 1"}),
        ]
        completed = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts" / "browser_mcp_proxy.py"),
                "--upload", str(self.resume), "--upload", str(self.cover), "--",
                sys.executable, str(fake),
            ],
            input="".join(json.dumps(message) + "\n" for message in messages),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        replies = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [3, 1, 2])
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertEqual(
            replies[1]["result"]["capabilities"],
            {"tools": {"listChanged": False}},
        )
        self.assertEqual(
            [tool["name"] for tool in replies[2]["result"]["tools"]],
            ["browser_snapshot"],
        )




class StatelessProtocolTests(unittest.TestCase):
    """The 2026-07-28 revision, which Claude Code uses, never sends initialize.

    A client that discovers this revision reads capabilities from the discovery
    answer and treats a result without the completion markers as coming from a
    server it cannot use. The firewall answered discovery in the new revision
    while replying in the old shape, so Claude Code reported the server
    connected and registered none of its tools. Every apply run then had no way
    to touch the page. application_mcp.py already had this; keep them in step.
    """

    MODERN = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name).resolve()
        self.resume = root / "resume.pdf"
        self.cover = root / "cover.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.policy = browser_mcp_proxy.BrowserPolicy(
            [str(self.resume), str(self.cover)]
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_discovery_answer_carries_the_stateless_completion_markers(self):
        decision = self.policy.prepare_client_message({
            "jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {},
        })
        result = decision.response["result"]
        self.assertEqual(result["supportedVersions"], ["2026-07-28"])
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            browser_mcp_proxy.SERVER_NAME,
        )
        self.assertEqual(result["cacheScope"], "private")

    def test_a_stateless_tool_list_is_marked_complete_and_a_legacy_one_is_not(self):
        child = {"tools": [{"name": name} for name in self.policy.tool_names]}
        for modern, expected in ((True, "complete"), (False, None)):
            with self.subTest(modern=modern):
                params = {"_meta": dict(self.MODERN)} if modern else {}
                decision = self.policy.prepare_client_message({
                    "jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": params,
                })
                self.assertEqual(decision.modern, modern)
                answer = self.policy.filter_child_message(
                    {"jsonrpc": "2.0", "id": 7, "result": child},
                    "tools/list", decision.modern,
                )
                result = answer.to_client["result"]
                self.assertEqual(len(result["tools"]), len(self.policy.tool_names))
                self.assertEqual(result.get("resultType"), expected)

    def test_a_stateless_tool_call_result_is_marked_complete(self):
        decision = self.policy.prepare_client_message({
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "browser_tabs", "arguments": {"action": "list"},
                       "_meta": dict(self.MODERN)},
        })
        self.assertTrue(decision.modern)
        answer = self.policy.filter_child_message(
            {"jsonrpc": "2.0", "id": 8,
             "result": {"content": [{"type": "text", "text": "ok"}]}},
            "tools/call", decision.modern,
        )
        self.assertEqual(answer.to_client["result"]["resultType"], "complete")

    def test_flattened_schemas_still_refuse_the_pairings_the_oneOf_expressed(self):
        for name, arguments in (
            ("browser_find", {}),
            ("browser_find", {"text": "a", "regex": "b"}),
            ("browser_tabs", {"action": "select"}),
            ("browser_tabs", {"action": "close"}),
            ("browser_tabs", {"action": "list", "index": 2}),
        ):
            with self.subTest(name=name, arguments=arguments):
                decision = self.policy.prepare_client_message(
                    request(1, name, arguments)
                )
                self.assertIsNone(decision.forward)
                self.assertTrue(decision.response["result"]["isError"])


class AttachFileTests(unittest.TestCase):
    """The upload that does not need a file chooser.

    Playwright MCP's browser_file_upload answers only while a chooser is open,
    and the click that opens one is the click that hangs: a file input is
    usually a 1px transparent element behind a styled button, so Playwright
    waits for actionability that never comes. Measured on a real GitLab form on
    2026-08-27, where six upload calls returned "can only be used when there is
    related modal state present" and the application parked with neither PDF
    attached. setInputFiles needs no chooser and no visibility.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name).resolve()
        self.resume = root / "resume.pdf"
        self.cover = root / "cover.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.policy = browser_mcp_proxy.BrowserPolicy(
            [str(self.resume), str(self.cover)]
        )

    def tearDown(self):
        self.temp.cleanup()

    def forward(self, arguments):
        decision = self.policy.prepare_client_message(
            request(1, "browser_attach_file", arguments)
        )
        self.assertIsNotNone(decision.forward, decision.response)
        return decision.forward["params"]

    def test_it_reaches_the_page_as_the_one_tool_the_firewall_removes(self):
        params = self.forward({"paths": [str(self.resume)]})
        self.assertEqual(params["name"], "browser_run_code_unsafe")
        self.assertEqual(set(params["arguments"]), {"code"})

    def test_that_tool_is_still_unreachable_by_name(self):
        decision = self.policy.prepare_client_message(
            request(1, "browser_run_code_unsafe",
                    {"code": "async (page) => 'anything at all'"})
        )
        self.assertIsNone(decision.forward)
        self.assertNotIn("browser_run_code_unsafe", self.policy.tool_names)

    def test_the_paths_that_reach_the_page_are_the_bound_ones(self):
        code = self.forward({"paths": [str(self.resume)]})["arguments"]["code"]
        self.assertIn(json.dumps(str(self.resume)), code)
        with self.assertRaises(browser_mcp_proxy.PolicyError):
            self.policy.validate_tool_call(
                "browser_attach_file", {"paths": ["/etc/passwd"]}
            )

    def test_client_text_becomes_a_javascript_literal_and_never_source(self):
        """The only two values the agent chooses cross as JSON, not as code."""
        hostile = "'); process.exit(1); //"
        code = self.forward(
            {"paths": [str(self.resume)], "target": hostile}
        )["arguments"]["code"]
        self.assertIn(json.dumps(hostile), code)
        self.assertNotIn("process.exit", code.replace(json.dumps(hostile), ""))

    def test_the_two_separators_json_leaves_raw_are_escaped(self):
        """U+2028 and U+2029 are legal in JSON and end a line in JavaScript."""
        code = self.forward(
            {"paths": [str(self.resume)], "target": "a b c"}
        )["arguments"]["code"]
        self.assertNotIn(" ", code)
        self.assertNotIn(" ", code)
        self.assertIn("\\u2028", code)

    def test_it_is_advertised_only_when_the_child_can_perform_it(self):
        without = self.policy.filter_tools_result(
            {"tools": [{"name": "browser_snapshot"}]}
        )
        self.assertNotIn(
            "browser_attach_file", [tool["name"] for tool in without["tools"]]
        )
        with_it = self.policy.filter_tools_result(
            {"tools": [{"name": "browser_run_code_unsafe"}]}
        )
        self.assertEqual(
            [tool["name"] for tool in with_it["tools"]], ["browser_attach_file"]
        )

    def test_its_schema_is_an_object_like_every_other(self):
        """A bare oneOf makes a client drop the whole server's tool list."""
        schema = next(
            tool["inputSchema"] for tool in self.policy.tool_definitions
            if tool["name"] == "browser_attach_file"
        )
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["properties"]["paths"]["items"]["enum"],
                         [str(self.resume), str(self.cover)])


class ActivityRecordTests(unittest.TestCase):
    """A park with no browser calls is a capability failure, not a blocker.

    Both look identical from outside: "Needs you", with a plausible sentence
    about the form. One needs the user and the other needs an engineer, and the
    firewall is the only part of a run that sees every call.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name).resolve()
        self.resume = root / "resume.pdf"
        self.cover = root / "cover.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nresume")
        self.cover.write_bytes(b"%PDF-1.4\ncover")
        self.policy = browser_mcp_proxy.BrowserPolicy(
            [str(self.resume), str(self.cover)]
        )
        self.target = root / "activity.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_it_counts_only_the_calls_the_firewall_accepted(self):
        self.policy.prepare_client_message(request(1, "browser_snapshot", {}))
        self.policy.prepare_client_message(request(2, "browser_snapshot", {}))
        self.policy.prepare_client_message(
            request(3, "browser_attach_file", {"paths": [str(self.cover)]})
        )
        self.policy.prepare_client_message(
            request(4, "browser_click", {"target": "e1"})
        )
        self.policy.prepare_client_message(
            request(5, "browser_file_upload", {"paths": ["/etc/passwd"]})
        )
        browser_mcp_proxy._write_activity(
            str(self.target), self.policy, browser_mcp_proxy.deque(), 0
        )
        recorded = json.loads(self.target.read_text())
        self.assertEqual(recorded["totalCalls"], 4)
        self.assertEqual(recorded["calls"]["browser_snapshot"], 2)
        self.assertNotIn("browser_file_upload", recorded["calls"])

    def test_a_run_that_never_reached_the_browser_says_zero(self):
        browser_mcp_proxy._write_activity(
            str(self.target), self.policy,
            browser_mcp_proxy.deque(["Error: connect ECONNREFUSED 127.0.0.1:9223"]),
            1,
        )
        recorded = json.loads(self.target.read_text())
        self.assertEqual(recorded["totalCalls"], 0)
        self.assertEqual(recorded["backendExit"], 1)
        # The reason used to go to DEVNULL, which is why this fault was never
        # reproduced: every hand probe afterwards succeeded.
        self.assertIn("ECONNREFUSED", recorded["backendStderr"][0])

    def test_bookkeeping_that_cannot_be_written_does_not_fail_the_run(self):
        browser_mcp_proxy._write_activity(
            str(self.target.parent / "no" / "such" / "dir" / "x" / "a.json"),
            self.policy, browser_mcp_proxy.deque(), 0,
        )
        browser_mcp_proxy._write_activity("", self.policy, browser_mcp_proxy.deque(), 0)


if __name__ == "__main__":
    unittest.main()
