"""A slot browser must not outlive the binary or the launch we would give it.

serve() starts one on demand and nothing stops it afterwards, so an unattended
Chrome main outlives both releases and merges. Two axes, and each was measured
on this machine on 2026-08-28.

The binary. Five mains were on 151.0.7922.174 with .175 on disk, some up since
2026-08-25, each executing a bundle that no longer existed at that path, so the
release's fixes were not in effect in the browsers that fill forms on employer
sites.

The launch. PR #111 added --use-mock-keychain, which is what keeps these
browsers off the user's keychain. A running Chrome cannot be given a flag, and
serve() starts one only when the slot has none, so nothing ever replaces it.
apply-1 started at 00:38:14 and #111 was committed at 00:38:36, twenty-two
seconds later; apply-1 was still running without the flag hours afterwards, on
the current build, where every version check called it healthy.

The two do not earn the same response and the tests below pin that. A stale
binary is untidy and stays out of the dead-man ping, because after #111 it is
not something the user can see. A missing flag on a browser nothing is allowed
to close is the user's own invariant failing, so that one pings.

Two properties hold across both, or the cure is worse than the fault:

  an idle stale browser is recycled, so the common case clears itself; and
  a browser holding a form is never closed behind the user's back, but is
  always named where someone looking can find it.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import plistlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

INSTALLED = "151.0.7922.175"
OLD = "151.0.7922.174"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watch = load("watch")
browser = watch.browser


class InstalledVersionTests(unittest.TestCase):
    def setUp(self):
        self.original = browser.CHROME_INFO_PLIST
        self.dir = tempfile.mkdtemp(prefix="jobby-chrome-plist-")

    def tearDown(self):
        browser.CHROME_INFO_PLIST = self.original

    def write(self, payload):
        path = pathlib.Path(self.dir) / "Info.plist"
        path.write_bytes(plistlib.dumps(payload))
        browser.CHROME_INFO_PLIST = path
        return path

    def test_reads_the_short_version_string(self):
        self.write({"CFBundleShortVersionString": INSTALLED,
                    "CFBundleVersion": "7922.175"})
        self.assertEqual(browser.installed_version(), INSTALLED)

    def test_not_the_bundle_version(self):
        """CFBundleVersion is the last two components and never matches CDP."""
        self.write({"CFBundleShortVersionString": INSTALLED,
                    "CFBundleVersion": "7922.175"})
        self.assertNotEqual(browser.installed_version(), "7922.175")

    def test_a_missing_plist_is_empty_rather_than_an_exception(self):
        browser.CHROME_INFO_PLIST = pathlib.Path(self.dir) / "nope.plist"
        self.assertEqual(browser.installed_version(), "")

    def test_an_unparseable_plist_is_empty(self):
        path = pathlib.Path(self.dir) / "Info.plist"
        path.write_bytes(b"not a plist at all")
        browser.CHROME_INFO_PLIST = path
        self.assertEqual(browser.installed_version(), "")

    def test_the_real_chrome_on_this_machine_reports_a_build(self):
        """Fails loudly if the key is ever renamed under us."""
        browser.CHROME_INFO_PLIST = self.original
        if not self.original.exists():
            self.skipTest("Google Chrome is not installed here")
        version = browser.installed_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+\.\d+$")


class RunningVersionTests(unittest.TestCase):
    def setUp(self):
        self.original = browser._cdp

    def tearDown(self):
        browser._cdp = self.original

    def answer(self, payload):
        browser._cdp = lambda path, slot=None, timeout=4: payload

    def test_reads_the_full_build_from_the_browser_field(self):
        self.answer({"Browser": f"Chrome/{INSTALLED}",
                     "User-Agent": "Mozilla/5.0 ... Chrome/151.0.0.0 Safari/537.36"})
        self.assertEqual(browser.running_version("apply-1"), INSTALLED)

    def test_never_the_user_agent(self):
        """Measured on 2026-08-28: CDP rounds the UA to 151.0.0.0.

        Reading it instead would report every slot in the fleet as drifted
        forever, which is a recycle loop rather than a fix.
        """
        self.answer({"Browser": f"Chrome/{INSTALLED}",
                     "User-Agent": "Mozilla/5.0 ... Chrome/151.0.0.0 Safari/537.36"})
        self.assertNotIn("151.0.0.0", browser.running_version("apply-1"))

    def test_a_browser_that_is_down_reports_nothing(self):
        self.answer(None)
        self.assertEqual(browser.running_version("apply-1"), "")

    def test_an_unexpected_payload_reports_nothing(self):
        for payload in ({}, {"Browser": "Chrome"}, {"Browser": 7}, "text", []):
            with self.subTest(payload=payload):
                self.answer(payload)
                self.assertEqual(browser.running_version("apply-1"), "")


class _FakeFleet:
    """Slot browsers as versions and launch flags, plus what was taken down.

    missing_launch_flags is stubbed rather than left live on purpose. It reads
    the real process table, and this machine genuinely runs slot browsers, so an
    unstubbed test would pass or fail on whatever the fleet happens to be doing.
    """

    def __init__(self, versions, installed=INSTALLED, missing=None):
        self.versions = dict(versions)
        self.installed = installed
        self.missing = dict(missing or {})
        self.stopped = []

    def install(self, case):
        case.addCleanup(setattr, browser, "installed_version",
                        browser.installed_version)
        case.addCleanup(setattr, browser, "running_version",
                        browser.running_version)
        case.addCleanup(setattr, browser, "missing_launch_flags",
                        browser.missing_launch_flags)
        case.addCleanup(setattr, browser, "stop_slot", browser.stop_slot)
        browser.installed_version = lambda: self.installed
        browser.running_version = lambda slot=None: self.versions.get(slot, "")
        browser.missing_launch_flags = lambda slot: (
            list(self.missing.get(slot, [])) if self.versions.get(slot) else [])
        browser.stop_slot = self._stop
        return self

    def _stop(self, slot):
        self.stopped.append(slot)
        self.versions.pop(slot, None)
        self.missing.pop(slot, None)
        return True


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-chrome-locks-")
        self.original_locks = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.dir)
        watch._CHROME_CHECK.update({"at": 0.0, "installed": "",
                                    "recycled": [], "held": []})

    def tearDown(self):
        watch.LOCK_DIR = self.original_locks

    def live(self, slot, aid):
        watch.take_lock(slot, __import__("os").getpid(), note=aid)

    def parked(self, slot, aid):
        watch.park_slot(slot, aid)

    def test_a_uniform_fleet_has_no_drift(self):
        _FakeFleet({"apply-1": INSTALLED, "apply-2": INSTALLED}).install(self)
        self.assertEqual(watch.chrome_drift()["drifted"], [])

    def test_an_older_browser_is_drift(self):
        _FakeFleet({"apply-1": OLD}).install(self)
        drifted = watch.chrome_drift()["drifted"]
        self.assertEqual([entry["slot"] for entry in drifted], ["apply-1"])
        self.assertEqual(drifted[0]["running"], OLD)
        self.assertEqual(drifted[0]["installed"], INSTALLED)
        self.assertIsNone(drifted[0]["holder"])

    def test_a_slot_with_no_browser_cannot_drift(self):
        _FakeFleet({}).install(self)
        self.assertEqual(watch.chrome_drift()["drifted"], [])

    def test_an_unreadable_installed_version_is_never_drift(self):
        """The guard that stops a moved plist emptying the whole fleet."""
        fleet = _FakeFleet({"apply-1": OLD, "apply-2": OLD}, installed="")
        fleet.install(self)
        self.assertEqual(watch.chrome_drift()["drifted"], [])
        watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, [])

    def test_an_unreadable_running_version_is_never_drift(self):
        """A browser too slow to answer is not evidence that it is stale."""
        fleet = _FakeFleet({"apply-1": ""})
        fleet.install(self)
        self.assertEqual(watch.chrome_drift()["drifted"], [])
        watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, [])

    def test_the_holder_is_reported_for_a_live_run(self):
        _FakeFleet({"apply-1": OLD}).install(self)
        self.live("apply-1", "app-live")
        entry = watch.chrome_drift()["drifted"][0]
        self.assertEqual(entry["holder"], "live")
        self.assertEqual(entry["applicationId"], "app-live")

    def test_the_holder_is_reported_for_a_parked_slot(self):
        _FakeFleet({"apply-1": OLD}).install(self)
        self.parked("apply-1", "app-parked")
        entry = watch.chrome_drift()["drifted"][0]
        self.assertEqual(entry["holder"], "parked")
        self.assertEqual(entry["applicationId"], "app-parked")


class RecycleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-chrome-recycle-")
        self.original_locks = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.dir)
        watch._CHROME_CHECK.update({"at": 0.0, "installed": "",
                                    "recycled": [], "held": []})

    def tearDown(self):
        watch.LOCK_DIR = self.original_locks

    def test_an_idle_drifted_browser_is_recycled(self):
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        result = watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, ["apply-1"])
        self.assertEqual([e["slot"] for e in result["recycled"]], ["apply-1"])
        self.assertEqual(result["held"], [])

    def test_a_uniform_browser_is_left_alone(self):
        fleet = _FakeFleet({"apply-1": INSTALLED}).install(self)
        watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, [])

    def test_a_live_run_keeps_its_browser(self):
        """The form being filled right now is worth more than the dialog."""
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        watch.take_lock("apply-1", __import__("os").getpid(), note="app-live")
        result = watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, [])
        self.assertEqual([e["slot"] for e in result["held"]], ["apply-1"])

    def test_a_parked_slot_keeps_its_browser(self):
        """The user was told to come back to that page. Do not close it."""
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        watch.park_slot("apply-1", "app-parked")
        result = watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, [])
        self.assertEqual([e["slot"] for e in result["held"]], ["apply-1"])

    def test_the_idle_half_of_a_mixed_fleet_is_recycled(self):
        """The shape of the real incident: five mains, some of them busy."""
        fleet = _FakeFleet({
            "apply-1": OLD, "apply-2": OLD, "apply-3": OLD,
            "apply-4": OLD, "apply-5": INSTALLED,
        }).install(self)
        watch.take_lock("apply-2", __import__("os").getpid(), note="app-live")
        watch.park_slot("apply-3", "app-parked")
        result = watch.recycle_stale_browsers(force=True)
        self.assertEqual(sorted(fleet.stopped), ["apply-1", "apply-4"])
        self.assertEqual(sorted(e["slot"] for e in result["held"]),
                         ["apply-2", "apply-3"])

    def test_a_browser_that_will_not_die_is_reported_rather_than_claimed(self):
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        browser.stop_slot = lambda slot: False
        result = watch.recycle_stale_browsers(force=True)
        self.assertEqual(result["recycled"], [])
        self.assertEqual([e["slot"] for e in result["held"]], ["apply-1"])
        self.assertEqual(fleet.stopped, [])

    def test_the_check_is_throttled_between_ticks(self):
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        watch.recycle_stale_browsers(now=1000.0)
        self.assertEqual(fleet.stopped, ["apply-1"])
        fleet.versions["apply-1"] = OLD
        watch.recycle_stale_browsers(now=1000.0 + watch.CHROME_CHECK_SECONDS - 1)
        self.assertEqual(fleet.stopped, ["apply-1"], "probed again inside the window")
        watch.recycle_stale_browsers(now=1000.0 + watch.CHROME_CHECK_SECONDS + 1)
        self.assertEqual(fleet.stopped, ["apply-1", "apply-1"])


class MessageTests(unittest.TestCase):
    def entry(self, **overrides):
        entry = {"slot": "apply-3", "running": OLD, "installed": INSTALLED,
                 "reasons": ["version"], "missingFlags": [],
                 "holder": "parked", "applicationId": "bc839847c93d9e46"}
        entry.update(overrides)
        return [entry]

    def test_a_uniform_fleet_says_nothing(self):
        self.assertEqual(watch.chrome_drift_message([]), "")

    def test_an_entry_naming_no_reason_says_nothing(self):
        """Otherwise the remedy is appended to nothing.

        That produced a message telling the reader how to clear a slot it had
        not named, which is worse than silence because it looks like a report.
        """
        self.assertEqual(
            watch.chrome_drift_message(self.entry(reasons=[], missingFlags=[])),
            "")

    def test_a_held_slot_names_itself_its_holder_and_its_application(self):
        message = watch.chrome_drift_message(self.entry())
        for expected in ("apply-3", "parked", "bc839847c93d9e46", INSTALLED, OLD):
            self.assertIn(expected, message)

    def test_an_idle_slot_is_labelled_idle_rather_than_left_blank(self):
        message = watch.chrome_drift_message(
            self.entry(holder=None, applicationId=""))
        self.assertIn("idle", message)

    def test_the_message_carries_no_moving_number(self):
        """The tick line is compared against the last one to decide to log."""
        held = self.entry(heldSinceSeconds=4210)
        first = watch.chrome_drift_message(held)
        self.assertNotIn("4210", first)
        held[0]["heldSinceSeconds"] = 9999
        self.assertEqual(watch.chrome_drift_message(held), first)

    def test_the_message_says_how_to_clear_a_parked_slot(self):
        self.assertIn("--stop --slot", watch.chrome_drift_message(self.entry()))

    def test_a_stale_binary_makes_no_keychain_claim(self):
        """PR #111 closed that at the source, for the version half.

        A message that blames a fault this branch can no longer produce sends
        the next reader after the wrong thing. The flags half is the one that
        may name the flag, and it does so by printing the flag rather than by
        explaining the dialog.
        """
        self.assertNotIn("keychain", watch.chrome_drift_message(self.entry()).lower())


class RunningLaunchTests(unittest.TestCase):
    """Reading the launch out of the process table, which is its only source."""

    def ps(self, lines):
        original = browser.subprocess.run
        self.addCleanup(setattr, browser.subprocess, "run", original)

        class Result:
            stdout = "\n".join(lines) + "\n"

        browser.subprocess.run = lambda *a, **k: Result()

    def main_line(self, extra=""):
        profile = browser._profile_dir("apply-1")
        port = browser.port_for("apply-1")
        return (f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                f"--remote-debugging-port={port} --user-data-dir={profile} "
                f"--no-first-run --no-default-browser-check {extra} about:blank")

    def test_the_main_is_found(self):
        self.ps([self.main_line("--use-mock-keychain")])
        self.assertIn("--use-mock-keychain", browser.running_launch("apply-1"))

    def test_a_helper_is_never_mistaken_for_the_main(self):
        """Helpers inherit much of the main's command line.

        One counted as the main answers about a renderer, and a renderer that
        happens to carry the flag would vouch for a main that does not.
        """
        helper = self.main_line("--type=renderer")
        self.ps([helper])
        self.assertEqual(browser.running_launch("apply-1"), [])

    def test_the_main_is_picked_out_from_beside_its_helpers(self):
        self.ps([self.main_line("--type=renderer"),
                 self.main_line("--use-mock-keychain"),
                 self.main_line("--type=gpu-process")])
        argv = browser.running_launch("apply-1")
        self.assertIn("--use-mock-keychain", argv)
        self.assertNotIn("--type=renderer", argv)

    def test_a_slot_with_no_process_reports_nothing_missing(self):
        self.ps(["/usr/bin/something --unrelated"])
        self.assertEqual(browser.running_launch("apply-1"), [])
        self.assertEqual(browser.missing_launch_flags("apply-1"), [])

    def test_a_main_started_before_the_flag_reports_it_missing(self):
        """The live apply-1 case on 2026-08-28, reconstructed."""
        self.ps([self.main_line()])
        self.assertEqual(browser.missing_launch_flags("apply-1"),
                         ["--use-mock-keychain"])

    def test_a_current_main_reports_nothing_missing(self):
        self.ps([self.main_line("--use-mock-keychain")])
        self.assertEqual(browser.missing_launch_flags("apply-1"), [])

    def test_another_slots_browser_is_not_consulted(self):
        other = (f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                 f"--remote-debugging-port={browser.port_for('apply-2')} "
                 f"--user-data-dir={browser._profile_dir('apply-2')} "
                 f"--use-mock-keychain about:blank")
        self.ps([other])
        self.assertEqual(browser.running_launch("apply-1"), [])

    def test_a_stranger_on_this_slots_port_is_not_this_slots_browser(self):
        """The profile is what makes the match ours rather than merely nearby.

        Matching on the port alone would read a hand-run Chrome, or a probe
        someone left on that port, and vouch for a slot browser using a launch
        that was never the slot's. The port is a coincidence; the profile is the
        identity.
        """
        stranger = (f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                    f"--remote-debugging-port={browser.port_for('apply-1')} "
                    f"--user-data-dir=/tmp/somebody-elses-profile "
                    f"--use-mock-keychain about:blank")
        self.ps([stranger])
        self.assertEqual(browser.running_launch("apply-1"), [])

    def test_an_unknown_slot_is_refused(self):
        with self.assertRaises(ValueError):
            browser.running_launch("apply-99")


class LaunchFlagDriftTests(unittest.TestCase):
    """The half a version check cannot see.

    #111 added --use-mock-keychain to the launch. A browser already running
    keeps the command line it started with, and serve() starts one only when the
    slot has none, so nothing replaces it. Measured on the live fleet on
    2026-08-28: apply-1 started at 00:38:14, #111 was committed at 00:38:36, and
    apply-1 was still running without the flag hours later on the current build.
    Every version check called it healthy.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-chrome-flags-")
        self.original_locks = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.dir)
        watch._CHROME_CHECK.update({"at": 0.0, "installed": "",
                                    "recycled": [], "held": []})

    def tearDown(self):
        watch.LOCK_DIR = self.original_locks

    def test_a_current_build_missing_a_flag_is_still_drift(self):
        """The exact live case: right binary, wrong launch."""
        _FakeFleet({"apply-1": INSTALLED},
                   missing={"apply-1": ["--use-mock-keychain"]}).install(self)
        drifted = watch.chrome_drift()["drifted"]
        self.assertEqual([entry["slot"] for entry in drifted], ["apply-1"])
        self.assertEqual(drifted[0]["reasons"], ["flags"])
        self.assertEqual(drifted[0]["missingFlags"], ["--use-mock-keychain"])

    def test_an_idle_browser_missing_a_flag_is_recycled(self):
        fleet = _FakeFleet({"apply-1": INSTALLED},
                           missing={"apply-1": ["--use-mock-keychain"]})
        fleet.install(self)
        watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, ["apply-1"])

    def test_a_busy_browser_missing_a_flag_is_kept_but_pings(self):
        """The one drift that does earn the alarm, because it reaches the user.

        Without --use-mock-keychain that process can raise a modal keychain
        dialog no agent can see, which is the fault this seam exists for.
        """
        fleet = _FakeFleet({"apply-1": INSTALLED},
                           missing={"apply-1": ["--use-mock-keychain"]})
        fleet.install(self)
        watch.park_slot("apply-1", "app-parked")
        watch.recycle_stale_browsers(force=True)
        self.assertEqual(fleet.stopped, [])
        warning = watch.chrome_flag_warning()
        self.assertIn("apply-1", warning)
        self.assertIn("--use-mock-keychain", warning)

    def test_both_reasons_are_reported_together(self):
        _FakeFleet({"apply-1": OLD},
                   missing={"apply-1": ["--use-mock-keychain"]}).install(self)
        entry = watch.chrome_drift()["drifted"][0]
        self.assertEqual(entry["reasons"], ["version", "flags"])

    def test_a_slot_that_is_down_is_never_asked_about_flags(self):
        _FakeFleet({}).install(self)
        self.assertEqual(watch.chrome_drift()["drifted"], [])


class AlarmScopeTests(unittest.TestCase):
    """A stale binary is untidy; a missing flag reaches the user. Not the same.

    #111 means a merely out-of-date browser no longer touches the keychain, so
    paging for it would be an alarm for a cosmetic condition. A browser that
    predates #111 still can, and nothing is allowed to close it if it holds a
    form, so that one has to say something.
    """

    def setUp(self):
        watch._CHROME_CHECK.update({"at": 0.0, "installed": "",
                                    "recycled": [], "held": []})

    def _held(self, **overrides):
        entry = {"slot": "apply-1", "running": INSTALLED, "installed": INSTALLED,
                 "reasons": ["flags"], "missingFlags": ["--use-mock-keychain"],
                 "holder": "parked", "applicationId": "abc"}
        entry.update(overrides)
        watch._CHROME_CHECK.update({"installed": INSTALLED, "recycled": [],
                                    "held": [entry]})

    def test_a_stale_binary_alone_never_pings(self):
        self._held(running=OLD, reasons=["version"], missingFlags=[])
        self.assertEqual(watch.chrome_flag_warning(), "")

    def test_a_missing_flag_pings(self):
        self._held()
        self.assertNotEqual(watch.chrome_flag_warning(), "")

    def test_a_recycled_slot_never_pings(self):
        watch._CHROME_CHECK.update({
            "installed": INSTALLED, "held": [],
            "recycled": [{"slot": "apply-1", "running": INSTALLED,
                          "installed": INSTALLED, "reasons": ["flags"],
                          "missingFlags": ["--use-mock-keychain"],
                          "holder": None, "applicationId": ""}]})
        self.assertEqual(watch.chrome_flag_warning(), "")

    def test_the_ping_carries_the_flag_branch_and_not_the_version_branch(self):
        source = (ROOT / "scripts" / "watch.py").read_text()
        marker = "stale = \"; \".join("
        self.assertIn(marker, source)
        joined = source.split(marker, 1)[1].split("if message", 1)[0]
        self.assertIn("stale_board_warning()", joined)
        self.assertIn("parked_slot_warning()", joined)
        self.assertIn("chrome_flag_warning()", joined)
        self.assertNotIn("chrome_drift_warning", joined)


class StopSlotTests(unittest.TestCase):
    def test_an_unknown_slot_is_refused(self):
        with self.assertRaises(ValueError):
            browser.stop_slot("apply-99")

    def test_a_slot_with_nothing_in_it_is_already_stopped(self):
        original = browser.running
        browser.running = lambda slot=None: False
        self.addCleanup(setattr, browser, "running", original)
        self.assertTrue(browser.stop_slot("apply-1"))

    def test_a_browser_still_shutting_down_is_waited_for(self):
        """Measured against a real Chrome: SIGTERM does not return a dead port.

        Reading `running` once after pkill answered False every time while the
        browser was on its way out, which reports a retired slot as a refusal.
        """
        answers = [True, True, True, True, False]
        originals = (browser.running, browser.tabs, browser.subprocess.run,
                     browser.time.sleep)
        browser.running = lambda slot=None: answers.pop(0) if answers else False
        browser.tabs = lambda slot=None: []
        browser.subprocess.run = lambda *a, **k: None
        browser.time.sleep = lambda _seconds: None
        self.addCleanup(setattr, browser, "running", originals[0])
        self.addCleanup(setattr, browser, "tabs", originals[1])
        self.addCleanup(setattr, browser.subprocess, "run", originals[2])
        self.addCleanup(setattr, browser.time, "sleep", originals[3])
        self.assertTrue(browser.stop_slot("apply-1"))

    def test_a_browser_that_never_goes_down_is_reported_as_such(self):
        originals = (browser.running, browser.tabs, browser.subprocess.run,
                     browser.time.sleep)
        browser.running = lambda slot=None: True
        browser.tabs = lambda slot=None: []
        browser.subprocess.run = lambda *a, **k: None
        browser.time.sleep = lambda _seconds: None
        self.addCleanup(setattr, browser, "running", originals[0])
        self.addCleanup(setattr, browser, "tabs", originals[1])
        self.addCleanup(setattr, browser.subprocess, "run", originals[2])
        self.addCleanup(setattr, browser.time, "sleep", originals[3])
        self.assertFalse(browser.stop_slot("apply-1", timeout=0.5))

    def test_stop_all_only_reports_the_browsers_it_took_down(self):
        seen = []
        originals = (browser.running, browser.stop_slot)
        up = {"apply-2"}
        browser.running = lambda slot=None: slot in up
        browser.stop_slot = lambda slot: (seen.append(slot), up.discard(slot), True)[-1]
        original_sleep = browser.time.sleep
        browser.time.sleep = lambda _seconds: None
        self.addCleanup(setattr, browser.time, "sleep", original_sleep)
        self.addCleanup(setattr, browser, "running", originals[0])
        self.addCleanup(setattr, browser, "stop_slot", originals[1])
        out = browser.stop_all()
        self.assertEqual(out["stopped"], ["apply-2"])
        self.assertEqual(seen, ["apply-2"])
        self.assertEqual(out["stillRunning"], [])

    def test_stop_all_can_take_one_slot(self):
        seen = []
        originals = (browser.running, browser.stop_slot)
        browser.running = lambda slot=None: slot in {"apply-1", "apply-2"}
        browser.stop_slot = lambda slot: (seen.append(slot), True)[-1]
        original_sleep = browser.time.sleep
        browser.time.sleep = lambda _seconds: None
        self.addCleanup(setattr, browser.time, "sleep", original_sleep)
        self.addCleanup(setattr, browser, "running", originals[0])
        self.addCleanup(setattr, browser, "stop_slot", originals[1])
        self.assertEqual(browser.stop_all(["apply-2"])["stopped"], ["apply-2"])
        self.assertEqual(seen, ["apply-2"])

    def test_stop_all_ignores_a_slot_that_does_not_exist(self):
        original = browser.running
        browser.running = lambda slot=None: False
        self.addCleanup(setattr, browser, "running", original)
        self.assertEqual(browser.stop_all(["apply-99"])["stopped"], [])


class StatusTests(unittest.TestCase):
    def test_status_reports_the_fleet_and_serialises(self):
        """`watch.py --status` is the pre-deploy check, so it has to hold this."""
        _FakeFleet({"apply-1": OLD, "apply-2": INSTALLED}).install(self)
        drift = watch.chrome_drift()
        block = {**drift, "warning": watch.chrome_drift_message(drift["drifted"])}
        self.assertEqual(block["installed"], INSTALLED)
        self.assertEqual(block["running"]["apply-1"], OLD)
        self.assertIsNone(block["running"]["apply-8"])
        self.assertIn("apply-1", block["warning"])
        json.dumps(block)


if __name__ == "__main__":
    unittest.main()
