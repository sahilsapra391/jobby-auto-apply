"""Each v2 lane draws the shape the board has always drawn for it.

**Five shapes across six lanes**: Found, Needs you and In flight are three
different cards, Watching, Submitted and Dropped are three different tables.
v2 once shipped one generic card for all of them, which is most of why it
stopped looking like Jobby.

This compared v2 against a live render of the original Python board. That
board is gone, so the row classes it drew in each lane are recorded below, as
measured from its last render on the day it was removed (2026-10-20), against a
fixture with one row in every lane.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: A quoted string in TSX: the opening quote, then everything up to its
#: own match. Written once as a constant because escaping three quote
#: characters inline is how the first version silently matched nothing.
QUOTED = re.compile(r'(["\'`])(.*?)\1', re.S)

ROWS = REPO / "apps" / "web" / "app" / "_ui" / "Rows.tsx"

#: The row-level classes the original board drew in each lane, as opposed to
#: panel chrome such as the filter bar above it.
BOARD_ROW_CLASSES = {
    "watch": {"apply", "btn", "company-cell", "company-mark", "drop", "loc",
              "muted", "note-cell", "rowacts", "rowacts-btns", "skills",
              "when", "xbtn"},
    "done": {"chip", "company-cell", "company-mark", "hold", "muted", "when"},
    "act": {"act", "ats", "btn", "card", "chip", "company-mark",
            "company-title", "drop", "finish", "hold", "mark", "retry", "role",
            "todo"},
    "flight": {"act", "ats", "btn", "card", "chip", "company-mark",
               "company-title", "drop", "flight", "phase", "retry", "role",
               "step", "steps", "warm"},
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


class LaneShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tsx = ROWS.read_text(encoding="utf-8")
        cls.mine = tsx_classes(cls.tsx)

    def test_each_table_lane_is_a_table_and_not_a_card(self):
        # The three table lanes are named in LaneList.
        lanelist = (REPO / "apps" / "web" / "app" / "_ui" / "LaneList.tsx").read_text()
        for lane in ("done", "watch", "dropped"):
            self.assertRegex(lanelist, rf"\b{lane}:\s*\{{\s*$|\b{lane}:\s*\{{",
                             f"{lane} is not declared as a table lane")

    def test_v2_draws_every_row_class_the_board_draws(self):
        want = set().union(*BOARD_ROW_CLASSES.values())
        self.assertEqual(len(want), 29, "the recorded set changed shape")
        missing = sorted(c for c in want if c not in self.mine)
        self.assertEqual(missing, [],
                         f"the board drew these and v2 does not: {missing}")

    def test_the_extractor_reads_the_file(self):
        """The control: an extractor that finds nothing passes the test above
        only if the recorded set is empty too, so it is checked directly."""
        self.assertGreaterEqual(len(self.mine), 40, f"only {len(self.mine)} classes")

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
