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
import re
import subprocess
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("toolpath", ROOT / "scripts" / "toolpath.py")
toolpath = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolpath)
import dashboard  # noqa: E402
import found_lane  # noqa: E402
import jobs_store  # noqa: E402
import store  # noqa: E402
import search_config  # noqa: E402


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

    def test_the_eye_is_drawn_not_an_emoji(self):
        # The eye emoji renders as a photoreal eyeball on macOS, which is an
        # odd thing to put on a card about a job.
        self.assertNotIn("\\u{1F441}", found_lane.SCRIPT)
        self.assertNotIn("\U0001F441", found_lane.SCRIPT)
        self.assertIn("createElementNS", found_lane.SCRIPT)

    def test_every_action_shares_one_button_shape(self):
        # Watch, Apply, Pass, Posting and Details were five different controls.
        self.assertNotIn("jb-small", found_lane.SCRIPT)
        self.assertNotIn("jb-eye", found_lane.SCRIPT)
        self.assertNotIn("jb-apply", found_lane.SCRIPT)
        self.assertIn("'jb-btn'", found_lane.SCRIPT)

    def test_every_action_has_its_own_icon(self):
        # One shape, five characters. Five identical pills were hard to tell
        # apart at a glance and Details read as an afterthought.
        for name in ("watch", "apply", "pass", "posting", "details"):
            self.assertIn(name + ": function", found_lane.SCRIPT, name)

    def test_the_board_is_four_columns_wide_on_a_wide_screen(self):
        self.assertIn("--cols:4", found_lane.CSS)
        self.assertIn("--cols:1", found_lane.CSS)

    def test_the_columns_are_independent_containers(self):
        # Not a grid: a grid puts every card in a row on one baseline, so
        # opening one grew its neighbours. Not CSS `columns` either: that
        # re-balances the whole container on any height change, so opening one
        # card would shuffle others between columns.
        grid = found_lane.CSS[found_lane.CSS.index(".jb-grid {"):]
        grid = grid[:grid.index("}")]
        self.assertNotIn("grid-template-columns", grid)
        self.assertNotIn("columns:", grid)
        self.assertIn("display:flex", grid)
        self.assertIn(".jb-col {", found_lane.CSS)
        self.assertIn("flex-direction:column", found_lane.CSS)

    def test_one_long_url_cannot_blow_a_column_out(self):
        # Descriptions arrive with tracking URLs pasted into them, and a
        # 156-character one took one column to two thirds of the board and
        # scrolled the page sideways.
        card = found_lane.CSS[found_lane.CSS.index(".jb-card {"):]
        card = card[:card.index("}")]
        self.assertIn("min-width:0", card)
        self.assertIn("overflow-wrap:anywhere", card)
        col = found_lane.CSS[found_lane.CSS.index(".jb-col {"):]
        self.assertIn("min-width:0", col[:col.index("}")])

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
// The payload is a document now: weights, component order and the interned
// rubric explanations ride once rather than on every job.
const JOBS = %s;
const CONTROLS = %s;
const DOC = {
  w: [35, 20, 15, 20, 10],
  c: ["title", "experience", "location", "keywords", "freshness"],
  y: ["", "title matches a searched title"],
  n: CONTROLS.census || {},
  t: CONTROLS.poolTotal || JOBS.length,
  j: JOBS,
};

// A DOM small enough to be obviously correct and big enough for the script.
function makeNode(tag, id) {
  const node = {
    tagName: tag, id: id || '', className: '', title: '',
    style: {}, hidden: false, checked: true, value: '', options: [],
    selectedOptions: [], children: [], disabled: false, href: '', target: '', rel: '',
    // parentNode, so a test can ask which column a card landed in.
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    remove() {},
    setAttribute(k, v) { this['attr_' + k] = v; },
    getAttribute(k) { return this['attr_' + k] === undefined ? null : this['attr_' + k]; },
    // Handlers are stored rather than dropped, so a test can drive the real
    // control flow — adding a keyword pill goes through the Enter key, and
    // nothing else reaches that code path.
    addEventListener(type, fn) {
      (this._on = this._on || {});
      (this._on[type] = this._on[type] || []).push(fn);
    },
    dispatch(type, event) {
      ((this._on || {})[type] || []).forEach(function (fn) { fn(event || {}); });
    },
    // Depth-first over real children, enough for `button.querySelector('span')`.
    querySelector(sel) {
      const want = String(sel).replace(/^\./, '');
      for (const c of this.children) {
        if (c.tagName === sel || (c.className || '').split(' ').indexOf(want) !== -1) return c;
        const deeper = c.querySelector ? c.querySelector(sel) : null;
        if (deeper) return deeper;
      }
      return null;
    },
    querySelectorAll() { return []; },
  };
  // Assigning textContent empties an element in a real DOM. The stub used to
  // store the string and leave children alone, which was invisible while
  // render() only ever ran once — and doubled every card the moment a filter
  // change triggered a second render.
  let text = '';
  Object.defineProperty(node, 'textContent', {
    get() { return text; },
    set(v) { text = String(v); if (text === '') node.children.length = 0; },
  });
  node.classList = {
    add(c) { if ((node.className || '').split(' ').indexOf(c) === -1)
               node.className = (node.className ? node.className + ' ' : '') + c; },
    remove(c) { node.className = (node.className || '').split(' ')
               .filter(function (x) { return x !== c; }).join(' '); },
    toggle(c, on) { on ? this.add(c) : this.remove(c); },
    contains(c) { return (node.className || '').split(' ').indexOf(c) !== -1; },
  };
  return node;
}
const nodes = {};
// The controls that are really <select> on the page, with the options they
// really carry. A stub that makes everything a div hides any bug in code that
// asks "is this value still on offer" — which is exactly what the restore does
// before it forces a stored value back into a control.
const SELECTS = {
  'jb-sort': ['new', 'direct', 'score'],
  'jb-scope': ['title+description', 'title'],
  'jb-cmode': ['all', 'only', 'hide'],
};
function node(id) {
  if (!nodes[id]) {
    const isSelect = Object.prototype.hasOwnProperty.call(SELECTS, id);
    nodes[id] = makeNode(isSelect ? 'SELECT' : 'div', id);
    if (isSelect) {
      nodes[id].options = SELECTS[id].map(function (v) { return { value: v }; });
      // A real select reports its first option, never an empty string.
      nodes[id].value = SELECTS[id][0];
    }
    if (CONTROLS[id] !== undefined) nodes[id].value = CONTROLS[id];
  }
  return nodes[id];
}
const dataNode = makeNode('script', 'jb-found-data');
dataNode.textContent = JSON.stringify(DOC);
nodes['jb-found-data'] = dataNode;
const grid = makeNode('div', 'jb-grid');
grid.textContent = '';
grid.appendChild = function (c) { this.children.push(c); return c; };
nodes['jb-grid'] = grid;
// Every control the script reads has to exist before it reads it, or an
// absent node reads as an empty value and the filter silently does nothing.
['jb-kw','jb-ex','jb-scope','jb-sort','jb-loc','jb-remote','jb-cmode','jb-companies',
 'jb-exp','jb-deg','jb-role','jb-emp','jb-jt','jb-count','jb-none','jb-pills',
 'jb-direct'
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
// Locations are toggle chips now: a native multi-select cannot deselect by
// clicking, which is the thing the control is for.
const locNodes = (CONTROLS.allLocations || CONTROLS.locations || []).map(function (v) {
  const n = makeNode('button');
  n.setAttribute('data-loc', v);
  n.setAttribute('aria-pressed',
    (CONTROLS.locations || []).indexOf(v) !== -1 ? 'true' : 'false');
  return n;
});
// The board reads its column count off a CSS custom property, so the stub has
// to answer for it. Without this every test ran at one column and could not
// see a distribution bug at all.
global.getComputedStyle = function () {
  return { getPropertyValue: function (name) {
    return name === '--cols' ? String(CONTROLS.cols || 1) : '';
  } };
};

const dateNode = makeNode('input');
dateNode.value = CONTROLS.date || 'all';
dateNode.checked = true;

// A fixed clock. The date filters measure against Date.now(), so a fixture
// written as a literal date silently ages out of its own window: "2026-08-19
// is inside 30 days" stops being true thirty days after it was written, and
// the test fails for a reason that has nothing to do with the code. Pinning
// the clock is what makes a date fixture mean the same thing next year.
const NOW = Date.parse(CONTROLS.now || '2026-08-20T00:00:00Z');
Date.now = function () { return NOW; };

// The direct-only filter ships checked on the real page. Most tests here are
// about something else and use aggregator-ranked fixtures, so the stub leaves
// it off unless a test asks; DirectListingTests covers the shipped default.
node('jb-direct').checked = !!CONTROLS['jb-direct'];
// "include remote for this selection" ships on, and it lets a remote job
// past a country filter on purpose. A test about the country filter alone
// has to turn it off, or it is testing both at once.
node('jb-remote').checked = CONTROLS['jb-remote'] !== false;
global.localStorage = {
  store: {},
  getItem(k) { return this.store[k] || null; },
  setItem(k, v) { this.store[k] = v; },
  removeItem(k) { delete this.store[k]; },
};
// A view the user set on a previous visit, so a test can check the board opens
// on it rather than on what the page was published with.
if (CONTROLS.storedView) {
  global.localStorage.setItem('jobby.foundView', JSON.stringify(CONTROLS.storedView));
}
if (CONTROLS.savedMarker) {
  global.localStorage.setItem('jobby.searchSaved', JSON.stringify([CONTROLS.savedMarker]));
}
const posted = [];
global.fetch = function (url, opts) {
  posted.push({ url: url, method: (opts && opts.method) || 'GET',
                body: opts && opts.body ? JSON.parse(opts.body) : null });
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
  createElementNS(ns, tag) { return makeNode(tag); },
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
    if (sel === '[data-loc]') return locNodes;
    return [];
  },
};

%s

// A test may ask for keyword pills; they are only reachable through the input's
// Enter handler, which is the path a user takes.
(CONTROLS.keywords || []).forEach(function (word) {
  const input = nodes['jb-kw'];
  input.value = word;
  input.dispatch('keydown', { key: 'Enter', preventDefault: function () {} });
});

// textContent on the stub returns a node's own text, so a test that wants to
// read what a card says has to walk it.
// Cards are distributed round robin into column containers, so the board's
// reading order is column 0 row 0, column 1 row 0, column 2 row 0, and so on.
// Rebuild that order rather than concatenating the columns, or every assertion
// about sort order reads the board wrong.
function cardsOf(grid) {
  const cols = grid.children.filter(function (c) {
    return (c.className || '').indexOf('jb-col') !== -1;
  });
  if (!cols.length) return grid.children.slice();
  const out = [];
  const deepest = Math.max.apply(null, cols.map(function (c) { return c.children.length; }));
  for (let row = 0; row < deepest; row++) {
    for (let i = 0; i < cols.length; i++) {
      if (cols[i].children[row]) out.push(cols[i].children[row]);
    }
  }
  return out;
}

function dumpText(node) {
  return [node.textContent || ''].concat(
    (node.children || []).map(dumpText)).join(' ').replace(/\s+/g, ' ').trim();
}

// Hover and click, in order, so a test can assert the disclosure's actual
// behaviour rather than the presence of a string in the source.
(CONTROLS.interact || []).forEach(function (step) {
  const card = cardsOf(grid)[step.index];
  if (!card) return;
  if (step.event === 'click') card.querySelector('.jb-det').dispatch('click', {});
  else card.dispatch(step.event, {});
});

setTimeout(function () {
  console.log(JSON.stringify({
    shown: cardsOf(grid).length,
    cards: cardsOf(grid).map(function (c) {
      const slot = c.querySelector('.jb-slot');
      return { id: c.getAttribute('data-id'), cls: c.className,
               col: grid.children.indexOf(c.parentNode),
               open: (c.className || '').indexOf('open') !== -1,
               panels: slot ? slot.children.length : 0,
               expanded: (c.querySelector('.jb-det') || {})['attr_aria-expanded'] };
    }),
    text: dumpText(grid),
    ids: cardsOf(grid).map(function (c) { return c.getAttribute('data-id'); }),
    count: (nodes['jb-count'] || {}).textContent || '',
    saveState: nodes['jb-savestate'].textContent,
    saveClass: nodes['jb-savestate'].className,
    savedMarker: global.localStorage.getItem('jobby.searchSaved'),
    storedView: global.localStorage.getItem('jobby.foundView'),
    sortValue: (nodes['jb-sort'] || {}).value,
    directChecked: !!(nodes['jb-direct'] || {}).checked,
    posted: posted.map(function (p) { return p.body; }),
    fetched: posted.map(function (p) { return p.method + ' ' + p.url; }),
  }));
}, 10);
"""

    def job(self, job_id, **fields):
        base = {"id": job_id, "co": "Northwind", "ti": "Product Manager", "url": "",
                "src": "greenhouse", "loc": "New York, NY", "cty": "US", "wp": "hybrid",
                "pa": "2026-08-13", "fs": "2026-08-19", "sc": 80, "pt": [35, 10, 15, 14, 8],
                "wy": [0, 0, 0, 0, 0], "un": [], "ga": [], "bd": 1, "sg": [], "tg": [],
                "yr": None, "yrt": None, "place": "New York, United States", "rank": 0,
                "sum": "Own the internal reporting platform.",
                "dg": None, "rc": None, "et": None, "sal": None}
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

    def test_a_keyword_found_only_in_the_body_still_matches(self):
        # The regression this pins: a payload optimisation renamed the field the
        # haystack read, so searching in title-and-description scope silently
        # matched nothing in the body while everything still rendered. Every
        # other keyword test used terms that also appear in a title, so they
        # would all have stayed green through it.
        #
        # "kubernetes" appears in one body and in no title, company or tag.
        out = self.run_script(
            [self.job("has", ti="Product Manager", co="Northwind",
                      sum="You will own our kubernetes migration.", tg=[]),
             self.job("lacks", ti="Product Manager", co="Northwind",
                      sum="You will own the billing roadmap.", tg=[])],
            {"keywords": ["kubernetes"], "jb-scope": "title+description"})
        self.assertEqual(out["ids"], ["has"])

    def test_a_body_only_keyword_is_ignored_in_title_scope(self):
        # The other half: title scope must not quietly search the body either.
        out = self.run_script(
            [self.job("a", ti="Product Manager", sum="kubernetes migration", tg=[])],
            {"keywords": ["kubernetes"], "jb-scope": "title"})
        self.assertEqual(out["ids"], [])

    def test_description_scope_searches_the_description(self):
        # The haystack referenced a field that the payload slimming removed, so
        # searching in title-and-description scope silently matched nothing in
        # the body. Undefined concatenates as "undefined" rather than throwing.
        out = self.run_script([self.job("a", sum="owns the billing platform"),
                               self.job("b", sum="runs the warehouse floor")],
                              {"jb-ex": "warehouse", "jb-scope": "title+description"})
        self.assertEqual(out["ids"], ["a"])

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

    def test_a_country_selection_bites_and_a_bound_remote_job_is_not_exempt(self):
        # "Remote - Canada: select locations" is remote *within Canada*, and it
        # showed up under a search for India. A job that names a country has to
        # be in a selected one whatever its arrangement.
        jobs = [self.job("us", cty="US"), self.job("de", cty="DE"),
                self.job("remote-de", cty="DE", wp="remote")]
        out = self.run_script(jobs, {"allLocations": ["US"], "locations": ["US"]})
        self.assertEqual(out["ids"], ["us"])

    def test_a_countryless_remote_job_answers_any_selection(self):
        # These are what the toggle is actually about: genuinely location-free.
        jobs = [self.job("us", cty="US"), self.job("anywhere", cty="", wp="remote")]
        out = self.run_script(jobs, {"allLocations": ["US"], "locations": ["US"],
                                     "jb-remote": True})
        self.assertEqual(sorted(out["ids"]), ["anywhere", "us"])

    def test_turning_the_toggle_off_drops_countryless_remote_jobs(self):
        jobs = [self.job("us", cty="US"), self.job("anywhere", cty="", wp="remote")]
        out = self.run_script(jobs, {"allLocations": ["US"], "locations": ["US"],
                                     "jb-remote": False})
        self.assertEqual(out["ids"], ["us"])

    def test_a_job_with_no_country_at_all_is_not_an_india_job(self):
        # The complaint. A "Hybrid" posting whose city nobody could parse has
        # no business in a search for India, whatever the remote toggle says.
        jobs = [self.job("in", cty="IN"),
                self.job("nowhere", cty="", wp="hybrid"),
                self.job("blank", cty="", wp="")]
        for toggle in (True, False):
            out = self.run_script(jobs, {"allLocations": ["IN"], "locations": ["IN"],
                                         "jb-remote": toggle})
            with self.subTest(includeRemote=toggle):
                self.assertEqual(out["ids"], ["in"])

    def test_with_no_country_selected_everything_is_in_scope(self):
        # The filter only narrows when the user has asked for somewhere.
        jobs = [self.job("in", cty="IN"), self.job("nowhere", cty="", wp="hybrid")]
        out = self.run_script(jobs, {"allLocations": ["IN"], "locations": []})
        self.assertEqual(sorted(out["ids"]), ["in", "nowhere"])

    def test_two_keywords_are_or_not_and(self):
        # Three job titles in the box means "show me all three kinds of job".
        # Requiring every word emptied the board on the second title, because
        # no posting is a product manager AND a business analyst.
        jobs = [self.job("pm", ti="Product Manager"),
                self.job("ba", ti="Business Analyst"),
                self.job("eng", ti="Backend Engineer")]
        out = self.run_script(jobs, {"keywords": ["product manager", "business analyst"],
                                     "jb-scope": "title"})
        self.assertEqual(sorted(out["ids"]), ["ba", "pm"])

    def test_one_keyword_still_narrows(self):
        jobs = [self.job("pm", ti="Product Manager"), self.job("ba", ti="Business Analyst")]
        out = self.run_script(jobs, {"keywords": ["business analyst"], "jb-scope": "title"})
        self.assertEqual(out["ids"], ["ba"])

    def test_no_keyword_matches_nothing_rather_than_everything(self):
        jobs = [self.job("pm", ti="Product Manager")]
        out = self.run_script(jobs, {"keywords": ["welder"], "jb-scope": "title"})
        self.assertEqual(out["ids"], [])

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
        # Both inside the two-month ceiling: this is about the sort, and a
        # fixture that the date filter drops would pass for the wrong reason.
        # The sort is named on both runs rather than leaned on as a default,
        # because the shipped default has moved once already.
        jobs = [self.job("old", pa="2026-07-01", sc=95),
                self.job("new", pa="2026-08-18", sc=10)]
        self.assertEqual(self.run_script(jobs, {"jb-sort": "direct"})["ids"],
                         ["old", "new"])
        self.assertEqual(self.run_script(jobs, {"jb-sort": "new"})["ids"],
                         ["new", "old"])

    def test_the_date_window_bites(self):
        jobs = [self.job("fresh", pa="2026-08-19"), self.job("ancient", pa="2020-01-01")]
        out = self.run_script(jobs, {"date": "30d"})
        self.assertEqual(out["ids"], ["fresh"])

    def test_a_hostile_title_becomes_text_not_markup(self):
        out = self.run_script([self.job("a", ti="</script><img src=x onerror=alert(1)>")])
        self.assertEqual(out["shown"], 1)


class DirectListingTests(ScriptHarness, unittest.TestCase):
    """Applying through the employer's own ATS beats applying through a
    redirect, so the lane leads with direct listings."""

    def test_direct_listings_lead_even_when_an_aggregator_scores_higher(self):
        out = self.run_script([self.job("agg", rank=2, src="careerjet", sc=95),
                               self.job("ats", rank=0, src="greenhouse", sc=70)],
                              {"jb-sort": "direct"})
        self.assertEqual(out["ids"], ["ats", "agg"])

    def test_within_a_group_the_better_match_still_leads(self):
        out = self.run_script([self.job("lo", rank=0, sc=60),
                               self.job("hi", rank=0, sc=90)], {"jb-sort": "direct"})
        self.assertEqual(out["ids"], ["hi", "lo"])

    def test_a_careers_page_counts_as_direct(self):
        out = self.run_script([self.job("agg", rank=2, sc=99),
                               self.job("careers", rank=1, sc=50)],
                              {"jb-sort": "direct"})
        self.assertEqual(out["ids"], ["careers", "agg"])

    def test_sorting_by_match_ignores_how_direct_it_is(self):
        out = self.run_script([self.job("agg", rank=2, sc=95),
                               self.job("ats", rank=0, sc=70)], {"jb-sort": "score"})
        self.assertEqual(out["ids"], ["agg", "ats"])

    def test_the_direct_only_filter_hides_aggregator_copies(self):
        out = self.run_script([self.job("agg", rank=2), self.job("ats", rank=0)],
                              {"jb-direct": True})
        self.assertEqual(out["ids"], ["ats"])

    """Review finding 3: a lost saved search must not read as a saved one.

    The single-key design is read-modify-write with no compare-and-swap, so a
    save landing in the same instant as a claim can be lost. A lost *click* is
    re-sent by the board's replay and heals itself. A lost *save* would not: the
    banner would keep reading "saved" while the next scan quietly ran the old
    search. That asymmetry is the whole reason this exists.
    """

    HOUR_AGO = 3600 * 1000
    # The same instant the harness pins Date.now() to. A marker built from
    # Python's own clock disagrees with the script's by however many hours
    # into the day the suite happens to run.
    NOW_MS = int(datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp() * 1000)

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
        out = self.run_script([self.job("a")], {
            "bannerVersion": 7,
            "savedMarker": {"at": self.NOW_MS, "priorVersion": 7}})
        self.assertIn("Waiting for the board", out["saveState"])
        self.assertIn("waiting", out["saveClass"])

    def test_a_save_that_never_landed_says_so_and_asks_for_a_retry(self):
        out = self.run_script([self.job("a")], {
            "bannerVersion": 7,
            "savedMarker": {"at": self.NOW_MS - self.HOUR_AGO,
                            "priorVersion": 7}})
        self.assertIn("did not land", out["saveState"])
        self.assertIn("lost", out["saveClass"])
        self.assertEqual(json.loads(out["savedMarker"]), [],
                         "a lost save is reported once, not on every load")

    def test_nothing_is_said_when_no_save_is_outstanding(self):
        out = self.run_script([self.job("a")], {"bannerVersion": 7})
        self.assertEqual(out["saveState"], "")
        self.assertNotIn("GET /api/search-config", out["fetched"])


class DisclosureTests(ScriptHarness, unittest.TestCase):
    """Details opens on hover, stays open on click, and only for its own card.

    It was a fifth full-size button sitting under Posting, and opening it grew
    every card in the row, because grid items stretch to the tallest sibling by
    default. Both of those are the complaint.
    """

    def test_the_details_control_is_not_a_button_in_the_action_row(self):
        self.assertNotIn("button('jb-det'", found_lane.SCRIPT)
        self.assertNotIn("acts.appendChild(details)", found_lane.SCRIPT)

    def test_each_card_sizes_itself(self):
        # Without this a card that opens drags its three row-mates taller.
        grid = found_lane.CSS[found_lane.CSS.index(".jb-grid {"):]
        self.assertIn("align-items:flex-start", grid[:grid.index("}")])

    def test_the_panel_animates_from_a_measured_height(self):
        # Never a guessed ceiling: the panel's own scrollHeight, released to
        # `none` once the transition ends so a reflow cannot clip it.
        self.assertIn("transition:max-height", found_lane.CSS)
        self.assertIn("slot.scrollHeight + 'px'", found_lane.SCRIPT)
        self.assertIn("maxHeight = 'none'", found_lane.SCRIPT)
        self.assertIn("prefers-reduced-motion", found_lane.CSS)

    def test_the_grid_trick_is_not_used_here(self):
        # It needs a grid parent. The card is a flex column, so 1fr resolves
        # against no free space and computes to zero: a panel that opens in the
        # DOM, reports aria-expanded=true, and stays visually shut.
        self.assertNotIn("grid-template-rows:1fr", found_lane.CSS)

    def cards(self, interact):
        jobs = [self.job("a"), self.job("b"), self.job("c")]
        out = self.run_script(jobs, {"jb-direct": False, "interact": interact})
        return {c["id"]: c for c in out["cards"]}

    def test_nothing_is_open_and_no_panel_is_built_until_asked(self):
        # 3,400 detail panels built up front is what the windowing exists to
        # avoid, and it is the reason this is built lazily rather than hidden.
        cards = self.cards([])
        self.assertTrue(all(not c["open"] for c in cards.values()))
        self.assertTrue(all(c["panels"] == 0 for c in cards.values()))
        self.assertTrue(all(c["expanded"] == "false" for c in cards.values()))

    def test_hover_opens_that_card_and_only_that_card(self):
        cards = self.cards([{"index": 0, "event": "mouseenter"}])
        self.assertTrue(cards["a"]["open"])
        self.assertEqual(cards["a"]["panels"], 1)
        self.assertFalse(cards["b"]["open"])
        self.assertFalse(cards["c"]["open"])

    def test_leaving_closes_what_hover_opened(self):
        cards = self.cards([{"index": 0, "event": "mouseenter"},
                            {"index": 0, "event": "mouseleave"}])
        self.assertFalse(cards["a"]["open"])
        self.assertEqual(cards["a"]["expanded"], "false")

    def test_a_click_survives_the_mouse_leaving(self):
        # Hover is a preview. It must never close what a click opened.
        cards = self.cards([{"index": 0, "event": "mouseenter"},
                            {"index": 0, "event": "click"},
                            {"index": 0, "event": "mouseleave"}])
        self.assertTrue(cards["a"]["open"])
        self.assertEqual(cards["a"]["expanded"], "true")

    def test_clicking_again_closes_it(self):
        cards = self.cards([{"index": 0, "event": "click"},
                            {"index": 0, "event": "click"}])
        self.assertFalse(cards["a"]["open"])

    def test_the_panel_is_built_once_however_often_it_is_opened(self):
        cards = self.cards([{"index": 0, "event": "mouseenter"},
                            {"index": 0, "event": "mouseleave"},
                            {"index": 0, "event": "mouseenter"},
                            {"index": 0, "event": "click"}])
        self.assertEqual(cards["a"]["panels"], 1)

    def test_cards_are_dealt_round_robin_across_the_columns(self):
        # Reading across the top of the board is reading down the sorted list.
        jobs = [self.job(x) for x in ("a", "b", "c", "d", "e", "f")]
        out = self.run_script(jobs, {"jb-direct": False, "cols": 3})
        where = {c["id"]: c["col"] for c in out["cards"]}
        self.assertEqual([where[x] for x in ("a", "b", "c", "d", "e", "f")],
                         [0, 1, 2, 0, 1, 2])

    def test_opening_a_card_touches_no_other_column(self):
        # The whole point of columns over a grid: a card that grows pushes only
        # what is below it in its own column.
        jobs = [self.job(x) for x in ("a", "b", "c", "d", "e", "f")]
        out = self.run_script(jobs, {"jb-direct": False, "cols": 3,
                                     "interact": [{"index": 0, "event": "click"}]})
        cards = {c["id"]: c for c in out["cards"]}
        self.assertTrue(cards["a"]["open"])
        for other in ("b", "c", "d", "e", "f"):
            with self.subTest(card=other):
                self.assertFalse(cards[other]["open"])

    def test_two_cards_open_independently(self):
        cards = self.cards([{"index": 0, "event": "click"},
                            {"index": 2, "event": "mouseenter"}])
        self.assertTrue(cards["a"]["open"])
        self.assertTrue(cards["c"]["open"])
        self.assertFalse(cards["b"]["open"])


class LocationChipTests(ScriptHarness, unittest.TestCase):
    """Clicking a location adds it; clicking it again removes it.

    A <select multiple> needs cmd-click to add a second country, and clicking a
    selected one replaces the selection rather than clearing it, so the control
    could not do either thing it was for.
    """

    def test_two_countries_can_be_selected_at_once(self):
        jobs = [self.job("us", cty="US"), self.job("in", cty="IN"),
                self.job("de", cty="DE")]
        out = self.run_script(jobs, {"allLocations": ["US", "IN", "DE"],
                                     "locations": ["US", "IN"], "jb-direct": False})
        self.assertEqual(sorted(out["ids"]), ["in", "us"])

    def test_selecting_none_means_no_location_filter(self):
        jobs = [self.job("us", cty="US"), self.job("in", cty="IN")]
        out = self.run_script(jobs, {"allLocations": ["US", "IN"], "locations": [],
                                     "jb-direct": False})
        self.assertEqual(sorted(out["ids"]), ["in", "us"])

    def test_a_pack_still_expands_to_its_countries(self):
        jobs = [self.job("de", cty="DE"), self.job("us", cty="US")]
        out = self.run_script(jobs, {"allLocations": ["pack:EU"],
                                     "locations": ["pack:EU"], "jb-direct": False})
        self.assertEqual(out["ids"], ["de"])


class ShippedDefaultTests(ScriptHarness, unittest.TestCase):
    """The two defaults Sahil asked for, asserted on the rendered controls."""

    def test_the_sort_ships_on_newest(self):
        bar = found_lane.filter_bar([], {})
        self.assertIn('<option value="new" selected>Newest</option>', bar)

    def test_direct_listings_only_ships_checked(self):
        bar = found_lane.filter_bar([], {})
        self.assertIn('id="jb-direct" checked', bar)

    def test_clear_restores_both_rather_than_wiping_them(self):
        script = found_lane.SCRIPT
        clear = script[script.index("jb-clear"):]
        clear = clear[:clear.index("var save =")]
        self.assertIn("sort.value = 'new'", clear)
        self.assertIn("direct.checked = true", clear)


class DateVocabularyTests(ScriptHarness, unittest.TestCase):
    """Every window the config can save has a chip, and one is always checked.

    A search saved as "60d" matched no chip, so none was checked, so the client
    read "no answer" and fell back to seven days. The board showed a tenth of
    what the saved search asked for and said nothing about it. That is the same
    shape as the workplace mismatch: a producer vocabulary the consumer never
    learned.
    """

    def test_every_config_window_has_a_chip(self):
        offered = dict(found_lane.DATE_WINDOWS)
        for name in search_config.POSTED_WITHIN:
            if name == "all":
                continue      # a pre-ceiling save; it names the same window as 60d
            with self.subTest(window=name):
                self.assertIn(name, offered)

    def test_a_saved_window_is_the_one_checked(self):
        for name, _ in found_lane.DATE_WINDOWS:
            with self.subTest(window=name):
                bar = found_lane.filter_bar([], {"postedWithin": name})
                self.assertIn(f'value="{name}" checked', bar)

    def test_a_window_the_chips_do_not_offer_falls_back_to_the_widest(self):
        # Never to nothing: an unchecked set reads to the client as seven days.
        bar = found_lane.filter_bar([], {"postedWithin": "all"})
        self.assertEqual(len(re.findall(r'value="\w+" checked', bar)), 1)
        self.assertIn('value="60d" checked', bar)

    def test_the_client_knows_every_chip_value(self):
        script = found_lane.SCRIPT
        table = script[script.index("var DAYS ="):]
        table = table[:table.index("};")]
        for name, _ in found_lane.DATE_WINDOWS:
            with self.subTest(window=name):
                self.assertIn("'" + name + "'", table)

    def test_no_chip_applies_a_wider_window_than_the_ceiling(self):
        script = found_lane.SCRIPT
        table = script[script.index("var DAYS ="):]
        table = table[:table.index("};")]
        self.assertNotIn("null", table)
        for value in re.findall(r":\s*([\d.]+)", table):
            self.assertLessEqual(float(value), search_config.MAX_AGE_DAYS)


class EndToEndFacetTests(ScriptHarness, unittest.TestCase):
    """Render the real page, take the payload back out, filter it with the real
    script, and check the counts against the store.

    This is the test that would have caught the workplace mismatch. The tests
    above hand-build their payload rows, so they asserted the client against a
    payload `compact` does not produce; `test_dashboard` asserted the producer
    in isolation and pinned the wrong spelling. Two green suites either side of
    a seam neither crossed, and 41% of the cards were invisible.

    Nothing here knows what the fields are called. It counts by facet in Python,
    straight off the store, then asks the client for the same facet and compares.
    A producer that renames a value, or a consumer that stops recognising one,
    fails here whatever the field is.
    """

    WORKPLACES = ("remote", "hybrid", "onsite")
    COUNTRIES = ("US", "IN", "DE")

    def store(self):
        """A store whose jobs span every facet, built through the real writers."""
        root = pathlib.Path(tempfile.mkdtemp(prefix="jobby-e2e-"))
        (root / "sessions").mkdir()
        jobs = {}
        n = 0
        for workplace in self.WORKPLACES + ("",):
            for country in self.COUNTRIES:
                n += 1
                jid = f"j{n:03d}"
                jobs[jid] = {
                    "id": jid, "company": f"Company {n}",
                    "title": "Product Manager", "normTitle": "product manager",
                    "canonicalUrl": f"https://boards.greenhouse.io/c{n}/jobs/{n}",
                    "canonicalSource": "greenhouse", "state": "new",
                    "workplace": workplace,
                    "location": {"country": country, "city": "Somewhere"},
                    "postedAt": "2026-08-18T00:00:00Z",
                    "firstSeenAt": "2026-08-19T00:00:00Z",
                    "score": {"total": 90 - n, "parts": {}, "weights": {}},
                    "sources": [{"source": "greenhouse", "sourceClass": "ats",
                                 "url": f"https://boards.greenhouse.io/c{n}/jobs/{n}"}],
                }
        jobs_store.save_jobs(root, jobs)
        store.write_private(root / "meta.json", {})
        return root, jobs

    def payload_rows(self, root):
        """The rows the real page actually ships."""
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dashboard.py"),
             "--out", str(root / "board.html"), "--root", str(root)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        html = (root / "board.html").read_text()
        blob = re.search(
            r'<script type="application/json" id="jb-found-data">(.*?)</script>',
            html, re.S)
        self.assertIsNotNone(blob, "the page shipped no Found payload")
        return json.loads(blob.group(1).replace("<\\/", "</"))["j"]

    def test_every_stored_job_reaches_the_page(self):
        root, jobs = self.store()
        try:
            rows = self.payload_rows(root)
            self.assertEqual(sorted(r["id"] for r in rows), sorted(jobs))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_filtering_by_workplace_returns_what_the_store_holds(self):
        root, jobs = self.store()
        try:
            rows = self.payload_rows(root)
            for workplace in self.WORKPLACES:
                expected = sorted(jid for jid, j in jobs.items()
                                  if j["workplace"] == workplace)
                out = self.run_script(rows, {"workplaces": [workplace],
                                             "unknowns": ["experience", "degree",
                                                          "postedAt"],
                                             "jb-direct": False, "date": "60d"})
                with self.subTest(workplace=workplace):
                    self.assertEqual(sorted(out["ids"]), expected)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_filtering_by_country_returns_what_the_store_holds(self):
        root, jobs = self.store()
        try:
            rows = self.payload_rows(root)
            for country in self.COUNTRIES:
                expected = sorted(jid for jid, j in jobs.items()
                                  if j["location"]["country"] == country)
                out = self.run_script(rows, {"allLocations": list(self.COUNTRIES),
                                             "locations": [country],
                                             "jb-direct": False, "date": "60d"})
                with self.subTest(country=country):
                    self.assertEqual(sorted(out["ids"]), expected)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_country_bound_remote_job_does_not_cross_the_country_filter(self):
        # Every job in this fixture names a country, so with the toggle on or
        # off the answer is the same: only the selected country's jobs. The
        # toggle is about postings that name no country at all.
        root, jobs = self.store()
        try:
            rows = self.payload_rows(root)
            expected = sorted(jid for jid, j in jobs.items()
                              if j["location"]["country"] == "US")
            for toggle in (True, False):
                out = self.run_script(rows, {"allLocations": list(self.COUNTRIES),
                                             "locations": ["US"], "jb-remote": toggle,
                                             "jb-direct": False, "date": "60d"})
                with self.subTest(includeRemote=toggle):
                    self.assertEqual(sorted(out["ids"]), expected)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_workplace_the_page_ships_is_never_unselectable(self):
        # The mismatch itself: every distinct `wp` on the page has to be a value
        # the checkboxes can select, or those cards can never be shown.
        root, _ = self.store()
        try:
            rows = self.payload_rows(root)
            shipped = {r.get("wp") for r in rows} - {"", None}
            self.assertTrue(shipped, "no workplace reached the page at all")
            self.assertLessEqual(shipped, set(found_lane.WORKPLACE_LABELS))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_the_whole_pool_is_reachable_with_the_filters_open(self):
        # The 41% that were invisible would fail here: with every facet allowed,
        # every row the page ships must be on the board.
        root, jobs = self.store()
        try:
            rows = self.payload_rows(root)
            out = self.run_script(rows, {"jb-direct": False, "date": "60d"})
            self.assertEqual(sorted(out["ids"]), sorted(jobs))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ViewPersistenceTests(ScriptHarness, unittest.TestCase):
    """Whatever the user last set is what the board opens on.

    The board is a published snapshot rendered with whatever the saved search
    was at publish time. So a filter change survived exactly until the next
    reload, and Save search could not help: the page in front of you was
    rendered minutes ago. Pressing Save and reloading put everything back, with
    nothing on screen saying why, which is indistinguishable from the save
    having failed.

    View state is deliberately separate from the saved search. The saved search
    decides what gets fetched; this decides what you are looking at.
    """

    def stored(self, out):
        return json.loads(out["storedView"]) if out["storedView"] else None

    def test_the_view_is_written_on_the_first_render(self):
        out = self.run_script([self.job("a")], {"jb-direct": False})
        self.assertIsNotNone(self.stored(out))

    def test_a_stored_view_beats_what_the_page_shipped_with(self):
        # The page ships sort=new; the stored view says score.
        out = self.run_script([self.job("a")], {
            "jb-direct": False,
            "storedView": {"keywords": [], "values": {"jb-sort": "score"},
                           "checks": {"jb-direct": False}, "date": "60d",
                           "workplaces": ["remote", "hybrid", "onsite"],
                           "unknowns": ["experience", "degree", "workplace", "postedAt"],
                           "locations": []}})
        self.assertEqual(out["sortValue"], "score")

    def test_a_stored_keyword_comes_back(self):
        jobs = [self.job("pm", ti="Product Manager"), self.job("ba", ti="Business Analyst")]
        out = self.run_script(jobs, {
            "jb-direct": False, "jb-scope": "title",
            "storedView": {"keywords": ["business analyst"], "values": {"jb-scope": "title"},
                           "checks": {"jb-direct": False}, "date": "60d",
                           "workplaces": ["remote", "hybrid", "onsite"],
                           "unknowns": ["experience", "degree", "workplace", "postedAt"],
                           "locations": []}})
        self.assertEqual(out["ids"], ["ba"])

    def test_a_stored_location_comes_back(self):
        jobs = [self.job("us", cty="US"), self.job("in", cty="IN")]
        out = self.run_script(jobs, {
            "jb-direct": False, "allLocations": ["US", "IN"],
            "storedView": {"keywords": [], "values": {}, "checks": {"jb-direct": False},
                           "date": "60d", "workplaces": ["remote", "hybrid", "onsite"],
                           "unknowns": ["experience", "degree", "workplace", "postedAt"],
                           "locations": ["IN"]}})
        self.assertEqual(out["ids"], ["in"])

    def test_a_retired_option_is_skipped_rather_than_forced(self):
        # A window or a sort that no longer exists must not blank the control.
        out = self.run_script([self.job("a")], {
            "jb-direct": False,
            "storedView": {"keywords": [], "values": {"jb-sort": "sideways"},
                           "checks": {}, "date": "99d", "workplaces": [],
                           "unknowns": [], "locations": []}})
        self.assertIn(out["sortValue"], [v for v, _ in found_lane.SORT_OPTIONS])

    def test_junk_in_storage_is_discarded_not_obeyed(self):
        out = self.run_script([self.job("a")], {
            "jb-direct": False, "storedView": "not an object"})
        self.assertEqual(out["ids"], ["a"])

    def test_clear_returns_the_date_to_the_saved_window(self):
        # Not to a hardcoded week. The marker Clear looks for was silently never
        # emitted, so nothing was checked afterwards and the client fell back to
        # seven days — a two-month search quietly narrowed by pressing Clear.
        for window in ("60d", "30d", "24h"):
            with self.subTest(window=window):
                bar = found_lane.filter_bar([], {"postedWithin": window})
                self.assertIn(f'value="{window}" checked data-shipped', bar)

    def test_exactly_one_chip_carries_the_marker(self):
        bar = found_lane.filter_bar([], {"postedWithin": "30d"})
        self.assertEqual(bar.count("data-shipped"), 1)

    def test_clear_reads_that_marker_rather_than_a_literal(self):
        script = found_lane.SCRIPT
        clear = script[script.index("jb-clear"):]
        clear = clear[:clear.index("var save =")]
        self.assertIn("data-shipped", clear)
        self.assertNotIn('[value="7d"]', clear)

    def test_clear_forgets_the_view(self):
        script = found_lane.SCRIPT
        clear = script[script.index("jb-clear"):]
        clear = clear[:clear.index("var save =")]
        self.assertIn("forgetView()", clear)

    def test_save_sends_the_sort_and_the_direct_toggle(self):
        # They are part of the search. A saved search that forgets them puts two
        # controls back to the factory setting on every republish.
        script = found_lane.SCRIPT
        save = script[script.index("var config = {"):]
        save = save[:save.index("};")]
        self.assertIn("sort:", save)
        self.assertIn("directOnly:", save)


class EveryControlTests(ScriptHarness, unittest.TestCase):
    """One test per control, because "it looks broken" is hard to act on.

    Match % had no branch at all: choosing it fell through every arm of the sort
    and left the rows in payload order, so the control did nothing and there was
    no test that would have said so. These drive each control through the real
    script and assert the actual ordering or the actual surviving ids.
    """

    def rows(self):
        # Deliberately varied on every axis a control can touch.
        return [
            self.job("a", ti="Product Manager", co="Alpha", cty="US", wp="remote",
                     sc=60, pa="2026-08-19", rank=0, yr=2, sum="owns billing"),
            self.job("b", ti="Senior Product Manager", co="Beta", cty="IN", wp="hybrid",
                     sc=90, pa="2026-08-10", rank=2, yr=7, sum="runs the warehouse"),
            self.job("c", ti="Business Analyst", co="Gamma", cty="US", wp="onsite",
                     sc=75, pa="2026-08-15", rank=1, yr=None, sum="kubernetes work"),
        ]

    def ids(self, controls):
        base = {"jb-direct": False, "date": "60d", "jb-scope": "title"}
        base.update(controls)
        return self.run_script(self.rows(), base)["ids"]

    # --- sorts -----------------------------------------------------------
    def test_sort_by_match_percent_orders_by_score(self):
        self.assertEqual(self.ids({"jb-sort": "score"}), ["b", "c", "a"])

    def test_sort_by_newest_orders_by_date(self):
        self.assertEqual(self.ids({"jb-sort": "new"}), ["a", "c", "b"])

    def test_sort_by_direct_first_groups_then_scores(self):
        # a and c are direct (rank 0 and 1), b is an aggregator copy.
        self.assertEqual(self.ids({"jb-sort": "direct"}), ["c", "a", "b"])

    def test_every_offered_sort_actually_reorders_something(self):
        # The guard for the bug itself: a sort that changes nothing is a sort
        # with no branch.
        seen = {}
        for value, _ in found_lane.SORT_OPTIONS:
            seen[value] = self.ids({"jb-sort": value})
        self.assertEqual(len(set(tuple(v) for v in seen.values())), len(seen),
                         f"two sorts produced identical order: {seen}")

    # --- the keyword box -------------------------------------------------
    def test_a_keyword_matches_anywhere_inside_the_title(self):
        # "product manager" has to find "Senior Product Manager".
        self.assertEqual(sorted(self.ids({"keywords": ["product manager"],
                                          "jb-sort": "new"})), ["a", "b"])

    def test_a_keyword_is_case_insensitive(self):
        self.assertEqual(sorted(self.ids({"keywords": ["PRODUCT MANAGER"]})), ["a", "b"])

    def test_keywords_are_or_across_titles(self):
        self.assertEqual(sorted(self.ids({"keywords": ["product manager", "analyst"]})),
                         ["a", "b", "c"])

    def test_title_scope_does_not_reach_the_description(self):
        self.assertEqual(self.ids({"keywords": ["kubernetes"]}), [])

    def test_widening_the_scope_reaches_the_description(self):
        self.assertEqual(self.ids({"keywords": ["kubernetes"],
                                   "jb-scope": "title+description"}), ["c"])

    # --- the other filters ------------------------------------------------
    def test_the_exclude_box_removes_a_match(self):
        self.assertEqual(sorted(self.ids({"jb-ex": "senior"})), ["a", "c"])

    def test_the_date_window_bites(self):
        # Clock pinned to 2026-08-20: a is 1 day old, c is 5, b is 10.
        self.assertEqual(self.ids({"date": "7d", "jb-sort": "new"}), ["a", "c"])
        self.assertEqual(self.ids({"date": "24h", "jb-sort": "new"}), ["a"])
        self.assertEqual(self.ids({"date": "60d", "jb-sort": "new"}), ["a", "c", "b"])

    def test_direct_listings_only_drops_the_aggregator_copy(self):
        self.assertEqual(sorted(self.ids({"jb-direct": True})), ["a", "c"])

    def test_a_country_selection_narrows(self):
        self.assertEqual(self.ids({"allLocations": ["US", "IN"], "locations": ["IN"],
                                   "jb-remote": False}), ["b"])

    def test_a_workplace_selection_narrows(self):
        self.assertEqual(self.ids({"workplaces": ["onsite"]}), ["c"])

    def test_max_experience_narrows_and_honours_unknown(self):
        loose = sorted(self.ids({"jb-exp": "3"}))
        self.assertEqual(loose, ["a", "c"])          # c is unknown, allowed
        strict = self.ids({"jb-exp": "3",
                           "unknowns": ["degree", "workplace", "postedAt"]})
        self.assertEqual(strict, ["a"])              # unknown experience excluded

    def test_only_these_companies(self):
        self.assertEqual(self.ids({"jb-cmode": "only", "jb-companies": "Beta"}), ["b"])

    def test_hide_these_companies(self):
        self.assertEqual(sorted(self.ids({"jb-cmode": "hide", "jb-companies": "Beta"})),
                         ["a", "c"])

    def test_two_filters_together_narrow_further_than_either(self):
        one = set(self.ids({"jb-direct": True}))
        two = set(self.ids({"jb-direct": True, "workplaces": ["onsite"]}))
        self.assertTrue(two < one, f"{two} is not a strict subset of {one}")


class DecisionFeedbackTests(ScriptHarness, unittest.TestCase):
    """Pressing a button has to look like something happened.

    The board is a published snapshot and publishing is throttled behind a
    deploy gap, so a decision does not appear in any lane for minutes. The card
    vanished from Found and nothing anywhere admitted to having gained it, which
    from the sofa is indistinguishable from the button being broken. It was not:
    six applications were in flight and five watch entries had been created
    while the board still showed the old counts.
    """

    def test_the_eye_moves_a_count_to_watching(self):
        script = found_lane.SCRIPT
        watch = script[script.index("function doWatch"):]
        watch = watch[:watch.index("function doPass")]
        self.assertIn("bump('watch', 1)", watch)
        self.assertIn("bump('found', -1)", found_lane.SCRIPT)

    def test_apply_moves_a_count_to_in_flight_not_watching(self):
        # An Apply goes straight past the watch list, so bumping Watching would
        # be a lie about where to look for it.
        script = found_lane.SCRIPT
        apply_fn = script[script.index("function doApply"):]
        apply_fn = apply_fn[:apply_fn.index("// Anything acted on")]
        self.assertIn("bump('flight', 1)", apply_fn)
        self.assertNotIn("bump('watch', 1)", apply_fn)

    def test_pass_moves_a_count_to_dropped(self):
        script = found_lane.SCRIPT
        pass_fn = script[script.index("function doPass"):]
        pass_fn = pass_fn[:pass_fn.index("function doApply")]
        self.assertIn("bump('dropped', 1)", pass_fn)

    def test_every_tab_named_here_exists_on_the_page(self):
        # The counts are found by data-tab, so a renamed tab silently stops
        # every one of these working.
        import re
        named = set(re.findall(r"bump\('(\w+)'", found_lane.SCRIPT))
        page = dashboard.build({"profile": {}, "sessions": [], "history": [],
                                "roles": {}, "jobs": {}, "meta": {},
                                "searchConfig": None}, "test")
        offered = set(re.findall(r'data-tab="(\w+)"', page))
        self.assertTrue(named, "no counts are being moved at all")
        self.assertLessEqual(named, offered, f"{named - offered} is not a tab")

    def test_a_failed_decision_puts_the_card_back(self):
        # Swallowing the failure meant a dead network and a successful queue
        # looked identical, and the job was gone either way.
        script = found_lane.SCRIPT
        decide = script[script.index("function decide"):]
        decide = decide[:decide.index("function note")]
        self.assertIn("hidden[job.id] = false", decide)
        self.assertIn("forget(WATCH_KEY", decide)
        self.assertIn("bump('found', 1)", decide)

    def test_a_refusal_from_the_server_counts_as_a_failure(self):
        # ok:false with HTTP 200 is the shape the API actually returns.
        script = found_lane.SCRIPT
        decide = script[script.index("function decide"):]
        self.assertIn("res.ok === false", decide[:decide.index("function note")])


class StoredWordingTests(unittest.TestCase):
    """The rubric's explanations are persisted, so fixing the writer is not enough.

    "posted 1 days ago" was written into fourteen thousand records before anyone
    read one closely. Tidying it where it is displayed reaches all of them
    without a migration and without touching a score.
    """

    def test_the_two_awkward_counts_are_reworded(self):
        self.assertEqual(found_lane.tidy_days("posted 0 days ago"), "posted today")
        self.assertEqual(found_lane.tidy_days("posted 1 days ago"), "posted yesterday")
        self.assertEqual(found_lane.tidy_days("first seen 1 days ago; no posted date"),
                         "first seen yesterday; no posted date")

    def test_every_other_count_is_left_exactly_alone(self):
        for n in [2, 10, 11, 21, 101]:
            with self.subTest(days=n):
                text = "posted %d days ago" % n
                self.assertEqual(found_lane.tidy_days(text), text)

    def test_it_runs_on_the_way_into_the_payload(self):
        table = found_lane.WhyTable()
        table.intern("posted 1 days ago")
        self.assertEqual(table.strings, ["posted yesterday"])


class RealPayloadTests(ScriptHarness, unittest.TestCase):
    """The rows the script actually receives, not rows a test made up.

    Every filter here compares a payload field against a vocabulary the
    controls know. When `compact` starts emitting a different spelling of a
    value the controls never learned, the comparison stops matching and the
    cards vanish in silence: no error, no empty state, just a smaller board.
    That happened twice — once when payload slimming dropped the field the
    description search read, and once when the workplace shipped as "On-site"
    while the checkbox offered "onsite". Both were invisible because the tests
    above hand-build their rows.

    So these run the real `compact` output through the real script.
    """

    def real_row(self, **job):
        base = {"id": "a", "company": "Northwind", "title": "Product Manager",
                "canonicalUrl": "https://boards.greenhouse.io/northwind/jobs/1",
                "canonicalSource": "greenhouse", "postedAt": "2026-08-13",
                "firstSeenAt": "2026-08-19", "score": {"total": 80},
                "location": {"country": "US", "city": "New York"}}
        base.update(job)
        return found_lane.compact(base, found_lane.WhyTable())

    def test_a_real_row_is_visible_with_the_controls_untouched(self):
        # The whole point: a job that came out of the ingest, drawn on a board
        # nobody has filtered yet, is on the page.
        out = self.run_script([self.real_row(workplace="onsite")])
        self.assertEqual(out["ids"], ["a"])

    def test_every_workplace_the_payload_emits_is_one_a_checkbox_selects(self):
        # The vocabulary pin. `wp` is compared with indexOf against the checkbox
        # values, so a label where a slug belongs hides the card for good.
        offered = set(found_lane.WORKPLACE_LABELS)
        for value in list(found_lane.WORKPLACE_LABELS) + ["", None, "Remote", "wat"]:
            with self.subTest(workplace=value):
                shipped = self.real_row(workplace=value)["wp"]
                self.assertIn(shipped, offered | {""},
                              f"workplace {value!r} ships as {shipped!r}, "
                              "which no workplace checkbox can select")

    def test_a_real_row_still_answers_its_workplace_filter(self):
        rows = [self.real_row(id="r", workplace="remote"),
                self.real_row(id="o", workplace="onsite")]
        rows[0]["id"], rows[1]["id"] = "r", "o"
        out = self.run_script(rows, {"workplaces": ["remote"]})
        self.assertEqual(out["ids"], ["r"])

    def test_the_card_still_shows_the_readable_label(self):
        # Shipping the slug must not turn the chip into "onsite".
        out = self.run_script([self.real_row(workplace="onsite")])
        self.assertIn("On-site", out["text"])


if __name__ == "__main__":
    unittest.main()
