"""The submit path must survive a database outage. Measured, not assumed.

An application mid-form is a real form on a real employer's site with a real
name attached, and a submitted one cannot be un-sent. `finish.py` records a
completion by shelling out to `store.py session-save` and `store.py
history-append`, and the duplicate guard reads that history: an application that
submitted but recorded nothing is one the next attempt does not know about.

Class A, the job pool, is mirrored Postgres-first and a Neon failure fails the
write. That is correct there: a failed pool write costs a sweep, is loud, and is
fixed by running it again.

Applying the same ordering to the submit path would trade a recoverable failure
for an unrecoverable one, so class B is deliberately not mirrored yet and
docs/write-paths.md says why. This is the test that keeps it deliberate.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"

# Loopback port 1: nothing listens, and the connection fails immediately
# rather than hanging, so a regression here shows up as a failure and not as a
# suite that stopped finishing.
DEAD_URL = "postgresql://nobody:nobody@127.0.0.1:1/none"


class TheSubmitPathSurvivesADatabaseOutage(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        root = self.home / ".job-application"
        root.mkdir(parents=True)
        # config.env is where dual_write looks, so this is what makes the
        # mirror believe a database exists and try to reach it.
        config = root / "config.env"
        config.write_text(f"JOBBY_DATABASE_URL={DEAD_URL}\n")
        config.chmod(0o600)
        self.root = root
        self.env = dict(os.environ, HOME=str(self.home),
                        JOB_APPLICATION_DIR=str(root))
        self.env.pop("JOBBY_TEST_DATABASE_URL", None)
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        self.store(["init"])

    def store(self, args, payload=None):
        return subprocess.run([sys.executable, str(STORE)] + args,
                              env=self.env, input=payload,
                              capture_output=True, text=True, timeout=60)

    def test_a_completion_is_recorded_while_the_database_is_down(self):
        result = self.store(
            ["session-save", "--id", "probe", "--input", "-"],
            json.dumps({"status": "completed", "phase": "done",
                        "applicationId": "probe"}))
        self.assertEqual(result.returncode, 0,
                         f"a completed application could not be recorded: "
                         f"{result.stderr[:300]}")
        self.assertTrue((self.root / "sessions" / "probe.json").exists())

    def test_history_is_appended_while_the_database_is_down(self):
        """The duplicate guard reads this. An application that submitted and
        appended nothing is one the next attempt will submit again."""
        result = self.store(
            ["history-append", "--input", "-"],
            json.dumps({"applicationId": "probe", "event": "completed",
                        "at": "2026-08-30T00:00:00Z"}))
        self.assertEqual(result.returncode, 0, result.stderr[:300])

    def test_an_answer_is_written_while_the_database_is_down(self):
        """An agent mid-form stores what it just typed. Losing that strands the
        run on a question it has already answered."""
        result = self.store(
            ["answer-put", "--input", "-"],
            json.dumps({"question": "Probe?", "value": "yes"}))
        self.assertEqual(result.returncode, 0, result.stderr[:300])

    def test_the_pool_write_does_fail_which_is_the_contrast(self):
        """The control. If this ever passes, the mirror is not engaging and
        every other assertion here is proving nothing."""
        (self.root / "jobs.json").write_text(json.dumps({"x1": {
            "id": "x1", "canonicalSource": "greenhouse",
            "canonicalUrl": "https://boards.greenhouse.io/a/jobs/1",
            "company": "A", "title": "T", "state": "new",
            "firstSeenAt": "2026-08-01T00:00:00Z",
            "lastSeenAt": "2026-08-29T00:00:00Z", "postedAt": None}}))
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "jobs_store.py"),
             "watch", "--id", "x1"],
            env=self.env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0,
                            "the pool write succeeded with no database, so "
                            "dual-write is not engaging at all")

    def test_the_failed_pool_write_leaves_the_file_untouched(self):
        """Postgres first means the file never moves when Postgres refuses."""
        pool = {"x1": {"id": "x1", "canonicalSource": "greenhouse",
                       "canonicalUrl": "https://boards.greenhouse.io/a/jobs/1",
                       "company": "A", "title": "T", "state": "new",
                       "firstSeenAt": "2026-08-01T00:00:00Z",
                       "lastSeenAt": "2026-08-29T00:00:00Z", "postedAt": None}}
        (self.root / "jobs.json").write_text(json.dumps(pool))
        subprocess.run(
            [sys.executable, str(REPO / "scripts" / "jobs_store.py"),
             "watch", "--id", "x1"],
            env=self.env, capture_output=True, text=True, timeout=60)
        after = json.loads((self.root / "jobs.json").read_text())
        self.assertEqual(after["x1"]["state"], "new")


class TheDocumentSaysWhichWayUpClassBIs(unittest.TestCase):
    """The claim and the code have to agree.

    An earlier draft said class B was "dual-written in Phase 1" when nothing
    wrote those tables at all. It is mirrored now, and the heading has to say
    the thing that actually matters about it, which is the direction."""

    def test_the_heading_names_the_inverted_ordering(self):
        doc = (REPO / "docs" / "write-paths.md").read_text(encoding="utf-8")
        heading = next(line for line in doc.splitlines()
                       if line.startswith("## Class B"))
        self.assertIn("other way up", heading)

    def test_the_reason_names_the_submit_path(self):
        doc = (REPO / "docs" / "write-paths.md").read_text(encoding="utf-8")
        self.assertIn("finish.py", doc)
        self.assertIn("un-send", doc)

    def test_it_records_why_the_table_is_not_called_answers(self):
        doc = (REPO / "docs" / "write-paths.md").read_text(encoding="utf-8")
        self.assertIn("answer_bank", doc)
        self.assertIn("if not exists", doc)
