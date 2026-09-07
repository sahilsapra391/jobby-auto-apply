"""The pool lock is held for the merge and for nothing else.

**That is the entire reason the fetch moved.** `jobs_store.POOL_LOCK` is the
string `"scan"` and it is the same lock file `watch.take_lock("scan")` writes,
so a local sweep holds the pool's write lock for its whole run: a median 26.8
minutes, six times a day, with eight non-sweep callers and two of them on the
apply path. Across `watch.log` from 2026-08-26 to 2026-09-07, 176 ticks skipped
their board reconcile with LockBusy on it.

Moving the fetch to a container buys nothing if the device still holds that
lock while it waits, so the shape of the lock's life is the property, not the
fact that a container ran.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import cloud_sweep  # noqa: E402
import watch  # noqa: E402
from sources import base  # noqa: E402


class Cursor:
    def __init__(self, answers): self.answers = answers; self.rows = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, args=None):
        for key, rows in self.answers.items():
            if key in sql:
                self.rows = rows
                return
        self.rows = []
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)


class Conn:
    def __init__(self, answers=None): self.answers = answers or {}
    def cursor(self): return Cursor(self.answers)
    def rollback(self): pass
    def commit(self): pass
    def close(self): pass


class ConfigTests(unittest.TestCase):
    def test_no_app_means_no_cloud_sweep(self):
        # A half-configured deployment must not stop sourcing. The caller falls
        # back to the local sweep, which is the failure direction that keeps
        # jobs arriving.
        self.assertEqual(cloud_sweep.configured({}), (None, None))
        self.assertEqual(cloud_sweep.configured({"JOBBY_FLY_APP": "a"}),
                         (None, None))

    def test_both_present_is_configured(self):
        self.assertEqual(
            cloud_sweep.configured({"JOBBY_FLY_APP": "jobby-sourcing",
                                    "JOBBY_FLY_MACHINE": "839522f7722458"}),
            ("jobby-sourcing", "839522f7722458"))


class WaitTests(unittest.TestCase):
    def test_it_returns_the_sweep_once_every_channel_has_finished(self):
        conn = Conn({"from sweep_runs": [("abc-123", 27, 27)]})
        self.assertEqual(
            cloud_sweep.wait_for_sweep(conn, "acct", datetime.now(timezone.utc)),
            "abc-123")

    def test_a_partly_finished_sweep_keeps_waiting_then_gives_up(self):
        conn = Conn({"from sweep_runs": [("abc-123", 27, 12)]})
        slept = []
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.wait_for_sweep(conn, "acct",
                                       datetime.now(timezone.utc),
                                       seconds=0, sleep=slept.append)
        self.assertIn("12 of 27 channels finished", str(caught.exception))

    def test_no_sweep_at_all_says_so_rather_than_hanging(self):
        conn = Conn({})
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.wait_for_sweep(conn, "acct",
                                       datetime.now(timezone.utc),
                                       seconds=0, sleep=lambda _s: None)
        self.assertIn("no sweep started", str(caught.exception))


class StagedTests(unittest.TestCase):
    def rows(self, runs, postings):
        return Conn({"from sweep_runs": runs, "from sweep_postings": postings})

    def test_it_rebuilds_the_envelope_a_connector_would_have_returned(self):
        conn = self.rows(
            [("greenhouse", "complete", 2, 0, True, "353 boards")],
            [("greenhouse", {"id": "a"}), ("greenhouse", {"id": "b"})])
        out = cloud_sweep.staged(conn, "acct", "s1")
        self.assertEqual(out["greenhouse"]["status"], "ok")
        self.assertEqual(len(out["greenhouse"]["postings"]), 2)
        self.assertTrue(out["greenhouse"]["listingComplete"])

    def test_a_failed_channel_stays_failed(self):
        conn = self.rows([("jooble", "failed", 0, 0, False, "HTTP 400")], [])
        out = cloud_sweep.staged(conn, "acct", "s1")
        self.assertEqual(out["jooble"]["status"], "error")
        self.assertIn("HTTP 400", out["jooble"]["reason"])

    def test_a_skip_stays_a_skip(self):
        conn = self.rows([("reed", "skipped", 0, 0, False, "not the UK")], [])
        self.assertEqual(cloud_sweep.staged(conn, "acct", "s1")["reed"]["status"],
                         "skipped")

    def test_a_count_that_disagrees_with_the_rows_refuses_to_be_complete(self):
        # A partial write is how a truncated fetch reaches the closure step
        # wearing `listingComplete`. Brake 1 protects a board nobody saw; it
        # cannot protect one whose postings were half-written.
        conn = self.rows(
            [("greenhouse", "complete", 900, 0, True, "353 boards")],
            [("greenhouse", {"id": "a"})])
        out = cloud_sweep.staged(conn, "acct", "s1")
        self.assertEqual(out["greenhouse"]["status"], "error")
        self.assertIn("against a job_count of 900", out["greenhouse"]["reason"])
        self.assertFalse(out["greenhouse"]["listingComplete"])

    def test_the_unchanged_count_survives_the_round_trip(self):
        # The coverage ratchet needs it, and a channel whose boards mostly 304'd
        # would otherwise read as truncated.
        conn = self.rows(
            [("pinpoint", "complete", 0, 36, True, "39 boards")], [])
        self.assertEqual(cloud_sweep.staged(conn, "acct", "s1")["pinpoint"]["unchanged"],
                         36)


class LockShapeTests(unittest.TestCase):
    """When the pool lock is taken, and when it is not."""

    def test_starting_and_waiting_take_no_lock(self):
        taken = []

        def record(root, name, note=""):
            taken.append(name)
            import contextlib
            return contextlib.nullcontext()

        with mock.patch.object(cloud_sweep.runlock, "held", record):
            cloud_sweep.start_machine(
                "app", "m1", run=lambda *a, **k: mock.Mock(returncode=0))
            conn = Conn({"from sweep_runs": [("s1", 3, 3)]})
            cloud_sweep.wait_for_sweep(conn, "acct", datetime.now(timezone.utc))
        self.assertEqual(taken, [],
                         "the fetch or the wait took a lock, which is the "
                         "26.8 minutes this change exists to stop")

    def test_the_merge_takes_the_pool_lock_and_names_itself(self):
        taken = []

        def record(root, name, note=""):
            taken.append((name, note))
            import contextlib
            return contextlib.nullcontext()

        import ingest
        with mock.patch.object(cloud_sweep.runlock, "held", record), \
             mock.patch.object(ingest, "run", return_value={"found": 0}):
            cloud_sweep.merge(Path("/tmp"), {"greenhouse": base.ok("greenhouse", [])})
        self.assertEqual(taken, [("scan", "cloud-merge")])

    def test_the_merge_note_is_one_the_apply_gate_already_tolerates(self):
        # `scan_blocking_apply` fails closed on any note it does not know, so a
        # merge under an unrecognised note would defer every Apply click for
        # its duration -- which is the fault, moved rather than fixed.
        self.assertIn("cloud-merge", watch.INGEST_LOCK_NOTES)

    def test_the_waiting_lock_is_not_the_pool_lock(self):
        # It stops two overlapping cloud sweeps and must block nothing else.
        self.assertNotEqual(watch.CLOUD_SWEEP_LOCK, "scan")
        import jobs_store
        self.assertNotEqual(watch.CLOUD_SWEEP_LOCK, jobs_store.POOL_LOCK)

    def test_the_apply_gate_never_learns_the_waiting_lock_s_name(self):
        # If it did, the wait would defer Apply exactly as the old sweep did.
        self.assertNotIn(watch.CLOUD_SWEEP_LOCK, watch.INGEST_LOCK_NOTES)

    def test_the_merge_restores_the_connectors_even_when_it_raises(self):
        import ingest
        before = dict(ingest.sources.ALL)
        with mock.patch.object(cloud_sweep.runlock, "held",
                               side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                cloud_sweep.merge(Path("/tmp"),
                                  {"greenhouse": base.ok("greenhouse", [])})
        self.assertEqual(set(ingest.sources.ALL), set(before),
                         "a failed merge left the registry holding stub "
                         "connectors, so the next local sweep would fetch "
                         "nothing and close the pool")


class FallbackTests(unittest.TestCase):
    def test_flyctl_failing_to_start_is_a_named_fallback(self):
        # **`run=` is passed rather than the module patched.** The first version
        # of this test patched `cloud_sweep.subprocess.run` and ran the real
        # flyctl, because `start_machine` had `run=subprocess.run` as a default
        # and a default binds when the `def` executes. It was saved by the
        # runner having no Fly token, which is luck rather than isolation.
        failed = mock.Mock(returncode=1, stderr="no such machine", stdout="")
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.start_machine("app", "gone", run=lambda *a, **k: failed)
        self.assertIn("no such machine", str(caught.exception))

    def test_no_test_here_can_reach_a_real_cli(self):
        # The guard on the guard: `start_machine` must have no callable default
        # for `run`, or a test can patch the wrong thing and shell out for real.
        import inspect
        default = inspect.signature(cloud_sweep.start_machine).parameters["run"].default
        self.assertIsNone(default,
                          "a callable default binds at def time and defeats "
                          "module-level patching")


class ImportBoardsTests(unittest.TestCase):
    """The one-time carry of `companies.json` into the `boards` table."""

    def setUp(self):
        import import_boards
        self.mod = import_boards

    def test_only_entries_with_both_halves_come_across(self):
        # A row with no slug is a row nothing can fetch.
        companies = {
            "a": {"ats": "greenhouse", "token": "spacex"},
            "b": {"ats": "greenhouse", "token": ""},
            "c": {"ats": "", "token": "orphan"},
            "d": {"ats": "unknown", "token": "x"},
        }
        self.assertEqual([r[1] for r in self.mod.rows(companies)], ["spacex"])

    def test_it_dedupes_a_token_two_companies_share(self):
        # An alias merge leaves two entries pointing at one board, and the
        # unique constraint would otherwise take whichever arrived first.
        companies = {"a": {"ats": "ashby", "token": "vanta", "addedFrom": "roles"},
                     "b": {"ats": "ashby", "token": "vanta", "addedFrom": "roles"}}
        self.assertEqual(len(self.mod.rows(companies)), 1)

    def test_the_same_slug_on_two_platforms_is_two_boards(self):
        companies = {"a": {"ats": "ashby", "token": "acme"},
                     "b": {"ats": "lever", "token": "acme"}}
        self.assertEqual(len(self.mod.rows(companies)), 2)

    def test_it_carries_where_the_board_came_from(self):
        companies = {"a": {"ats": "ashby", "token": "v", "addedFrom": "probe"}}
        self.assertEqual(self.mod.rows(companies)[0][2], "probe")

    def test_the_insert_never_re_enables_a_disabled_board(self):
        # `on conflict do nothing`, not `do update`. A registry import that
        # un-retired every board the breaker had disabled would undo the one
        # thing `board_health` exists to do, quietly and on every run.
        import inspect
        source = inspect.getsource(self.mod.insert)
        self.assertIn("do nothing", source)
        self.assertNotIn("do update", source)
