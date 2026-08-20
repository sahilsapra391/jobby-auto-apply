"""The Found lane's client script, checked by an actual JavaScript engine.

Every other lane is server-rendered HTML that a Python test can read. This one
builds its cards in the browser, so a syntax error in it would render a blank
Found tab on a page that otherwise looks completely fine — and no Python
assertion about the surrounding HTML would notice.

So node parses it, and node runs its filtering against a small DOM stub. Same
reasoning as running the KV queue's tests from inside pytest: a check nobody
remembers to run separately is a check that drifts.

Run from the repo root: python3 -m pytest tests/ -q
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
import found_lane  # noqa: E402


def node_or_skip(case):
    found = toolpath.find("node", required=False)
    if found is None:
        case.skipTest("node is not installed")
    return found


class ScriptSyntaxTests(unittest.TestCase):
    def test_the_found_script_parses(self):
        # A syntax error here renders an empty Found tab on a page that looks
        # entirely healthy otherwise.
        node = node_or_skip(self)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(found_lane.SCRIPT)
            path = fh.name
        try:
            result = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        finally:
            pathlib.Path(path).unlink()

    def test_it_uses_no_syntax_older_browsers_would_reject(self):
        # The board is opened on a phone. Nothing here needs modern syntax, and
        # a parse failure is a blank tab rather than a degraded one.
        self.assertNotIn("=>", found_lane.SCRIPT)
        self.assertNotIn("`", found_lane.SCRIPT)

    def test_no_job_text_is_ever_concatenated_into_markup(self):
        # Titles and descriptions are written by strangers. innerHTML with any
        # of them is the whole attack, and textContent is the whole defence.
        self.assertNotIn("innerHTML", found_lane.SCRIPT)
        self.assertNotIn("insertAdjacentHTML", found_lane.SCRIPT)
        self.assertNotIn("document.write", found_lane.SCRIPT)


class ScriptHarness:
    """Shared rig for running the real script under node.

    Deliberately not a TestCase: subclassing one to borrow its helpers reruns
    every test it owns, which is how a suite quietly doubles a count.
    """

    HARNESS = r"""
const assert = require('node:assert/strict');
const JOBS = %s;
const CONTROLS = %s;

// A DOM small enough to be obviously correct and big enough for the script.
function makeNode(tag, id) {
  return {
    tagName: tag, id: id || '', className: '', textContent: '', title: '',
    style: {}, hidden: false, checked: true, value: '', options: [],
    selectedOptions: [], children: [], disabled: false,
    appendChild(c) { this.children.push(c); return c; },
    remove() {},
    setAttribute(k, v) { this['attr_' + k] = v; },
    getAttribute(k) { return this['attr_' + k] === undefined ? null : this['attr_' + k]; },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}
const nodes = {};
function node(id) {
  if (!nodes[id]) {
    nodes[id] = makeNode('div', id);
    if (CONTROLS[id] !== undefined) nodes[id].value = CONTROLS[id];
  }
  return nodes[id];
}
const dataNode = makeNode('script', 'jb-found-data');
dataNode.textContent = JSON.stringify(JOBS);
nodes['jb-found-data'] = dataNode;
const grid = makeNode('div', 'jb-grid');
grid.textContent = '';
grid.appendChild = function (c) { this.children.push(c); return c; };
nodes['jb-grid'] = grid;
// Every control the script reads has to exist before it reads it, or an
// absent node reads as an empty value and the filter silently does nothing.
['jb-kw','jb-ex','jb-scope','jb-sort','jb-loc','jb-remote','jb-cmode','jb-companies',
 'jb-exp','jb-deg','jb-role','jb-emp','jb-jt','jb-count','jb-none','jb-pills'
].forEach(function (id) { node(id); });

const wpNodes = ['remote', 'hybrid', 'onsite'].map(function (w) {
  const n = makeNode('input');
  n.setAttribute('data-wp', w);
  n.checked = (CONTROLS.workplaces || ['remote', 'hybrid', 'onsite']).indexOf(w) !== -1;
  return n;
});
const unknownNodes = ['experience', 'degree', 'workplace', 'postedAt'].map(function (u) {
  const n = makeNode('input');
  n.setAttribute('data-unknown', u);
  n.checked = (CONTROLS.unknowns || ['experience', 'degree', 'workplace', 'postedAt'])
    .indexOf(u) !== -1;
  return n;
});
const dateNode = makeNode('input');
dateNode.value = CONTROLS.date || 'all';
dateNode.checked = true;

global.localStorage = {
  store: {},
  getItem(k) { return this.store[k] || null; },
  setItem(k, v) { this.store[k] = v; },
};
if (CONTROLS.savedMarker) {
  global.localStorage.setItem('jobby.searchSaved', JSON.stringify([CONTROLS.savedMarker]));
}
const posted = [];
global.fetch = function (url, opts) {
  posted.push({ url: url, method: (opts && opts.method) || 'GET' });
  const body = (url === '/api/search-config' && (!opts || !opts.method || opts.method === 'GET'))
    ? { ok: true, pending: CONTROLS.pending || null }
    : { ok: true };
  return Promise.resolve({ json: function () { return Promise.resolve(body); } });
};
const banner = makeNode('div');
banner.className = 'jb-banner';
banner.setAttribute('data-version', String(CONTROLS.bannerVersion === undefined ? 0 : CONTROLS.bannerVersion));
nodes['jb-savestate'] = makeNode('span', 'jb-savestate');
global.document = {
  getElementById(id) { return nodes[id] || null; },
  createElement(tag) { return makeNode(tag); },
  querySelector(sel) {
    if (sel === '.jb-banner') return banner;
    if (sel.indexOf('input[name="jb-date"]:checked') === 0) return dateNode;
    if (sel.indexOf('[data-unknown=') === 0) {
      const want = sel.split('"')[1];
      return unknownNodes.filter(function (n) { return n.getAttribute('data-unknown') === want; })[0];
    }
    return null;
  },
  querySelectorAll(sel) {
    if (sel.indexOf('[data-wp]') === 0 && sel.indexOf('data-unknown') !== -1) {
      return wpNodes.concat(unknownNodes).concat([dateNode]);
    }
    if (sel === '[data-wp]') return wpNodes;
    return [];
  },
};
if (CONTROLS.locations) {
  const loc = node('jb-loc');
  loc.selectedOptions = CONTROLS.locations.map(function (v) { return { value: v }; });
}

%s

setTimeout(function () {
  console.log(JSON.stringify({
    shown: grid.children.length,
    ids: grid.children.map(function (c) { return c.getAttribute('data-id'); }),
    count: (nodes['jb-count'] || {}).textContent || '',
    saveState: nodes['jb-savestate'].textContent,
    saveClass: nodes['jb-savestate'].className,
    savedMarker: global.localStorage.getItem('jobby.searchSaved'),
    fetched: posted.map(function (p) { return p.method + ' ' + p.url; }),
  }));
}, 10);
"""

    def job(self, job_id, **fields):
        base = {"id": job_id, "co": "Northwind", "ti": "Product Manager", "url": "",
                "src": "greenhouse", "loc": "New York, NY", "cty": "US", "wp": "hybrid",
                "pa": "2026-08-13", "fs": "2026-08-19", "sc": 80, "pt": {}, "wt": {},
                "wy": {}, "un": [], "ga": [], "bd": 1, "sg": [], "tg": [], "ex": "",
                "yr": None, "dg": None, "rc": None, "et": None, "sal": None}
        base.update(fields)
        return base

    def run_script(self, jobs, controls=None):
        node = node_or_skip(self)
        script = self.HARNESS % (json.dumps(jobs), json.dumps(controls or {}),
                                 found_lane.SCRIPT)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0,
                             f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
            return json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            pathlib.Path(path).unlink()


class FilterBehaviourTests(ScriptHarness, unittest.TestCase):
    """Run the real script against a DOM stub and check what it shows."""

    def test_every_job_renders_a_card_by_default(self):
        out = self.run_script([self.job("a"), self.job("b"), self.job("c")])
        self.assertEqual(out["shown"], 3)
        self.assertEqual(sorted(out["ids"]), ["a", "b", "c"])

    def test_the_count_says_how_many_of_how_many(self):
        out = self.run_script([self.job("a"), self.job("b")])
        self.assertIn("2", out["count"])

    def test_an_excluded_word_hides_a_card(self):
        out = self.run_script([self.job("a", ti="Product Manager"),
                               self.job("b", ti="Warehouse Operative")],
                              {"jb-ex": "warehouse", "jb-scope": "title"})
        self.assertEqual(out["ids"], ["a"])

    def test_a_workplace_filter_hides_the_others(self):
        out = self.run_script([self.job("a", wp="remote"), self.job("b", wp="onsite")],
                              {"workplaces": ["remote"]})
        self.assertEqual(out["ids"], ["a"])

    def test_an_unknown_workplace_follows_its_toggle(self):
        jobs = [self.job("a", wp=""), self.job("b", wp="hybrid")]
        self.assertEqual(sorted(self.run_script(jobs)["ids"]), ["a", "b"])
        strict = self.run_script(jobs, {"unknowns": ["experience", "degree", "postedAt"]})
        self.assertEqual(strict["ids"], ["b"])

    def test_a_country_selection_bites_but_lets_remote_through(self):
        jobs = [self.job("us", cty="US"), self.job("de", cty="DE"),
                self.job("anywhere", cty="DE", wp="remote")]
        out = self.run_script(jobs, {"locations": ["US"]})
        self.assertEqual(sorted(out["ids"]), ["anywhere", "us"])

    def test_a_region_pack_expands(self):
        jobs = [self.job("de", cty="DE"), self.job("jp", cty="JP", wp="hybrid")]
        out = self.run_script(jobs, {"locations": ["pack:EU"]})
        self.assertEqual(out["ids"], ["de"])

    def test_the_experience_filter_treats_unknown_as_unknown(self):
        jobs = [self.job("known", yr=3), self.job("over", yr=9), self.job("unknown")]
        out = self.run_script(jobs, {"jb-exp": "5"})
        self.assertEqual(sorted(out["ids"]), ["known", "unknown"])
        strict = self.run_script(jobs, {"jb-exp": "5",
                                        "unknowns": ["degree", "workplace", "postedAt"]})
        self.assertEqual(strict["ids"], ["known"])

    def test_companies_only_and_hide_both_work(self):
        jobs = [self.job("a", co="Northwind"), self.job("b", co="Southwind")]
        only = self.run_script(jobs, {"jb-cmode": "only", "jb-companies": "Northwind"})
        self.assertEqual(only["ids"], ["a"])
        hide = self.run_script(jobs, {"jb-cmode": "hide", "jb-companies": "Northwind"})
        self.assertEqual(hide["ids"], ["b"])

    def test_sorting_by_newest_reorders(self):
        jobs = [self.job("old", pa="2026-01-01", sc=95),
                self.job("new", pa="2026-08-18", sc=10)]
        self.assertEqual(self.run_script(jobs)["ids"], ["old", "new"])
        self.assertEqual(self.run_script(jobs, {"jb-sort": "new"})["ids"], ["new", "old"])

    def test_the_date_window_bites(self):
        jobs = [self.job("fresh", pa="2026-08-19"), self.job("ancient", pa="2020-01-01")]
        out = self.run_script(jobs, {"date": "30d"})
        self.assertEqual(out["ids"], ["fresh"])

    def test_a_hostile_title_becomes_text_not_markup(self):
        out = self.run_script([self.job("a", ti="</script><img src=x onerror=alert(1)>")])
        self.assertEqual(out["shown"], 1)


if __name__ == "__main__":
    unittest.main()


class SaveReconciliationTests(ScriptHarness, unittest.TestCase):
    """Review finding 3: a lost saved search must not read as a saved one.

    The single-key design is read-modify-write with no compare-and-swap, so a
    save landing in the same instant as a claim can be lost. A lost *click* is
    re-sent by the board's replay and heals itself. A lost *save* would not: the
    banner would keep reading "saved" while the next scan quietly ran the old
    search. That asymmetry is the whole reason this exists.
    """

    HOUR_AGO = 3600 * 1000

    def test_a_save_that_landed_says_which_version_is_running(self):
        out = self.run_script([self.job("a")], {
            "bannerVersion": 8,
            "savedMarker": {"at": 1, "priorVersion": 7}})
        self.assertIn("v8 is the one running", out["saveState"])
        self.assertIn("landed", out["saveClass"])
        self.assertEqual(json.loads(out["savedMarker"]), [],
                         "a landed save must stop being tracked")

    def test_a_save_still_waiting_to_be_picked_up_says_so(self):
        out = self.run_script([self.job("a")], {
            "bannerVersion": 7,
            "savedMarker": {"at": 1, "priorVersion": 7},
            "pending": {"config": {"keywords": ["pm"]}, "at": "2026-08-19T20:00:00Z"}})
        self.assertIn("Waiting for the next check", out["saveState"])
        self.assertIn("waiting", out["saveClass"])

    def test_a_recent_save_with_nothing_pending_waits_rather_than_crying_wolf(self):
        # The watcher takes a config within three seconds but the board only
        # republishes every couple of minutes, so this window is normal.
        import time
        out = self.run_script([self.job("a")], {
            "bannerVersion": 7,
            "savedMarker": {"at": int(time.time() * 1000), "priorVersion": 7}})
        self.assertIn("Waiting for the board", out["saveState"])
        self.assertIn("waiting", out["saveClass"])

    def test_a_save_that_never_landed_says_so_and_asks_for_a_retry(self):
        import time
        out = self.run_script([self.job("a")], {
            "bannerVersion": 7,
            "savedMarker": {"at": int(time.time() * 1000) - self.HOUR_AGO,
                            "priorVersion": 7}})
        self.assertIn("did not land", out["saveState"])
        self.assertIn("lost", out["saveClass"])
        self.assertEqual(json.loads(out["savedMarker"]), [],
                         "a lost save is reported once, not on every load")

    def test_nothing_is_said_when_no_save_is_outstanding(self):
        out = self.run_script([self.job("a")], {"bannerVersion": 7})
        self.assertEqual(out["saveState"], "")
        self.assertNotIn("GET /api/search-config", out["fetched"])
