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
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browser  # noqa: E402
from mcp_stdio_client import Client  # noqa: E402
import mcp_preflight  # noqa: E402


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """The fixture server's request log is noise in a test run."""

    def log_message(self, fmt, *args):
        pass


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
            QuietHandler, directory=str(ROOT / "tests" / "fixtures")
        )
        # Threading, with daemon threads and no blocking close. A single-threaded
        # server hangs on teardown when the browser is still holding a keep-alive
        # connection: the handler sits in a read that shutdown() cannot interrupt,
        # and the suite stops rather than fails, which is the worse outcome.
        cls.server = ThreadingServer(("127.0.0.1", 0), handler)
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

    def open_fixture(self, name="upload-form.html"):
        """Open a fixture and wait until Chrome is actually showing it.

        `_open_tab` returns when Chrome has created the tab, not when the page
        has parsed. Attaching immediately raced the load and lost about one run
        in four, failing as "the target resolved to no file input" -- which
        reads exactly like the tool being broken and is the test being wrong.
        Another session lost an afternoon to it believing it was their diff.
        """
        url = f"http://127.0.0.1:{self.port}/{name}"
        opened = browser._open_tab(url, self.slot)
        target = (opened or {}).get("id") if isinstance(opened, dict) else None
        if target:
            self.addCleanup(browser._cdp, f"/json/close/{target}", self.slot)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            here = next((t for t in browser.tabs(self.slot)
                         if t.get("id") == target), None)
            if here and name in (here.get("url") or ""):
                break
            time.sleep(0.1)
        else:
            self.fail("the fixture page never loaded in the slot")
        return target

    def wait_for_form(self, connection, marker="Cover letter"):
        """And wait until the document has the inputs, not just the address.

        The URL is set before the body parses, so the tab reporting the fixture
        is necessary and not sufficient. This asks the page itself, through the
        same firewall the agent uses, which is the only thing that can answer.
        """
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            found = connection.tool("browser_find", {"text": marker})
            text = found.get("result", {}).get("content", [{}])[0].get("text", "")
            if marker in text and "No matches" not in text:
                return
            time.sleep(0.2)
        self.fail("the upload form never appeared on the page")

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
        self.wait_for_form(connection)

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

        What this test asserts changed on 2026-08-28. It used to prove only
        that the reply did not mention a timeout, and the code under it reached
        that answer by assuming any read-back failure meant acceptance -- from
        an `error` it never looked at, so a navigation, a destroyed context and
        an expired timeout all became the same confident sentence. Now the
        success has to be earned from the page: the container the field sat in
        shows the filename afterwards. The suppressed error is reported beside
        it rather than thrown away.
        """
        self.open_fixture()
        connection = self.client()
        self.wait_for_form(connection)
        reply = connection.tool("browser_attach_file", {
            "paths": [str(self.resume)], "target": "#portfolio_file",
            "element": "portfolio upload",
        })["result"]["content"][0]["text"]
        self.assertNotIn("Error", reply)
        self.assertIn("attached", reply)
        self.assertIn(self.resume.name, reply)
        self.assertIn("shows " + self.resume.name, reply)
        # The read-back fault is named rather than discarded. It is the value
        # that says whether this branch was right, and it used to be unread.
        self.assertIn("could not be read back", reply)
        # And the page really does hold it.
        found = connection.tool(
            "browser_find", {"text": self.resume.name}
        )["result"]["content"][0]["text"]
        self.assertIn(self.resume.name, found)

    def test_a_field_that_clears_itself_on_accept_is_not_called_a_failure(self):
        """The 2026-08-28 fault: a working attach reported as no file attached.

        Two SmartRecruiters forms produced it the same night, Versant
        6c3824319c68c547 and SWIGGY 40c1f32b247a885a. Versant proves the
        direction on its own: both targeted calls returned "the input reported
        no file after the attachment", and that application's own park note
        says the resume is attached. The snapshot it took immediately after the
        last failed call has the filename rendered beside the field.

        The component takes the file off the input and clears `input.value` so
        that re-choosing the same file still fires change, which empties
        `node.files` on success. Measured on the live Versant posting on
        2026-08-28, where both file inputs sit in their own `spl-dropzone`
        shadow root and both carry id=file-input.
        """
        self.open_fixture("clearing-upload-form.html")
        connection = self.client()
        self.wait_for_form(connection, "Cover letter")
        reply = connection.tool("browser_attach_file", {
            "paths": [str(self.resume)],
            "target": "input[type=file] >> nth=0",
            "element": "resume field",
        })["result"]["content"][0]["text"]
        self.assertNotIn("Error", reply)
        self.assertIn("attached", reply)
        # The evidence is named, not asserted. The input itself still says
        # nothing, and saying so is the point: the success came from the page.
        self.assertIn("reports no file", reply)
        self.assertIn("shows " + self.resume.name, reply)
        found = connection.tool(
            "browser_find", {"text": self.resume.name}
        )["result"]["content"][0]["text"]
        self.assertIn(self.resume.name, found)

    def test_a_field_that_rejects_the_file_is_not_called_a_success(self):
        """The other direction, and the one with a submission behind it.

        A field that rejects on type or size clears itself exactly as a field
        that accepts does, so `node.files` cannot tell them apart. Nor can a
        page-wide search for the filename: the rejection names the file. This
        is why the confirmation is scoped to the field's own neighbourhood, why
        it is a change against a baseline taken before the attach, and why a
        match inside an alert does not count.

        A false negative parks a run and a person looks at it. A false positive
        walks an agent toward submit believing a resume is attached, under a
        real name, and cannot be recalled.
        """
        self.open_fixture("clearing-upload-form.html")
        connection = self.client()
        self.wait_for_form(connection, "Cover letter")
        reply = connection.tool("browser_attach_file", {
            "paths": [str(self.resume)],
            "target": "input[type=file] >> nth=1",
            "element": "cover letter field",
        })["result"]["content"][0]["text"]
        self.assertIn("unconfirmed", reply)
        # And the page's own reason is carried out rather than dropped, which
        # is the difference between "no file" and a fault someone can act on.
        self.assertIn("was rejected", reply)

    def test_two_inputs_sharing_one_id_are_still_told_apart(self):
        """Both SmartRecruiters inputs carry id=file-input, legally.

        Each is unique inside its own shadow root. So the id names neither, and
        every error on that form read identically -- from which two sessions
        concluded that nth=0 and nth=1 had resolved to the same element, on
        evidence that could not support it. The way out of the element is what
        distinguishes them.
        """
        self.open_fixture("clearing-upload-form.html")
        connection = self.client()
        self.wait_for_form(connection, "Cover letter")
        reply = connection.tool("browser_attach_file", {
            "paths": [str(self.resume)],
        })["result"]["content"][0]["text"]
        self.assertIn("2 file inputs", reply)
        self.assertIn("resume-upload", reply)
        self.assertIn("cover-letter-upload", reply)

    def test_the_run_is_recorded_as_having_reached_the_browser(self):
        """A park with zero calls is a capability failure; this is the other case."""
        self.open_fixture()
        connection = self.client()
        self.wait_for_form(connection)
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
