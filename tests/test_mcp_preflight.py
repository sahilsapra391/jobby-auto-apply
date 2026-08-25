"""Tests for scripts/mcp_preflight.py, the handshake that runs before an agent.

A server that connects and advertises nothing is the failure this exists for.
It is not an error to any client: the agent simply finds no browser tools,
improvises, and parks the application on a blocker the user cannot act on while
holding one of five slots.

Every server here is a stub written by the test. Nothing spawns npx and nothing
touches the network, which is the point: the tests that drive run_apply used to
start real servers and took eight seconds to say so.

Run from the repo root: python3 -m unittest discover -s tests
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mcp_preflight  # noqa: E402


STUB = '''
import json, sys
TOOLS = {tools!r}
BEHAVIOUR = {behaviour!r}
if BEHAVIOUR == "exit":
    sys.stderr.write("stub refused to start\\n")
    raise SystemExit(3)
for line in sys.stdin:
    try:
        m = json.loads(line)
    except Exception:
        continue
    if m.get("method") == "initialize":
        if BEHAVIOUR == "silent_init":
            continue
        print(json.dumps({{"jsonrpc": "2.0", "id": m["id"], "result": {{
            "protocolVersion": "2025-06-18", "capabilities": {{"tools": {{}}}},
            "serverInfo": {{"name": "stub", "version": "1"}}}}}}), flush=True)
    elif m.get("method") == "tools/list":
        if BEHAVIOUR == "silent_tools":
            continue
        if BEHAVIOUR == "no_list":
            print(json.dumps({{"jsonrpc": "2.0", "id": m["id"],
                              "result": {{}}}}), flush=True)
            continue
        print(json.dumps({{"jsonrpc": "2.0", "id": m["id"], "result": {{
            "tools": [{{"name": n, "inputSchema": {{"type": "object"}}}}
                      for n in TOOLS]}}}}), flush=True)
'''


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="jobby-preflight-"))

    def server(self, tools=(), behaviour="ok"):
        path = self.dir / f"stub-{behaviour}-{len(tools)}.py"
        path.write_text(STUB.format(tools=list(tools), behaviour=behaviour))
        return {"command": sys.executable, "args": [str(path)]}

    def check(self, config, name="browser", required=None):
        return mcp_preflight.check(
            {name: config},
            required=required or {name: mcp_preflight.REQUIRED["browser"]},
            timeout=20,
        )

    def test_a_server_advertising_every_required_tool_passes(self):
        seen = self.check(self.server(mcp_preflight.REQUIRED["browser"]))
        self.assertEqual(
            sorted(seen["browser"]), sorted(mcp_preflight.REQUIRED["browser"]))

    def test_a_server_advertising_nothing_is_refused(self):
        # The exact production shape: connected, answering, advertising nothing.
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            self.check(self.server(()))
        self.assertIn("advertised no tools", caught.exception.reason)

    def test_a_partial_tool_list_is_refused_and_names_what_is_missing(self):
        # Discovering the gap mid-form is worse than not starting: the posting
        # is already open and half the fields are already filled.
        partial = list(mcp_preflight.REQUIRED["browser"])[:4]
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            self.check(self.server(partial))
        self.assertIn("missing", caught.exception.reason)
        self.assertIn("browser_file_upload", caught.exception.capture)

    def test_a_server_that_will_not_start_is_refused(self):
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            self.check({"command": "/nonexistent/mcp-server", "args": []})
        self.assertIn("could not be started", caught.exception.reason)

    def test_a_server_that_exits_during_the_handshake_is_refused(self):
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            self.check(self.server((), behaviour="exit"))
        self.assertTrue(caught.exception.reason)

    def test_a_server_that_never_answers_initialize_times_out(self):
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            mcp_preflight.check(
                {"browser": self.server((), behaviour="silent_init")},
                required={"browser": ()}, timeout=2)
        self.assertIn("did not answer initialize", caught.exception.reason)

    def test_a_server_that_never_answers_tools_list_times_out(self):
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            mcp_preflight.check(
                {"browser": self.server((), behaviour="silent_tools")},
                required={"browser": ()}, timeout=2)
        self.assertIn("did not answer tools/list", caught.exception.reason)

    def test_an_answer_without_a_tool_list_is_refused(self):
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            self.check(self.server((), behaviour="no_list"))
        self.assertIn("without a tool list", caught.exception.reason)

    def test_a_server_the_run_needs_but_never_configured_is_refused(self):
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            mcp_preflight.check({}, required={"browser": ("browser_snapshot",)},
                                timeout=5)
        self.assertIn("not configured", caught.exception.reason)

    def test_the_capture_is_bounded(self):
        with self.assertRaises(mcp_preflight.PreflightError) as caught:
            self.check(self.server(tuple(f"tool_{i}" for i in range(400))))
        self.assertLessEqual(
            len(caught.exception.capture), mcp_preflight.MAX_CAPTURE)

    def test_no_server_process_is_left_running(self):
        config = self.server(mcp_preflight.REQUIRED["browser"])
        self.check(config)
        found = subprocess.run(
            ["pgrep", "-f", config["args"][0]], capture_output=True, text=True)
        self.assertNotEqual(found.returncode, 0, found.stdout)


if __name__ == "__main__":
    unittest.main()
