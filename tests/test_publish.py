"""Tests for scripts/publish.py, the Vercel hosting path.

The thing worth guarding is the inlining step: the board is stuffed into a JS
module as a string literal, and the board contains scraped text. If that
escaping is wrong the deployment breaks, or worse, page content escapes the
string and becomes code.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import inspect
import json
import sys
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish", ROOT / "scripts" / "publish.py")
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)

STORE = ROOT / "scripts" / "store.py"


def store(root, *args, stdin=None):
    return subprocess.run(
        ["python3", str(STORE), "--root", str(root), *args],
        input=stdin, text=True, capture_output=True, check=True,
    )


class InliningTest(unittest.TestCase):
    """The generated module is built from a store seeded with hostile content."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store_root = pathlib.Path(cls.tmp) / "store"
        store(cls.store_root, "init")

        session = {
            "company": "</script><script>alert(1)</script>",
            "role": "PM — Agents éà中文 \U0001f600",
            "status": "active",
            "url": "javascript:alert(1)",
            "pendingFields": [{"question": "Backtick ` and ${expr} and \\ backslash",
                               "reason": "line separator"}],
        }
        payload = pathlib.Path(cls.tmp) / "s.json"
        payload.write_text(json.dumps(session), encoding="utf-8")
        store(cls.store_root, "session-save", "--id", "hostile", "--input", str(payload))

        cls.summary = publish.build_dashboard(str(cls.store_root))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def html(self):
        return self.summary["html"]

    def test_the_board_is_returned_rather_than_written_into_the_deployment(self):
        # It used to be generated into deploy/api/_dashboard.js and shipped with
        # the code. That meant a full redeploy for every board change against a
        # hundred-a-day limit, and a build from GitHub had no board at all
        # because the file was untracked. It goes to KV now.
        self.assertIn("html", self.summary)
        self.assertFalse((publish.DEPLOY / "api" / "_dashboard.js").exists(),
                         "publishing must not leave a generated module behind")

    def test_html_round_trips_exactly(self):
        html = self.html()
        self.assertIn("<title>Jobby is a free agent!</title>", html)
        self.assertIn('<link rel="icon" href="data:image/svg+xml;base64,', html)
        # The doctype has to lead. Without it the browser drops into quirks
        # mode, where width=device-width is ignored and a phone lays the board
        # out at 980px.
        lead = html.lstrip()
        self.assertTrue(lead.startswith("<!doctype html>"), lead[:60])
        self.assertIn('<meta charset="utf-8">', lead[:80])

    def test_non_ascii_survives(self):
        html = self.html()
        for ch in ("\u2014", "\u00e9", "\u4e2d\u6587", "\U0001f600"):
            self.assertIn(ch, html)

    def test_the_page_survives_the_json_round_trip_it_takes_to_reach_kv(self):
        # The board reaches KV as a JSON value and comes back parsed. Hostile
        # page content has to survive that byte for byte, including the
        # separators that are legal in JSON but not in JavaScript source.
        html = self.html()
        self.assertEqual(json.loads(json.dumps({"html": html}))["html"], html)

    def test_hostile_page_content_is_carried_as_data(self):
        html = self.html()
        self.assertIn("Backtick", html)
        self.assertIn("${expr}", html)
        self.assertIn("\u2028", html)
        # Escaped when serialised, so it cannot terminate anything it lands in.
        self.assertIn("\\u2028", json.dumps(html))

    def test_scraped_javascript_url_did_not_survive_into_the_page(self):
        self.assertNotIn("javascript:", self.html())

    def test_summary_reports_the_board_contents(self):
        self.assertEqual(self.summary["blocked"], 1)
        self.assertIn("generatedAt", self.summary)


class NodeReadsTheBoardTest(unittest.TestCase):
    """Node is what serves this on Vercel, so let Node judge that it survives.

    The board reaches the function as a JSON value out of KV. Page text is
    scraped from postings, so it can contain anything: closing script tags,
    backticks, ${...}, and the line separators that are legal in JSON but not in
    JavaScript source.
    """

    def setUp(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")

    def test_node_recovers_the_page_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            store_root = tmp / "store"
            store(store_root, "init")
            session = {"company": "Ren\u00e9e </script> & Cie",
                       "role": "PM \u2014 AI \u2028\u2029 ${x} `tick`",
                       "status": "active", "pendingFields": []}
            payload = tmp / "s.json"
            payload.write_text(json.dumps(session), encoding="utf-8")
            store(store_root, "session-save", "--id", "hostile", "--input", str(payload))

            html = publish.build_dashboard(str(store_root))["html"]
            blob = tmp / "board.json"
            blob.write_text(json.dumps({"html": html, "generatedAt": "now"}),
                            encoding="utf-8")
            script = tmp / "read.mjs"
            script.write_text(
                "import { readFileSync } from 'node:fs';\n"
                f"const board = JSON.parse(readFileSync({json.dumps(str(blob))}, 'utf8'));\n"
                "process.stdout.write(board.html);\n",
                encoding="utf-8")
            proc = subprocess.run(["node", str(script)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, html, "the page must survive Node exactly")
            self.assertIn("</script>", proc.stdout)
            self.assertIn("\u2028", proc.stdout)

class CliTest(unittest.TestCase):
    def test_missing_vercel_and_npx_is_a_clear_error(self):
        original = publish.toolpath.find
        publish.toolpath.find = lambda name, required=True: None
        try:
            with self.assertRaises(publish.PublishError) as ctx:
                publish.vercel_cmd()
            self.assertIn("npm install -g vercel", str(ctx.exception))
        finally:
            publish.toolpath.find = original

    def test_falls_back_to_npx_when_vercel_is_not_installed(self):
        # The seam is toolpath, not shutil: every binary is resolved to an
        # absolute path because launchd does not put /opt/homebrew/bin on PATH.
        original = publish.toolpath.find
        publish.toolpath.find = lambda name, required=True: (
            None if name == "vercel" else "/opt/homebrew/bin/npx")
        try:
            cmd = publish.vercel_cmd()
            self.assertEqual(cmd[0], "/opt/homebrew/bin/npx")
            self.assertEqual(cmd[1], "--yes")
        finally:
            publish.toolpath.find = original


class ProviderPayloadTests(unittest.TestCase):
    def test_raw_registry_is_sanitized_for_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "providers.json").write_text(json.dumps({
                "version": 1,
                "nextConnectionOrder": 3,
                "connections": {
                    "gemini": {
                        "connected": True,
                        "connectionOrder": 2,
                        "health": "authentication_required",
                        "lastCheckedAt": "2026-08-23T15:00:00Z",
                        "secret": "must-not-leak",
                    },
                    "openai": {
                        "connected": True,
                        "connectionOrder": 1,
                        "health": "healthy",
                    },
                },
            }), encoding="utf-8")

            payload = publish.provider_payload(str(root))

        self.assertEqual(payload["activeProvider"], "openai")
        rows = {item["id"]: item for item in payload["providers"]}
        self.assertEqual(rows["openai"]["status"], "connected")
        self.assertEqual(rows["gemini"]["status"], "needs_auth")
        self.assertNotIn("secret", json.dumps(payload))

    def test_missing_registry_yields_three_disconnected_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = publish.provider_payload(tmp)
        self.assertIsNone(payload["activeProvider"])
        self.assertEqual(len(payload["providers"]), 3)
        self.assertTrue(all(item["status"] == "disconnected"
                            for item in payload["providers"]))

class DegradedRenderGuardTests(unittest.TestCase):
    """The board must never lose data the store still has.

    Three applications running at once meant three processes publishing, and a
    render that caught the store mid-write produced a board with no profile name
    and an empty Submitted tab. It overwrote a good board, and thirty-four
    submitted applications appeared to vanish while every one was still on disk.
    """

    def test_a_render_with_fewer_submitted_than_the_store_is_refused(self):
        original = publish.expected_counts
        publish.expected_counts = lambda root: {"submitted": 34, "hasProfile": True}
        try:
            with self.assertRaises(publish.PublishError) as ctx:
                publish.guard_render({"submitted": 0, "greeting": "Sahil"}, None)
            self.assertIn("mid-write", str(ctx.exception))
        finally:
            publish.expected_counts = original

    def test_a_render_with_no_profile_name_is_refused(self):
        original = publish.expected_counts
        publish.expected_counts = lambda root: {"submitted": 34, "hasProfile": True}
        try:
            with self.assertRaises(publish.PublishError) as ctx:
                publish.guard_render({"submitted": 34, "greeting": "there"}, None)
            self.assertIn("no profile name", str(ctx.exception))
        finally:
            publish.expected_counts = original

    def test_a_healthy_render_passes(self):
        original = publish.expected_counts
        publish.expected_counts = lambda root: {"submitted": 34, "hasProfile": True}
        try:
            publish.guard_render({"submitted": 34, "greeting": "Sahil"}, None)
        finally:
            publish.expected_counts = original

    def test_more_submitted_than_expected_is_fine(self):
        # An application completing between the two reads is normal, not a fault.
        original = publish.expected_counts
        publish.expected_counts = lambda root: {"submitted": 34, "hasProfile": True}
        try:
            publish.guard_render({"submitted": 35, "greeting": "Sahil"}, None)
        finally:
            publish.expected_counts = original


class RedirectedStoreTests(unittest.TestCase):
    """A store that is not the real one must never reach the real board.

    The KV namespace comes from deploy/.env.local, not from the store, so a
    board rendered from anywhere still lands on the live one. guard_render does
    not help: it compares the render against the same redirected store, so an
    empty render of an empty store agrees with itself.

    These drive publish.py as a subprocess on purpose. The first version of this
    guard was covered by a test that monkeypatched the call and passed green
    while the real path published twice.
    """

    def run_publish(self, extra, env_root):
        env = dict(os.environ, JOB_APPLICATION_DIR=str(env_root))
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "publish.py")] + extra,
            capture_output=True, text=True, env=env)

    def test_a_redirected_store_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "sessions").mkdir()
            out = json.loads(self.run_publish([], d).stdout)
            self.assertFalse(out["published"])
            self.assertIn("refusing to publish", out["error"])

    def test_passing_root_does_not_unlock_it(self):
        # The escape hatch is the bug: it was tested by checking the refusal,
        # then the allowed branch wrote a 0-submitted board over the live one.
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "sessions").mkdir()
            out = json.loads(self.run_publish(["--root", d], d).stdout)
            self.assertFalse(out["published"])

    def test_it_never_reaches_the_deploy_step(self):
        # Nothing should be rendered or pushed, so no board size is reported.
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "sessions").mkdir()
            r = self.run_publish([], d)
            self.assertNotIn("written to KV", r.stdout)
            self.assertNotIn("Board updated", r.stdout)


class DryRunTests(unittest.TestCase):
    """The hatch that lets a test store exercise publishing without reaching KV.

    There is deliberately no --root override: the board is served from one KV
    namespace fixed in deploy/.env.local, so any store that publishes for real
    overwrites the live board. The only safe hatch is one that stops short.
    """

    def run_publish(self, extra, env_root):
        env = dict(os.environ, JOB_APPLICATION_DIR=str(env_root))
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "publish.py")] + extra,
            capture_output=True, text=True, env=env)

    def test_a_dry_run_from_a_temp_store_renders_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "sessions").mkdir()
            r = self.run_publish(["--dry-run"], d)
            self.assertEqual(r.returncode, 0, r.stderr)
            body = json.loads(r.stdout[r.stdout.index("{"):])
            self.assertFalse(body["published"])
            self.assertTrue(body["dryRun"])
            self.assertGreater(body["wouldWriteBytes"], 0)
            self.assertNotIn("written to KV", r.stdout)
            self.assertNotIn("Board updated", r.stdout)

    def test_without_the_flag_a_temp_store_is_still_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "sessions").mkdir()
            body = json.loads(self.run_publish([], d).stdout)
            self.assertFalse(body["published"])
            self.assertIn("refusing to publish", body["error"])


class RendererSyncOnPublishTests(unittest.TestCase):
    """A publish can never write state the deployed renderer was not built for.

    deploy/renderer is a copy Vercel builds from git, so a change to
    scripts/found_lane.py that nobody synced deploys the old interface silently
    while every local test passes. Three commits of UI work reached the repo and
    never reached the board exactly that way.
    """

    def test_publish_syncs_before_it_builds(self):
        source = inspect.getsource(publish.cmd_publish)
        self.assertIn("sync_renderer()", source)
        self.assertLess(source.index("sync_renderer()"),
                        source.index("build_dashboard"))

    def test_it_reports_what_moved_rather_than_silently_fixing_it(self):
        # A script cannot commit on someone's behalf, and Vercel builds from
        # git, so saying nothing would leave the deployment stale anyway.
        source = inspect.getsource(publish.cmd_publish)
        self.assertIn("Commit deploy/renderer/", source)

    def test_a_clean_tree_reports_nothing_moved(self):
        self.assertEqual(publish.sync_renderer(), [])

    def test_a_changed_module_is_detected(self):
        target = ROOT / "deploy" / "renderer" / "found_lane.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# drift\n")
            self.assertIn("found_lane.py", publish.sync_renderer())
        finally:
            target.write_bytes(original)
            publish.sync_renderer()


class ArtifactUploadTests(unittest.TestCase):
    """Publishing puts the artifacts in R2 first, and stops if it cannot.

    A board that published successfully while its documents did not arrive is a
    board full of links to nothing, and "reports success while doing nothing" is
    the failure mode this project's whole health apparatus exists because of.
    """

    class Args:
        root = None
        deploy = False
        preview = False
        dry_run = False

    def setUp(self):
        self.calls = []
        self.original = {
            "sync_renderer": publish.sync_renderer,
            "build_dashboard": publish.build_dashboard,
            "build_documents": publish.build_documents,
            "guard_render": publish.guard_render,
            "push_content": publish.push_content,
            "provider_payload": publish.provider_payload,
            "configured": publish.r2.configured,
            "r2_sync": publish.r2_sync.sync,
            "current_url": publish.current_url,
        }
        publish.sync_renderer = lambda: []
        publish.build_dashboard = lambda root: {
            "blocked": 0, "inflight": 0, "submitted": 3,
            "html": "<html></html>", "state": {"jobs": {}}, "greeting": "Sahil"}
        publish.build_documents = lambda root: {
            "documents": 2, "bytes": 10, "docs": {"A.pdf": "eA=="}}
        publish.guard_render = lambda summary, root: None
        publish.provider_payload = lambda root=None: {}
        publish.current_url = lambda root: "https://board.example"
        publish.push_content = lambda *a, **k: (
            self.calls.append(("push_content", a, k)) or {
                "state": {"bytes": 1024},
                "documents": {"written": 0, "skipped": 0},
                "docIndex": {"entries": 0}})

    def tearDown(self):
        publish.sync_renderer = self.original["sync_renderer"]
        publish.build_dashboard = self.original["build_dashboard"]
        publish.build_documents = self.original["build_documents"]
        publish.guard_render = self.original["guard_render"]
        publish.push_content = self.original["push_content"]
        publish.provider_payload = self.original["provider_payload"]
        publish.r2.configured = self.original["configured"]
        publish.r2_sync.sync = self.original["r2_sync"]
        publish.current_url = self.original["current_url"]

    def test_an_upload_failure_fails_the_publish_and_writes_nothing_to_kv(self):
        publish.r2.configured = lambda env=None: True

        def explode(*a, **k):
            raise publish.r2.R2Error("R2 PUT u/account-1/app/a/resume.pdf failed: 403")

        publish.r2_sync.sync = explode
        with self.assertRaises(publish.PublishError) as caught:
            publish.cmd_publish(self.Args(), [])
        message = str(caught.exception)
        self.assertIn("refusing to publish", message)
        self.assertIn("still on this machine", message)
        self.assertEqual(self.calls, [])

    def test_a_successful_upload_sends_the_index_and_no_document_bundle(self):
        publish.r2.configured = lambda env=None: True
        publish.r2_sync.sync = lambda root, apply=False, **k: {
            "objects": 621, "bytes": 31137308, "uploaded": 4,
            "matchedRemote": 0, "skippedByManifest": 617,
            "index": {"A.pdf": {"key": "u/account-1/app/a/resume.pdf"}}}
        self.assertEqual(publish.cmd_publish(self.Args(), []), 0)
        (_name, args, _kw) = self.calls[0]
        _html, _state, bundle, _providers, doc_index = args
        # The documents no longer travel through KV; only the filename index does.
        self.assertEqual(bundle, {})
        self.assertEqual(doc_index["A.pdf"]["key"], "u/account-1/app/a/resume.pdf")

    def test_without_r2_the_old_kv_document_path_still_works(self):
        publish.r2.configured = lambda env=None: False
        self.assertEqual(publish.cmd_publish(self.Args(), []), 0)
        (_name, args, _kw) = self.calls[0]
        _html, _state, bundle, _providers, doc_index = args
        self.assertEqual(bundle, {"A.pdf": "eA=="})
        self.assertIsNone(doc_index)

if __name__ == "__main__":
    unittest.main()
