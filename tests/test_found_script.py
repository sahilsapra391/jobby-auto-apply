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
from datetime import datetime, timezone

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

    def test_the_grid_is_four_wide_on_a_wide_screen(self):
        self.assertIn("repeat(4, minmax(0, 1fr))", found_lane.CSS)

    def test_one_long_url_cannot_blow_the_columns_out(self):
        # A grid track's automatic minimum is min-content, so a plain `1fr`
        # lets one unbreakable string in one card widen its whole column and
        # squeeze the other three. Descriptions arrive with tracking URLs in
        # them, and a 156-character one took column one to two thirds of the
        # grid and scrolled the page sideways.
        self.assertNotIn("repeat(4, 1fr)", found_lane.CSS)
        card = found_lane.CSS[found_lane.CSS.index(".jb-card {"):]
        card = card[:card.index("}")]
        self.assertIn("min-width:0", card)
        self.assertIn("overflow-wrap:anywhere", card)

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
    appendChild(c) { this.children.push(c); return c; },
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
function node(id) {
  if (!nodes[id]) {
    nodes[id] = makeNode('div', id);
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

// Checkboxes in this stub default to checked; the direct-only filter is an
// opt-in, so it has to be turned off unless a test asks for it.
node('jb-direct').checked = !!CONTROLS['jb-direct'];
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
    return [];
  },
};
if (CONTROLS.locations) {
  const loc = node('jb-loc');
  loc.selectedOptions = CONTROLS.locations.map(function (v) { return { value: v }; });
}

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
function dumpText(node) {
  return [node.textContent || ''].concat(
    (node.children || []).map(dumpText)).join(' ').replace(/\s+/g, ' ').trim();
}

setTimeout(function () {
  console.log(JSON.stringify({
    shown: grid.children.length,
    text: dumpText(grid),
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
        # Both inside the two-month ceiling: this is about the sort, and a
        # fixture that the date filter drops would pass for the wrong reason.
        jobs = [self.job("old", pa="2026-07-01", sc=95),
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


class DirectListingTests(ScriptHarness, unittest.TestCase):
    """Applying through the employer's own ATS beats applying through a
    redirect, so the lane leads with direct listings."""

    def test_direct_listings_lead_even_when_an_aggregator_scores_higher(self):
        out = self.run_script([self.job("agg", rank=2, src="careerjet", sc=95),
                               self.job("ats", rank=0, src="greenhouse", sc=70)])
        self.assertEqual(out["ids"], ["ats", "agg"])

    def test_within_a_group_the_better_match_still_leads(self):
        out = self.run_script([self.job("lo", rank=0, sc=60),
                               self.job("hi", rank=0, sc=90)])
        self.assertEqual(out["ids"], ["hi", "lo"])

    def test_a_careers_page_counts_as_direct(self):
        out = self.run_script([self.job("agg", rank=2, sc=99),
                               self.job("careers", rank=1, sc=50)])
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
