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

    def test_focus_reports_honestly_when_the_browser_is_down(self):
        original = self.browser.running
        self.browser.running = lambda: False
        try:
            result = self.browser.focus("https://jobs.example.com/apply/123")
            self.assertFalse(result["focused"])
            self.assertIn("not running", result["why"])
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
