"""The board's click memories, run by an actual JavaScript engine.

The Apply memory always expired; the Drop memory was a bare, immortal id list.
Its replay re-sent /api/drop on every page load for as long as the served page
still drew the card, and on 2026-08-27 that killed the user's Apply and Retry
clicks for the same four applications for two hours. Python can assert the
functions are on the page; only a JavaScript engine can show what they do to
storage and to the clock, so node runs them the way test_found_script.py runs
the Found lane.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("toolpath", ROOT / "scripts" / "toolpath.py")
toolpath = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolpath)
import dashboard  # noqa: E402


def node_or_skip(case):
    found = toolpath.find("node", required=False)
    if found is None:
        case.skipTest("node is not installed")
    return found


def run_in_node(case, driver):
    node = node_or_skip(case)
    prelude = """
'use strict';
var stored = {};
var localStorage = {
  getItem: function (k) {
    return Object.prototype.hasOwnProperty.call(stored, k) ? stored[k] : null;
  },
  setItem: function (k, v) { stored[k] = String(v); },
};
var NOW = 1700000000000;
Date.now = function () { return NOW; };
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(prelude + dashboard.CLICK_MEMORY + driver)
        path = fh.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True)
        case.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout)
    finally:
        pathlib.Path(path).unlink()


class ClickMemoryBehaviourTests(unittest.TestCase):
    def test_drop_memory_stamps_expires_and_discards_the_prestamp_poison(self):
        out = run_in_node(self, """
var out = {};
// The poisoned shape from before the stamp: bare ids, no age. Discarded.
localStorage.setItem('jobby.dropping', JSON.stringify(['poison-a', 'poison-b']));
out.legacyDrops = readDropping();
// A remembered drop is stamped and deduped.
rememberDropping('a');
rememberDropping('a');
rememberDropping('b');
out.remembered = JSON.parse(localStorage.getItem('jobby.dropping'));
NOW += 9 * 60 * 1000;
out.beforeTtl = readDropping().map(function (x) { return x.id; });
forgetDropping('a');
out.afterForget = readDropping().map(function (x) { return x.id; });
NOW += 2 * 60 * 1000;
out.afterTtl = readDropping().map(function (x) { return x.id; });
console.log(JSON.stringify(out));
""")
        self.assertEqual(out["legacyDrops"], [])
        self.assertEqual(out["remembered"],
                         [{"id": "a", "at": 1700000000000},
                          {"id": "b", "at": 1700000000000}])
        self.assertEqual(out["beforeTtl"], ["a", "b"])
        self.assertEqual(out["afterForget"], ["b"])
        self.assertEqual(out["afterTtl"], [])

    def test_apply_memory_still_migrates_bare_ids_and_dedupes(self):
        """The two memories treat pre-stamp entries differently on purpose.

        Replaying an old apply re-queues work the watcher refuses cleanly;
        replaying an old drop kills a click the user just made. So applies
        migrate and drops are discarded, and this pins the asymmetry.
        """
        out = run_in_node(self, """
var out = {};
localStorage.setItem('jobby.applying', JSON.stringify(['old-apply']));
out.migrated = readApplying();
rememberApplying('x');
rememberApplying('x');
out.xEntries = readApplying().filter(function (e) { return e.id === 'x'; }).length;
console.log(JSON.stringify(out));
""")
        self.assertEqual(out["migrated"], [{"id": "old-apply", "at": 1700000000000}])
        self.assertEqual(out["xEntries"], 1)

    def test_an_entry_with_no_stamp_at_all_is_not_read_as_live(self):
        out = run_in_node(self, """
localStorage.setItem('jobby.dropping', JSON.stringify([{ id: 'a' }]));
console.log(JSON.stringify(readDropping()));
""")
        self.assertEqual(out, [])


class PageScriptTests(unittest.TestCase):
    def page(self):
        return dashboard.build(
            {"sessions": [{"applicationId": "a", "status": "watch",
                           "company": "Acme", "role": "PM"}],
             "history": [], "jobs": {}, "meta": {}, "profile": {}}, "now")

    def test_the_memory_is_interpolated_not_quoted(self):
        # `{CLICK_MEMORY}` is itself valid JavaScript (a block containing an
        # identifier), so a failed interpolation would pass a syntax check.
        page = self.page()
        self.assertIn("function readDropping()", page)
        self.assertIn("function rememberApplying(", page)
        self.assertNotIn("{CLICK_MEMORY}", page)

    def test_every_script_on_the_page_parses(self):
        # The page script is assembled from format templates, where one brace
        # too few turns into a syntax error that renders a board with dead
        # controls and no Python assertion the wiser.
        node = node_or_skip(self)
        page = self.page()
        chunks = [part.split("</script>")[0]
                  for part in page.split("<script>")[1:]]
        self.assertGreaterEqual(len(chunks), 2)
        for n, chunk in enumerate(chunks):
            with self.subTest(script=n):
                with tempfile.NamedTemporaryFile("w", suffix=".js",
                                                 delete=False) as fh:
                    fh.write(chunk)
                    path = fh.name
                try:
                    result = subprocess.run([node, "--check", path],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                finally:
                    pathlib.Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
