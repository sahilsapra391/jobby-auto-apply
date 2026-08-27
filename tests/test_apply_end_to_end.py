"""Drive the real firewall against a real browser and attach real files.

This is the invariant the apply path did not have. Both faults found on
2026-08-27 were invisible to every test in the suite because every test stopped
at a seam: the firewall was tested against a stub, the provider invocation was
tested as a list of strings, and nothing ever asked a browser to take a file.
Fault 1 was a browser server that advertised nothing to a live client. Fault 2
was an upload tool that cannot work on a form whose file input is hidden, which
is most of them.

Excluded from CI for the same reason test_resume_pdf is: it needs Chrome, npx
and the Playwright package. It skips rather than fails when they are absent, so
running the suite on a machine without them stays honest about what it proved.
"""

import functools
import http.server
import json
import pathlib
import socketserver
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browser  # noqa: E402
from mcp_stdio_client import Client  # noqa: E402
import mcp_preflight  # noqa: E402


def live_slot():
    """A slot whose Chrome answers, or None."""
    for slot in browser.SLOTS:
        try:
            if browser.running(slot):
                return slot
        except Exception:  # noqa: BLE001 - absence is the answer
            continue
    return None


class RealBrowserUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.slot = live_slot()
        if cls.slot is None:
            raise unittest.SkipTest("no browser slot is running on this machine")
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler,
            directory=str(ROOT / "tests" / "fixtures"),
        )
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "server", None):
            cls.server.shutdown()
            cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name).resolve()
        self.resume = root / "resume.pdf"
        self.cover = root / "cover-letter.pdf"
        self.resume.write_bytes(b"%PDF-1.4\n" + b"r" * 2048)
        self.cover.write_bytes(b"%PDF-1.4\n" + b"c" * 4096)
        self.context = {
            "applicationId": "endtoend",
            "resume": str(self.resume), "cover": str(self.cover),
            "uploadResume": str(self.resume), "uploadCover": str(self.cover),
        }
        self.config = browser.server_configs(
            self.slot, application_context=self.context
        )["browser"]
        self.addCleanup(self.temp.cleanup)

    def open_fixture(self):
        opened = browser._open_tab(
            f"http://127.0.0.1:{self.port}/upload-form.html", self.slot
        )
        target = (opened or {}).get("id") if isinstance(opened, dict) else None
        if target:
            self.addCleanup(browser._cdp, f"/json/close/{target}", self.slot)

    def client(self):
        connection = Client(self.config)
        self.addCleanup(connection.close)
        return connection

    def test_the_firewall_advertises_every_tool_a_run_requires(self):
        """Fault 1: a live client got an empty browser tool list and improvised."""
        names = mcp_preflight.tool_names("browser", self.config)
        missing = set(mcp_preflight.REQUIRED["browser"]) - set(names)
        self.assertEqual(missing, set())
        self.assertNotIn("browser_run_code_unsafe", names)

    def test_both_documents_attach_to_a_form_whose_input_is_hidden(self):
        """Fault 2: six upload calls, both PDFs unattached, application parked."""
        self.open_fixture()
        connection = self.client()

        def text(reply):
            return reply["result"]["content"][0]["text"]

        # The tool the agent had. It answers only while a chooser is open, and
        # the click that opens one hangs on a 1px transparent input.
        self.assertIn("modal state",
                      text(connection.tool("browser_file_upload",
                                           {"paths": [str(self.resume)]})))

        for path, selector in ((self.resume, "#resume_file"),
                               (self.cover, "#cover_file")):
            reply = text(connection.tool("browser_attach_file", {
                "paths": [str(path)], "target": selector,
                "element": f"{path.name} field",
            }))
            self.assertIn(path.name, reply, reply)
            self.assertIn(str(path.stat().st_size), reply, reply)

    def test_a_field_the_page_replaces_on_attach_still_reports_success(self):
        """Greenhouse swaps the input out the moment it takes a file.

        The handle that did the attaching is detached before it can be asked
        what landed, and the first version of this tool reported a 30-second
        timeout for an upload that had worked. An agent told its upload failed
        retries it, which on a real form is the expensive kind of wrong. Seen on
        the live Justworks posting on 2026-08-27.
        """
        self.open_fixture()
        connection = self.client()
        reply = connection.tool("browser_attach_file", {
            "paths": [str(self.resume)], "target": "#portfolio_file",
            "element": "portfolio upload",
        })["result"]["content"][0]["text"]
        self.assertNotIn("Timeout", reply)
        self.assertIn("attached", reply)
        self.assertIn(self.resume.name, reply)
        # And the page really does hold it.
        found = connection.tool(
            "browser_find", {"text": self.resume.name}
        )["result"]["content"][0]["text"]
        self.assertIn(self.resume.name, found)

    def test_the_run_is_recorded_as_having_reached_the_browser(self):
        """A park with zero calls is a capability failure; this is the other case."""
        self.open_fixture()
        connection = self.client()
        connection.tool("browser_snapshot", {})
        connection.tool("browser_attach_file",
                        {"paths": [str(self.cover)], "target": "#cover_file"})
        connection.finish()
        recorded = json.loads(
            browser.activity_path(self.slot, self.context).read_text()
        )
        self.assertEqual(recorded["totalCalls"], 2)
        self.assertEqual(recorded["backendExit"], 0)


if __name__ == "__main__":
    unittest.main()
