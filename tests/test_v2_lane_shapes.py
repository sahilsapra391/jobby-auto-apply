"""Each v2 lane draws the shape the board draws for it.

**The board draws five shapes across six lanes**: Found, Needs you and In
flight are three different cards, Watching, Submitted and Dropped are three
different tables. v2 shipped one generic card for all of them, which is most of
why it stopped looking like Jobby.

The comparison is against a real `dashboard.build` render, not against a list
of classes written here. A list written here drifts the moment the board grows
a column, and it drifts in the passing direction.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

#: A quoted string in TSX: the opening quote, then everything up to its
#: own match. Written once as a constant because escaping three quote
#: characters inline is how the first version silently matched nothing.
QUOTED = re.compile(r'(["\'`])(.*?)\1', re.S)

ROWS = REPO / "apps" / "web" / "app" / "_ui" / "Rows.tsx"

#: Enough state for every lane to render at least one row. An empty state
#: renders `empty_state(...)` in place of every table, and a comparison against
#: a lane that drew nothing passes while measuring nothing -- which is the
#: failure this repository has now catalogued nine times.
def _session(key, **kw):
    base = {"applicationId": key, "company": f"Co{key}", "role": f"Role {key}",
            "url": f"https://boards.greenhouse.io/co{key}/jobs/{key}",
            "updatedAt": "2026-08-30T00:00:00Z"}
    base.update(kw)
    return base


ROW_BEARING_STATE = {
    "userId": "sync", "profile": {}, "memoryBank": {}, "searchConfig": {},
    "providers": {}, "meta": {},
    # **One found job, so the Found panel renders at all.** With `jobs: []` the
    # lane draws `empty_state` and the entire filter block above it -- the date
    # chips, Advanced search, the location picker and the Save search row --
    # never reaches the HTML. `board_classes()` reads this render to decide
    # which classes the board emits, so an empty pool silently narrowed it to
    # the table lanes and reported `jb-actions` as a class v2 had invented.
    # `state: "new"` is what `found_lane.select` filters on. Without it the
    # job is in the pool and not in the lane, which is the same empty Found
    # panel by a subtler route.
    "jobs": {"j1": {"id": "j1", "company": "CoJ1", "role": "Role j1",
                    "url": "https://example.com/j1", "postedAt": "2026-08-30",
                    "location": {"country": "US", "text": "Remote"},
                    "fitScore": 70, "state": "new"}},
    "sessions": [
        _session("w1", status="watch"),
        _session("a1", status="review", pendingFields=[{"question": "Q?"}]),
        _session("f1", status="inflight", phase="Filling the form"),
        _session("d1", status="dropped", droppedAt="2026-08-29T00:00:00Z",
                 droppedFrom="watch"),
        _session("s1", status="active"),
    ],
    "history": [{"event": "completed", "applicationId": "s1",
                 "at": "2026-08-28T00:00:00Z"}],
    "roles": {k: {"company": f"Co{k}", "role": f"Role {k}", "url": f"https://x/{k}"}
              for k in ("w1", "a1", "f1", "d1", "s1")},
}

#: Row-level classes, as opposed to panel chrome. Restricted so the comparison
#: is about the shape of a row rather than about the filter bar above it.
ROW_CLASSES = {
    "company-cell", "company-mark", "muted", "when", "loc", "skills",
    "note-cell", "rowacts", "rowacts-btns", "rowacts-inline", "btn", "apply",
    "xbtn", "drop", "chip", "card", "hold", "flight", "company-title", "role",
    "todo", "stub", "undo", "act", "retry", "finish", "mark", "ats", "steps",
    "step", "phase", "warm", "lane", "undrop", "jb-card", "jb-head",
    "jb-identity", "jb-co", "jb-ti", "jb-sum", "jb-chips", "jb-acts",
}

#: Classes the board draws and v2 cannot, each with the measurement that says
#: why. **Not a way to make the test pass**: every entry is a fact about the
#: data, and when the data arrives the entry comes out and the test fails until
#: the class does.
CANNOT_DRAW_YET = {
    # A link needs a URL. applications.posting_id is NULL for all 204 rows, so
    # no application lane can offer "View posting". phase3-inputs.md §5.
    "go": "applications carry no posting URL; posting_id is NULL for all 204",
    # The board's Needs you card lists pendingFields and offers Undo on a
    # marked-submitted stub. Neither is mirrored by §3.
    "stub": "the marked-submitted stub is session state, not mirrored",
    "undo": "undo acts on the stub above it",
    # The Found ring is a conic-gradient of the match percentage, and no score
    # reaches Postgres: postings has no score column, job_scores does not
    # exist, and jobs.score is a frozen snapshot over 9,286 of 17,339 rows.
    "jb-ring": "no match score is mirrored; drawing 0% would be a wrong number",
}


def tsx_classes(source):
    """Every class name a TSX file can put on an element.

    **A `className` is not always a string literal.** The first version matched
    `className="..."` and stopped at the first brace, so
    `className={on ? "step on" : "step"}` contributed nothing and the test
    reported `step` missing from a file that draws it. The instrument was
    wrong, not the code.

    So: find each `className=`, take the literal or the balanced `{...}` after
    it, and collect every quoted string inside. Over-collecting from a ternary
    is safe here because both arms really are class names.
    """
    out = set()
    for match in re.finditer(r"className=", source):
        rest = source[match.end():]
        if rest[:1] in ('"', "'"):
            # Already the class string. Searching *this* for quoted substrings
            # finds none, which is how the first version extracted 4 classes
            # from 53 attributes and reported the file as drawing almost
            # nothing.
            quote = rest[0]
            end = rest.find(quote, 1)
            out.update(rest[1:end].split() if end > 0 else [])
            continue
        elif rest[:1] == "{":
            depth, i = 0, 0
            while i < len(rest):
                if rest[i] == "{":
                    depth += 1
                elif rest[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            chunk = rest[1:i]
        else:
            continue
        for _q, literal in QUOTED.findall(chunk):
            out.update(literal.split())
    return out


def classes_in(markup):
    return {c for m in re.findall(r'class=["\']([^"\']+)["\']', markup)
            for c in m.split()}


def panel(html, pid):
    start = html.find(f'id="{pid}"')
    if start < 0:
        raise AssertionError(f"the board rendered no panel {pid}")
    end = html.find('<div class="panel', start + 1)
    return html[start:end if end > start else start + 9000]


class LaneShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dashboard
        cls.board = dashboard.build(ROW_BEARING_STATE, "2026-01-01T00:00:00Z")
        cls.tsx = ROWS.read_text(encoding="utf-8")
        cls.mine = tsx_classes(cls.tsx)

    def test_the_synthetic_state_actually_renders_every_lane(self):
        """**The vacuity guard, and it is the point of the file.**

        Every other assertion here compares two sets of classes. If the board
        rendered an empty lane, its set is empty, every comparison passes, and
        the test reports success about a lane it never looked at. That is the
        exact shape of the eight silent checks CLAUDE.md catalogues, so it is
        asserted first and by row count rather than assumed.
        """
        for pid, want in (("p-watch", "<tr"), ("p-done", "<tr"),
                          ("p-act", "<article"), ("p-flight", "<article")):
            seg = panel(self.board, pid)
            self.assertIn(want, seg,
                          f"{pid} rendered no rows, so any comparison against "
                          f"it is vacuous")

    def test_each_table_lane_is_a_table_and_not_a_card(self):
        for pid in ("p-watch", "p-done"):
            self.assertIn("<table>", panel(self.board, pid))
        # And v2 knows it: the three table lanes are named in LaneList.
        lanelist = (REPO / "apps" / "web" / "app" / "_ui" / "LaneList.tsx").read_text()
        for lane in ("done", "watch", "dropped"):
            self.assertRegex(lanelist, rf"\b{lane}:\s*\{{\s*$|\b{lane}:\s*\{{",
                             f"{lane} is not declared as a table lane")

    def test_v2_draws_every_row_class_the_board_draws(self):
        drawn = set()
        for pid in ("p-watch", "p-done", "p-act", "p-flight"):
            drawn |= classes_in(panel(self.board, pid))
        want = (drawn & ROW_CLASSES) - set(CANNOT_DRAW_YET)
        self.assertTrue(want, "no row classes were collected; the check is vacuous")
        missing = sorted(c for c in want if c not in self.mine)
        self.assertEqual(missing, [],
                         f"the board draws these and v2 does not: {missing}")

    def test_nothing_is_excused_without_a_reason(self):
        for name, why in CANNOT_DRAW_YET.items():
            self.assertTrue(why and len(why) > 20,
                            f"{name} is excused with no measurement behind it")

    def test_no_lane_control_is_left_inert(self):
        """The inverse of what this asserted, because the gate has closed.

        It used to require every `<button>` in a lane to carry `disabled`: v2
        wrote nothing until the parity gate closed, and a control that looked
        live would have been lying. That was right then and is the opposite of
        right now. `live_board` flipped on 2026-09-05 and the lane controls
        write.

        **Its own anti-vacuity floor is what retired it.** After "Open &
        finish" was wired, `Rows.tsx` contains no raw `<button>` at all -- every
        control is an `<Act>` -- so `assertTrue(buttons)` failed rather than the
        rule passing on an empty list. A check that goes red when its subject
        disappears is worth more than one that quietly certifies nothing, and
        this is the second time this week that a floor rather than an assertion
        found the real change.

        The property that replaced it lives in
        `tests/lane_actions_are_driven.test.mjs`, which asks whether anything
        *drives* each control rather than what it renders as: an `<Act>` must
        carry a `body`, and a `<button>` with a literal `disabled` must be
        declared with a reason. That is the check "Open & finish" needed and
        did not have while it sat dark on every Needs You card since cutover.

        What remains here is the half that is still true: a lane must not
        contain a control nothing can drive. It is asserted on the source the
        same way, with comments stripped first for the reason below.

        **Comments are stripped, and that is not a convenience.** The scan is a
        regex over source, so a comment explaining the rule -- "every
        `<button>` in a lane must carry `disabled`" -- is itself a bare
        `<button>` and fails the check it documents.
        """
        source = re.sub(r"/\*.*?\*/", " ", self.tsx, flags=re.S)
        source = re.sub(r"(?m)^\s*//.*$", " ", source)
        buttons = re.findall(r"<button[^>]*>", source, re.S)
        inert = [b for b in buttons if re.search(r"\bdisabled(?![=\w])", b)]
        self.assertEqual(
            inert, [],
            "these lane controls are hard-disabled, so they render as finished "
            "work and post nothing. See tests/lane_actions_are_driven.test.mjs "
            "for why that is the failure rather than the safe default: "
            f"{inert}")
