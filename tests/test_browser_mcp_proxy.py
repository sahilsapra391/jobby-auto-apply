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
        tab_choices = by_name["browser_tabs"]["inputSchema"]["oneOf"]
        self.assertEqual(
            {choice["properties"]["action"]["const"] for choice in tab_choices},
            {"list", "select"},
        )

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


if __name__ == "__main__":
    unittest.main()
