"""Every Chrome Jobby launches runs with a mock keychain.

Chrome keeps its storage key in the macOS login keychain, and asking for it
is how a browser nobody is watching puts a modal "Keychain Not Found" dialog
on the user's screen: after Chrome updates on disk underneath a long-lived
instance, or whenever the keychain is locked for the launch context, the
request fails and SecurityAgent asks the human. Headless renders ask too,
because the dialog belongs to SecurityAgent rather than to Chrome's own UI.
On 2026-08-28 the user sat through a loop of these that Cancel could not end.

An isolated agent profile and a print-to-PDF process need nothing the
keychain holds, so the invariant is total: the repo's two Chrome launch
sites, the slot browsers and the PDF renderer, both pass --use-mock-keychain,
and a third launch site must too.
"""

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


browser = load("browser")
resume_pdf = load("resume_pdf")


class MockKeychainTests(unittest.TestCase):
    def test_every_slot_browser_launches_with_a_mock_keychain(self):
        for slot in browser.SLOTS:
            with self.subTest(slot=slot):
                argv = browser._launch_argv(slot, f"/tmp/profile-{slot}")
                self.assertIn("--use-mock-keychain", argv)

    def test_the_slot_argv_still_carries_its_load_bearing_flags(self):
        argv = browser._launch_argv(browser.SLOTS[0], "/tmp/profile")
        self.assertEqual(argv[-1], "about:blank", "the keep-alive tab is last")
        self.assertTrue(any(a.startswith("--remote-debugging-port=") for a in argv))
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in argv))

    def test_the_pdf_renderer_launches_with_a_mock_keychain(self):
        command = resume_pdf.chrome_command("chrome", "/tmp/out.pdf", "file:///x")
        self.assertIn("--use-mock-keychain", command)

    def test_the_low_memory_renderer_keeps_the_flag_too(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"JOBBY_CHROMIUM_LOW_MEMORY": "1"}):
            command = resume_pdf.chrome_command(
                "chrome", "/tmp/out.pdf", "file:///x", profile_dir="/tmp/p")
        self.assertIn("--use-mock-keychain", command)


if __name__ == "__main__":
    unittest.main()
