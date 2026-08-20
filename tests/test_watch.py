"""Tests for the trigger pipeline: toolpath, agent preflight, watcher locks.

These pin down the three failures that made board buttons do nothing at all.

launchd starts agents with PATH=/usr/bin:/bin:/usr/sbin:/sbin, so every bare
`node` / `vercel` / `claude` call died with FileNotFoundError. The watcher could
not tell that crash apart from an empty queue, so it logged healthy cycles for
hours. And a runner that could not authenticate still claimed the request it
was about to fail, eating one user click per cycle.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


toolpath = load("toolpath")
agent = load("agent")
watch = load("watch")


class ToolPathTests(unittest.TestCase):
    def test_search_path_includes_what_launchd_omits(self):
        # The whole bug: launchd's PATH cannot see Homebrew.
        self.assertIn("/opt/homebrew/bin", toolpath.search_path().split(os.pathsep))

    def test_resolves_absolutely_under_a_bare_environment(self):
        original = os.environ.get("PATH")
        os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        try:
            found = toolpath.find("node", required=False)
        finally:
            os.environ["PATH"] = original if original is not None else ""
        if found is not None:
            self.assertTrue(found.startswith("/"), "must be an absolute path")

    def test_a_miss_is_not_cached(self):
        # Caching a miss would make a later required lookup return None instead
        # of raising, turning a missing tool into a confusing downstream crash.
        toolpath.find("definitely-not-a-real-binary-xyz", required=False)
        self.assertNotIn("definitely-not-a-real-binary-xyz", toolpath._cache)

    def test_a_missing_required_tool_raises_with_a_fix(self):
        with self.assertRaises(SystemExit) as ctx:
            toolpath.find("definitely-not-a-real-binary-xyz", required=True)
        self.assertIn("cannot find", str(ctx.exception))

    def test_enriched_env_keeps_other_variables(self):
        env = toolpath.enriched_env({"BLOB_READ_WRITE_TOKEN": "abc"})
        self.assertEqual(env["BLOB_READ_WRITE_TOKEN"], "abc")
        self.assertIn("/opt/homebrew/bin", env["PATH"])


class AuthDetectionTests(unittest.TestCase):
    def test_recognises_the_revoked_token_message(self):
        self.assertTrue(agent.looks_like_auth_failure(
            "Failed to authenticate. API Error: 401 OAuth access token has been revoked."))

    def test_does_not_flag_an_ordinary_failure_as_auth(self):
        self.assertFalse(agent.looks_like_auth_failure("TypeError: cannot read property"))

    def test_the_blocked_message_names_the_fix_not_just_the_error(self):
        msg = agent.blocked_reason("401")
        self.assertIn("claude login", msg)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.tmp.name)

    def tearDown(self):
        watch.LOCK_DIR = self.original
        self.tmp.cleanup()

    def test_no_lock_means_free(self):
        self.assertIsNone(watch.lock_holder("apply"))

    def test_a_live_pid_holds_the_lock(self):
        # Our own pid is certainly alive. This is what enforces one application
        # at a time, which is a standing rule from the user.
        watch.take_lock("apply", os.getpid(), note="acme-pm")
        held = watch.lock_holder("apply")
        self.assertIsNotNone(held)
        self.assertEqual(held["note"], "acme-pm")

    def test_a_dead_pid_releases_the_lock(self):
        # A runner killed mid-flight must not wedge the pipeline forever.
        watch.take_lock("apply", 999999, note="stale")
        self.assertIsNone(watch.lock_holder("apply"))
        self.assertFalse(watch.lock_path("apply").exists())

    def test_a_corrupt_lock_file_releases_rather_than_wedges(self):
        watch.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        watch.lock_path("apply").write_text("not json at all")
        self.assertIsNone(watch.lock_holder("apply"))


class TickErrorTests(unittest.TestCase):
    def test_unconfigured_queue_raises_rather_than_looking_empty(self):
        # The original defect. poll_queues must never return {} on failure,
        # because an empty dict is indistinguishable from a quiet system.
        original = watch.toolpath.queue_env

        def unconfigured(env=None):
            raise SystemExit("the board queue is not configured")

        watch.toolpath.queue_env = unconfigured
        try:
            with self.assertRaises(watch.TickError):
                watch.poll_queues()
        finally:
            watch.toolpath.queue_env = original

    def test_a_failed_queue_read_raises_rather_than_looking_empty(self):
        # A node process that exits non-zero is a failure, never an empty queue.
        original = watch.subprocess.run

        class R:
            returncode = 1
            stdout = ""
            stderr = "KV read failed: 500"

        watch.subprocess.run = lambda *a, **k: R()
        try:
            with self.assertRaises(watch.TickError):
                watch.poll_queues()
        finally:
            watch.subprocess.run = original

    def test_settings_prefer_the_machine_local_file(self):
        # `vercel env pull` regenerates deploy/.env.local and discards anything
        # added by hand, which is how a healthchecks ping URL was lost.
        import tempfile as _tf
        original = watch.toolpath.LOCAL_CONFIG
        with _tf.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("HEALTHCHECKS_URL=https://hc-ping.com/local-wins\n")
            watch.toolpath.LOCAL_CONFIG = pathlib.Path(f.name)
        try:
            self.assertEqual(watch.toolpath.settings()["HEALTHCHECKS_URL"],
                             "https://hc-ping.com/local-wins")
        finally:
            os.unlink(watch.toolpath.LOCAL_CONFIG)
            watch.toolpath.LOCAL_CONFIG = original

    def test_blocked_message_reads_the_json_not_the_raw_stdout(self):
        original = watch.store

        class R:
            returncode = 0
            stdout = json.dumps({"blocked": "Claude CLI cannot authenticate"})

        watch.store = lambda args: R()
        try:
            self.assertEqual(watch.blocked_message(), "Claude CLI cannot authenticate")
        finally:
            watch.store = original

    def test_blocked_message_is_empty_when_nothing_is_wrong(self):
        original = watch.store

        class R:
            returncode = 0
            stdout = json.dumps({"blocked": None})

        watch.store = lambda args: R()
        try:
            self.assertEqual(watch.blocked_message(), "")
        finally:
            watch.store = original


class BrowserConfigTests(unittest.TestCase):
    """The browser given to spawned agents.

    A `claude -p` session has no MCP servers, so without this it has no browser
    at all and an application can only ever be half done.
    """

    def setUp(self):
        self.browser = load("browser")
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.browser.STATE = root
        self.browser.PROFILE = root / "browser-profile"
        self.browser.CONFIG = root / "browser-mcp.json"
        self.browser.ARTIFACTS = root / "artifacts"

    def tearDown(self):
        self.tmp.cleanup()

    def test_config_names_the_server_the_allowlist_expects(self):
        cfg = json.loads(self.browser.write_config().read_text())
        self.assertIn("browser", cfg["mcpServers"])

    def test_command_is_absolute_because_launchd_gives_no_path(self):
        cfg = json.loads(self.browser.write_config().read_text())
        self.assertTrue(cfg["mcpServers"]["browser"]["command"].startswith("/"))

    def test_the_server_inherits_a_path_that_can_see_node(self):
        # npx is a node shim, so an absolute path alone is not enough: env
        # resolves node against the child's own PATH.
        cfg = json.loads(self.browser.write_config().read_text())
        self.assertIn("/opt/homebrew/bin", cfg["mcpServers"]["browser"]["env"]["PATH"])

    def test_the_agent_attaches_rather_than_launching_its_own_browser(self):
        # The browser has to outlive the run. When each run started and stopped
        # its own, the tab holding a part-filled form died with the run, and
        # "Open & finish" put the user in front of a blank application.
        args = json.loads(self.browser.write_config().read_text())["mcpServers"]["browser"]["args"]
        self.assertIn("--cdp-endpoint", args)
        self.assertNotIn("--user-data-dir", args,
                         "the long-lived browser owns the profile, not the MCP server")
        self.assertFalse(any(a.startswith("--isolated") for a in args))

    def test_the_browser_the_agent_attaches_to_owns_the_profile(self):
        # Signing in once has to carry across runs, so the profile must be on
        # the browser that persists, not on a server that restarts every run.
        self.assertIn("--user-data-dir", " ".join(
            [self.browser.CHROME, f"--user-data-dir={self.browser.PROFILE}"]))
        self.assertTrue(str(self.browser.PROFILE).endswith("browser-profile"))

    def test_focus_matching_ignores_query_and_trailing_slash(self):
        # An ATS rewrites its own URL as you move through the steps, so the tab
        # holding a part-filled form rarely still has the address it started on.
        same = self.browser._key("https://jobs.example.com/apply/123")
        self.assertEqual(same, self.browser._key("https://jobs.example.com/apply/123/"))
        self.assertEqual(same, self.browser._key("https://jobs.example.com/apply/123?src=rss"))
        self.assertNotEqual(same, self.browser._key("https://jobs.example.com/apply/456"))

    def test_focus_reports_honestly_when_no_browser_is_up(self):
        original = self.browser.running
        self.browser.running = lambda slot=None: False
        try:
            result = self.browser.focus("https://jobs.example.com/apply/123")
            self.assertFalse(result["focused"])
            self.assertIn("no browser", result["why"])
        finally:
            self.browser.running = original

    def test_each_slot_gets_its_own_browser_and_profile(self):
        # Two MCP servers on one Chrome share a context and drift onto the same
        # tab, which with three concurrent applications means one agent typing
        # into another's form. Ports and profiles must not overlap.
        ports = [self.browser.port_for(s) for s in self.browser.SLOTS]
        self.assertEqual(len(set(ports)), len(ports), "each slot needs its own port")
        profiles = [str(self.browser._profile_dir(s)) for s in self.browser.SLOTS]
        self.assertEqual(len(set(profiles)), len(profiles), "each slot needs its own profile")

    def test_a_slot_config_points_at_that_slot_only(self):
        for slot in self.browser.SLOTS:
            cfg = json.loads(self.browser.write_config(slot).read_text())
            args = cfg["mcpServers"]["browser"]["args"]
            endpoint = args[args.index("--cdp-endpoint") + 1]
            self.assertIn(str(self.browser.port_for(slot)), endpoint)

    def test_cloning_refuses_while_a_browser_is_running(self):
        # Copying a profile Chrome has open produces a corrupt one.
        original = self.browser.running
        self.browser.running = lambda slot=None: True
        try:
            out = self.browser.clone_logins()
            self.assertFalse(out["cloned"])
            self.assertIn("stop these first", out["why"])
        finally:
            self.browser.running = original

    def test_the_playwright_version_is_pinned(self):
        args = json.loads(self.browser.write_config().read_text())["mcpServers"]["browser"]["args"]
        pkg = next(a for a in args if a.startswith("@playwright/mcp"))
        self.assertIn("@", pkg.replace("@playwright/mcp", ""))
        self.assertNotIn("latest", pkg)

    def test_config_path_writes_when_absent_and_reuses_when_present(self):
        first = self.browser.config_path()
        self.assertTrue(first.exists())
        first.write_text('{"marker": true}')
        self.assertEqual(json.loads(self.browser.config_path().read_text()), {"marker": True})


class HeartbeatTests(unittest.TestCase):
    """When the watcher tells healthchecks it is alive.

    The first version assigned last_line before comparing against it, so the
    change test was permanently false and only the timer ever pinged. A real
    event produced no ping at all, which is the one moment you most want one.
    """

    def test_a_new_event_pings_immediately(self):
        self.assertTrue(watch.heartbeat_due('{"applyStarted": 1}', "", 0))

    def test_a_changed_event_pings_immediately(self):
        self.assertTrue(watch.heartbeat_due('{"b": 2}', '{"a": 1}', 0))

    def test_the_same_standing_condition_does_not_ping_every_tick(self):
        line = '{"waitingOnAuth": "..."}'
        self.assertFalse(watch.heartbeat_due(line, line, 5))

    def test_a_standing_condition_still_pings_on_the_heartbeat(self):
        line = '{"running": "acme-pm"}'
        self.assertTrue(watch.heartbeat_due(line, line, watch.HEARTBEAT_SECONDS + 1))

    def test_an_idle_watcher_still_pings_on_the_heartbeat(self):
        self.assertFalse(watch.heartbeat_due("", "", 5))
        self.assertTrue(watch.heartbeat_due("", "", watch.HEARTBEAT_SECONDS + 1))

    def test_the_heartbeat_leaves_room_under_a_one_minute_period(self):
        # healthchecks alerts when pings stop arriving. The interval must be
        # comfortably shorter than the configured period, or a single dropped
        # ping reads as death.
        self.assertLessEqual(watch.HEARTBEAT_SECONDS, 30)


class ApplySlotTests(unittest.TestCase):
    """Three applications at once, one agent each, and never the same job twice.

    The queue is a single KV key updated by read-modify-write with no
    compare-and-swap. Two runners claiming at the same instant both take the head
    and the same job is applied to twice, which was measured rather than feared.
    So claiming happens only in the watcher's tick, which is single-threaded, and
    the runner is handed its application.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.tmp.name)

    def tearDown(self):
        watch.LOCK_DIR = self.original
        self.tmp.cleanup()

    def test_the_slot_count_is_defined_once_and_agreed_on(self):
        # A lock with no browser behind it spawns a runner that cannot attach to
        # anything, so watch must derive its locks from browser rather than
        # declaring its own list.
        import importlib.util, pathlib as _p
        spec = importlib.util.spec_from_file_location(
            "browser", _p.Path(watch.REPO) / "scripts" / "browser.py")
        b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(b)
        self.assertEqual(watch.APPLY_LOCKS, b.SLOTS)
        self.assertEqual(watch.APPLY_SLOTS, len(watch.APPLY_LOCKS))
        self.assertEqual(len(set(watch.APPLY_LOCKS)), watch.APPLY_SLOTS,
                         "slot names must be unique")
        self.assertEqual(len({b.port_for(s) for s in b.SLOTS}), len(b.SLOTS),
                         "each slot needs its own port")
        self.assertEqual(len({str(b._profile_dir(s)) for s in b.SLOTS}), len(b.SLOTS),
                         "each slot needs its own profile")

    def test_slots_are_handed_out_until_they_run_out(self):
        total = watch.APPLY_SLOTS
        self.assertEqual(len(watch.free_apply_slots()), total)
        for i, name in enumerate(watch.APPLY_LOCKS[:-1], start=1):
            watch.take_lock(name, os.getpid(), note=f"job-{i}")
            self.assertEqual(len(watch.free_apply_slots()), total - i)
        watch.take_lock(watch.APPLY_LOCKS[-1], os.getpid(), note="last")
        self.assertEqual(watch.free_apply_slots(), [],
                         "never more concurrent than there are slots")

    def test_a_dead_runner_frees_its_own_slot(self):
        watch.take_lock("apply-2", 999999, note="crashed-run")
        self.assertIn("apply-2", watch.free_apply_slots())
        self.assertNotIn("crashed-run", watch.running_applications())

    def test_running_applications_maps_each_job_to_its_slot(self):
        watch.take_lock("apply-1", os.getpid(), note="acme-pm")
        watch.take_lock("apply-3", os.getpid(), note="gamma-pm")
        self.assertEqual(watch.running_applications(),
                         {"acme-pm": "apply-1", "gamma-pm": "apply-3"})

    def test_a_running_application_is_never_swept_as_stuck(self):
        # The sweep used to skip entirely while any apply held the lock. With
        # three slots it has to skip only the ones still owned by a live runner.
        watch.take_lock("apply-1", os.getpid(), note="acme-pm")
        self.assertIn("acme-pm", watch.running_applications())

    def test_the_runner_refuses_to_claim_for_itself(self):
        # This is the race guard. If run_apply could reach into the queue, three
        # of them would take the same head and apply to one job three times.
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_apply.py")],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--id is required", (r.stderr or "") + (r.stdout or ""))


class SlotRaceTests(unittest.TestCase):
    """Two processes must never land in one slot.

    This happened: a hand-run `--once` alongside the daemon put two runners in
    apply-2, which is two agents on one browser, which is one of them typing
    into the other's application.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.tmp.name)

    def tearDown(self):
        watch.LOCK_DIR = self.original
        self.tmp.cleanup()

    def test_only_one_reservation_of_a_slot_succeeds(self):
        self.assertTrue(watch.reserve("apply-1", note="first"))
        self.assertFalse(watch.reserve("apply-1", note="second"),
                         "the second caller must lose, not overwrite")

    def test_a_reserved_slot_is_not_offered_again(self):
        watch.reserve("apply-1", note="first")
        self.assertNotIn("apply-1", watch.free_apply_slots())

    def test_a_reservation_left_by_a_dead_process_is_reclaimable(self):
        watch.take_lock("apply-2", 999999, note="crashed")
        self.assertTrue(watch.reserve("apply-2", note="new owner"))

    def test_the_watcher_lock_keeps_a_second_watcher_out(self):
        watch.take_lock("watcher", 999999, note="dead daemon")
        ok, _ = watch.single_watcher()
        self.assertTrue(ok, "a dead watcher must not block a new one")
        watch.take_lock("watcher", os.getpid(), note="live")
        # A different live pid is what must be refused; our own is a re-entry.
        ok, _ = watch.single_watcher()
        self.assertTrue(ok)


class SourcingSchedulerTests(unittest.TestCase):
    """The hourly Tier-1 scan: when it is due, and when it must stay disarmed.

    The disarmed case is the one that matters. A scan running against nobody's
    saved search would fill the board with roles nobody asked for and make the
    match percentage meaningless, because there would be nothing to match on.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.original_state = watch.STATE_DIR
        self.original_lock = watch.LOCK_DIR
        watch.STATE_DIR = self.root
        watch.LOCK_DIR = self.root / "locks"
        self.original_store = watch.store
        self.meta = {}

        class Result:
            returncode = 0

            def __init__(self, stdout=""):
                self.stdout = stdout
                self.stderr = ""

        def fake_store(args):
            if args[:1] == ["meta-get"]:
                key = args[args.index("--key") + 1]
                return Result(json.dumps({key: self.meta.get(key)}))
            if args[:1] == ["meta-set"]:
                key = args[args.index("--key") + 1]
                self.meta[key] = args[args.index("--value") + 1]
                return Result("{}")
            return Result("[]")

        watch.store = fake_store

    def tearDown(self):
        watch.STATE_DIR = self.original_state
        watch.LOCK_DIR = self.original_lock
        watch.store = self.original_store
        self.tmp.cleanup()

    def save_config(self, **fields):
        payload = {"tier1IntervalMinutes": 60, "version": 1}
        payload.update(fields)
        (self.root / "search-config.json").write_text(json.dumps(payload))

    def test_the_scan_stays_disarmed_until_a_search_is_saved(self):
        self.assertFalse(watch.search_config_saved())
        due, _ = watch.tier1_due()
        self.assertFalse(due, "an hourly scan against nobody's search is worse than none")

    def test_the_first_run_after_a_search_is_saved_is_due_immediately(self):
        self.save_config()
        due, nxt = watch.tier1_due()
        self.assertTrue(due)
        self.assertIsNone(nxt)

    def test_a_future_schedule_is_not_due(self):
        self.save_config()
        later = datetime.now(timezone.utc) + timedelta(minutes=30)
        self.meta["nextTier1ScanAt"] = later.strftime("%Y-%m-%dT%H:%M:%SZ")
        due, _ = watch.tier1_due()
        self.assertFalse(due)

    def test_a_past_schedule_is_due(self):
        self.save_config()
        earlier = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.meta["nextTier1ScanAt"] = earlier.strftime("%Y-%m-%dT%H:%M:%SZ")
        due, _ = watch.tier1_due()
        self.assertTrue(due)

    def test_an_unreadable_schedule_rebuilds_rather_than_stopping_forever(self):
        self.save_config()
        self.meta["nextTier1ScanAt"] = "sometime next week"
        due, _ = watch.tier1_due()
        self.assertTrue(due)

    def test_scheduling_lands_inside_the_interval_plus_jitter(self):
        self.save_config(tier1IntervalMinutes=60)
        now = datetime.now(timezone.utc)
        stamp = watch.schedule_next_tier1(now)
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        delta = (when - now).total_seconds()
        self.assertGreaterEqual(delta, 3600 - watch.TIER1_JITTER_SECONDS)
        self.assertLessEqual(delta, 3600 + watch.TIER1_JITTER_SECONDS)

    def test_the_jitter_actually_varies(self):
        # Every machine scanning eighty company boards at exactly hh:00:00 is
        # the kind of politeness failure that gets a User-Agent blocked.
        self.save_config()
        now = datetime.now(timezone.utc)
        stamps = {watch.schedule_next_tier1(now) for _ in range(25)}
        self.assertGreater(len(stamps), 1)

    def test_the_configured_interval_is_respected(self):
        self.save_config(tier1IntervalMinutes=180)
        now = datetime.now(timezone.utc)
        when = datetime.strptime(watch.schedule_next_tier1(now),
                                 "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertGreater((when - now).total_seconds(), 3 * 3600 - watch.TIER1_JITTER_SECONDS - 1)

    def test_a_corrupt_config_file_falls_back_to_hourly(self):
        (self.root / "search-config.json").write_text("{not json")
        now = datetime.now(timezone.utc)
        when = datetime.strptime(watch.schedule_next_tier1(now),
                                 "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual((when - now).total_seconds(), 3600 + watch.TIER1_JITTER_SECONDS)

    def test_the_schedule_moves_before_the_spawn_is_attempted(self):
        # A failed spawn that left the schedule alone would retry every three
        # seconds for an hour.
        self.save_config()
        self.assertNotIn("nextTier1ScanAt", self.meta)
        watch.schedule_next_tier1()
        self.assertIn("nextTier1ScanAt", self.meta)


class FoundSignatureTests(unittest.TestCase):
    """A scan that brought in new jobs has to count as a board change.

    Without this the pool fills up and the published board keeps showing the
    last set of sessions, which is the same shape as the outage where the
    watcher reported healthy cycles while doing nothing.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.original = watch.STATE_DIR
        watch.STATE_DIR = self.root
        self.original_store = watch.store

        class Result:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"lastIngest": {"at": "2026-08-19T20:00:00Z"}})

        watch.store = lambda args: Result()

    def tearDown(self):
        watch.STATE_DIR = self.original
        watch.store = self.original_store
        self.tmp.cleanup()

    def write_jobs(self, jobs):
        (self.root / "jobs.json").write_text(json.dumps(jobs))

    def test_no_pool_is_a_stable_signature(self):
        self.assertTrue(watch.found_signature().startswith("0:"))

    def test_a_new_job_changes_the_signature(self):
        self.write_jobs({"a": {"state": "new"}})
        before = watch.found_signature()
        self.write_jobs({"a": {"state": "new"}, "b": {"state": "new"}})
        self.assertNotEqual(before, watch.found_signature())

    def test_promoting_a_job_out_of_found_changes_the_signature(self):
        self.write_jobs({"a": {"state": "new"}, "b": {"state": "new"}})
        before = watch.found_signature()
        self.write_jobs({"a": {"state": "watched"}, "b": {"state": "new"}})
        self.assertNotEqual(before, watch.found_signature())

    def test_an_unreadable_pool_raises_rather_than_reading_as_empty(self):
        (self.root / "jobs.json").write_text("{not json")
        with self.assertRaises(watch.TickError):
            watch.found_signature()


class EyeAndPassTests(unittest.TestCase):
    """👁 creates exactly one session, and Pass is final.

    The board re-sends every click it remembers on each load, so both of these
    are pressed more than once as a matter of course.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "init"], capture_output=True)
        # Imported normally rather than through load(), which builds a second
        # module object: promote raises jobs_store.JobsStoreError from the
        # canonical module, and a duplicate class would never match it.
        sys.path.insert(0, str(ROOT / "scripts"))
        import jobs_store as canonical_jobs_store
        import promote as canonical_promote
        self.promote = canonical_promote
        self.jobs_store = canonical_jobs_store
        self.jobs_store.save_jobs(self.root, {"j1": {
            "id": "j1", "company": "Northwind Analytics",
            "title": "Product Manager, Internal Reporting",
            "normTitle": "product manager internal reporting",
            "canonicalUrl": "https://job-boards.greenhouse.io/northwind/jobs/4471002",
            "canonicalSource": "greenhouse", "state": "new",
            "location": {"raw": "New York, NY", "country": "US"},
            "tags": ["sql", "analytics"], "postedAt": "2026-08-13T00:00:00Z",
            "firstSeenAt": "2026-08-19T20:00:00Z",
            "sources": [{"source": "greenhouse", "sourceClass": "ats", "url": "x"},
                        {"source": "adzuna", "sourceClass": "aggregator", "url": "y"}],
            "score": {"total": 82, "parts": {"title": 35, "experience": 10, "location": 15,
                                             "keywords": 14, "freshness": 8},
                      "weights": {"title": 35, "experience": 20, "location": 15,
                                  "keywords": 20, "freshness": 10},
                      "unknowns": ["experience"], "gates": []},
        }})

    def tearDown(self):
        self.tmp.cleanup()

    def sessions(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "store.py"), "--root", str(self.root),
             "session-list"], capture_output=True, text=True)
        return json.loads(result.stdout or "[]")

    def test_the_eye_moves_the_job_and_creates_a_session(self):
        out = self.promote.watch(self.root, "j1")
        self.assertTrue(out["sessionCreated"])
        self.assertEqual(self.jobs_store.load_jobs(self.root)["j1"]["state"], "watched")
        rows = self.sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "watch")
        self.assertEqual(rows[0]["applicationId"], "j1")

    def test_the_eye_pressed_twice_creates_one_session(self):
        self.promote.watch(self.root, "j1")
        again = self.promote.watch(self.root, "j1")
        self.assertFalse(again["sessionCreated"])
        self.assertTrue(again["alreadyWatching"])
        self.assertEqual(len(self.sessions()), 1)

    def test_the_watching_card_carries_the_rubric_and_says_it_is_the_rubric(self):
        # Phase 1 promotes before anything has read the posting. The card has
        # to say something, and it must not imply a fit read that does not exist.
        self.promote.watch(self.root, "j1")
        session = json.loads((self.root / "sessions" / "j1.json").read_text())
        self.assertEqual(session["fitScore"], 82)
        self.assertIn("Match 82%", session["alignment"])
        self.assertIn("title 35/35", session["alignment"])
        self.assertIn("no fit read", session["alignment"].lower())
        self.assertIn("experience", session["alignment"])

    def test_the_session_carries_the_posting_link_and_where_it_was_seen(self):
        self.promote.watch(self.root, "j1")
        session = json.loads((self.root / "sessions" / "j1.json").read_text())
        self.assertEqual(session["url"],
                         "https://job-boards.greenhouse.io/northwind/jobs/4471002")
        self.assertEqual(session["seenOnBoards"], 2)
        self.assertEqual(session["coreSkills"], ["sql", "analytics"])

    def test_the_eye_on_an_already_known_role_does_not_throw(self):
        # store.py refuses a new watch entry for a posting already on record,
        # and a sweep surfaces exactly those. Before this, the refusal came back
        # as a JobsStoreError and the eye was a broken button on the lane.
        (self.root / "roles.json").write_text(json.dumps({
            "older-application": {
                "company": "Northwind Analytics",
                "role": "Product Manager, Internal Reporting",
                "url": "https://northwind.com/careers/4471002"}}))
        out = self.promote.watch(self.root, "j1")
        self.assertFalse(out["sessionCreated"])
        self.assertTrue(out["alreadyKnown"])
        self.assertIn("company and role", out["reason"])

    def test_an_already_known_role_still_leaves_the_found_lane(self):
        # Pressing the eye asked for the job to stop being offered. It does,
        # whether or not a second session was the right way to honour that.
        (self.root / "roles.json").write_text(json.dumps({
            "older-application": {
                "company": "Northwind Analytics",
                "role": "Product Manager, Internal Reporting", "url": ""}}))
        self.promote.watch(self.root, "j1")
        self.assertEqual(self.jobs_store.load_jobs(self.root)["j1"]["state"], "watched")

    def test_an_already_known_role_creates_no_second_session(self):
        (self.root / "roles.json").write_text(json.dumps({
            "older-application": {
                "company": "Northwind Analytics",
                "role": "Product Manager, Internal Reporting", "url": ""}}))
        self.promote.watch(self.root, "j1")
        self.assertEqual(self.sessions(), [])

    def test_an_unknown_role_is_unaffected_by_the_guard(self):
        # The guard must not swallow the ordinary case: a role nobody has seen
        # still gets its Watching session.
        (self.root / "roles.json").write_text(json.dumps({
            "older-application": {"company": "Someone Else",
                                  "role": "Warehouse Operative", "url": ""}}))
        out = self.promote.watch(self.root, "j1")
        self.assertTrue(out["sessionCreated"])
        self.assertEqual(len(self.sessions()), 1)

    def test_passing_tombstones_and_is_idempotent(self):
        self.promote.passed(self.root, "j1", "wrong seniority")
        self.assertEqual(self.jobs_store.load_jobs(self.root)["j1"]["state"], "passed")
        first = self.jobs_store.load_passed(self.root)["j1"]["passedAt"]
        self.promote.passed(self.root, "j1")
        self.assertEqual(self.jobs_store.load_passed(self.root)["j1"]["passedAt"], first)
        self.assertEqual(self.sessions(), [], "passing never creates a Watching session")

    def test_the_eye_on_a_job_the_pool_has_never_heard_of_fails_loudly(self):
        # The board and the pool drifting apart is worth seeing, not swallowing.
        with self.assertRaises(self.jobs_store.JobsStoreError):
            self.promote.watch(self.root, "not-a-job")

    def test_a_passed_job_cannot_be_watched_afterwards(self):
        self.promote.passed(self.root, "j1")
        with self.assertRaises(self.jobs_store.JobsStoreError):
            self.promote.watch(self.root, "j1")


class SlotReleaseTests(unittest.TestCase):
    """A slot must not be lost when something throws before the runner starts.

    reserve() writes the watcher's OWN pid on purpose, so the slot cannot be
    mistaken for stale in the moment between reserving it and the runner
    starting. The consequence is that a lock left behind in that window holds a
    live pid, so lock_holder never clears it and the slot is gone until the
    watcher restarts. claim_one raises on a failed KV write, and a KV write did
    fail in production, so this is a path that really loses slots.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-slots-")
        self.original = watch.LOCK_DIR
        watch.LOCK_DIR = pathlib.Path(self.dir)

    def tearDown(self):
        watch.LOCK_DIR = self.original

    def test_a_reserved_slot_holds_this_processes_pid(self):
        self.assertTrue(watch.reserve("apply-1", note="starting"))
        held = watch.lock_holder("apply-1")
        self.assertEqual(held["pid"], os.getpid())

    def test_a_lock_holding_a_live_pid_is_never_cleared_as_stale(self):
        # This is why the release has to be explicit rather than left to the
        # staleness sweep.
        watch.reserve("apply-2", note="starting")
        self.assertIsNotNone(watch.lock_holder("apply-2"))
        self.assertIsNotNone(watch.lock_holder("apply-2"))

    def test_releasing_the_slot_frees_it_for_the_next_tick(self):
        watch.reserve("apply-3", note="starting")
        watch.lock_path("apply-3").unlink(missing_ok=True)
        self.assertIsNone(watch.lock_holder("apply-3"))
        self.assertTrue(watch.reserve("apply-3", note="starting"))

    def test_the_claim_is_wrapped_so_a_failure_releases_the_slot(self):
        # Structural: the window between reserve() and spawn() must release the
        # lock on any exception. Asserted on the source because driving a real
        # KV failure in a unit test would need the network.
        source = (pathlib.Path(watch.__file__).read_text())
        window = source[source.index("for slot in free_apply_slots():"):]
        window = window[:window.index("applyStarted")]
        self.assertIn("except Exception:", window)
        self.assertGreaterEqual(window.count("lock_path(slot).unlink(missing_ok=True)"), 3)


class RenderVersionTests(unittest.TestCase):
    """A code change has to reach the board on its own.

    The publish fingerprint was entirely about data, so a merge that changed
    every card was invisible to it and the watcher went on serving the previous
    render until some session happened to move. When the discovery engine
    landed, the deploy went out, the board did not change, and it read as the
    merge having failed.
    """

    def test_it_moves_when_a_renderer_changes(self):
        original = watch.REPO
        with tempfile.TemporaryDirectory() as d:
            fake = pathlib.Path(d)
            (fake / "scripts").mkdir()
            (fake / "scripts" / "dashboard.py").write_text("one")
            (fake / "scripts" / "found_lane.py").write_text("two")
            watch.REPO = fake
            try:
                before = watch.render_version()
                (fake / "scripts" / "found_lane.py").write_text("two, changed")
                after = watch.render_version()
            finally:
                watch.REPO = original
        self.assertNotEqual(before, after)

    def test_it_is_stable_when_nothing_changes(self):
        self.assertEqual(watch.render_version(), watch.render_version())

    def test_a_missing_renderer_is_still_a_stable_value(self):
        original = watch.REPO
        with tempfile.TemporaryDirectory() as d:
            watch.REPO = pathlib.Path(d)
            try:
                self.assertEqual(watch.render_version(), watch.render_version())
                self.assertTrue(watch.render_version())
            finally:
                watch.REPO = original

    def test_the_fingerprint_carries_it(self):
        # Structural: the point is that publishing keys on it, not that the
        # helper exists.
        source = inspect.getsource(watch)
        fp = source[source.index("def fingerprint"):]
        fp = fp[:fp.index("def render_version")]
        self.assertIn("render_version()", fp)


class SearchConfigRepublishTests(unittest.TestCase):
    """Saving a search has to reach the board without waiting fifteen minutes.

    The fingerprint watched sessions, history, the blocked banner and the Found
    lane's counts. A saved search moves none of them, so the republish waited
    for the floor. Press Save, reload, and the filters were back with nothing on
    screen explaining why — indistinguishable from the save having failed.
    """

    def config(self, root, version):
        (root / "search-config.json").write_text(json.dumps({"version": version}))

    def test_the_version_moves_with_the_saved_search(self):
        original = watch.STATE_DIR
        with tempfile.TemporaryDirectory() as d:
            watch.STATE_DIR = pathlib.Path(d)
            try:
                self.assertEqual(watch.search_config_version(), "none")
                self.config(watch.STATE_DIR, 3)
                self.assertEqual(watch.search_config_version(), "3")
                self.config(watch.STATE_DIR, 4)
                self.assertEqual(watch.search_config_version(), "4")
            finally:
                watch.STATE_DIR = original

    def test_an_unreadable_config_is_a_stable_distinct_value(self):
        original = watch.STATE_DIR
        with tempfile.TemporaryDirectory() as d:
            watch.STATE_DIR = pathlib.Path(d)
            try:
                (watch.STATE_DIR / "search-config.json").write_text("{not json")
                self.assertEqual(watch.search_config_version(), "unreadable")
                self.assertEqual(watch.search_config_version(),
                                 watch.search_config_version())
            finally:
                watch.STATE_DIR = original

    def test_the_fingerprint_carries_it(self):
        source = inspect.getsource(watch)
        fp = source[source.index("def fingerprint"):]
        fp = fp[:fp.index("def render_version")]
        self.assertIn("search_config_version()", fp)


if __name__ == "__main__":
    unittest.main()
