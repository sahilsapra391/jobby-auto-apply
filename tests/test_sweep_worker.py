"""The container fetches and stages; it never merges and never claims.

**The seam is the point.** `jobs_store` contains zero SELECTs and `run()` reads
the whole 98 MB pool file, so the pool cannot move in Phase 3 -- that is Phase
4's "the device reading Postgres". What can move is the fetch, and it is worth
moving for a reason that is not CPU: `jobs_store.POOL_LOCK` is the string
"scan", so the sweep's lock is the pool's write lock, eight non-sweep callers
take it, two on the apply path, and 176 ticks across 13 days skipped their
board reconcile with LockBusy on it.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import sweep_worker  # noqa: E402
from sources import base  # noqa: E402


class AccountTests(unittest.TestCase):
    def test_it_refuses_to_run_without_an_account(self):
        # Every row is account-scoped. A default files one account's postings
        # under another the first time a second account exists, which is Phase
        # 5 and close enough that guessing now is a bug with a date on it.
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"JOBBY_ACCOUNT_ID": ""}):
            with self.assertRaises(SystemExit) as caught:
                sweep_worker.account_id()
        self.assertIn("no safe default", str(caught.exception))


class StatusTests(unittest.TestCase):
    def test_ok_becomes_complete_and_error_becomes_failed(self):
        self.assertEqual(sweep_worker._status({"status": "ok"}), "complete")
        self.assertEqual(sweep_worker._status({"status": "error"}), "failed")

    def test_a_skip_stays_a_skip(self):
        # A channel this run had no reason to ask is not a channel that could
        # not answer, and the device's problems list depends on the difference.
        self.assertEqual(sweep_worker._status({"status": "skipped"}), "skipped")

    def test_an_unknown_status_is_failed_not_complete(self):
        # The safe direction: the device closes postings only against runs
        # marked complete, so an unrecognised status must never become one.
        for got in (None, "", "weird", "running"):
            with self.subTest(status=got):
                self.assertEqual(sweep_worker._status({"status": got}), "failed")


class StagingOrderTests(unittest.TestCase):
    """`complete` is written after the postings, never before."""

    class Cursor:
        def __init__(self, log): self.log = log
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def executemany(self, sql, rows): self.log.append(("postings", len(rows)))
        def execute(self, sql, args=None):
            self.log.append(("run", args[3] if args else None))

    class Conn:
        def __init__(self): self.log = []; self.commits = 0
        def cursor(self): return StagingOrderTests.Cursor(self.log)
        def commit(self): self.commits += 1

    def test_the_postings_are_written_before_the_status(self):
        conn = self.Conn()
        result = base.ok("greenhouse", [{"id": "a"}, {"id": "b"}], complete=True)
        sweep_worker.stage(conn, "acct", "sweep", "greenhouse", result)
        kinds = [k for k, _ in conn.log]
        self.assertEqual(kinds, ["postings", "run"],
                         "the run row was written before the postings, so a "
                         "failed write leaves a `complete` claiming them")
        self.assertEqual(conn.log[-1][1], "complete")
        self.assertEqual(conn.commits, 1)

    def test_a_failed_channel_is_written_rather_than_omitted(self):
        # An absent row and a failed row are different facts, and the device
        # cannot tell "not asked" from "could not answer" if the failure is
        # simply not there.
        conn = self.Conn()
        sweep_worker.stage(conn, "acct", "sweep", "jooble",
                           base.failed("jooble", "HTTP 400"))
        self.assertEqual([k for k, _ in conn.log], ["run"])
        self.assertEqual(conn.log[-1][1], "failed")

    def test_it_batches_rather_than_writing_a_row_at_a_time(self):
        conn = self.Conn()
        result = base.ok("greenhouse", [{"id": str(i)} for i in range(1200)],
                         complete=True)
        sweep_worker.stage(conn, "acct", "sweep", "greenhouse", result, batch=500)
        sizes = [n for k, n in conn.log if k == "postings"]
        self.assertEqual(sizes, [500, 500, 200])


class ChannelIsolationTests(unittest.TestCase):
    def test_a_raising_connector_becomes_a_failure_not_a_stop(self):
        # A connector that raises must not take the other twenty-six with it,
        # and must not vanish either. Restated from `ingest.run` because this
        # is a different caller and a silent divergence between the two is a
        # channel that behaves differently in the cloud.
        def boom(fetcher, **kw):
            raise ValueError("the board moved")
        result = sweep_worker.run_channel("greenhouse", boom, None, {}, {},
                                          None, None)
        self.assertEqual(result["status"], "error")
        self.assertIn("ValueError", result["reason"])
        self.assertIn("the board moved", result["reason"])


class BoardCountTests(unittest.TestCase):
    def test_it_reads_the_board_count_out_of_the_connector_s_own_note(self):
        self.assertEqual(sweep_worker._boards({"reason": "353 boards"}), 353)
        self.assertEqual(
            sweep_worker._boards({"reason": "194 boards, 2 token(s) 404"}), 194)

    def test_a_note_with_no_count_is_zero_rather_than_a_guess(self):
        for note in ("1 of 1 countries x 1 terms", "", None, "boards"):
            with self.subTest(note=note):
                self.assertEqual(sweep_worker._boards({"reason": note}), 0)


class ImageContractTests(unittest.TestCase):
    """What the image must carry, checked against what the code reads.

    The *workflow* is not checked here. `tests/test_build_context.py` owns it,
    along with the `.dockerignore` it depends on, because two files asserting
    on one workflow is two things to update in step and one of them will not
    be -- which is the declared-twice shape this repository has met five times.
    """

    def dockerfile(self):
        return (REPO / "deploy" / "sourcing" / "Dockerfile").read_text()

    def test_it_copies_every_tree_the_entrypoint_imports_from(self):
        text = self.dockerfile()
        for path in ("scripts/", "packages/"):
            with self.subTest(path=path):
                self.assertIn(f"COPY {path}", text)

    def test_it_carries_the_file_write_path_coverage_reads(self):
        # Found by running the container: a FileNotFoundError on every sweep.
        # Inert here, and a diagnostic that fires every run is noise that hides
        # the next real one.
        self.assertIn("docs/write-paths.md", self.dockerfile())

    def test_it_pins_the_python_and_the_driver(self):
        text = self.dockerfile()
        self.assertIn("FROM python:3.12", text)
        self.assertRegex(text, r"psycopg\[binary\]==\d+\.\d+\.\d+")

    def test_it_does_not_run_as_root(self):
        self.assertIn("USER sweeper", self.dockerfile())

    def test_the_comment_strip_does_not_hide_a_real_service_block(self):
        # The control for the strip above. If the file grew a real service
        # block, stripping comments must not make it invisible.
        with_service = "# no [[services]] here\n[[services]]\n  internal_port = 80\n"
        code = "\n".join(line for line in with_service.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertIn("[[services]]", code)

    def test_the_region_is_the_one_next_to_the_database(self):
        # The container's job ends in a multi-thousand-row insert into Neon on
        # AWS us-east-2. The connectors are HTTP-bound against third parties
        # whose latency this cannot influence.
        toml = (REPO / "deploy" / "sourcing" / "fly.toml").read_text()
        self.assertIn('primary_region = "iad"', toml)

    def test_it_is_a_scheduled_machine_and_not_a_server(self):
        # **The comments are stripped first, and the first version of this test
        # did not strip them.** It failed on the line explaining why there is
        # no `[[services]]` block -- a scanner reading a comment as if it were
        # code, which is the fault CLAUDE.md already records for the sensitive-
        # question markers and for the v2 control scan that tripped on a
        # `<button>` inside a comment about buttons. Written and hit within
        # minutes of each other, which is the point of that entry: knowing the
        # shape does not prevent repeating it.
        toml = (REPO / "deploy" / "sourcing" / "fly.toml").read_text()
        code = "\n".join(line for line in toml.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("[[services]]", code)
        self.assertNotIn("[http_service]", code)
        # And the strip has to leave something, or this passes on an empty file.
        self.assertIn("primary_region", code)


class DatabaseUrlTests(unittest.TestCase):
    """The file first, then the environment, and never the environment here.

    **The container needed an environment read and the environment read is how
    a test run reaches production.** `migrate.device_url_refusal` documents
    what that cost on 2026-08-30: 39 rows into `applications`, `answer_bank`
    emptied from ~180 rows to 0, the single `profiles` row overwritten with
    `{}`, parity green to 221 divergences, the seven-day clock back to zero.

    `HOME=$(mktemp -d)` covered that only incidentally, by making `config.env`
    unreadable. An environment read walks straight past it, so it carries the
    same refusal `migrate` does -- and this class exists because a mutation
    removing that refusal left every other test green.
    """

    def setUp(self):
        import jobs_db
        self.jobs_db = jobs_db

    def test_the_file_wins_when_there_is_one(self):
        # The device's reason, unchanged: a launchd service inherits almost
        # nothing, and a service that picked up a stray exported URL would
        # write to whatever database that names.
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.env"
            cfg.write_text("JOBBY_DATABASE_URL=postgresql://from-the-file/x\n")
            with mock.patch.dict(os.environ,
                                 {"JOBBY_DATABASE_URL": "postgresql://from-env/y"}):
                self.assertEqual(self.jobs_db.database_url(cfg),
                                 "postgresql://from-the-file/x")

    def test_the_environment_is_refused_under_a_test_runner(self):
        # This very process is a test runner, which is the point.
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nothing.env"
            with mock.patch.dict(os.environ,
                                 {"JOBBY_DATABASE_URL": "postgresql://prod/db"}):
                with mock.patch("sys.stderr"):
                    self.assertIsNone(self.jobs_db.database_url(missing))

    def test_it_says_so_rather_than_going_quiet(self):
        # A refusal nobody can see is the failure this repository has met
        # eleven times. It names the variable and the way out.
        import io
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nothing.env"
            captured = io.StringIO()
            with mock.patch.dict(os.environ,
                                 {"JOBBY_DATABASE_URL": "postgresql://prod/db"}), \
                 mock.patch("sys.stderr", captured):
                self.jobs_db.database_url(missing)
            said = captured.getvalue()
        self.assertIn("refusing", said)
        self.assertIn("JOBBY_TEST_DATABASE_URL", said)

    def test_the_environment_is_read_when_nothing_is_a_test_runner(self):
        # The positive control, and the whole reason the read exists: a
        # container has no config file and the environment is its only channel.
        # `_under_a_test_runner` is patched rather than the modules removed,
        # because unittest is genuinely in sys.modules right now.
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nothing.env"
            with mock.patch.dict(os.environ,
                                 {"JOBBY_DATABASE_URL": "postgresql://container/db"}), \
                 mock.patch.object(self.jobs_db, "_under_a_test_runner",
                                   return_value=False):
                self.assertEqual(self.jobs_db.database_url(missing),
                                 "postgresql://container/db")

    def test_an_empty_file_value_does_not_shadow_the_environment(self):
        # A config.env carrying `JOBBY_DATABASE_URL=` is a variable somebody
        # blanked, not a URL. Returning it would be an empty connection string.
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.env"
            cfg.write_text("JOBBY_DATABASE_URL=\n")
            with mock.patch.dict(os.environ,
                                 {"JOBBY_DATABASE_URL": "postgresql://container/db"}), \
                 mock.patch.object(self.jobs_db, "_under_a_test_runner",
                                   return_value=False):
                self.assertEqual(self.jobs_db.database_url(cfg),
                                 "postgresql://container/db")

    def test_nothing_anywhere_is_still_none(self):
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nothing.env"
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(self.jobs_db, "_under_a_test_runner",
                                   return_value=False):
                self.assertIsNone(self.jobs_db.database_url(missing))
