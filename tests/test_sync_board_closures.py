"""A role taken down has to leave Watching too, not just Found.

Found is derived from the job pool, so expiring a record removes the card by
itself. Watching is not: it is a session the user created by clicking the eye,
and it survives independently of the posting. Without this the board keeps
offering a role that cannot be applied to, which is the complaint the sweep
exists to answer.

These run the real script against a temporary store, because the thing being
checked is which sessions it deletes.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import jobs_store  # noqa: E402


class SyncBoardCase(unittest.TestCase):
    """Harness only. The real script against a temporary store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.env = dict(os.environ, JOB_APPLICATION_DIR=str(self.root))
        self.store("init")

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"), *args],
                              capture_output=True, text=True, env=self.env)

    def watch(self, app_id, status="watch"):
        # A distinct role per entry: the store refuses a second watch entry for
        # the same company and role, which is the duplicate-sourcing guard.
        payload = self.root / f"{app_id}.json"
        payload.write_text(json.dumps({
            "status": status, "company": "Northwind",
            "role": f"Product Manager, {app_id.title()}",
            "url": f"https://job-boards.greenhouse.io/northwind/jobs/{app_id}",
        }))
        r = self.store("session-save", "--id", app_id, "--input", str(payload))
        self.assertEqual(r.returncode, 0, r.stderr)

    def pool(self, **states):
        jobs = {}
        for job_id, state in states.items():
            job = jobs_store.blank_job(job_id, "Northwind",
                                       f"Product Manager, {job_id.title()}")
            job["state"] = state
            if state == "expired":
                job["closedAt"] = "2026-08-25T17:00:00Z"
            jobs[job_id] = job
        jobs_store.save_jobs(self.root, jobs)

    def sync(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_board.py"),
                            "--no-publish"], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def live_sessions(self):
        """Applications still on the board's workflow lanes.

        A dropped session is not deleted; it moves to the archive. So
        "still watching" means present AND not dropped.
        """
        r = self.store("session-list")
        return {s["applicationId"] for s in json.loads(r.stdout or "[]")
                if s.get("status") != "dropped"}

    def session(self, app_id):
        r = self.store("session-load", "--id", app_id)
        return json.loads(r.stdout or "{}")


class SyncBoardClosureTests(SyncBoardCase):
    def test_a_watched_role_whose_posting_closed_leaves_watching(self):
        self.watch("alive")
        self.watch("dead")
        self.pool(alive="watched", dead="expired")
        result = self.sync()
        self.assertEqual(result["closedAndArchived"], ["dead"])
        self.assertEqual(self.live_sessions(), {"alive"})

    def test_the_closed_role_is_archived_with_a_reason_not_deleted(self):
        """A Found card nobody looked at can vanish silently. A role the user
        starred cannot: disappearing without a trace reads as a glitch."""
        self.watch("dead")
        self.pool(dead="expired")
        self.sync()
        archived = self.session("dead")
        self.assertEqual(archived["status"], "dropped")
        self.assertEqual(archived["droppedFrom"], "Watching")
        self.assertIn("closed by the employer", archived["droppedReason"])
        self.assertIn("2026-08-25", archived["droppedReason"])
        self.assertTrue(archived["droppedAt"])

    def test_watching_now_counts_what_is_actually_left(self):
        self.watch("alive")
        self.watch("dead")
        self.pool(alive="watched", dead="expired")
        self.assertEqual(self.sync()["watchingNow"], 1)

    def test_a_live_posting_keeps_its_watch_entry(self):
        self.watch("alive")
        self.pool(alive="watched")
        result = self.sync()
        self.assertEqual(result["closedAndArchived"], [])
        self.assertEqual(self.live_sessions(), {"alive"})

    def test_only_watch_sessions_are_retired_this_way(self):
        """In flight and Needs you are work the user started.

        The employer pulling the posting does not undo an application that is
        already moving, and a card vanishing mid-application would look like
        data loss.
        """
        for status in ("active", "inflight", "review"):
            with self.subTest(status=status):
                self.tearDown()
                self.setUp()
                self.watch("busy", status=status)
                self.pool(busy="expired")
                result = self.sync()
                self.assertEqual(result["closedAndArchived"], [])
                self.assertEqual(self.live_sessions(), {"busy"})

    def test_an_unreadable_pool_retires_nothing(self):
        """Not knowing what is closed must not read as everything being closed."""
        self.watch("alive")
        (self.root / "jobs.json").write_text("{ this is not json")
        result = self.sync()
        self.assertEqual(result["closedAndArchived"], [])
        self.assertEqual(self.live_sessions(), {"alive"})


class ReconcileToPoolTests(SyncBoardCase):
    """Decisions flow into the pool, and only that way.

    Sessions are authoritative for lane membership: they are what the person
    did. The pool is a corpus whose `state` is derived. Reversed, a stale pool
    would overwrite a real decision, which is the overwrite incident wearing a
    different hat.
    """

    def submit(self, app_id):
        payload = self.root / f"{app_id}-event.json"
        payload.write_text(json.dumps({
            "event": "completed", "applicationId": app_id,
            "company": "Northwind", "role": f"Product Manager, {app_id.title()}",
            "at": "2026-08-25T18:00:00Z"}))
        r = self.store("history-append", "--input", str(payload))
        self.assertEqual(r.returncode, 0, r.stderr)

    def pool_state(self, job_id):
        return jobs_store.load_jobs(self.root)[job_id]["state"]

    def test_submitting_moves_the_pool_off_watched(self):
        """The bug that was manufacturing drift.

        finish.py writes the completed event and never touched the pool, and
        sync_board then deleted the session. The record stayed `watched` for
        good, and every submission added another.
        """
        self.watch("sent")
        self.pool(sent="watched")
        self.submit("sent")
        result = self.sync()
        self.assertEqual([c["id"] for c in result["reconciledToPool"]], ["sent"])
        self.assertEqual(self.pool_state("sent"), "applied")

    def test_the_pool_is_reconciled_before_the_session_is_retired(self):
        """After retirement the session carrying the decision is gone, so the
        order is load-bearing rather than incidental."""
        self.watch("sent")
        self.pool(sent="watched")
        self.submit("sent")
        self.sync()
        self.assertEqual(self.live_sessions(), set())
        self.assertEqual(self.pool_state("sent"), "applied")

    def test_a_needs_you_session_leaves_the_pool_watched(self):
        """`review` is Needs you: chosen, not yet submitted."""
        self.watch("looking", status="review")
        self.pool(looking="new")
        self.sync()
        self.assertEqual(self.pool_state("looking"), "watched")

    def test_a_dropped_session_moves_the_pool_to_passed(self):
        self.watch("gone", status="dropped")
        self.pool(gone="watched")
        self.sync()
        self.assertEqual(self.pool_state("gone"), "passed")

    def test_a_terminal_pool_state_is_never_walked_back(self):
        """`applied` and `passed` are terminal. A session that still says
        `watch` must not undo an application that already went out."""
        self.watch("done")
        self.pool(done="applied")
        result = self.sync()
        self.assertEqual(result["reconciledToPool"], [])
        self.assertEqual(self.pool_state("done"), "applied")

    def test_a_decision_about_a_posting_the_pool_never_saw_creates_nothing(self):
        """The 43 sessions with no job record. Inventing a pool entry here
        would guess at a posting nobody has fetched."""
        self.watch("stranger")
        self.pool()
        self.sync()
        self.assertEqual(jobs_store.load_jobs(self.root), {})

    def test_reconciling_twice_changes_nothing_the_second_time(self):
        self.watch("sent")
        self.pool(sent="watched")
        self.submit("sent")
        self.sync()
        self.assertEqual(self.sync()["reconciledToPool"], [])
