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
import inspect
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



class NoOnDeviceModelTests(unittest.TestCase):
    """Neither Chrome downloads its own AI model.

    Measured 2026-08-29: OptGuideOnDeviceModel was 4.0 GB in each of the eight
    apply profiles, 32 GB in total, and 84% of everything in the store. Nothing
    in Jobby calls it -- the intelligence is the provider CLIs, and these
    browsers only fill forms and print PDFs.

    Pinned at both launch sites for the same reason the keychain flag is: this
    repo has exactly two, they have drifted apart before, and nothing about a
    profile quietly growing by four gigabytes announces itself.
    """

    FEATURES = ("OptimizationGuideOnDeviceModel",
                "OptimizationGuideModelDownloading",
                "TextSafetyClassifier")

    def test_the_slot_browser_disables_it(self):
        argv = browser._launch_argv("apply-1", "/tmp/profile")
        flags = " ".join(argv)
        for feature in self.FEATURES:
            self.assertIn(feature, flags)
        self.assertIn("--disable-features=", flags)

    def test_the_pdf_renderer_disables_it(self):
        source = pathlib.Path(
            pathlib.Path(__file__).resolve().parents[1] / "scripts" / "resume_pdf.py"
        ).read_text()
        for feature in self.FEATURES:
            self.assertIn(feature, source)

    def test_both_launch_sites_name_the_same_features(self):
        """A flag on one and not the other is how 32 GB accumulated in the
        profiles while the renderer stayed clean and nobody noticed either."""
        root = pathlib.Path(__file__).resolve().parents[1] / "scripts"
        for name in ("browser.py", "resume_pdf.py"):
            text = (root / name).read_text()
            with self.subTest(module=name):
                self.assertIn("OptimizationGuideOnDeviceModel", text)



class HeadlessByDefaultTests(unittest.TestCase):
    """Orchestration runs without a window; a window is for being watched.

    Eight visible Chromes are eight windows on the user's screen for work
    nobody is looking at. The one moment a window is the point is open-and-fill
    on something parked in Needs you, and that path asks for it.
    """

    def test_a_slot_is_headless_unless_asked(self):
        argv = browser._launch_argv("apply-1", "/tmp/profile")
        self.assertIn("--headless=new", argv)

    def test_the_new_headless_mode_not_the_old_one(self):
        """The old mode was a different browser with different behaviour around
        uploads and rendering. The new one is the same Chrome with no window,
        which is what makes it safe for a path that attaches real files."""
        argv = browser._launch_argv("apply-1", "/tmp/profile")
        self.assertNotIn("--headless", argv)
        self.assertIn("--headless=new", argv)

    def test_headed_drops_the_flag_entirely(self):
        argv = browser._launch_argv("apply-1", "/tmp/profile", headed=True)
        self.assertFalse([a for a in argv if a.startswith("--headless")])

    def test_the_mode_is_read_from_the_process_not_remembered(self):
        """Same reason the drift check reads the command line: what a process
        was started with is the only thing that is true about it."""
        source = inspect.getsource(browser.is_headed)
        self.assertIn("running_launch", source)

    def test_showing_a_slot_already_headed_does_not_restart_it(self):
        """Pressing open-and-fill twice must not take the browser away from the
        form the user is looking at."""
        source = inspect.getsource(browser.show)
        self.assertIn("is_headed", source)
        self.assertLess(source.index("is_headed"), source.index("stop_slot"))
