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
        def __init__(self): self.log = []; self.commits = 0; self.closed = 0
        def cursor(self): return StagingOrderTests.Cursor(self.log)
        def commit(self): self.commits += 1
        def close(self): self.closed += 1

    def test_the_postings_are_written_before_the_status(self):
        conn = self.Conn()
        result = base.ok("greenhouse", [{"id": "a"}, {"id": "b"}], complete=True)
        sweep_worker.stage(lambda: conn, "acct", "sweep", "greenhouse", result)
        kinds = [k for k, _ in conn.log]
        self.assertEqual(kinds, ["postings", "run"],
                         "the run row was written before the postings, so a "
                         "failed write leaves a `complete` claiming them")
        self.assertEqual(conn.log[-1][1], "complete")
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.closed, 1,
                         "the stage kept its connection open, which is how a "
                         "24-minute fetch reached Neon with a closed socket")

    def test_a_failed_channel_is_written_rather_than_omitted(self):
        # An absent row and a failed row are different facts, and the device
        # cannot tell "not asked" from "could not answer" if the failure is
        # simply not there.
        conn = self.Conn()
        sweep_worker.stage(lambda: conn, "acct", "sweep", "jooble",
                           base.failed("jooble", "HTTP 400"))
        self.assertEqual([k for k, _ in conn.log], ["run"])
        self.assertEqual(conn.log[-1][1], "failed")

    def test_it_batches_rather_than_writing_a_row_at_a_time(self):
        conn = self.Conn()
        result = base.ok("greenhouse", [{"id": str(i)} for i in range(1200)],
                         complete=True)
        sweep_worker.stage(lambda: conn, "acct", "sweep", "greenhouse", result, batch=500)
        sizes = [n for k, n in conn.log if k == "postings"]
        self.assertEqual(sizes, [500, 500, 200])


class ConnectionLifetimeTests(unittest.TestCase):
    """A connection per stage, never one for the run.

    **Measured on the first real full sweep.** One connection was opened at the
    start and held while the fetch worked: adzuna staged four seconds in, ashby
    took about twenty-four minutes, and the stage after it died with
    `psycopg.OperationalError: consuming input failed: SSL connection has been
    closed unexpectedly`. The run ended with 1 of 27 channels filed. Neon
    closes an idle connection long before a 795-board sweep finishes.
    """

    def test_stage_opens_and_closes_its_own_connection(self):
        opened = []

        class C:
            def __init__(self): self.closed = False
            def cursor(self): return StagingOrderTests.Cursor([])
            def commit(self): pass
            def close(self): self.closed = True

        def connect():
            c = C(); opened.append(c); return c

        sweep_worker.stage(connect, "acct", "s", "greenhouse",
                           base.ok("greenhouse", [{"id": "a"}], complete=True))
        sweep_worker.stage(connect, "acct", "s", "ashby",
                           base.ok("ashby", [{"id": "b"}], complete=True))
        self.assertEqual(len(opened), 2, "two stages shared one connection")
        self.assertTrue(all(c.closed for c in opened))

    def test_stage_takes_a_callable_not_a_connection(self):
        # The signature is the guard: a caller cannot accidentally hold one
        # across a fetch if it never has one to hold.
        import inspect
        first = list(inspect.signature(sweep_worker.stage).parameters)[0]
        self.assertEqual(first, "connect")

    def test_the_sweep_holds_nothing_across_a_fetch(self):
        import inspect
        self.assertEqual(
            list(inspect.signature(sweep_worker.sweep).parameters)[0], "connect")
        source = inspect.getsource(sweep_worker.sweep)
        # It reads the registry on a connection it then closes, and the fetch
        # loop below must not reference that name again.
        after = source.split("finally:", 1)[1]
        self.assertNotIn("conn.", after.split("for name, connector")[1],
                         "the fetch loop touches a connection it opened before "
                         "the fetch")


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


class ValidatorCacheTests(unittest.TestCase):
    """Every cloud sweep was a cold sweep, and that was the whole story.

    `base.Fetcher` keeps ETag and Last-Modified in `<root>/source-cache.json`,
    and a Fly machine's root is wiped on every start. Measured 2026-09-08 on
    the first full cloud sweep against the laptop's own last one:

        laptop     ashby 193 boards -> 1,050 postings   most answer 304
        container  ashby 193 boards -> 6,855 postings   717s, every board whole

    Same boards, same code, six times the payload. No machine size fixes it.
    """

    class Cur:
        def __init__(self, rows, log): self.rows = rows; self.log = log
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, args=None): self.log.append(("execute", sql.split()[0]))
        def executemany(self, sql, rows): self.log.append(("many", len(rows)))
        def fetchall(self): return self.rows

    class Conn:
        def __init__(self, rows=()): self.rows = list(rows); self.log = []; self.closed = 0
        def cursor(self): return ValidatorCacheTests.Cur(self.rows, self.log)
        def rollback(self): pass
        def commit(self): self.log.append(("commit", 1))
        def close(self): self.closed += 1

    def test_it_writes_the_file_the_fetcher_reads(self):
        # The file is the interface: `Fetcher` loads it in its constructor, and
        # teaching it about Postgres would put a database in every connector's
        # fetch path, including on the device where the file is correct.
        import tempfile
        from datetime import datetime, timezone
        at = datetime(2026, 9, 8, tzinfo=timezone.utc)
        conn = self.Conn([("https://api.ashbyhq.com/x", 'W/"abc"', None, at)])
        with tempfile.TemporaryDirectory() as tmp:
            n = sweep_worker.load_validators(conn, "acct", Path(tmp))
            self.assertEqual(n, 1)
            written = json.loads((Path(tmp) / "source-cache.json").read_text())
        self.assertEqual(written["https://api.ashbyhq.com/x"]["etag"], 'W/"abc"')
        self.assertEqual(written["https://api.ashbyhq.com/x"]["at"],
                         "2026-09-08T00:00:00Z")

    def test_it_is_seeded_before_the_fetcher_exists(self):
        # After would be useless: the Fetcher reads the file once, in __init__.
        import inspect
        source = inspect.getsource(sweep_worker.sweep)
        self.assertLess(source.index("load_validators"),
                        source.index("base.Fetcher("),
                        "the cache is seeded after the Fetcher has read it")

    def test_saving_uses_its_own_connection(self):
        # It runs after the whole fetch. A connection held since before that is
        # one Neon closed twenty minutes ago -- the bug this file already has a
        # class for.
        import inspect
        first = list(inspect.signature(sweep_worker.save_validators).parameters)[0]
        self.assertEqual(first, "connect")

    def test_a_damaged_cache_is_a_slow_run_not_a_failed_one(self):
        # The same judgement `base.Fetcher` makes about reading it.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "source-cache.json").write_text("{not json")
            self.assertEqual(
                sweep_worker.save_validators(lambda: self.Conn(), "acct", Path(tmp)),
                0)

    def test_a_missing_cache_is_not_an_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                sweep_worker.save_validators(lambda: self.Conn(), "acct", Path(tmp)),
                0)

    def test_it_upserts_rather_than_replacing_the_table(self):
        # Two workers sweeping different channels each want their own URLs and
        # no other's. A whole-map write is what the single JSON file did and
        # why it could not be shared.
        import inspect
        source = inspect.getsource(sweep_worker.save_validators)
        self.assertIn("on conflict (account_id, url) do update", source)
        self.assertNotIn("truncate", source.lower())

    def test_it_prunes_to_the_same_cap_the_fetcher_uses(self):
        from sources import base
        self.assertEqual(sweep_worker.VALIDATOR_CAP, 5000)
        # Derived from the same number the file trim uses, so the table and the
        # file cannot drift into disagreeing about how much history is kept.
        import inspect
        self.assertIn("5000", inspect.getsource(base.Fetcher.save_validators))


class FetchProgressTests(unittest.TestCase):
    """A long fetch is legible from outside, and only when asked.

    Ninety minutes of greenhouse looked identical whether the sweep was working
    or stuck, because the progress line was per channel and 309 boards sat
    inside one. `Fetcher.get_json` is the one place every connector's requests
    pass through, so it is the one place a per-board line covers all
    twenty-seven without touching any of them.
    """

    class Resp:
        def __init__(self, status=200, unchanged=False):
            self.status = status; self.unchanged = unchanged

    def fetcher(self, resp):
        import tempfile
        from sources import base
        self.tmp = tempfile.TemporaryDirectory()
        f = base.Fetcher(root=self.tmp.name, opener=None)
        f._get_json_inner = lambda url, headers=None, conditional=True: ({}, resp)
        return f

    def test_it_is_silent_on_the_laptop(self):
        import io, os
        from unittest import mock
        f = self.fetcher(self.Resp())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("sys.stdout", out):
            os.environ.pop("JOBBY_FETCH_PROGRESS", None)
            f.get_json("https://boards-api.greenhouse.io/v1/boards/spacex/jobs")
        self.assertEqual(out.getvalue(), "")

    def test_it_names_host_status_and_duration_in_the_container(self):
        import io, os
        from unittest import mock
        f = self.fetcher(self.Resp(200))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"JOBBY_FETCH_PROGRESS": "1"}), \
             mock.patch("sys.stdout", out):
            f.get_json("https://boards-api.greenhouse.io/v1/boards/spacex/jobs")
        line = out.getvalue()
        self.assertIn("boards-api.greenhouse.io", line)
        self.assertIn(" 200 ", line)
        self.assertRegex(line, r"\d+\.\ds")

    def test_a_304_is_shown_as_one(self):
        # The whole point of the warm run is that most of these say 304, and a
        # line that showed a bare status would not distinguish it.
        import io, os
        from unittest import mock
        f = self.fetcher(self.Resp(304, unchanged=True))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"JOBBY_FETCH_PROGRESS": "1"}), \
             mock.patch("sys.stdout", out):
            f.get_json("https://api.ashbyhq.com/posting-api/job-board/vanta")
        self.assertIn(" 304 ", out.getvalue())

    def test_the_container_asks_for_it(self):
        docker = (REPO / "deploy" / "sourcing" / "Dockerfile").read_text()
        self.assertIn("JOBBY_FETCH_PROGRESS=1", docker)


class SeedValidatorsTests(unittest.TestCase):
    def setUp(self):
        import seed_validators
        self.mod = seed_validators

    def test_it_keeps_only_entries_with_a_validator_in_them(self):
        cache = {"https://a": {"etag": 'W/"x"', "lastModified": None},
                 "https://b": {"etag": None, "lastModified": "Mon"},
                 "https://c": {"etag": None, "lastModified": None},
                 "https://d": "not a dict"}
        self.assertEqual([r[0] for r in self.mod.rows(cache)],
                         ["https://a", "https://b"])

    def test_seeding_never_overwrites_a_newer_cloud_validator(self):
        # The upsert is guarded on `seen_at`, so a validator a cloud sweep
        # refreshed after the laptop's last run is left alone.
        import inspect
        source = inspect.getsource(self.mod.main)
        self.assertIn("where http_validators.seen_at < excluded.seen_at", source)


class MemoryTests(unittest.TestCase):
    def test_the_machine_has_the_memory_the_measurement_needs(self):
        # 23 MB free of 459 at 97 minutes into a cold greenhouse sweep. The
        # sweep is single-threaded, so this is the axis that matters and vCPU
        # is not.
        import re
        toml = (REPO / "deploy" / "sourcing" / "fly.toml").read_text()
        code = "\n".join(l for l in toml.splitlines() if not l.lstrip().startswith("#"))
        m = re.search(r'memory = "(\d+)mb"', code)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 2048)
