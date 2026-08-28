"""A Chrome update must not leave slot browsers running a deleted binary.

serve() starts a slot browser on demand and nothing stops it afterwards, so an
unattended Chrome main outlives releases. Measured on 2026-08-28: five were on
151.0.7922.174 with .175 on disk, some up since 2026-08-25. Each was executing a
bundle that no longer existed at that path, so the release's fixes were not in
effect in the browsers that fill forms on employer sites, and every helper they
spawned from then on came from the replaced bundle while the main did not.

This is hygiene. The incident that led here was a looping macOS "Keychain Not
Found" dialog, and PR #111 closed that class at the source instead by passing
--use-mock-keychain at both Chrome launch sites, so no Jobby browser consults
the keychain at all. That is why nothing here pages anyone: with #111 in place a
drifted browser is untidy rather than visible to the user, and the tests below
pin the reporting to --status and the tick log rather than to the dead-man ping.

Two properties, and both have to hold or the cure is worse than the fault:

  an idle drifted browser is recycled, so the common case clears itself; and
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
    """Slot browsers as versions, and a record of what was taken down."""

    def __init__(self, versions, installed=INSTALLED):
        self.versions = dict(versions)
        self.installed = installed
        self.stopped = []

    def install(self, case):
        case.addCleanup(setattr, browser, "installed_version",
                        browser.installed_version)
        case.addCleanup(setattr, browser, "running_version",
                        browser.running_version)
        case.addCleanup(setattr, browser, "stop_slot", browser.stop_slot)
        browser.installed_version = lambda: self.installed
        browser.running_version = lambda slot=None: self.versions.get(slot, "")
        browser.stop_slot = self._stop
        return self

    def _stop(self, slot):
        self.stopped.append(slot)
        self.versions.pop(slot, None)
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
        self.assertEqual(watch.chrome_version_drift()["drifted"], [])

    def test_an_older_browser_is_drift(self):
        _FakeFleet({"apply-1": OLD}).install(self)
        drifted = watch.chrome_version_drift()["drifted"]
        self.assertEqual([entry["slot"] for entry in drifted], ["apply-1"])
        self.assertEqual(drifted[0]["running"], OLD)
        self.assertEqual(drifted[0]["installed"], INSTALLED)
        self.assertIsNone(drifted[0]["holder"])

    def test_a_slot_with_no_browser_cannot_drift(self):
        _FakeFleet({}).install(self)
        self.assertEqual(watch.chrome_version_drift()["drifted"], [])

    def test_an_unreadable_installed_version_is_never_drift(self):
        """The guard that stops a moved plist emptying the whole fleet."""
        fleet = _FakeFleet({"apply-1": OLD, "apply-2": OLD}, installed="")
        fleet.install(self)
        self.assertEqual(watch.chrome_version_drift()["drifted"], [])
        watch.recycle_updated_browsers(force=True)
        self.assertEqual(fleet.stopped, [])

    def test_an_unreadable_running_version_is_never_drift(self):
        """A browser too slow to answer is not evidence that it is stale."""
        fleet = _FakeFleet({"apply-1": ""})
        fleet.install(self)
        self.assertEqual(watch.chrome_version_drift()["drifted"], [])
        watch.recycle_updated_browsers(force=True)
        self.assertEqual(fleet.stopped, [])

    def test_the_holder_is_reported_for_a_live_run(self):
        _FakeFleet({"apply-1": OLD}).install(self)
        self.live("apply-1", "app-live")
        entry = watch.chrome_version_drift()["drifted"][0]
        self.assertEqual(entry["holder"], "live")
        self.assertEqual(entry["applicationId"], "app-live")

    def test_the_holder_is_reported_for_a_parked_slot(self):
        _FakeFleet({"apply-1": OLD}).install(self)
        self.parked("apply-1", "app-parked")
        entry = watch.chrome_version_drift()["drifted"][0]
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
        result = watch.recycle_updated_browsers(force=True)
        self.assertEqual(fleet.stopped, ["apply-1"])
        self.assertEqual([e["slot"] for e in result["recycled"]], ["apply-1"])
        self.assertEqual(result["held"], [])

    def test_a_uniform_browser_is_left_alone(self):
        fleet = _FakeFleet({"apply-1": INSTALLED}).install(self)
        watch.recycle_updated_browsers(force=True)
        self.assertEqual(fleet.stopped, [])

    def test_a_live_run_keeps_its_browser(self):
        """The form being filled right now is worth more than the dialog."""
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        watch.take_lock("apply-1", __import__("os").getpid(), note="app-live")
        result = watch.recycle_updated_browsers(force=True)
        self.assertEqual(fleet.stopped, [])
        self.assertEqual([e["slot"] for e in result["held"]], ["apply-1"])

    def test_a_parked_slot_keeps_its_browser(self):
        """The user was told to come back to that page. Do not close it."""
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        watch.park_slot("apply-1", "app-parked")
        result = watch.recycle_updated_browsers(force=True)
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
        result = watch.recycle_updated_browsers(force=True)
        self.assertEqual(sorted(fleet.stopped), ["apply-1", "apply-4"])
        self.assertEqual(sorted(e["slot"] for e in result["held"]),
                         ["apply-2", "apply-3"])

    def test_a_browser_that_will_not_die_is_reported_rather_than_claimed(self):
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        browser.stop_slot = lambda slot: False
        result = watch.recycle_updated_browsers(force=True)
        self.assertEqual(result["recycled"], [])
        self.assertEqual([e["slot"] for e in result["held"]], ["apply-1"])
        self.assertEqual(fleet.stopped, [])

    def test_the_check_is_throttled_between_ticks(self):
        fleet = _FakeFleet({"apply-1": OLD}).install(self)
        watch.recycle_updated_browsers(now=1000.0)
        self.assertEqual(fleet.stopped, ["apply-1"])
        fleet.versions["apply-1"] = OLD
        watch.recycle_updated_browsers(now=1000.0 + watch.CHROME_CHECK_SECONDS - 1)
        self.assertEqual(fleet.stopped, ["apply-1"], "probed again inside the window")
        watch.recycle_updated_browsers(now=1000.0 + watch.CHROME_CHECK_SECONDS + 1)
        self.assertEqual(fleet.stopped, ["apply-1", "apply-1"])


class MessageTests(unittest.TestCase):
    def test_a_uniform_fleet_says_nothing(self):
        self.assertEqual(watch.chrome_drift_message([]), "")

    def test_a_held_slot_names_itself_its_holder_and_its_application(self):
        message = watch.chrome_drift_message(
            [{"slot": "apply-3", "running": OLD, "installed": INSTALLED,
              "holder": "parked", "applicationId": "bc839847c93d9e46"}])
        for expected in ("apply-3", "parked", "bc839847c93d9e46", INSTALLED, OLD):
            self.assertIn(expected, message)

    def test_an_idle_slot_is_labelled_idle_rather_than_left_blank(self):
        message = watch.chrome_drift_message(
            [{"slot": "apply-3", "running": OLD, "installed": INSTALLED,
              "holder": None, "applicationId": ""}])
        self.assertIn("idle", message)

    def test_the_message_carries_no_moving_number(self):
        """The tick line is compared against the last one to decide to log."""
        held = [{"slot": "apply-3", "running": OLD, "installed": INSTALLED,
                 "holder": "parked", "applicationId": "abc",
                 "heldSinceSeconds": 4210}]
        first = watch.chrome_drift_message(held)
        self.assertNotIn("4210", first)
        held[0]["heldSinceSeconds"] = 9999
        self.assertEqual(watch.chrome_drift_message(held), first)

    def test_the_message_says_how_to_clear_a_parked_slot(self):
        message = watch.chrome_drift_message(
            [{"slot": "apply-3", "running": OLD, "installed": INSTALLED,
              "holder": "parked", "applicationId": "abc"}])
        self.assertIn("--stop --slot", message)

    def test_the_message_makes_no_keychain_claim(self):
        """PR #111 closed that at the source. Saying it here would be stale.

        A message that blames a fault the code can no longer produce sends the
        next reader after the wrong thing, which is the expensive kind of wrong.
        """
        message = watch.chrome_drift_message(
            [{"slot": "apply-3", "running": OLD, "installed": INSTALLED,
              "holder": "parked", "applicationId": "abc"}])
        self.assertNotIn("keychain", message.lower())


class NoAlarmTests(unittest.TestCase):
    """Drift is untidy, not user-visible, so it must never page anyone.

    #111 means a stale browser no longer reaches the user at all. A dead-man
    ping that fires for it would be an alarm for a cosmetic condition, and this
    codebase has already learned twice over what that does to an alarm.
    """

    def test_the_dead_man_ping_is_only_the_board_and_the_parked_slots(self):
        source = (ROOT / "scripts" / "watch.py").read_text()
        marker = "stale = \"; \".join("
        self.assertIn(marker, source)
        joined = source.split(marker, 1)[1].split(")\n", 1)[0]
        self.assertIn("stale_board_warning()", joined)
        self.assertIn("parked_slot_warning()", joined)
        self.assertNotIn("chrome", joined.lower())

    def test_no_chrome_drift_warning_survives_for_the_ping_to_reach(self):
        self.assertFalse(hasattr(watch, "chrome_drift_warning"))


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
        drift = watch.chrome_version_drift()
        block = {**drift, "warning": watch.chrome_drift_message(drift["drifted"])}
        self.assertEqual(block["installed"], INSTALLED)
        self.assertEqual(block["running"]["apply-1"], OLD)
        self.assertIsNone(block["running"]["apply-8"])
        self.assertIn("apply-1", block["warning"])
        json.dumps(block)


if __name__ == "__main__":
    unittest.main()
