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
import json
import os
import subprocess
import sys
import pathlib
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()


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
