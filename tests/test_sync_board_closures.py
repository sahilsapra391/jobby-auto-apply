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


class SyncBoardClosureTests(unittest.TestCase):
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
            jobs[job_id] = job
        jobs_store.save_jobs(self.root, jobs)

    def sync(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_board.py"),
                            "--no-publish"], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def live_sessions(self):
        r = self.store("session-list")
        return {s["applicationId"] for s in json.loads(r.stdout or "[]")}

    def test_a_watched_role_whose_posting_closed_leaves_watching(self):
        self.watch("alive")
        self.watch("dead")
        self.pool(alive="watched", dead="expired")
        result = self.sync()
        self.assertEqual(result["closedAndRetired"], ["dead"])
        self.assertEqual(self.live_sessions(), {"alive"})

    def test_watching_now_counts_what_is_actually_left(self):
        self.watch("alive")
        self.watch("dead")
        self.pool(alive="watched", dead="expired")
        self.assertEqual(self.sync()["watchingNow"], 1)

    def test_a_live_posting_keeps_its_watch_entry(self):
        self.watch("alive")
        self.pool(alive="watched")
        result = self.sync()
        self.assertEqual(result["closedAndRetired"], [])
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
                self.assertEqual(result["closedAndRetired"], [])
                self.assertEqual(self.live_sessions(), {"busy"})

    def test_an_unreadable_pool_retires_nothing(self):
        """Not knowing what is closed must not read as everything being closed."""
        self.watch("alive")
        (self.root / "jobs.json").write_text("{ this is not json")
        result = self.sync()
        self.assertEqual(result["closedAndRetired"], [])
        self.assertEqual(self.live_sessions(), {"alive"})


if __name__ == "__main__":
    unittest.main()
