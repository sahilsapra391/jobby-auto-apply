"""Tests for scripts/publish.py, the Vercel hosting path.

The thing worth guarding is the inlining step: the board is stuffed into a JS
module as a string literal, and the board contains scraped text. If that
escaping is wrong the deployment breaks, or worse, page content escapes the
string and becomes code.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
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

        # Redirect the generated module into the temp dir so a test run never
        # overwrites a real publish.
        cls.generated = pathlib.Path(cls.tmp) / "_dashboard.js"
        cls.original = publish.GENERATED
        publish.GENERATED = cls.generated
        cls.summary = publish.build_dashboard(str(cls.store_root))
        cls.text = cls.generated.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        publish.GENERATED = cls.original
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def html(self):
        literal = self.text.split("export const HTML = ", 1)[1].rsplit(";\n", 2)[0]
        return json.loads(literal)

    def test_generated_module_is_owner_readable_only(self):
        self.assertEqual(os.stat(self.generated).st_mode & 0o777, 0o600)

    def test_html_round_trips_exactly(self):
        html = self.html()
        self.assertIn("<title>Jobby is a free agent!</title>", html)
        self.assertIn('<link rel="icon" href="data:image/svg+xml;base64,', html)
        self.assertTrue(html.lstrip().startswith('<meta charset="utf-8">'))

    def test_non_ascii_survives_inlining(self):
        html = self.html()
        for ch in ("—", "é", "中文", "\U0001f600"):
            self.assertIn(ch, html)

    def test_literal_is_pure_ascii_so_encoding_cannot_corrupt_it(self):
        # json.dumps escapes everything above ASCII, which means the module
        # reads the same whatever encoding the deploy toolchain assumes.
        self.text.encode("ascii")

    def test_literal_is_double_quoted_not_a_template(self):
        # A backtick-delimited literal would interpolate ${...} out of scraped
        # page text and would end early on a backtick. A JSON double-quoted
        # string does neither. This is the assertion that stops someone
        # "simplifying" the generator into a template literal.
        self.assertIn('export const HTML = "', self.text)
        self.assertNotIn("export const HTML = `", self.text)

    def test_hostile_page_content_stays_inert_inside_the_literal(self):
        html = self.html()
        # All present in the recovered HTML, none of them able to act: the
        # module is JS, so </script> is just text, and ${...} and the backtick
        # are inert in a double-quoted string.
        self.assertIn("Backtick", html)
        self.assertIn("${expr}", html)
        self.assertIn("\u2028", html)
        # U+2028 and the backslash are escaped in the literal, not raw.
        self.assertIn("\\u2028", self.text)

    def test_scraped_javascript_url_did_not_survive_into_the_page(self):
        self.assertNotIn("javascript:", self.html())

    def test_summary_reports_the_board_contents(self):
        self.assertEqual(self.summary["blocked"], 1)
        self.assertIn("generatedAt", self.summary)


class NodeParsesGeneratedModuleTest(unittest.TestCase):
    """Node is what actually imports this file on Vercel, so let Node judge."""

    def setUp(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")

    def test_node_imports_the_module_and_recovers_the_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            store_root = tmp / "store"
            store(store_root, "init")

            session = {"company": "Renée </script> & Cie", "role": "PM — AI",
                       "status": "active", "pendingFields": []}
            payload = tmp / "s.json"
            payload.write_text(json.dumps(session), encoding="utf-8")
            store(store_root, "session-save", "--id", "x", "--input", str(payload))

            original = publish.GENERATED
            publish.GENERATED = tmp / "mod.mjs"
            try:
                publish.build_dashboard(str(store_root))
            finally:
                publish.GENERATED = original

            probe = tmp / "probe.mjs"
            probe.write_text(
                "import { HTML, GENERATED_AT } from './mod.mjs';\n"
                "process.stdout.write(JSON.stringify({"
                "  len: HTML.length,"
                "  hasName: HTML.includes('Ren\\u00e9e'),"
                "  hasDash: HTML.includes('\\u2014'),"
                "  stamped: /^\\d{4}-/.test(GENERATED_AT) }));\n",
                encoding="utf-8",
            )
            out = subprocess.run(["node", str(probe)], capture_output=True,
                                 text=True, cwd=tmp, check=True)
            result = json.loads(out.stdout)
            self.assertGreater(result["len"], 1000)
            self.assertTrue(result["hasName"])
            self.assertTrue(result["hasDash"])
            self.assertTrue(result["stamped"])


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


if __name__ == "__main__":
    unittest.main()
