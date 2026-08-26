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


class AnswerRecordBundleTest(unittest.TestCase):
    """Records ride the documents bundle, not the board state.

    The state was 19.1 MB against a 20 MB ceiling on 2026-08-25. A record is a
    couple of kilobytes; a hundred of them is not something the state has room
    for, and the documents bundle already writes only what changed.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store_root = pathlib.Path(self.tmp) / "store"
        store(self.store_root, "init")
        self.apps = pathlib.Path(self.tmp) / "applications"
        self.apps.mkdir()
        self._real_apps = publish.answer_record.APPS
        publish.answer_record.APPS = self.apps
        self.addCleanup(setattr, publish.answer_record, "APPS", self._real_apps)

        for aid, company, role in (("done-1", "Acme", "PM"), ("live-1", "Beta", "PM")):
            entry = pathlib.Path(self.tmp) / f"{aid}.json"
            entry.write_text(json.dumps(
                {"applicationId": aid, "company": company, "role": role}))
            store(self.store_root, "session-save", "--id", aid, "--input", str(entry))
        event = pathlib.Path(self.tmp) / "event.json"
        event.write_text(json.dumps({"event": "completed", "applicationId": "done-1",
                                     "company": "Acme", "role": "PM"}))
        store(self.store_root, "history-append", "--input", str(event))

        for aid in ("done-1", "live-1"):
            publish.answer_record.write(aid, [
                {"question": "First name", "answer": "Sahil", "source": "profile",
                 "readBack": "match"},
                {"question": "Verification code", "answer": "884213",
                 "source": "provided", "readBack": "match"},
            ], root=self.store_root)

    def bundle(self):
        return publish.build_documents(str(self.store_root))

    def test_a_submitted_application_s_record_is_published(self):
        docs = self.bundle()
        self.assertIn("answers-done-1.json", docs["docs"])
        self.assertEqual(docs["answerRecords"], 1)
        self.assertGreater(docs["answerRecordBytes"], 0)

    def test_an_application_still_in_flight_publishes_nothing(self):
        # Its form has not been sent. There is nothing on the board to link
        # from, and the answers would be in KV before the submission was.
        self.assertNotIn("answers-live-1.json", self.bundle()["docs"])

    def test_a_withheld_value_is_absent_from_what_reaches_kv(self):
        import base64
        blob = base64.b64decode(self.bundle()["docs"]["answers-done-1.json"]).decode()
        self.assertNotIn("884213", blob)
        self.assertIn("Verification code", blob)

    def test_the_published_record_carries_no_answer_store_keys(self):
        import base64
        blob = base64.b64decode(self.bundle()["docs"]["answers-done-1.json"]).decode()
        self.assertNotIn("sourceKey", blob)


if __name__ == "__main__":
    unittest.main()
