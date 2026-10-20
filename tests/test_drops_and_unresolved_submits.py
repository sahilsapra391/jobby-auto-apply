"""A drop holds, and a submit nobody has answered lands where it can be answered.

Two faults from 2026-09-12, both against a real store in a temporary root.

**A dropped application.** Dropped at 05:52:51Z inside a tick that had already
passed `do_not_start`. The runner refused the start as dropped and its refusal
handler ran `phase.py --restore`, which set the status from `previousStatus`
with no dropped check: the application came back as `review`, `droppedAt` still
set, and Retry worked on it from Needs you.

**An unresolved submit.** The run clicked Submit, saw no confirmation, and
ended with a latest `submitting`. The session stayed `inflight` at phase
"Submitting", the stuck sweep would have told it to press Retry (which the
device refuses), and nothing anywhere could write the `abandoned` that says it
never went in.

Run from the repo root: python3 -m unittest discover -s tests
"""

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import store_isolation  # noqa: E402
import apply_marks  # noqa: E402
import run_apply  # noqa: E402
import watch  # noqa: E402

STORE = ROOT / "scripts" / "store.py"
PHASE = ROOT / "scripts" / "phase.py"
AID = "acme-pm"


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


class StoreCase(unittest.TestCase):
    """A fresh store per test, reached the way the scripts reach it: through
    `JOB_APPLICATION_DIR`, with no `--root` anywhere, so every subprocess the
    code under test spawns lands in the same temporary root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="jobby-drops-")
        self.root = pathlib.Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        subprocess.run([sys.executable, str(STORE), "init"], capture_output=True)

    def store(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(STORE), *args],
                              capture_output=True, text=True, input=stdin)

    def save(self, **session):
        body = {"applicationId": AID, "company": "Acme", "role": "PM", **session}
        r = self.store("session-save", "--id", AID, "--input", "-",
                       stdin=json.dumps(body))
        self.assertEqual(r.returncode, 0, r.stderr)

    def load(self):
        r = self.store("session-load", "--id", AID)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def event(self, kind):
        r = self.store("history-append", "--input", "-", stdin=json.dumps(
            {"event": kind, "applicationId": AID, "company": "Acme", "role": "PM"}))
        self.assertEqual(r.returncode, 0, r.stderr)

    def history(self):
        r = self.store("history-list")
        return [e for e in json.loads(r.stdout or "[]") if e.get("applicationId") == AID]

    def phase(self, *args):
        return subprocess.run([sys.executable, str(PHASE), "--id", AID, *args],
                              capture_output=True, text=True)

    def dropped(self):
        self.save(status="dropped", previousStatus="review",
                  droppedAt="2026-09-12T05:52:51Z", droppedFrom="Needs you")


# --- bug 1: a drop that does not hold ----------------------------------------

class PhaseNeverUndropsTests(StoreCase):
    def test_restore_leaves_a_dropped_application_dropped(self):
        # The exact call from that drop: the runner's refusal handler.
        self.dropped()
        r = self.phase("--restore", "--phase", "Stopped",
                       "--detail", "the user cancelled this application")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout).get("refused"), "dropped")
        after = self.load()
        self.assertEqual(after["status"], "dropped")
        self.assertEqual(after["droppedAt"], "2026-09-12T05:52:51Z")
        self.assertNotIn("phase", after)

    def test_no_flag_changes_a_dropped_status(self):
        for flags in (["--done"], ["--phase", "Queued"], ["--unconfirmed"],
                      ["--not-sent"], ["--restore"]):
            with self.subTest(flags=flags):
                self.dropped()
                self.phase(*flags)
                self.assertEqual(self.load()["status"], "dropped")

    def test_restore_still_hands_back_a_live_application(self):
        # The control: the refusal is about dropped, not about restore.
        self.save(status="inflight", previousStatus="review", phase="Queued")
        self.phase("--restore", "--phase", "Failed")
        self.assertEqual(self.load()["status"], "review")


class TheRunnersRefusalKeepsTheDropTests(StoreCase):
    def test_a_refused_start_of_a_dropped_application_leaves_it_dropped(self):
        self.dropped()
        with mock.patch.object(run_apply.browser, "clear_slot",
                               return_value={"cleared": True, "slot": "apply-1"}):
            run_apply.restore_preflight_refusal(
                AID, "apply-1", "the user cancelled this application")
        after = self.load()
        self.assertEqual(after["status"], "dropped")
        self.assertEqual(after["droppedAt"], "2026-09-12T05:52:51Z")


class TheTickRereadsTheDropTests(StoreCase):
    def test_session_dropped_reads_the_store(self):
        self.save(status="inflight", phase="Documents ready")
        self.assertFalse(watch.session_dropped(AID))
        self.dropped()
        self.assertTrue(watch.session_dropped(AID))

    def test_an_absent_session_is_not_dropped(self):
        self.assertFalse(watch.session_dropped("never-existed"))

    def test_an_unreadable_store_is_not_an_answer(self):
        # "Could not look" must not read as "not dropped": that is a spawn.
        failed = subprocess.CompletedProcess([], 1, "", "store is locked")
        with mock.patch.object(watch, "store", return_value=failed):
            with self.assertRaises(watch.TickError):
                watch.session_dropped(AID)


# --- bug 2: the stuck sweep and an unresolved submit -------------------------

class TheSweepReadsTheHistoryTests(StoreCase):
    def stale(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    def sweep(self):
        with mock.patch.object(watch, "running_applications", return_value={}):
            return watch.sweep_stuck(())

    def test_an_unresolved_submit_is_not_told_to_press_retry(self):
        self.save(status="inflight", phase="Submitting", phaseAt=self.stale())
        self.event("submitting")
        self.assertEqual(self.sweep(), [AID])
        after = self.load()
        self.assertEqual(after["status"], "review")
        self.assertEqual(after["phase"], "Submit unconfirmed")
        self.assertNotIn("Retry", after["phaseDetail"])
        self.assertIn("Confirm submitted", after["phaseDetail"])
        self.assertIn("It did not go through", after["phaseDetail"])

    def test_it_moves_without_waiting_the_hour(self):
        # Nothing holds it and nothing queues it, so no runner can confirm it.
        self.save(status="inflight", phase="Submitting")
        self.event("submitting")
        self.assertEqual(self.sweep(), [AID])
        self.assertEqual(self.load()["status"], "review")

    def test_a_running_submit_is_left_alone(self):
        self.save(status="inflight", phase="Submitting", phaseAt=self.stale())
        self.event("submitting")
        with mock.patch.object(watch, "running_applications",
                               return_value={AID: "apply-1"}):
            self.assertEqual(watch.sweep_stuck(()), [])
        self.assertEqual(self.load()["status"], "inflight")

    def test_an_ordinary_stuck_run_still_says_press_retry(self):
        # `session-list` carries `updatedAt` and not `phaseAt`, and a save
        # stamps `updatedAt` now, so the hour is taken away rather than waited.
        self.save(status="inflight", phase="Filling the form")
        self.event("started")
        with mock.patch.object(watch, "STUCK_AFTER_SECONDS", -1):
            self.sweep()
        after = self.load()
        self.assertEqual(after["phase"], "Stalled")
        self.assertIn("press Retry", after["phaseDetail"])

    def test_an_unreadable_history_is_a_failure_not_an_empty_one(self):
        self.save(status="inflight", phase="Submitting", phaseAt=self.stale())
        real = watch.store

        def store(args):
            if args[:1] == ["history-list"]:
                return subprocess.CompletedProcess(args, 1, "", "unreadable")
            return real(args)

        with mock.patch.object(watch, "store", side_effect=store):
            with self.assertRaises(watch.TickError):
                self.sweep()
        self.assertEqual(self.load()["phase"], "Submitting")


# --- bug 3: nothing could resolve an unresolved submit -----------------------

class TheRunsEndHandsItBackTests(StoreCase):
    def test_a_submit_with_no_confirmation_lands_in_needs_you(self):
        self.save(status="inflight", phase="Submitting", previousStatus="watch")
        self.event("submitting")
        moved = run_apply.hand_back_unconfirmed_submit(
            AID, {"terminal": "submission_unverified", "ok": False})
        self.assertEqual(moved, {"moved": True, "status": "review",
                                 "phase": "Submit unconfirmed"})
        after = self.load()
        self.assertEqual(after["status"], "review")
        self.assertNotIn("previousStatus", after)
        self.assertEqual(len(after["pendingFields"]), 1)
        item = after["pendingFields"][0]
        self.assertIn("email or the employer's portal", item["question"])
        self.assertIn("It did not go through", item["action"])
        # Nothing was marked completed and nothing was written to the history.
        self.assertEqual([e["event"] for e in self.history()], ["submitting"])

    def test_any_other_outcome_is_left_alone(self):
        self.save(status="inflight", phase="Filling the form")
        for outcome in ({"terminal": "parked"}, {"terminal": None}, {}):
            self.assertIsNone(run_apply.hand_back_unconfirmed_submit(AID, outcome))
        self.assertEqual(self.load()["status"], "inflight")

    def test_a_dropped_application_is_not_moved(self):
        self.dropped()
        moved = run_apply.hand_back_unconfirmed_submit(
            AID, {"terminal": "submission_unverified"})
        self.assertFalse(moved["moved"])
        self.assertEqual(self.load()["status"], "dropped")


class ItDidNotGoThroughTests(StoreCase):
    def unresolved(self):
        self.save(status="inflight", phase="Submitting")
        self.event("submitting")
        run_apply.hand_back_unconfirmed_submit(
            AID, {"terminal": "submission_unverified"})

    def latest(self):
        return apply_marks.latest_events(self.history()).get(AID)

    def test_it_records_abandoned_and_hands_back_a_retryable_card(self):
        self.unresolved()
        result = apply_marks.apply_unsent(AID, self.latest())
        self.assertTrue(result.get("applied"), result)
        events = self.history()
        self.assertEqual([e["event"] for e in events], ["submitting", "abandoned"])
        abandoned = events[-1]
        self.assertEqual(abandoned["confirmedBy"], "user")
        self.assertIn("nothing was sent", abandoned["note"])
        # A person said so; nobody read an employer's page.
        self.assertNotIn("confirmation", abandoned)
        after = self.load()
        self.assertEqual(after["status"], "review")
        self.assertEqual(after["phase"], "Not sent")
        self.assertIn("Retry", after["pendingFields"][0]["action"])

    def test_do_not_start_refuses_before_and_allows_a_retry_after(self):
        self.unresolved()
        with mock.patch.object(watch.browser_identity, "any_signed_in",
                               return_value=True):
            before = watch.do_not_start(AID, retry=True)
            self.assertIn("It did not go through", before)
            self.assertIn("Confirm submitted", before)
            self.assertNotIn("Stop", before)
            apply_marks.apply_unsent(AID, self.latest())
            self.assertIsNone(watch.do_not_start(AID, retry=True))
            # Still parked for a plain apply: the card is in Needs you and its
            # control is Retry, which is the one that waives the park.
            self.assertIsNotNone(watch.do_not_start(AID))

    def test_it_never_retracts_a_completed_submission(self):
        self.save(status="review", pendingFields=[{"question": "x"}])
        self.event("submitting")
        self.event("completed")
        result = apply_marks.apply_unsent(AID, self.latest())
        self.assertTrue(result.get("skipped"))
        self.assertEqual([e["event"] for e in self.history()],
                         ["submitting", "completed"])

    def test_it_waits_while_a_run_may_still_confirm(self):
        self.save(status="inflight", phase="Submitting")
        self.event("submitting")
        result = apply_marks.apply_unsent(AID, self.latest())
        self.assertTrue(result.get("skipped"))
        self.assertEqual([e["event"] for e in self.history()], ["submitting"])

    def test_a_dropped_application_is_resolved_and_stays_dropped(self):
        self.dropped()
        self.event("submitting")
        result = apply_marks.apply_unsent(AID, self.latest())
        self.assertTrue(result.get("applied"), result)
        self.assertEqual(self.history()[-1]["event"], "abandoned")
        self.assertEqual(self.load()["status"], "dropped")


class TheTickClaimsTheUnsentQueueTests(unittest.TestCase):
    def test_the_unsent_queue_reaches_apply_marks_before_the_apply_queue(self):
        # Before the apply queue, so a Retry pressed straight after the answer
        # finds the `abandoned` already written. Read from the tick's AST: the
        # `q.get("unsent")` lookup against the line that takes the apply queue
        # it is about to walk (`apply_items = ...`).
        tree = ast.parse((ROOT / "scripts" / "watch.py").read_text())
        tick = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "tick")
        unsent = [node.lineno for node in ast.walk(tick)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "get" and node.args
                  and isinstance(node.args[0], ast.Constant)
                  and node.args[0].value == "unsent"]
        walked = [node.lineno for node in ast.walk(tick)
                  if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "apply_items"
                          for t in node.targets)]
        self.assertTrue(unsent, "the tick never looks at the unsent queue")
        self.assertTrue(walked, "the tick no longer takes apply_items; this check is stale")
        self.assertLess(min(unsent), min(walked))
