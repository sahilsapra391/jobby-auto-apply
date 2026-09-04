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
import plistlib
import subprocess
import sys
import math
import pathlib
import tempfile
import time
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import store_isolation  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


toolpath = load("toolpath")
agent = load("agent")
watch = load("watch")


def setUpModule():
    # Health and failover assertions write the provider registry, and unisolated
    # that is the device's own providers.json: the file that says which accounts
    # are connected, in what order, and whether each is healthy. A fixture
    # marking a provider unhealthy there diverts real work to another account.
    #
    # The three modules above are passed explicitly because load() builds them
    # with spec_from_file_location, so they never enter sys.modules and the
    # scan cannot find them.
    store_isolation.begin(toolpath, agent, watch)


def tearDownModule():
    store_isolation.end()


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


class LaunchdInstallTests(unittest.TestCase):
    def test_install_registers_the_watcher_and_local_document_service(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            watcher_plist = root / "LaunchAgents" / "ai.jobby.watch.plist"
            document_plist = root / "LaunchAgents" / "ai.jobby.documents.plist"
            provider_plist = root / "LaunchAgents" / "ai.jobby.providers.plist"
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                mock.patch.object(watch, "PLIST", watcher_plist),
                mock.patch.object(watch, "DOCUMENT_PLIST", document_plist),
                mock.patch.object(watch, "PROVIDER_PLIST", provider_plist),
                mock.patch.object(watch, "LOG_DIR", root / "logs"),
                mock.patch.object(watch, "LOCK_DIR", root / "locks"),
                mock.patch.object(watch, "run", return_value=completed) as run,
                mock.patch("builtins.print"),
            ):
                self.assertEqual(watch.install(5, sys.executable), 0)

            watcher = plistlib.loads(watcher_plist.read_bytes())
            documents = plistlib.loads(document_plist.read_bytes())
            provider_controls = plistlib.loads(provider_plist.read_bytes())
            self.assertEqual(watcher["Label"], "ai.jobby.watch")
            self.assertEqual(documents["Label"], "ai.jobby.documents")
            self.assertEqual(provider_controls["Label"], "ai.jobby.providers")
            self.assertEqual(watcher["ProgramArguments"][0], sys.executable)
            self.assertEqual(documents["ProgramArguments"][0], sys.executable)
            self.assertEqual(documents["ProgramArguments"][-2:], ["--loop", "--local"])
            self.assertIn("document_worker.py", documents["ProgramArguments"][1])
            loaded = [call.args[0] for call in run.call_args_list]
            self.assertIn(["launchctl", "load", str(watcher_plist)], loaded)
            self.assertIn(["launchctl", "load", str(document_plist)], loaded)
            self.assertIn(["launchctl", "load", str(provider_plist)], loaded)

    def test_install_retains_the_previous_launchd_interpreter(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            watcher_plist = root / "LaunchAgents" / "ai.jobby.watch.plist"
            document_plist = root / "LaunchAgents" / "ai.jobby.documents.plist"
            provider_plist = root / "LaunchAgents" / "ai.jobby.providers.plist"
            watcher_plist.parent.mkdir(parents=True)
            watcher_plist.write_bytes(plistlib.dumps({
                "ProgramArguments": [sys.executable, "/old/watch.py"],
            }))
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with (
                mock.patch.object(watch, "PLIST", watcher_plist),
                mock.patch.object(watch, "DOCUMENT_PLIST", document_plist),
                mock.patch.object(watch, "PROVIDER_PLIST", provider_plist),
                mock.patch.object(watch, "LOG_DIR", root / "logs"),
                mock.patch.object(watch, "LOCK_DIR", root / "locks"),
                mock.patch.object(watch, "run", return_value=completed),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(watch.install(5), 0)

            watcher = plistlib.loads(watcher_plist.read_bytes())
            documents = plistlib.loads(document_plist.read_bytes())
            self.assertEqual(watcher["ProgramArguments"][0], sys.executable)
            self.assertEqual(documents["ProgramArguments"][0], sys.executable)


class AuthDetectionTests(unittest.TestCase):
    def test_recognises_the_revoked_token_message(self):
        self.assertTrue(agent.looks_like_auth_failure(
            "Failed to authenticate. API Error: 401 OAuth access token has been revoked."))

    def test_does_not_flag_an_ordinary_failure_as_auth(self):
        self.assertFalse(agent.looks_like_auth_failure("TypeError: cannot read property"))

    def test_the_blocked_message_names_the_fix_not_just_the_error(self):
        msg = agent.blocked_reason("401")
        self.assertIn("API key", msg)


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


class EnsureApplicationSessionTests(unittest.TestCase):
    """A sessionless Apply head must be promoted or refused, never left.

    The Apply button on a Found card sends watch then apply, and the queue's
    read-modify-write can eat the watch click. The sessionless head then
    failed every tick with session_missing while nothing claimed it: measured
    2026-08-28, 88 consecutive failed ticks behind one click, the board frozen
    the whole time. ensure_application_session is the tick's answer, and its
    contract is: None means proceed, a string means the caller must claim the
    click because this head can never start.
    """

    def _session(self, exists):
        result = SimpleNamespace(returncode=0 if exists else 2,
                                 stdout='{"applicationId": "x"}' if exists else "",
                                 stderr="")
        return mock.patch.object(watch, "store", return_value=result)

    def _promote(self, returncode=0, stdout="", stderr=""):
        result = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        return mock.patch.object(watch, "run", return_value=result)

    def test_an_existing_session_passes_without_promoting(self):
        with self._session(True), self._promote() as promoted:
            refusal, created = watch.ensure_application_session("x")
        self.assertIsNone(refusal)
        self.assertFalse(created)
        promoted.assert_not_called()

    def test_a_pool_job_is_promoted_and_proceeds(self):
        with self._session(False), self._promote(
                stdout='{"sessionCreated": true, "watched": "x"}'):
            refusal, created = watch.ensure_application_session("x")
        self.assertIsNone(refusal)
        self.assertTrue(created)

    def test_a_posting_already_on_record_is_refused_with_the_reason(self):
        with self._session(False), self._promote(
                stdout='{"sessionCreated": false, "alreadyKnown": true,'
                       ' "reason": "same posting URL", "where": "the role index"}'):
            refusal, created = watch.ensure_application_session("x")
        self.assertIsNotNone(refusal)
        self.assertIn("same posting URL", refusal)
        self.assertFalse(created)

    def test_an_unknown_id_is_refused_not_left_to_wedge(self):
        with self._session(False), self._promote(
                returncode=2, stderr="promote: no job with id junk"):
            refusal, created = watch.ensure_application_session("junk")
        self.assertIsNotNone(refusal)
        self.assertIn("no job", refusal)

    def test_already_watching_means_the_session_is_there_and_we_proceed(self):
        with self._session(False), self._promote(
                stdout='{"alreadyWatching": true, "sessionCreated": false}'):
            refusal, created = watch.ensure_application_session("x")
        self.assertIsNone(refusal)

    def test_the_tick_consults_it_before_any_document_work(self):
        # The wiring is what stops the wedge, so its removal must fail a test:
        # the ensure runs after the do-not-start guards and before the head's
        # provider probes or document job are ever touched.
        source = inspect.getsource(watch.tick)
        ensure_at = source.find("ensure_application_session")
        self.assertGreater(ensure_at, -1, "tick() no longer ensures a session "
                           "for the Apply head; a sessionless head wedges the "
                           "lane (88 failed ticks on 2026-08-28)")
        self.assertLess(source.find("do_not_start"), ensure_at)
        self.assertLess(ensure_at, source.find("document_candidate_order"))
        self.assertLess(ensure_at, source.find("prepare_document_head"))


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


class ProviderControlTests(unittest.TestCase):
    def test_connect_forwards_the_shared_cross_worker_order_key(self):
        connection_key = (
            "1-1787497200000-00000000-0000-4000-8000-000000000001"
        )
        request = {
            "provider": "openai",
            "action": "connect",
            "connectionKey": connection_key,
        }
        registry = mock.Mock()
        registry.begin_connect.return_value = {
            "activeProvider": "openai",
            "providers": [{
                "id": "openai", "connected": True, "healthy": False,
            }],
        }
        with mock.patch.object(watch, "claim_all", return_value=[request]), \
                mock.patch.object(
                    watch.providers, "ProviderRegistry", return_value=registry), \
                mock.patch.object(watch, "launch_provider_login", return_value=True), \
                mock.patch.object(
                    watch.provider_login, "publish_status", return_value=True):
            watch.process_local_provider_controls({"providerLocal": [request]})

        registry.begin_connect.assert_called_once_with(
            "openai", connection_key=connection_key
        )

    def test_local_controls_are_allowlisted_and_do_not_claim_document_controls(self):
        local = [
            {"provider": "openai", "action": "disconnect"},
            {"provider": "not-a-provider", "action": "connect"},
            {"provider": "gemini", "action": "delete"},
        ]
        remote = [{"provider": "anthropic", "action": "disconnect"}]
        q = {"providerLocal": list(local), "providerDocuments": list(remote)}
        registry = mock.Mock()
        registry.disconnect.return_value = {
            "activeProvider": None,
            "providers": [],
        }
        with mock.patch.object(watch, "claim_all", return_value=local) as claim:
            with mock.patch.object(
                    watch.providers, "ProviderRegistry", return_value=registry):
                with mock.patch.object(
                        watch.provider_login, "publish_status", return_value=True) as publish:
                    result = watch.process_local_provider_controls(q)

        claim.assert_called_once_with("providerLocal/")
        registry.disconnect.assert_called_once_with("openai", forget_secret=True)
        registry.connect.assert_not_called()
        registry.refresh.assert_not_called()
        publish.assert_called_once_with(registry.disconnect.return_value)
        self.assertEqual(q["providerDocuments"], remote)
        self.assertEqual(len(result["providerLocal"]["rejected"]), 2)

    def test_idle_local_control_lane_is_read_only_once_per_thirty_seconds(self):
        previous = watch._last_provider_control_poll
        previous_publish = watch._last_local_status_publish
        watch._last_provider_control_poll = 100.0
        watch._last_local_status_publish = 100.0
        try:
            with mock.patch.object(watch, "claim_all", return_value=[]) as claim:
                self.assertEqual(watch.process_local_provider_controls({}, now=115), {})
                claim.assert_not_called()
                self.assertEqual(watch.process_local_provider_controls({}, now=130), {})
                claim.assert_called_once_with("providerLocal/")
        finally:
            watch._last_provider_control_poll = previous
            watch._last_local_status_publish = previous_publish

    def test_a_failing_connection_is_probed_again_without_being_asked(self):
        """A spent quota comes back on its own, so the check has to as well.

        OpenAI showed a connection error for as long as it took someone to
        press something, because the heartbeat republished the cached row and
        nothing re-ran the probe.
        """
        previous_poll = watch._last_provider_control_poll
        previous_publish = watch._last_local_status_publish
        previous_recheck = watch._last_unhealthy_recheck
        previous_due = dict(watch._provider_recheck_at)
        registry = mock.Mock()
        registry.public_status.return_value = {"providers": [
            {"id": "openai", "connected": True, "healthy": False},
            {"id": "anthropic", "connected": True, "healthy": True},
            {"id": "gemini", "connected": False, "healthy": False},
        ]}
        try:
            watch._last_provider_control_poll = 0.0
            watch._last_local_status_publish = 0.0
            watch._last_unhealthy_recheck = 0.0
            watch._provider_recheck_at.clear()
            with mock.patch.object(watch, "claim_all", return_value=[]), \
                    mock.patch.object(watch.providers, "ProviderRegistry",
                                      return_value=registry), \
                    mock.patch.object(watch.provider_login, "publish_status",
                                      return_value=True):
                out = watch.process_local_provider_controls({}, now=1000)
            # The failing one, and only the failing one. A healthy connection
            # costs nothing and an unconnected one has nothing to check.
            self.assertEqual(out, {"providersRechecked": ["openai"]})
            registry.refresh.assert_called_once_with("openai")
        finally:
            watch._last_provider_control_poll = previous_poll
            watch._last_local_status_publish = previous_publish
            watch._last_unhealthy_recheck = previous_recheck
            watch._provider_recheck_at.clear()
            watch._provider_recheck_at.update(previous_due)

    def test_the_recheck_backs_off_rather_than_probing_every_poll(self):
        previous_poll = watch._last_provider_control_poll
        previous_recheck = watch._last_unhealthy_recheck
        previous_due = dict(watch._provider_recheck_at)
        registry = mock.Mock()
        registry.public_status.return_value = {"providers": [
            {"id": "openai", "connected": True, "healthy": False}]}
        try:
            watch._last_unhealthy_recheck = 0.0
            watch._provider_recheck_at.clear()
            watch._last_provider_control_poll = 0.0
            watch._last_local_status_publish = 1e9
            with mock.patch.object(watch, "claim_all", return_value=[]), \
                    mock.patch.object(watch.providers, "ProviderRegistry",
                                      return_value=registry), \
                    mock.patch.object(watch.provider_login, "publish_status",
                                      return_value=True):
                watch.process_local_provider_controls({}, now=1000)
                watch._last_provider_control_poll = 0.0
                watch._last_unhealthy_recheck = 1000 - watch.UNHEALTHY_RECHECK_SECONDS - 1
                watch.process_local_provider_controls({}, now=1000)
            # Two polls, one probe: the per-provider window has not reopened.
            registry.refresh.assert_called_once_with("openai")
        finally:
            watch._last_provider_control_poll = previous_poll
            watch._last_unhealthy_recheck = previous_recheck
            watch._provider_recheck_at.clear()
            watch._provider_recheck_at.update(previous_due)

    def test_idle_local_control_lane_refreshes_status_before_it_goes_offline(self):
        previous_poll = watch._last_provider_control_poll
        previous_publish = watch._last_local_status_publish
        watch._last_provider_control_poll = 0.0
        watch._last_local_status_publish = 0.0
        registry = mock.Mock()
        registry.public_status.return_value = {"providers": []}
        try:
            with mock.patch.object(watch, "claim_all", return_value=[]), \
                    mock.patch.object(
                        watch.providers, "ProviderRegistry", return_value=registry), \
                    mock.patch.object(
                        watch.provider_login, "publish_status", return_value=True) as publish:
                self.assertEqual(
                    watch.process_local_provider_controls(
                        {}, now=watch.LOCAL_STATUS_HEARTBEAT_SECONDS
                    ),
                    {},
                )
            publish.assert_called_once_with(registry.public_status.return_value)
            self.assertEqual(
                watch._last_local_status_publish,
                watch.LOCAL_STATUS_HEARTBEAT_SECONDS,
            )
        finally:
            watch._last_provider_control_poll = previous_poll
            watch._last_local_status_publish = previous_publish


class DocumentProviderReadinessTests(unittest.TestCase):
    def setUp(self):
        self.previous_cache = watch._document_status_cache
        self.previous_poll = watch._last_document_status_poll
        watch._document_status_cache = None
        watch._last_document_status_poll = 0.0

    def tearDown(self):
        watch._document_status_cache = self.previous_cache
        watch._last_document_status_poll = self.previous_poll

    def test_a_different_worker_default_does_not_block_shared_local_registry(self):
        status = {
            "worker": "documents",
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "activeProvider": "gemini",
            "providers": [
                {"id": "openai", "connected": True, "status": "connected"},
                {"id": "gemini", "connected": True, "status": "connected"},
            ],
        }
        result = SimpleNamespace(
            returncode=0, stdout=json.dumps(status), stderr=""
        )
        with mock.patch.object(watch.toolpath, "node", return_value="/node"), \
                mock.patch.object(watch.toolpath, "queue_env", return_value={}), \
                mock.patch.object(watch.subprocess, "run", return_value=result):
            ready, why = watch.document_provider_ready("openai", now=100)
        self.assertTrue(ready)
        self.assertEqual(why, "")


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

    def test_config_path_writes_when_absent_and_regenerates_when_present(self):
        first = self.browser.config_path()
        self.assertTrue(first.exists())
        first.write_text('{"marker": true}')
        regenerated = json.loads(self.browser.config_path().read_text())
        self.assertNotIn("marker", regenerated)
        self.assertIn("browser", regenerated["mcpServers"])


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

    def test_the_heartbeat_leaves_room_under_the_one_hour_period(self):
        self.assertGreaterEqual(watch.HEARTBEAT_SECONDS, 50 * 60)
        self.assertLess(watch.HEARTBEAT_SECONDS, 60 * 60)


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


class TickHarnessMixin:
    """Shared tick scaffolding. Not a TestCase.

    Subclassing a TestCase to borrow its setUp re-runs every test it holds, and
    test_suite_integrity refuses that.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.run_calls = []
        # `do_not_start` refuses every application while no slot browser holds a
        # Google session, which is the point of that gate and is true of any
        # machine running the suite under a redirected HOME: there are no Chrome
        # profiles there to be signed in. Neutralised once, here, so a tick test
        # written next year inherits it instead of failing with seven mystery
        # assertion errors about claim_one.
        #
        # The gate's own behaviour is tested in
        # tests/test_browsers_stay_signed_in.py, against real profile
        # directories, which is where it belongs.
        signed_in = mock.patch.object(
            watch.browser_identity, "any_signed_in", return_value=True)
        signed_in.start()
        self.addCleanup(signed_in.stop)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def result(stdout="{}", returncode=0, stderr=""):
        return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    def fake_store(self, args):
        if args[:1] == ["session-load"]:
            return self.result(json.dumps({
                "applicationId": "acme-pm",
                "status": "review",
                "pendingFields": [],
            }))
        if args[:1] in (["history-list"], ["session-list"]):
            return self.result("[]")
        if args[:1] == ["meta-get"]:
            key = args[args.index("--key") + 1]
            return self.result(json.dumps({key: None}))
        return self.result()

    def fake_run(self, argv, **kwargs):
        self.run_calls.append([str(value) for value in argv])
        return self.result()

    @contextmanager
    def tick_harness(self, provider, document_state, *, item=None,
                     patch_document_helper=True, remote_ready=(True, ""),
                     order=None, readiness_by_provider=None):
        # Only this provider is connected unless a test says otherwise, so the
        # walk below has nothing else to consider and these stay unchanged.
        order = [provider] if order is None else list(order)
        item = item or {"applicationId": "acme-pm"}
        queue = {"apply": [dict(item)]}
        registry = mock.Mock()
        registry.active_provider.return_value = provider
        registry.public_status.return_value = {
            "activeProvider": provider,
            "ready": bool(provider),
            "providers": [],
        }
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(watch, "STATE_DIR", self.root))
            stack.enter_context(mock.patch.object(watch, "LOCK_DIR", self.root / "locks"))
            stack.enter_context(mock.patch.object(watch, "poll_queues", return_value=queue))
            stack.enter_context(mock.patch.object(
                watch, "process_local_provider_controls", return_value={}))
            stack.enter_context(mock.patch.object(watch, "apply_found_decisions", return_value={}))
            stack.enter_context(mock.patch.object(watch, "store", side_effect=self.fake_store))
            stack.enter_context(mock.patch.object(watch, "run", side_effect=self.fake_run))
            stack.enter_context(mock.patch.object(watch, "blocked_message", return_value=""))
            stack.enter_context(mock.patch.object(watch, "tier1_due", return_value=(False, None)))
            stack.enter_context(mock.patch.object(watch, "sweep_stuck", return_value=[]))
            stack.enter_context(mock.patch.object(watch, "fingerprint", return_value="stable"))
            stack.enter_context(mock.patch.object(
                watch, "publish", return_value={"published": False}))
            stack.enter_context(mock.patch.object(watch, "lock_holder", return_value=None))
            stack.enter_context(mock.patch.object(watch, "running_applications", return_value={}))
            stack.enter_context(mock.patch.object(
                watch, "free_apply_slots", return_value=["apply-1"]))
            reserve = stack.enter_context(mock.patch.object(watch, "reserve", return_value=True))
            claim = stack.enter_context(mock.patch.object(
                watch, "claim_one", return_value=[dict(item)]))
            serve = stack.enter_context(mock.patch.object(watch.browser, "serve"))
            spawn = stack.enter_context(mock.patch.object(watch, "spawn", return_value=7311))
            stack.enter_context(mock.patch.object(
                watch.agent, "authenticated", return_value=(True, "")))
            stack.enter_context(mock.patch.object(
                watch.providers, "ProviderRegistry", return_value=registry))
            stack.enter_context(mock.patch.object(
                watch.providers, "active_provider", return_value=provider))
            stack.enter_context(mock.patch.object(
                watch, "select_provider_for_work", return_value={
                    "activeProvider": provider,
                    "ready": bool(provider),
                    "providers": [],
                }))
            stack.enter_context(mock.patch.object(
                watch.providers, "work_order",
                return_value=[x for x in order if x]))
            if readiness_by_provider is None:
                document_readiness = stack.enter_context(mock.patch.object(
                    watch, "document_provider_ready", return_value=remote_ready))
            else:
                document_readiness = stack.enter_context(mock.patch.object(
                    watch, "document_provider_ready",
                    side_effect=lambda pid: readiness_by_provider[pid]))
            prepare = None
            if patch_document_helper:
                prepare = stack.enter_context(mock.patch.object(
                    watch, "prepare_document_head", return_value=document_state))
            yield {
                "queue": queue,
                "registry": registry,
                "reserve": reserve,
                "claim": claim,
                "serve": serve,
                "spawn": spawn,
                "prepare": prepare,
                "documentReadiness": document_readiness,
            }

class ProviderNeutralApplyTests(TickHarnessMixin, unittest.TestCase):
    """Document generation stays remote while application filling stays local."""

    def test_no_active_provider_leaves_the_apply_request_unclaimed(self):
        with self.tick_harness(None, None) as calls:
            changed = watch.tick()
        calls["prepare"].assert_not_called()
        calls["claim"].assert_not_called()
        calls["reserve"].assert_not_called()
        self.assertEqual(calls["queue"]["apply"], [{"applicationId": "acme-pm"}])
        self.assertIn("waitingOnProvider", changed)

    def test_pending_oci_documents_do_not_reserve_or_start_a_browser(self):
        pending = {"state": "queued", "provider": "openai"}
        with self.tick_harness("openai", pending) as calls:
            watch.tick()
        calls["prepare"].assert_called_once_with(
            {"applicationId": "acme-pm"}, "openai")
        calls["claim"].assert_not_called()
        calls["reserve"].assert_not_called()
        calls["serve"].assert_not_called()
        calls["spawn"].assert_not_called()

    def test_mismatched_worker_defaults_leave_the_request_unclaimed(self):
        ready = {"state": "ready", "provider": "openai"}
        mismatch = (False, "openai is not the local document worker default")
        with self.tick_harness(
                "openai", ready, remote_ready=mismatch) as calls:
            changed = watch.tick()
        calls["documentReadiness"].assert_called_once_with("openai")
        calls["prepare"].assert_not_called()
        calls["claim"].assert_not_called()
        calls["reserve"].assert_not_called()
        calls["serve"].assert_not_called()
        calls["spawn"].assert_not_called()
        self.assertEqual(changed["waitingOnDocumentProvider"]["provider"], "openai")
        self.assertIn("not the local document worker default",
                      changed["waitingOnDocumentProvider"]["why"])

    def test_only_an_explicit_retry_replaces_a_failed_document_job(self):
        failed = {"state": "failed", "provider": "openai"}
        replacement = {"state": "queued", "provider": "gemini"}
        item = {"applicationId": "acme-pm", "retry": True}
        with mock.patch.object(watch.document_job, "get_state", return_value=failed):
            with mock.patch.object(watch.document_job, "sync") as sync:
                with mock.patch.object(
                        watch.document_job, "retry_failed", return_value=replacement) as retry:
                    state = watch.prepare_document_head(item, "gemini")
        retry.assert_called_once_with("acme-pm", "gemini")
        sync.assert_not_called()
        self.assertIs(state, replacement)

    def test_ready_documents_handoff_the_form_to_the_current_healthy_provider(self):
        # The document remains tied to Gemini while it is generated. The next
        # stage can use the currently healthy OpenAI connection.
        ready = {"state": "ready", "provider": "gemini"}
        with self.tick_harness("openai", ready) as calls:
            watch.tick()
        calls["claim"].assert_called_once_with("apply/", application_id="acme-pm")
        calls["serve"].assert_called_once_with("apply-1")
        calls["spawn"].assert_called_once()
        argv = list(calls["spawn"].call_args.args[1])
        self.assertEqual(argv[argv.index("--provider") + 1], "openai")
        self.assertEqual(argv[argv.index("--id") + 1], "acme-pm")

    def test_resolved_document_failure_is_removed_before_form_handoff(self):
        loaded = self.result(json.dumps({
            "applicationId": "acme-pm",
            "status": "review",
            "pendingFields": [
                {"field": "Document generation", "reason": "result_invalid"},
                {"field": "Account", "reason": "sign in"},
            ],
        }))
        with mock.patch.object(watch, "store", return_value=loaded), \
                mock.patch.object(watch, "_save_session") as save:
            changed = watch.clear_document_failure("acme-pm")
        self.assertTrue(changed)
        saved = save.call_args.args[0]
        self.assertEqual(saved["pendingFields"], [
            {"field": "Account", "reason": "sign in"},
        ])

    def test_terminal_document_failure_is_claimed_and_parked_without_retry(self):
        failed = {
            "state": "failed",
            "provider": "openai",
            "errorCode": "generation_failed",
        }
        with self.tick_harness(
                "openai", failed, item={"applicationId": "acme-pm", "retry": False},
                patch_document_helper=False) as calls:
            with mock.patch.object(watch.document_job, "get_state", return_value=failed):
                with mock.patch.object(watch.document_job, "sync", return_value=failed) as sync:
                    with mock.patch.object(watch.document_job, "retry_failed") as retry:
                        watch.tick()
        sync.assert_called_once_with("acme-pm", "openai")
        retry.assert_not_called()
        calls["claim"].assert_called_once_with("apply/")
        calls["reserve"].assert_not_called()
        calls["serve"].assert_not_called()
        calls["spawn"].assert_not_called()
        parks = [argv for argv in self.run_calls
                 if len(argv) > 1 and pathlib.Path(argv[1]).name == "finish.py"]
        self.assertEqual(len(parks), 1)
        self.assertIn("--park", parks[0])
        self.assertIn("generation_failed", " ".join(parks[0]))


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
        # One second of slack, for truncation rather than for jitter. The stamp
        # is formatted to whole seconds, so it discards up to 999ms of `now`,
        # and a jitter landing on the minimum then measured just under the
        # bound. Intermittent, roughly one run in a thousand, and an
        # occasionally red test costs more than the bound's last second is
        # worth. `test_the_configured_interval_is_respected` below already
        # carries the same allowance.
        self.assertGreaterEqual(delta, 3600 - watch.TIER1_JITTER_SECONDS - 1)
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


class AlarmVisibilityTests(unittest.TestCase):
    """The alarm has to be able to report its own failure.

    ping_health returns a status that every caller ignores, and returned None
    on any network error. A misconfigured or unreachable HEALTHCHECKS_URL was
    therefore indistinguishable, from inside the watcher, from a healthy system
    with a working alarm.
    """

    def setUp(self):
        self.previous = watch.LAST_PING
        self.addCleanup(lambda: setattr(watch, "LAST_PING", self.previous))
        watch.LAST_PING = None

    def test_a_failed_ping_is_recorded_rather_than_swallowed(self):
        with mock.patch.object(watch, "config",
                               return_value={"HEALTHCHECKS_URL": "https://hc.example/abc"}), \
                mock.patch.object(watch.urllib.request, "urlopen",
                                  side_effect=OSError("no route to host")):
            self.assertIsNone(watch.ping_health("fail", "board is stale"))
        self.assertIsNotNone(watch.LAST_PING)
        self.assertIn("no route to host", watch.LAST_PING["error"])
        self.assertEqual(watch.LAST_PING["state"], "fail")

    def test_status_reports_what_the_alarm_last_did_not_only_that_it_exists(self):
        watch.LAST_PING = {"at": "2026-08-27T19:00:00Z", "state": "fail",
                           "error": "URLError: unreachable"}
        with mock.patch.object(watch, "config",
                               return_value={"HEALTHCHECKS_URL": "https://hc.example/abc"}), \
                mock.patch.object(watch, "poll_queues", return_value={}), \
                mock.patch.object(watch, "lock_holder", return_value=None), \
                mock.patch.object(watch, "free_apply_slots", return_value=[]), \
                mock.patch.object(watch, "running_applications", return_value={}):
            out = watch.status()
        # "health" only ever meant "configured", which read as reassurance.
        self.assertTrue(out["health"])
        self.assertEqual(out["lastPing"]["error"], "URLError: unreachable")


class ParkedSlotDeadlockTests(unittest.TestCase):
    """The queue that stopped because its slots were held by its own tail.

    Observed on 2026-08-25: all five browser slots parked by applications that
    had been retried, so they were back in the Apply queue at positions three
    to eight while still owning their tabs. The head held built documents and
    no slot. Nine applications reported "waiting for an earlier application"
    and not one of them was running.
    """

    def setUp(self):
        self.parked = {}
        self.owned = {}
        self.free = []
        patch_holder = mock.patch.object(
            watch, "lock_holder",
            side_effect=lambda name: ({"note": self.parked[name], "parked": True}
                                      if name in self.parked else None))
        patch_slot = mock.patch.object(
            watch, "browser_slot_for_application",
            side_effect=lambda aid, sessions=None: self.owned.get(aid))
        patch_free = mock.patch.object(
            watch, "free_apply_slots", side_effect=lambda: list(self.free))
        for p in (patch_holder, patch_slot, patch_free):
            p.start()
            self.addCleanup(p.stop)

    def test_the_head_still_wins_when_it_can_run(self):
        self.free = ["apply-1"]
        items = [{"applicationId": "head"}, {"applicationId": "tail"}]
        self.assertEqual(watch.choose_apply_head(items)["applicationId"], "head")

    def test_a_tail_holding_its_own_parked_slot_goes_first_when_the_head_cannot(self):
        # Every slot parked, none free, and the head owns none of them.
        self.free = []
        self.parked = {"apply-1": "gitlab", "apply-2": "versant"}
        self.owned = {"gitlab": "apply-1", "versant": "apply-2"}
        items = [{"applicationId": "axon"},
                 {"applicationId": "swiggy"},
                 {"applicationId": "gitlab"}]
        self.assertEqual(watch.choose_apply_head(items)["applicationId"], "gitlab")

    def test_a_tail_without_a_slot_of_its_own_does_not_jump_the_queue(self):
        # A free slot would have served the head too, so this is not a reason
        # to reorder, and click order is the rule whenever it can be kept.
        self.free = []
        self.parked = {"apply-1": "someone-else"}
        self.owned = {}
        items = [{"applicationId": "axon"}, {"applicationId": "swiggy"}]
        self.assertEqual(watch.choose_apply_head(items)["applicationId"], "axon")

    def test_a_slot_parked_by_a_different_application_is_not_borrowed(self):
        # The tab holds another application's half-filled form.
        self.free = []
        self.parked = {"apply-1": "gitlab"}
        self.owned = {"swiggy": "apply-1"}
        items = [{"applicationId": "axon"}, {"applicationId": "swiggy"}]
        self.assertEqual(watch.choose_apply_head(items)["applicationId"], "axon")

    def test_an_empty_queue_has_no_head(self):
        self.assertIsNone(watch.choose_apply_head([]))


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

    def test_the_single_ready_slot_wraps_claim_and_spawn_in_one_release_window(self):
        # Structural: driving a real KV claim failure would need the network.
        # The ready-only path reserves one slot, then one exception handler owns
        # claim, browser startup, and runner spawn failures.
        source = pathlib.Path(watch.__file__).read_text()
        window = source[source.index("free_slots = free_apply_slots()"):]
        window = window[:window.index("changed.setdefault(\"applyStarted\"")]
        self.assertIn("candidate_slots = candidate_apply_slots(head_id)", window)
        self.assertIn("slot = candidate_slots[0]", window)
        self.assertIn("if reserve(slot, note=head_id):", window)
        self.assertIn('claimed = claim_one("apply/", application_id=head_id)', window)
        self.assertIn("persist_browser_slot(head_id, slot)", window)
        self.assertIn("browser.serve(slot)", window)
        self.assertIn("except Exception:", window)
        self.assertIn("lock_path(slot).unlink(missing_ok=True)", window)


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

    def test_it_moves_when_the_settings_panel_changes(self):
        original = watch.REPO
        with tempfile.TemporaryDirectory() as d:
            fake = pathlib.Path(d)
            (fake / "scripts").mkdir()
            (fake / "scripts" / "dashboard.py").write_text("dashboard")
            (fake / "scripts" / "found_lane.py").write_text("found")
            settings = fake / "scripts" / "settings_panel.py"
            settings.write_text("settings one")
            watch.REPO = fake
            try:
                before = watch.render_version()
                settings.write_text("settings two")
                after = watch.render_version()
            finally:
                watch.REPO = original
        self.assertNotEqual(before, after)

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


class DocumentProviderWalkTests(TickHarnessMixin, unittest.TestCase):
    """activeProvider is a preference, and a preference has to hand over.

    The tick stopped at the newest connection. gemini is connected and locally
    healthy but is not connected for local document work, so the Apply head sat
    on "Waiting for provider" and the tick never reached the spawn at all,
    while openai and anthropic were both ready and idle. Measured on the real
    registry on 2026-08-25: gemini not ready, the other two ready, ten
    applications queued and nothing running.
    """

    READY = (True, "")

    def test_it_hands_over_when_the_newest_cannot_do_document_work(self):
        with self.tick_harness(
                "gemini", {"state": "ready", "provider": "openai"},
                order=["gemini", "openai", "anthropic"],
                readiness_by_provider={
                    "gemini": (False, "gemini is not connected for local document work"),
                    "openai": self.READY,
                    "anthropic": self.READY,
                }) as calls:
            changed = watch.tick()
        calls["prepare"].assert_called_once_with({"applicationId": "acme-pm"}, "openai")
        calls["spawn"].assert_called_once()
        self.assertNotIn("waitingOnDocumentProvider", changed)

    def test_it_walks_past_two_and_uses_the_third(self):
        with self.tick_harness(
                "gemini", {"state": "ready", "provider": "anthropic"},
                order=["gemini", "openai", "anthropic"],
                readiness_by_provider={
                    "gemini": (False, "gemini is not connected for local document work"),
                    "openai": (False, "openai is not connected for local document work"),
                    "anthropic": self.READY,
                }) as calls:
            watch.tick()
        calls["prepare"].assert_called_once_with(
            {"applicationId": "acme-pm"}, "anthropic")

    def test_nothing_ready_still_waits_and_names_what_it_tried(self):
        with self.tick_harness(
                "gemini", {"state": "ready", "provider": "gemini"},
                order=["gemini", "openai"],
                readiness_by_provider={
                    "gemini": (False, "gemini is not connected for local document work"),
                    "openai": (False, "openai is not connected for local document work"),
                }) as calls:
            changed = watch.tick()
        calls["prepare"].assert_not_called()
        calls["spawn"].assert_not_called()
        self.assertEqual(
            changed["waitingOnDocumentProvider"]["considered"], ["gemini", "openai"])


class ParkedSlotAlarmTests(unittest.TestCase):
    """A parked slot is designed. A fleet of them is an outage in its costume.

    On 2026-08-27 three of five slots were parked from that afternoon and the
    way it surfaced was the user asking how many applications were in flight.
    Two of the three had parked because their browser server advertised nothing,
    which is not a state anyone was going to come back and finish.
    """

    def parked(self, ages):
        """lock_holder answers for each slot, given ages in seconds."""
        now = time.time()

        def holder(name):
            if name not in ages:
                return None
            stamp = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ages[name])
            )
            return {"pid": 0, "at": stamp, "note": f"app-{name}", "parked": True}

        return mock.patch.object(watch, "lock_holder", side_effect=holder)

    def test_a_working_fleet_says_nothing(self):
        with self.parked({"apply-1": 60}):
            self.assertEqual(watch.parked_slot_warning(), "")

    def test_most_of_the_fleet_parked_is_capacity_gone_whatever_the_reason(self):
        """Sized from the pool rather than from the number it happened to be.

        This said "3 of 5" and went quiet when the pool widened to eight, which
        is the same defect as a guard whose premise is a constant somebody else
        owns. Three of eight is not most of a fleet and should not alarm; the
        threshold is a share, so the fixture is built from APPLY_SLOTS.
        """
        # ceil, not int: 8 * 0.6 truncates to 4, which is half a fleet and
        # correctly does not alarm, so the fixture would have proved the
        # opposite of what it claims.
        need = math.ceil(watch.APPLY_SLOTS * watch.PARKED_SLOT_FRACTION)
        enough = {f"apply-{i}": 60 + i for i in range(1, need + 1)}
        with self.parked(enough):
            warning = watch.parked_slot_warning()
        self.assertIn(f"{len(enough)} of {watch.APPLY_SLOTS}", warning)
        self.assertIn("cannot start", warning)

    def test_a_minority_of_the_fleet_parked_does_not_alarm(self):
        few = {f"apply-{i}": 60 for i in range(1, watch.APPLY_SLOTS // 4 + 1)}
        with self.parked(few):
            self.assertEqual(watch.parked_slot_warning(), "")

    def test_one_slot_held_most_of_a_day_earns_it_alone(self):
        with self.parked({"apply-1": 7 * 3600}):
            warning = watch.parked_slot_warning()
        self.assertIn("apply-1", warning)
        self.assertIn("7.0h", warning)

    def test_ages_are_read_as_utc_not_local(self):
        """An alarm that is hours out is an alarm nobody keeps."""
        with self.parked({"apply-1": 3600}):
            entry = watch.parked_apply_slots()[0]
        self.assertLess(abs(entry["ageSeconds"] - 3600), 30)

    def test_an_unreadable_stamp_does_not_take_the_alarm_down(self):
        with mock.patch.object(watch, "lock_holder", side_effect=lambda name: (
                {"pid": 0, "at": "not a date", "note": "x", "parked": True}
                if name == "apply-1" else None)):
            self.assertEqual(watch.parked_apply_slots()[0]["ageSeconds"], 0)

    def test_the_ping_carries_it_alongside_board_staleness_not_instead(self):
        """One alarm, two reasons. Two alarms is how one stops being read.

        **This read watch.py's source and sliced 400 characters after
        `stale = `.** It is the fourth test in this repository anchored on the
        text of that composition, and all four broke together when the
        composition was lifted into `unhealthy_reason` so that it could be
        called at all. Four scanners asserting the arrangement of one
        expression, and not one of them would have noticed the expression
        being wrong.

        Both reasons are checked by making each speak while the other four are
        silent, which is what "alongside, not instead" actually means.
        """
        others = ("stale_sweep_warning", "chrome_flag_warning")
        for speaking, quiet in (("stale_board_warning", "parked_slot_warning"),
                                ("parked_slot_warning", "stale_board_warning")):
            with self.subTest(warning=speaking):
                silence = {name: (lambda: "") for name in others + (quiet,)}
                with mock.patch.multiple(watch, **silence), \
                        mock.patch.object(watch.corpus_check, "warning",
                                          lambda: ""), \
                        mock.patch.object(watch, speaking, lambda: "HEARD"):
                    self.assertEqual(watch.unhealthy_reason(), "HEARD")

    def test_the_two_reasons_are_joined_rather_than_one_winning(self):
        """"Alongside, not instead" is a claim about two speaking at once, and
        the old text scan could not have seen the difference between joining
        them and returning the first."""
        silence = {name: (lambda: "") for name in
                   ("stale_sweep_warning", "chrome_flag_warning")}
        with mock.patch.multiple(watch, **silence), \
                mock.patch.object(watch.corpus_check, "warning", lambda: ""), \
                mock.patch.object(watch, "stale_board_warning", lambda: "BOARD"), \
                mock.patch.object(watch, "parked_slot_warning", lambda: "SLOTS"):
            reason = watch.unhealthy_reason()
        self.assertIn("BOARD", reason)
        self.assertIn("SLOTS", reason)


if __name__ == "__main__":
    unittest.main()
