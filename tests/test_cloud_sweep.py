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
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct", datetime.now(timezone.utc)),
            "abc-123")

    def test_a_partly_finished_sweep_keeps_waiting_then_gives_up(self):
        conn = Conn({"from sweep_runs": [("abc-123", 27, 12)]})
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct",
                                       datetime.now(timezone.utc),
                                       seconds=0, sleep=lambda _s: None)
        self.assertIn("12 of 27 channels finished", str(caught.exception))

    def test_no_sweep_at_all_says_so_rather_than_hanging(self):
        conn = Conn({})
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct",
                                       datetime.now(timezone.utc),
                                       seconds=0, sleep=lambda _s: None)
        self.assertIn("without writing a sweep", str(caught.exception))

    def test_one_finished_channel_is_not_a_finished_sweep(self):
        """**The fault the first real run hit.**

        `sweep_worker.stage` commits per channel, so a sweep two seconds old
        with greenhouse written and twenty-six channels to come satisfies
        "every row has a finished_at" exactly. On 2026-09-07 at 22:57:49 that
        returned a sweep of 1 channel and 50 postings and the merge treated a
        twenty-seventh of a sweep as a whole one. Nothing was closed, because
        brake 1 never lets a channel it did not see be touched -- but the wait
        had no business relying on a guard one process away.
        """
        conn = Conn({"from sweep_runs": [("partial", 1, 1)]})
        running = lambda *a, **k: mock.Mock(
            returncode=0, stdout='[{"id": "m1", "state": "started"}]')
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct", datetime.now(timezone.utc),
                                       app="jobby-sourcing", machine="m1",
                                       seconds=0, sleep=lambda _s: None,
                                       run=running)
        self.assertIn("still running", str(caught.exception))

    def test_a_stopped_machine_with_a_whole_sweep_is_accepted(self):
        # The positive control: the check above passes for a machine that never
        # stops, so this proves it is not simply refusing everything.
        conn = Conn({"from sweep_runs": [("whole", 27, 27)]})
        stopped = lambda *a, **k: mock.Mock(
            returncode=0, stdout='[{"id": "m", "state": "stopped"}]')
        self.assertEqual(
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct", datetime.now(timezone.utc),
                                       app="a", machine="m", seconds=5,
                                       sleep=lambda _s: None, run=stopped),
            "whole")

    def test_an_unreadable_machine_state_waits_rather_than_merging(self):
        # "" is could-not-read, which is never "stopped". Waiting until the
        # deadline is the safe direction; the alternative merges a sweep still
        # being written.
        conn = Conn({"from sweep_runs": [("partial", 1, 1)]})
        broken = lambda *a, **k: mock.Mock(returncode=1, stdout="")
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct", datetime.now(timezone.utc),
                                       app="a", machine="m", seconds=0,
                                       sleep=lambda _s: None, run=broken)
        self.assertIn("still running", str(caught.exception))

    def test_the_timeout_names_where_to_look(self):
        # A wait that gives up without saying what it was waiting on is the
        # silent timeout this was explicitly asked not to be.
        conn = Conn({})
        running = lambda *a, **k: mock.Mock(
            returncode=0, stdout='[{"id": "839522f7722458", "state": "started"}]')
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable) as caught:
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct", datetime.now(timezone.utc),
                                       app="jobby-sourcing",
                                       machine="839522f7722458",
                                       seconds=0, sleep=lambda _s: None,
                                       run=running)
        said = str(caught.exception)
        self.assertIn("flyctl logs", said)
        self.assertIn("839522f7722458", said)


def _key_posting(slug, external):
    """A posting carrying the §3 record key, which is what the canary reads."""
    return {"boardKey": {"slug": slug, "externalId": external},
            "canonicalUrl": f"https://boards.greenhouse.io/{slug}/jobs/{external}",
            "title": "Product Manager"}


class StagedTests(unittest.TestCase):
    def rows(self, runs, postings):
        # The select's column order, in one place. A row here is written the
        # way the table is read: (source, status, job_count, unchanged,
        # unchanged_slugs, listing_complete, reason). A five-field row from
        # before 0022 is padded, so the tests that have no opinion about slugs
        # keep saying what they were written to say.
        padded = [r if len(r) == 7 else (r[0], r[1], r[2], r[3], [], r[4], r[5])
                  for r in runs]
        return Conn({"from sweep_runs": padded, "from sweep_postings": postings})

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


    def test_which_boards_were_unchanged_survives_the_round_trip(self):
        """The count answers the ratchet; the canary asks about one board.

        **This is the one that was missing and it fired on the strictest
        sources.** `staged` rebuilt the envelope with `unchanged=96` and no
        slugs, `base.ok` defaulted `unchangedSlugs` to empty, and
        `canary_problem`'s first guard reads exactly that list. So on the first
        warm sweep spacex and snowflake both 304'd, both contributed no
        postings, and both were reported "was fetched and returned no
        postings" -- the wording for a tenant that has moved.
        """
        conn = self.rows(
            [("greenhouse", "complete", 1, 96,
              ["spacex", "stripe"], True, "353 boards")],
            [("greenhouse", _key_posting("figma", "1"))])
        out = cloud_sweep.staged(conn, "acct", "s1")
        self.assertEqual(out["greenhouse"]["unchanged"], 96)
        self.assertEqual(out["greenhouse"]["unchangedSlugs"],
                         ["spacex", "stripe"])

    def test_a_304_canary_is_excused_only_because_the_slugs_travelled(self):
        """End to end over the staged row, not over a hand-built envelope.

        A test that assembled the envelope itself would pass with the plumbing
        removed, which is the shape this repo has paid for: a fixture supplying
        an input the real caller does not supply.
        """
        import source_health
        conn = self.rows(
            [("greenhouse", "complete", 1, 96,
              ["spacex"], True, "353 boards")],
            # One posting from a board that did answer, so the source is
            # visibly still emitting keys: `canary_yield` returns 0 rather than
            # None, which is the "fetched and empty" case the excuse is for.
            [("greenhouse", _key_posting("figma", "1"))])
        env = cloud_sweep.staged(conn, "acct", "s1")["greenhouse"]
        count = source_health.canary_yield("greenhouse", env["postings"])
        self.assertIsNone(
            source_health.canary_problem("greenhouse", count, 60,
                                         env.get("unchangedSlugs")),
            "a canary board that answered 304 is not a silent canary")

    def test_a_canary_that_did_not_304_is_still_reported_silent(self):
        # The excuse is one board's, not the channel's. 96 other boards being
        # unchanged says nothing about this one.
        import source_health
        conn = self.rows(
            [("greenhouse", "complete", 1, 96,
              ["stripe", "figma"], True, "353 boards")],
            [("greenhouse", _key_posting("figma", "1"))])
        env = cloud_sweep.staged(conn, "acct", "s1")["greenhouse"]
        count = source_health.canary_yield("greenhouse", env["postings"])
        problem = source_health.canary_problem("greenhouse", count, 60,
                                               env.get("unchangedSlugs"))
        self.assertEqual((problem or {}).get("kind"), "canary_silent")

    def test_the_slug_column_read_as_text_is_not_iterated_as_characters(self):
        # jsonb comes back parsed, but a driver or a fixture may hand over the
        # text. Iterating a string would make every character a board slug and
        # excuse a canary named "s".
        conn = self.rows(
            [("greenhouse", "complete", 0, 2, '["spacex"]', True, "")], [])
        self.assertEqual(
            cloud_sweep.staged(conn, "acct", "s1")["greenhouse"]["unchangedSlugs"],
            ["spacex"])
        conn = self.rows([("greenhouse", "complete", 0, 2, "spacex", True, "")], [])
        self.assertEqual(
            cloud_sweep.staged(conn, "acct", "s1")["greenhouse"]["unchangedSlugs"],
            [])


class ConnectionLifetimeTests(unittest.TestCase):
    """Nothing holds a Postgres connection across the wait.

    **The same fault as `sweep_worker.stage`, one file away, found the same
    way.** The measured warm sweep fetched for 29.5 minutes; Neon closes an
    idle connection long before that, so a connection opened before
    `start_machine` and used again after `wait_for_sweep` is dead by the time
    the fetch lands. The device died with `consuming input failed: SSL
    connection has been closed unexpectedly` **after** the container had
    written all 27 channels -- a completed fetch reported as a failed run.

    A rule stated in one function's docstring is not a rule the codebase
    holds, and the closer the neighbour the less likely anybody checks.
    """

    def test_wait_for_sweep_takes_a_callable_and_opens_one_per_poll(self):
        opened, closed = [], []

        class C(Conn):
            def __init__(self, n):
                super().__init__({"from sweep_runs": [("s1", 27, 27)]})
                self.n = n
            def close(self): closed.append(self.n)

        def connect():
            opened.append(len(opened))
            return C(len(opened) - 1)

        out = cloud_sweep.wait_for_sweep(
            connect, "acct", datetime(1970, 1, 1, tzinfo=timezone.utc),
            app=None, machine=None, seconds=0, sleep=lambda _s: None)
        self.assertEqual(out, "s1")
        self.assertEqual(len(opened), 1)
        self.assertEqual(closed, [0], "the poll's connection was left open")

    def test_a_second_poll_does_not_reuse_the_first_connection(self):
        # Two polls, two connections. Reusing one is the whole defect: it is
        # the connection that has been idle the longest that gets used last.
        made = []
        answers = [[("s1", 27, 3)], [("s1", 27, 27)]]

        class C(Conn):
            def __init__(self, rows):
                super().__init__({"from sweep_runs": rows})
                self.closed = False
            def close(self): self.closed = True

        def connect():
            c = C(answers[min(len(made), len(answers) - 1)])
            made.append(c)
            return c

        out = cloud_sweep.wait_for_sweep(
            connect, "acct", datetime(1970, 1, 1, tzinfo=timezone.utc),
            app=None, machine=None, seconds=30,
            sleep=lambda _s: None, poll=0)
        self.assertEqual(out, "s1")
        self.assertEqual(len(made), 2)
        self.assertTrue(all(c.closed for c in made))

    def test_main_never_holds_one_across_the_wait(self):
        import inspect
        source = inspect.getsource(cloud_sweep.main)
        before, _, after = source.partition("wait_for_sweep(")
        self.assertNotIn("jobs_db.connect()", before,
                         "a connection was opened before the wait, which is "
                         "the connection Neon closes")
        self.assertIn("jobs_db.connect,", source,
                      "wait_for_sweep must be handed the callable")
        # `staged` gets its own, after the wait, and closes it.
        self.assertIn("jobs_db.connect()", after)


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
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct", datetime.now(timezone.utc))
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


class LockDurationTests(unittest.TestCase):
    """The report says how long the pool was locked, from inside the lock.

    **The number this whole phase exists to move.** The device's own sweep
    holds `POOL_LOCK` for a median 26.8 minutes; the point of the container is
    that only the merge does. A report that says `mergeSeconds` is answering a
    slightly different question -- it includes the connector swap now and
    whatever gets added beside it later -- and the reader comparing against
    26.8 needs the span the pool was actually unavailable for.
    """

    def test_the_span_is_measured_inside_the_lock_and_handed_back(self):
        held = []
        seen = {}

        class FakeIngest:
            sources = type("S", (), {"ALL": {}})()

            @staticmethod
            def run(root, **kw):
                seen["locked"] = held == ["taken"]
                return ({"found": 1}, [])

        import contextlib

        @contextlib.contextmanager
        def fake_lock(root, name, note=""):
            held.append("taken")
            yield
            held.append("released")

        spans = []
        with mock.patch.dict(sys.modules, {"ingest": FakeIngest}), \
                mock.patch.object(cloud_sweep.runlock, "held", fake_lock):
            cloud_sweep.merge("/tmp/x", {}, lock_seconds=spans)
        self.assertTrue(seen["locked"], "the merge ran outside the lock")
        self.assertEqual(len(spans), 1)
        self.assertIsInstance(spans[0], float)

    def test_a_merge_that_raises_still_reports_the_span(self):
        # An exception carrying no measurement is how "the work was done and
        # the run looked failed" happened here once already.
        class Boom:
            sources = type("S", (), {"ALL": {}})()

            @staticmethod
            def run(root, **kw):
                raise RuntimeError("nope")

        import contextlib

        @contextlib.contextmanager
        def fake_lock(root, name, note=""):
            yield

        spans = []
        with mock.patch.dict(sys.modules, {"ingest": Boom}), \
                mock.patch.object(cloud_sweep.runlock, "held", fake_lock):
            with self.assertRaises(RuntimeError):
                cloud_sweep.merge("/tmp/x", {}, lock_seconds=spans)
        self.assertEqual(len(spans), 1)

    def test_main_reports_it_separately_from_mergeSeconds(self):
        import inspect
        source = inspect.getsource(cloud_sweep.main)
        self.assertIn("lockSeconds", source)
        self.assertIn("lock_seconds=lock_seconds", source)


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

    def test_it_runs_where_flyctl_is_not_installed(self):
        """CI has no flyctl, and the first version raised before `run` was consulted.

        `toolpath.find` raises SystemExit for a missing binary unless told
        `required=False`, so `find("flyctl") or "flyctl"` was a fallback
        guarding a None that never comes. Six tests passed on this Mac, where
        flyctl exists, and errored on the runner. Reproduced here by hiding it.
        """
        import os
        from unittest import mock
        import toolpath
        with mock.patch.object(toolpath, "search_path", return_value="/nonexistent"), \
             mock.patch.dict(toolpath._cache, {}, clear=True):
            failed = mock.Mock(returncode=1, stderr="no such machine", stdout="")
            with self.assertRaises(cloud_sweep.CloudSweepUnavailable):
                cloud_sweep.start_machine("app", "gone", run=lambda *a, **k: failed)
            stopped = lambda *a, **k: mock.Mock(returncode=0, stdout='[{"id": "m", "state": "stopped"}]')
            self.assertEqual(cloud_sweep.machine_state("a", "m", run=stopped), "stopped")

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

    def test_it_dedupes_on_the_constraint_key_and_not_the_whole_row(self):
        # `discovered_via` is not in `(account_id, source, board_slug)`, so
        # deduping on all three counted 866 boards where the database stored
        # 795: 71 slugs carry two `addedFrom` values. Nothing was lost and the
        # report was wrong, which is a dedup key standing in for a constraint.
        companies = {"a": {"ats": "breezy", "token": "surge", "addedFrom": "pack"},
                     "b": {"ats": "breezy", "token": "surge", "addedFrom": "roles"}}
        got = self.mod.rows(companies)
        self.assertEqual(len(got), 1)
        # Deterministic rather than arbitrary: sorted, so the same input always
        # produces the same `discovered_via`.
        self.assertEqual(got[0][2], "pack")

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


class MergeOnlyTests(unittest.TestCase):
    """A fetch that already landed needs no machine.

    **Starting one is not harmless.** `main` always called `start_machine`, so
    an attempt to merge staged work kicked off a *second* sweep and then waited
    for it: a finished 27-channel sweep sat in `sweep_postings` while the
    device waited out a deadline on a run it had just caused, and the partial
    second sweep had to be deleted by hand.
    """

    def test_the_flag_exists_and_skips_the_machine(self):
        import inspect
        source = inspect.getsource(cloud_sweep.main)
        self.assertIn("args.merge_only", source)
        # The start must be inside the else, not before the branch.
        before, _, after = source.partition("if args.merge_only:")
        self.assertNotIn("start_machine(", before)
        self.assertIn("start_machine(", after)

    def test_it_does_not_need_an_app_configured(self):
        # The machine is not the subject; the staged sweep is.
        import inspect
        self.assertIn("if not app and not args.merge_only:",
                      inspect.getsource(cloud_sweep.main))

    def test_it_takes_the_newest_finished_sweep_regardless_of_age(self):
        # `wait_for_sweep` with no app treats the sweep as already landed and
        # asks only whether every channel finished.
        conn = Conn({"from sweep_runs": [("landed", 27, 27)]})
        self.assertEqual(
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct",
                                       datetime(1970, 1, 1, tzinfo=timezone.utc),
                                       app=None, machine=None, seconds=0,
                                       sleep=lambda _s: None),
            "landed")

    def test_a_half_written_sweep_is_still_refused_in_merge_only(self):
        # The 1-of-27 guard is not waived by the mode.
        conn = Conn({"from sweep_runs": [("partial", 27, 3)]})
        with self.assertRaises(cloud_sweep.CloudSweepUnavailable):
            cloud_sweep.wait_for_sweep(
            lambda: conn, "acct",
                                       datetime(1970, 1, 1, tzinfo=timezone.utc),
                                       app=None, machine=None, seconds=0,
                                       sleep=lambda _s: None)


class MachineStateTests(unittest.TestCase):
    """`machines list --json`, because `machine status --json` is not a flag.

    It exits non-zero with usage text, so `machine_state` returned "" forever,
    `wait_for_sweep` correctly refused to read could-not-look as stopped, and a
    finished sweep waited out its deadline. Third unverified flyctl invocation
    in this feature after `--command` and `--config`.
    """

    def listing(self, payload, code=0):
        return lambda *a, **k: mock.Mock(returncode=code, stdout=payload)

    def test_it_finds_the_machine_by_id(self):
        run = self.listing('[{"id": "a", "state": "started"}, '
                           '{"id": "b", "state": "stopped"}]')
        self.assertEqual(cloud_sweep.machine_state("app", "b", run=run), "stopped")

    def test_a_machine_absent_from_a_readable_listing_is_destroyed(self):
        # Absent from a listing that was read is not the same as unreadable,
        # and neither is "stopped": a machine that no longer exists cannot be
        # waited for.
        run = self.listing('[{"id": "a", "state": "started"}]')
        self.assertEqual(cloud_sweep.machine_state("app", "gone", run=run),
                         "destroyed")

    def test_an_unreadable_listing_is_empty_not_a_state(self):
        for payload, code in (("", 1), ("not json", 0), ("{}", 0)):
            with self.subTest(payload=payload):
                self.assertEqual(
                    cloud_sweep.machine_state("app", "m",
                                              run=self.listing(payload, code)),
                    "")

    def test_it_asks_the_command_that_exists(self):
        seen = {}
        def run(argv, **kw):
            seen["argv"] = argv
            return mock.Mock(returncode=0, stdout="[]")
        cloud_sweep.machine_state("app", "m", run=run)
        self.assertIn("machines", seen["argv"])
        self.assertIn("list", seen["argv"])
        self.assertNotIn("status", seen["argv"])
