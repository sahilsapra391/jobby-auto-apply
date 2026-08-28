"""Drive the real firewall against a real browser and attach real files.

This is the invariant the apply path did not have. Both faults found on
2026-08-27 were invisible to every test in the suite because every test stopped
at a seam: the firewall was tested against a stub, the provider invocation was
tested as a list of strings, and nothing ever asked a browser to take a file.
Fault 1 was a browser server that advertised nothing to a live client. Fault 2
was an upload tool that cannot work on a form whose file input is hidden, which
is most of them.

CI runs this module and it skips there. It is not on the workflow's exclusion
list the way test_resume_pdf is; it needs Chrome, npx and the Playwright
package, and `live_slot` finds no browser on a runner, so it skips rather than
fails and a machine without them stays honest about what it proved. Which means
only the production Mac ever exercises any of this, and a failure here sits
behind a wall of green checks until somebody runs the suite by hand.
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
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browser  # noqa: E402
from mcp_stdio_client import Client  # noqa: E402
import mcp_preflight  # noqa: E402
import watch  # noqa: E402


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """The fixture server's request log is noise in a test run."""

    def log_message(self, fmt, *args):
        pass


def live_slot():
    """A slot whose Chrome answers and that no application is using, or None.

    Both halves matter and the second one was missing. Every slot whose Chrome
    is up on the production Mac is a production slot, so taking the first one
    took whichever slot the watcher was in the middle of using. On 2026-08-28
    that was apply-1, holding a real SmartRecruiters application: this suite
    opened fixture tabs in that browser while an agent was filling somebody's
    job application, and its three browser tests failed with "the upload form
    never appeared" because the page they were driving was not the fixture.

    A running Chrome and an unused Chrome are identical from the browser's
    side, and only the lock tells them apart. `reserve` writes <slot>.lock
    before the watcher spawns run_apply, so this asks `free_apply_slots` rather
    than restating what free means: the watcher's definition is the one that
    has to be right, and borrowing it means this follows if it ever changes.
    Parked slots are excluded by it too, for a better reason than tidiness,
    since a parked slot keeps its page state precisely so its application can
    retry into it.

    Worth knowing: `lock_holder` repairs as it reads, converting a dead
    holder's lock into a parked one when the application still owns the slot.
    That is the watcher's own recovery and it runs every couple of seconds
    anyway, so asking here only ever performs it a moment sooner.
    """
    try:
        free = watch.free_apply_slots()
    except Exception:  # noqa: BLE001 - absence is the answer
        return None
    for slot in free:
        try:
            if browser.running(slot):
                return slot
        except Exception:  # noqa: BLE001 - absence is the answer
            continue
    return None


def owned_slots():
    """Running slots an application holds, so a skip can say which case it is.

    "No browser at all" and "every browser is busy" both stop this suite, and
    a run that cannot tell them apart reads as the machine being unequipped
    when it is only busy.
    """
    try:
        free = set(watch.free_apply_slots())
    except Exception:  # noqa: BLE001 - absence is the answer
        return []
    held = []
    for slot in browser.SLOTS:
        try:
            if slot not in free and browser.running(slot):
                held.append(slot)
        except Exception:  # noqa: BLE001 - absence is the answer
            continue
    return held


class RealBrowserUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.slot = live_slot()
        if cls.slot is None:
            busy = owned_slots()
            if busy:
                raise unittest.SkipTest(
                    "every running browser slot is held by an application: "
                    + ", ".join(busy)
                )
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
        # The slot stays real; the artifact tree under it does not have to.
        # Playwright's --output-dir and the firewall's --activity path both hang
        # off browser.ARTIFACTS, and the only thing here that must be real is the
        # slot's CDP port, which port_for derives from the slot name alone. Left
        # pointing at the real tree, this filed an application called "endtoend"
        # and a heap of console logs under a live slot's artifact root. That was
        # harmless only because _write_activity sits after StdioProxy.run() and
        # no real run reaches it. PR #112 adds the signal handlers that make it
        # reachable, and from then on a row called endtoend sits among real
        # applications for anyone measuring which runs reached the browser.
        #
        # server_configs below is what tells the firewall child where to write,
        # and the read-back at the end of the upload test resolves through
        # browser.activity_path, so both ends move together and neither
        # assertion changes.
        artifacts = mock.patch.object(browser, "ARTIFACTS", root / "artifacts")
        artifacts.start()
        self.addCleanup(artifacts.stop)
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
        """Open the fixture and wait until Chrome is actually showing it.

        `_open_tab` returns when Chrome has created the tab, not when the page
        has parsed. Attaching immediately raced the load and lost about one run
        in four, failing as "the target resolved to no file input" -- which
        reads exactly like the tool being broken and is the test being wrong.
        Another session lost an afternoon to it believing it was their diff.
        """
        url = f"http://127.0.0.1:{self.port}/upload-form.html"
        opened = browser._open_tab(url, self.slot)
        target = (opened or {}).get("id") if isinstance(opened, dict) else None
        if target:
            self.addCleanup(browser._cdp, f"/json/close/{target}", self.slot)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            here = next((t for t in browser.tabs(self.slot)
                         if t.get("id") == target), None)
            if here and "upload-form.html" in (here.get("url") or ""):
                break
            time.sleep(0.1)
        else:
            self.fail("the fixture page never loaded in the slot")
        return target

    def wait_for_form(self, connection):
        """And wait until the document has the inputs, not just the address.

        The URL is set before the body parses, so the tab reporting the fixture
        is necessary and not sufficient. This asks the page itself, through the
        same firewall the agent uses, which is the only thing that can answer.

        Returns how many probes that took. They are accepted tool calls like
        any other, so the firewall counts them, and a caller asserting on that
        count cannot know the number without being told.
        """
        probes = 0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            found = connection.tool("browser_find", {"text": "Cover letter"})
            probes += 1
            text = found.get("result", {}).get("content", [{}])[0].get("text", "")
            if "Cover letter" in text and "No matches" not in text:
                return probes
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
        """
        self.open_fixture()
        connection = self.client()
        self.wait_for_form(connection)
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
        """A park with zero calls is a capability failure; this is the other case.

        The total is asserted per tool and against the probes actually sent,
        rather than against a constant. It was 2, correct when this test made
        exactly two calls, and stale three hours later when the fix for the
        load race put a readiness probe in front of them: that probe is an
        accepted call and the firewall counts it, so the file recorded 3 and
        the one local invariant the apply browser path has failed on the
        production Mac while every check stayed green. Writing 3 would have
        been the same bug waiting for a slow load, because the probe loop
        runs until the page answers and a busy machine needs more than one.
        """
        self.open_fixture()
        connection = self.client()
        probes = self.wait_for_form(connection)
        connection.tool("browser_snapshot", {})
        connection.tool("browser_attach_file",
                        {"paths": [str(self.cover)], "target": "#cover_file"})
        connection.finish()
        recorded = json.loads(
            browser.activity_path(self.slot, self.context).read_text()
        )
        self.assertEqual(recorded["calls"].get("browser_find"), probes)
        self.assertEqual(recorded["calls"].get("browser_snapshot"), 1)
        self.assertEqual(recorded["calls"].get("browser_attach_file"), 1)
        # Still exact: an uncounted call or a double-counted one fails here.
        self.assertEqual(recorded["totalCalls"], probes + 2)
        self.assertEqual(recorded["backendExit"], 0)


if __name__ == "__main__":
    unittest.main()
