"""Browser slot ownership across parked application sessions."""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import browser  # noqa: E402
import run_apply  # noqa: E402
import store_isolation  # noqa: E402
import watch  # noqa: E402


def setUpModule():
    # reconcile_browser_slots() saves the sessions it reconciles through the
    # real store CLI. Unisolated, that filed acme-pm in the device's own
    # sessions/ and roles.json: a fabricated application sitting in Needs you,
    # pending on "Enter the OTP", written by the same command a real run uses
    # and so indistinguishable from one.
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


class ParkedReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_lock_dir = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.temp.name)

    def tearDown(self):
        watch.LOCK_DIR = self.original_lock_dir
        self.temp.cleanup()

    def test_parked_slot_stays_reserved_after_runner_exit(self):
        watch.park_slot("apply-2", "acme-pm")

        self.assertNotIn("apply-2", watch.free_apply_slots())
        self.assertNotIn("acme-pm", watch.running_applications())
        self.assertFalse(watch.reserve("apply-2", note="another-application"))

    def test_the_same_application_can_reclaim_its_parked_slot(self):
        watch.park_slot("apply-3", "acme-pm")

        self.assertEqual(watch.candidate_apply_slots("acme-pm"), ["apply-3"])
        self.assertTrue(watch.reserve("apply-3", note="acme-pm"))
        holder = watch.lock_holder("apply-3")
        self.assertEqual(holder["pid"], os.getpid())
        self.assertNotIn("parked", holder)

    def test_dead_runner_becomes_parked_when_the_session_keeps_affinity(self):
        watch.take_lock("apply-1", 999999, note="acme-pm")
        stored = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "applicationId": "acme-pm",
                "browserSlot": "apply-1",
            }),
            stderr="",
        )
        with mock.patch.object(watch, "store", return_value=stored):
            holder = watch.lock_holder("apply-1")

        self.assertTrue(holder["parked"])
        self.assertEqual(holder["note"], "acme-pm")

    def test_a_crossed_affinity_falls_through_to_a_free_slot(self):
        """Affinity protects form state, and a crossed affinity protects none.

        This asserted [] until 2026-08-25, which is not "wait for your slot",
        it is "never run". apply-4's Chrome profile belongs to whoever parked
        it, so there is nothing of acme-pm's in it to go back to, and holding
        out for it costs everything queued behind acme-pm: only the head of the
        Apply queue is ever started. It wedged the real queue for four and a
        half hours with a free slot unused, and no Retry could clear it,
        because pressing Retry re-queued behind the same stuck head.

        What affinity is actually for is still pinned, one test above: an
        application reclaims a slot it parked itself.
        """
        watch.park_slot("apply-4", "different-application")
        with mock.patch.object(
                watch, "browser_slot_for_application", return_value="apply-4"):
            self.assertEqual(
                watch.candidate_apply_slots("acme-pm"), watch.free_apply_slots())
            self.assertIn("apply-1", watch.candidate_apply_slots("acme-pm"))

    def test_a_crossed_affinity_with_nothing_free_still_waits(self):
        for slot in watch.APPLY_LOCKS:
            watch.park_slot(slot, "different-application")
        with mock.patch.object(
                watch, "browser_slot_for_application", return_value="apply-4"):
            self.assertEqual(watch.candidate_apply_slots("acme-pm"), [])

    def test_a_live_runner_on_the_affinity_slot_also_falls_through(self):
        # Held by another application's live runner rather than parked. Same
        # conclusion: that profile is not ours and never will be.
        watch.take_lock("apply-4", os.getpid(), note="different-application")
        with mock.patch.object(
                watch, "browser_slot_for_application", return_value="apply-4"):
            self.assertIn("apply-1", watch.candidate_apply_slots("acme-pm"))

    def test_a_slot_this_application_parked_is_not_stale(self):
        watch.park_slot("apply-4", "acme-pm")
        self.assertFalse(watch.slot_affinity_is_stale("acme-pm", "apply-4"))
        self.assertTrue(watch.slot_affinity_is_stale("someone-else", "apply-4"))

    def test_a_free_affinity_slot_is_not_stale(self):
        self.assertFalse(watch.slot_affinity_is_stale("acme-pm", "apply-4"))


class SlotReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_lock_dir = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.temp.name)

    def tearDown(self):
        watch.LOCK_DIR = self.original_lock_dir
        self.temp.cleanup()

    @staticmethod
    def parked_session():
        return {
            "applicationId": "acme-pm",
            "status": "review",
            "pendingFields": [{"question": "Enter the OTP"}],
            "browserSlot": "apply-2",
        }

    def test_restart_rebuilds_a_reservation_for_a_run_that_is_still_in_flight(self):
        """The recovery property, kept, and moved onto the rule that replaced it.

        A restart loses the lock files and the sessions are what remember who
        owned what. That still matters, but only for an application actually in
        flight: rebuilding a reservation for one sitting in Needs you is how the
        pool refilled itself with dead holders after every restart.
        """
        entry = dict(self.parked_session(), status="inflight")
        entry.pop("pendingFields", None)
        with mock.patch.object(watch, "_sessions", return_value=[entry]), \
                mock.patch.object(watch, "_latest_history", return_value={}):
            result = watch.reconcile_browser_slots()

        self.assertEqual(result["parked"], [
            {"applicationId": "acme-pm", "slot": "apply-2"}
        ])
        self.assertTrue(watch.lock_holder("apply-2")["parked"])

    def test_restart_does_not_rebuild_one_for_an_application_waiting_on_the_user(self):
        entry = self.parked_session()
        with mock.patch.object(watch, "_sessions", return_value=[entry]), \
                mock.patch.object(watch, "_latest_history", return_value={}):
            result = watch.reconcile_browser_slots()

        self.assertEqual(result["parked"], [])
        self.assertIsNone(watch.lock_holder("apply-2"))

    def test_terminal_completion_clears_tabs_and_state_before_unlock(self):
        entry = self.parked_session()
        watch.park_slot("apply-2", "acme-pm")
        order = []

        def cleared(slot):
            order.append(("clear", slot, watch.lock_path(slot).exists()))
            return {"cleared": True, "slot": slot}

        def saved(session):
            order.append(("save", session.get("browserSlot"),
                          watch.lock_path("apply-2").exists()))

        with mock.patch.object(watch, "_sessions", return_value=[entry]), \
                mock.patch.object(
                    watch, "_latest_history", return_value={"acme-pm": "completed"}), \
                mock.patch.object(watch.browser, "clear_slot", side_effect=cleared), \
                mock.patch.object(watch, "_save_session", side_effect=saved):
            result = watch.reconcile_browser_slots()

        self.assertEqual(order, [
            ("clear", "apply-2", True),
            ("save", None, True),
        ])
        self.assertFalse(watch.lock_path("apply-2").exists())
        self.assertNotIn("browserSlot", entry)
        self.assertEqual(result["released"], [
            {"applicationId": "acme-pm", "slot": "apply-2"}
        ])

    def test_failed_tab_cleanup_keeps_affinity_and_reservation(self):
        entry = self.parked_session()
        watch.park_slot("apply-2", "acme-pm")
        with mock.patch.object(
                watch.browser, "clear_slot",
                return_value={"cleared": False, "slot": "apply-2", "error": "busy"}), \
                mock.patch.object(watch, "_save_session") as save:
            released = watch.release_browser_slot("acme-pm", "apply-2", entry)

        self.assertFalse(released)
        self.assertEqual(entry["browserSlot"], "apply-2")
        self.assertTrue(watch.lock_holder("apply-2")["parked"])
        save.assert_not_called()


class BrowserAffinityTests(unittest.TestCase):
    def test_open_and_finish_searches_only_the_recorded_slot(self):
        page = {
            "id": "target-page",
            "type": "page",
            "url": "https://jobs.example.com/apply/123?step=two",
            "title": "Application",
        }
        with mock.patch.object(browser, "running", return_value=True), \
                mock.patch.object(browser, "tabs", return_value=[page]) as tabs, \
                mock.patch.object(browser, "_cdp", return_value="ok") as cdp, \
                mock.patch.object(browser.subprocess, "run", return_value=SimpleNamespace()):
            result = browser.focus(
                "https://jobs.example.com/apply/123", slot="apply-3"
            )

        tabs.assert_called_once_with("apply-3")
        cdp.assert_called_once_with("/json/activate/target-page", "apply-3")
        self.assertEqual(result["slot"], "apply-3")

    def test_clear_slot_verifies_that_application_pages_are_gone(self):
        page = {"id": "private-form", "type": "page", "url": "https://jobs.example.com"}
        blank = {"id": "blank", "type": "page", "url": "about:blank"}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(browser, "ARTIFACTS", pathlib.Path(directory)), \
                    mock.patch.object(browser, "running", return_value=True), \
                    mock.patch.object(browser, "tabs", side_effect=[[page], [blank]]), \
                    mock.patch.object(browser, "_cdp", return_value="closing") as cdp:
                result = browser.clear_slot("apply-1")

        self.assertTrue(result["cleared"])
        cdp.assert_called_once_with("/json/close/private-form", "apply-1")

    def test_clear_slot_removes_persistent_mcp_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            original = browser.ARTIFACTS
            browser.ARTIFACTS = pathlib.Path(directory)
            private = browser.ARTIFACTS / "apply-2"
            private.mkdir()
            (private / "filled-form-snapshot.txt").write_text("private form")
            try:
                with mock.patch.object(browser, "running", return_value=False):
                    result = browser.clear_slot("apply-2")
            finally:
                browser.ARTIFACTS = original
        self.assertTrue(result["cleared"])
        self.assertFalse(private.exists())

    def test_runner_removes_affinity_only_after_terminal_cleanup(self):
        session = {
            "applicationId": "acme-pm",
            "status": "inflight",
            "browserSlot": "apply-5",
        }
        saved_payloads = []

        def save(argv, **kwargs):
            saved_payloads.append(json.loads(kwargs["input"]))
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with mock.patch.object(
                run_apply.browser, "clear_slot",
                return_value={"cleared": True, "slot": "apply-5"}) as clear, \
                mock.patch.object(run_apply, "_current_session", return_value=session), \
                mock.patch.object(run_apply.subprocess, "run", side_effect=save):
            result = run_apply.release_terminal_browser_slot("acme-pm", "apply-5")

        clear.assert_called_once_with("apply-5")
        self.assertTrue(result["released"])
        self.assertNotIn("browserSlot", saved_payloads[0])


class IdleSlotReleaseTests(unittest.TestCase):
    """A parked slot exists for a form the user still has to act on.

    Once the application is back in Watching there is no form in flight and no
    blocker, so the reservation is holding a Chrome profile nobody is coming
    back to. There are five, and on 2026-08-25 four were held that way while
    the Apply queue sat behind them. The applications looked healthy on the
    board, which is why nothing surfaced it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.temp.name)

    def tearDown(self):
        watch.LOCK_DIR = self.original
        self.temp.cleanup()

    def reconcile(self, session):
        with mock.patch.object(watch, "_sessions", return_value=[session]), \
                mock.patch.object(watch, "_latest_history", return_value={}), \
                mock.patch.object(watch, "release_browser_slot",
                                  side_effect=lambda aid, slot, entry=None: True):
            return watch.reconcile_browser_slots()

    def session(self, status, pending=None, slot="apply-2"):
        entry = {"applicationId": "acme-pm", "status": status, "browserSlot": slot}
        if pending:
            entry["pendingFields"] = pending
        return entry

    def test_a_slot_for_an_application_back_in_watching_is_released(self):
        watch.park_slot("apply-2", "acme-pm")
        result = self.reconcile(self.session("watch"))
        self.assertEqual(
            result["released"], [{"applicationId": "acme-pm", "slot": "apply-2"}])

    def test_a_slot_parked_for_the_user_is_released(self):
        """Reversed on 2026-08-27, deliberately, and this is the whole change.

        The reservation existed so a half-filled form survived until the user
        cleared a CAPTCHA or signed in, and a Retry could return to it. What it
        bought in practice was a pool that deadlocks: five parked applications
        held all five slots, four queued retries could never get one, and three
        Drops drained into a fleet with nothing to give them, for twenty hours,
        with every application looking healthy.

        So a park releases now and Retry reopens the posting. The form state is
        the price and it is the right way round; a convenience that can take
        throughput to zero is not a convenience.
        """
        watch.park_slot("apply-2", "acme-pm")
        result = self.reconcile(
            self.session("review", pending=[{"question": "Enter the OTP"}]))
        self.assertEqual(result["released"],
                         [{"applicationId": "acme-pm", "slot": "apply-2"}])

    def test_a_slot_for_a_live_application_is_kept(self):
        watch.park_slot("apply-2", "acme-pm")
        self.assertEqual(self.reconcile(self.session("inflight"))["released"], [])

    def test_only_in_flight_keeps_a_slot_whatever_else_is_recorded(self):
        """One rule beats a list of exceptions, and the list was what was wrong.

        Status and blocker disagreeing used to buy a slot on the reasoning that
        somebody might come back to the form. Every such exception is capacity
        held on a maybe, and the exceptions are what let the pool reach zero.
        """
        for status in ("watch", "review", "active", "completed", "dropped"):
            with self.subTest(status=status):
                watch.park_slot("apply-2", "acme-pm")
                result = self.reconcile(
                    self.session(status, pending=[{"question": "Sign in"}]))
                self.assertEqual(
                    result["released"],
                    [{"applicationId": "acme-pm", "slot": "apply-2"}],
                    f"{status} held a slot",
                )
