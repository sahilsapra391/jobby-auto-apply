"""The trusted runner puts each slot on its own posting before the agent starts.

The form agent has no navigation tool, on purpose: the firewall removes it so a
posting URL cannot be swapped mid-run. Nothing opened the page either, so Chrome
sat on the about:blank tab that serve() uses to hold the window, the agent saw a
blank page, and the run parked itself. Applications looked like they simply did
not start.
"""

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import browser  # noqa: E402


BLANK = {"id": "blank", "type": "page", "url": "about:blank", "title": "about:blank"}


class FakeChrome:
    """Enough of the DevTools HTTP endpoint to drive one slot's tabs."""

    def __init__(self, pages, landing=None):
        self.pages = [dict(page) for page in pages]
        self.landing = landing
        self.closed = []
        self.activated = []

    def tabs(self, slot=None):
        return [dict(page) for page in self.pages]

    def open_tab(self, url, slot=None):
        page = {"id": "opened", "type": "page",
                "url": self.landing or url, "title": "Apply"}
        self.pages.append(page)
        return dict(page)

    def cdp(self, path, slot=None, timeout=4):
        if path.startswith("/json/close/"):
            target = path.rsplit("/", 1)[1]
            self.closed.append(target)
            self.pages = [p for p in self.pages if p["id"] != target]
        elif path.startswith("/json/activate/"):
            self.activated.append(path.rsplit("/", 1)[1])
        return {}


class OpenApplicationTests(unittest.TestCase):
    def drive(self, pages, url="https://boards.example.com/acme/jobs/5", landing=None,
              settle_timeout=20):
        chrome = FakeChrome(pages, landing=landing)
        with mock.patch.object(browser, "running", return_value=True), \
             mock.patch.object(browser, "tabs", chrome.tabs), \
             mock.patch.object(browser, "_open_tab", chrome.open_tab), \
             mock.patch.object(browser, "_cdp", chrome.cdp):
            return browser.open_application(
                url, "apply-1", settle_timeout=settle_timeout), chrome

    def test_a_fresh_slot_lands_on_the_posting_and_keeps_only_that_tab(self):
        result, chrome = self.drive([BLANK])

        self.assertTrue(result["opened"])
        self.assertFalse(result["reused"])
        self.assertEqual(result["url"], "https://boards.example.com/acme/jobs/5")
        self.assertEqual([page["id"] for page in chrome.pages], ["opened"])
        self.assertEqual(chrome.closed, ["blank"])
        # Not raised. Every apply run passes through here, so activating took
        # the user's screen once per application; and with the other tabs closed
        # the posting is already the selected one, so the call did nothing else.
        self.assertEqual(chrome.activated, [])

    def test_a_retry_returns_to_the_part_filled_form_on_the_same_site(self):
        # An ATS rewrites its own path between steps, so the tab holding the
        # half-finished form rarely still has the address the run started from.
        started = {"id": "step2", "type": "page", "title": "Application",
                   "url": "https://boards.example.com/acme/application?step=2"}
        result, chrome = self.drive([BLANK, started])

        self.assertTrue(result["opened"])
        self.assertTrue(result["reused"])
        self.assertEqual(result["url"], started["url"])
        self.assertEqual(chrome.activated, [])
        self.assertEqual(chrome.closed, ["blank"])
        self.assertEqual([page["id"] for page in chrome.pages], ["step2"])

    def test_a_tab_on_another_site_is_closed_rather_than_reused(self):
        # A leftover from a crashed run is the one thing that could put the
        # agent on another company's form, so it does not survive the handover.
        stale = {"id": "other", "type": "page", "title": "Other",
                 "url": "https://careers.other.test/jobs/1"}
        result, chrome = self.drive([BLANK, stale])

        self.assertTrue(result["opened"])
        self.assertFalse(result["reused"])
        self.assertEqual(chrome.activated, [])
        self.assertEqual([page["id"] for page in chrome.pages], ["opened"])
        self.assertEqual(sorted(chrome.closed), ["blank", "other"])

    def test_an_ats_redirect_is_recorded_rather_than_refused(self):
        # Greenhouse and Workday postings routinely land on the employer's own
        # domain. Refusing a host change would stall real applications.
        result, _ = self.drive([BLANK], landing="https://www.acme.test/careers/5")

        self.assertTrue(result["opened"])
        self.assertTrue(result["redirected"])
        self.assertEqual(result["url"], "https://www.acme.test/careers/5")

    def test_a_page_that_never_leaves_about_blank_is_a_failure_not_a_pass(self):
        result, _ = self.drive([BLANK], landing="about:blank", settle_timeout=1)

        self.assertFalse(result["opened"])
        self.assertIn("did not load", result["why"])

    def test_only_http_addresses_and_known_slots_are_accepted(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "", "acme.test"):
            with self.subTest(url=url):
                with mock.patch.object(browser, "running", return_value=True):
                    self.assertFalse(browser.open_application(url, "apply-1")["opened"])
        self.assertFalse(
            browser.open_application("https://acme.test/j/1", "apply-9")["opened"]
        )

    def test_a_stopped_browser_is_reported_rather_than_silently_skipped(self):
        with mock.patch.object(browser, "running", return_value=False):
            result = browser.open_application("https://acme.test/j/1", "apply-1")
        self.assertFalse(result["opened"])
        self.assertIn("no browser is running", result["why"])




class RunnerRefusesABlankPageTests(unittest.TestCase):
    """No agent is launched at a page that is not the application.

    Launching anyway is what produced the parked runs: a real provider turn was
    spent, tokens and all, to look at about:blank and give up. Failing here
    instead records the reason on the application, where the user can see it.
    """

    def test_a_posting_that_will_not_open_blocks_before_any_provider_runs(self):
        import run_apply  # noqa: PLC0415

        recorded = {}

        def fake_store(argv):
            if argv[:1] == ["meta-set"]:
                recorded[argv[2]] = argv[4]
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        summary = {
            "applicationId": "acme-pm",
            "url": "https://boards.example.com/acme/jobs/5",
            "company": "Acme", "role": "PM", "ats": "greenhouse", "region": "us",
        }
        artifacts = {
            "resume": {"path": "/tmp/resume.pdf", "size": 1, "sha256": "x"},
            "coverLetter": {"path": "/tmp/cover.pdf", "size": 1, "sha256": "y"},
        }
        staged = mock.Mock()
        with mock.patch.object(run_apply.mcp_preflight, "check",
                                  return_value={"browser": [], "jobby": []}), \
                mock.patch.object(run_apply, "store", fake_store), \
             mock.patch.object(run_apply, "load_brief", return_value=summary), \
             mock.patch.object(run_apply, "_refuse_duplicate", return_value=False), \
             mock.patch.object(run_apply, "artifact_snapshot", return_value=artifacts), \
             mock.patch.object(run_apply.agent, "resolve_provider", return_value="anthropic"), \
             mock.patch.object(run_apply.agent, "authenticated", return_value=(True, "")), \
             mock.patch.object(run_apply.browser, "serve", return_value={"started": True}), \
             mock.patch.object(run_apply.browser, "stage_application_uploads", staged), \
             mock.patch.object(run_apply.browser, "open_application", return_value={
                 "opened": False, "why": "the posting did not load within 20s"}), \
             mock.patch.object(
                 sys, "argv",
                 ["run_apply.py", "--id", "acme-pm", "--slot", "apply-1"]):
            code = run_apply.main()

        self.assertEqual(code, 2)
        staged.assert_not_called()
        self.assertIn("the posting would not open in apply-1", recorded["blocked"])
        self.assertIn("did not load", recorded["blocked"])
