"""v2's constraints are checked against the board, never against a paraphrase.

A test that spells out "no fill mode on a panel keyframe" in its own words can
drift from what `premium_ui.py` actually says -- the same failure as the
hand-port, one level up. The hand-port turned that rule into "no transform on a
panel keyframe", which is wrong and which no amount of careful reading would
have caught, because the reader and the writer were the same.

So the oracle here is `premium_ui.py` and `dashboard.build`. Every assertion
compares v2 against what those produce, and none of them restates a rule.
"""

import re
import sys
import unittest
import sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import dashboard        # noqa: E402
import premium_ui       # noqa: E402
import sync_v2_styles   # noqa: E402

WEB = REPO / "apps" / "web" / "app"


def block_at(css, start):
    """The text of the brace-balanced block beginning at `start`.

    Regexes do not balance braces, and a lazy `.*?\n\s*\}` looks like it
    does. The first version of this file used one to pull out
    `@keyframes premium-panel-in` and matched four thousand characters of
    unrelated rules, so an assertion that the keyframe animates a transform
    passed on somebody else's `transform` far below it. Deleting the transform
    from the keyframe changed nothing the test could see.
    """
    depth, i = 0, css.index("{", start)
    for j in range(i, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[start:j + 1]
    raise AssertionError(f"unbalanced braces from offset {start}")


def blocks_named(css, prefix):
    """Every brace-balanced block whose selector starts with `prefix`."""
    out, at = [], 0
    while True:
        at = css.find(prefix, at)
        if at == -1:
            return out
        out.append(block_at(css, at))
        at += len(prefix)


def v2_markup():
    """Every class v2's own pages use, and the raw source they come from."""
    source = "\n".join(p.read_text(encoding="utf-8")
                       for p in sorted(WEB.rglob("*.tsx")))
    classes = set()
    for match in re.finditer(r'className=(?:"([^"]*)"|\{`([^`]*)`\})', source):
        for chunk in (match.group(1) or match.group(2) or "").split():
            if "$" in chunk or "{" in chunk:
                continue
            classes.add(chunk)
    # Ternaries: className={x ? "tab on" : "tab"}
    #
    # **A ternary's condition is not a class.** `className={lane === "found"
    # ? "jb-grid" : "grid"}` contributed `found`, and the guard then reported
    # v2 as inventing a class it never puts on an element. Operands of an
    # equality test are dropped for that reason.
    for match in re.finditer(r'className=\{([^}]*)\}', source):
        body = match.group(1)
        operands = set(re.findall(r'[=!]==\s*"([^"]+)"', body))
        for literal in re.findall(r'"([^"]+)"', body):
            if literal in operands:
                continue
            for chunk in literal.split():
                classes.add(chunk)
    return source, classes


def board_markup():
    """The real board's rendered HTML, from a synthetic state."""
    return dashboard.build(sync_v2_styles.SYNTHETIC_STATE, "2026-01-01T00:00:00Z")


def hollow(state):
    """The same state with every collection emptied.

    What "the fixture populated nothing" renders as, computed rather than
    guessed, so the floor below it moves when the board does.
    """
    return {k: ({} if isinstance(v, dict) else [] if isinstance(v, list) else v)
            for k, v in state.items()}


def board_classes(state=None):
    """Every class the board itself puts on an element.

    **A class the board emits is not an invented one, even when no rule
    selects it.** `retry`, `finish`, `undrop` and `skills` are behavioural
    hooks and semantic markers that `dashboard.py` writes and `board.css` never
    styles, so a guard that only reads the stylesheet calls them inventions the
    moment v2 draws the same row the board draws.

    Rendered from a row-bearing state rather than the empty one, because the
    lanes are exactly where those classes live: against an empty state every
    table renders `empty_state(...)` and this set silently loses them.
    """
    from test_v2_lane_shapes import ROW_BEARING_STATE
    html = dashboard.build(state or ROW_BEARING_STATE, "2026-01-01T00:00:00Z")
    return {c for m in re.findall(r'class=["\']([^"\']+)["\']', html)
            for c in m.split()}


class EveryClassV2UsesIsOneTheBoardStyles(unittest.TestCase):
    """A carried-across rule does nothing if the markup does not carry the
    class it selects. This is the half a stylesheet diff cannot see."""

    def test_no_class_is_invented(self):
        css = sync_v2_styles.TARGET.read_text(encoding="utf-8")
        selectors = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
        from test_v2_lane_shapes import ROW_BEARING_STATE
        emitted = board_classes()
        # **"Not zero" is not "complete", and that distinction cost this
        # check two thirds of its subject.** The guard here used to be
        # `assertTrue(emitted)`, which is the anti-vacuity check this
        # repository prescribes, set to a threshold nothing could fail: the
        # fixture carried `jobs: []`, Found drew `empty_state`, and the entire
        # filter block -- date chips, Advanced search, the 199-country picker,
        # the actions row -- never reached the HTML. 99 classes for a wholly
        # empty state, 132 for one with rows but no pool, 164 with one job.
        # All three are non-empty.
        #
        # The floor is derived rather than a literal, so it cannot rot the way
        # a number would: rendering the same state with every collection
        # emptied is what "the fixture populated nothing" looks like, and the
        # real fixture has to beat it by a margin.
        self.assertTrue(emitted, "the board rendered no classes; the check is vacuous")
        self.assertGreater(
            len(emitted), len(board_classes(hollow(ROW_BEARING_STATE))) + 40,
            "the fixture renders barely more than an empty board, so this "
            "scan is running against a fraction of the markup it claims to "
            "check. The usual cause is a collection left empty, which makes a "
            "lane draw its empty state and takes the panel above it too.")
        _, used = v2_markup()
        allowed = selectors | emitted
        invented = sorted(c for c in used if c not in allowed)
        self.assertEqual(
            invented, [],
            f"v2 uses classes the board's stylesheet does not define: "
            f"{invented}. Anything new belongs in premium_ui.py first, or it "
            f"is a second design system growing beside the first.")

    def test_it_is_actually_reading_some_classes(self):
        """The control. An extractor that finds nothing passes the test above
        perfectly."""
        _, used = v2_markup()
        self.assertGreaterEqual(len(used), 8, f"only found {used}")
        for expected in ("appbar", "wrap", "top", "segwrap", "seg", "tab", "panel"):
            self.assertIn(expected, used)


class TheConstrainedElementsCarryTheBoardsOwnClasses(unittest.TestCase):
    """Each constraint in CLAUDE.md is a rule about a *named* element. The rule
    travels with the stylesheet; what has to be checked here is that v2 puts
    the name on the same thing the board puts it on."""

    @classmethod
    def setUpClass(cls):
        cls.board = board_markup()
        cls.source, cls.used = v2_markup()

    def test_the_lane_is_the_element_the_board_calls_panel(self):
        self.assertIn('class="panel', self.board)
        self.assertIn("panel", self.used)

    def test_the_app_bar_is_the_element_the_board_names_for_transitions(self):
        """`view-transition-name: jobby-header` is on `.appbar` in
        premium_ui.py. Read from there rather than asserted."""
        rule = re.search(r"\.appbar\s*\{([^}]*)\}", premium_ui.CSS)
        self.assertIsNotNone(rule)
        self.assertIn("view-transition-name", rule.group(1))
        self.assertIn("appbar", self.used)

    def test_nothing_else_in_premium_ui_is_transition_named(self):
        """The constraint itself, read out of the source of truth: whatever
        premium_ui names, it is exactly one selector and it is the app bar.
        If a future edit names `.panel`, this fails without anyone having
        written down what `.panel` is."""
        named = []
        for block in re.finditer(r"([^{}]+)\{([^}]*)\}", premium_ui.CSS):
            decl = re.search(r"view-transition-name\s*:\s*([^;]+)", block.group(2))
            if not decl or decl.group(1).strip().startswith("none"):
                continue
            named.append(block.group(1).strip().split("\n")[-1].strip())
        self.assertEqual(named, [".appbar"], f"transition-named: {named}")

    def test_the_panel_keyframe_holds_nothing(self):
        """The real constraint, taken from premium_ui.py's own animation
        shorthand rather than from a sentence about it. The hand-port read this
        as "no transform", which is wrong -- the board's keyframe animates
        transform and must, it simply may not hold it."""
        rule = re.search(r"(?<![\w-])\.panel\s*\{([^}]*)\}", premium_ui.CSS)
        self.assertIsNotNone(rule, "premium_ui has no .panel rule")
        shorthand = re.search(r"animation\s*:\s*([^;]+)", rule.group(1))
        self.assertIsNotNone(shorthand, "the panel is not animated any more")
        for mode in ("forwards", "backwards", "both"):
            self.assertNotIn(mode, shorthand.group(1),
                             "the panel animation holds its final frame; on a "
                             "50,000px lane that exceeds the max texture")
        # And the control: it does animate a transform, so a future test that
        # forbade transforms would fail here rather than pass quietly. Brace
        # matched, not regexed -- see block_at.
        frames = blocks_named(premium_ui.CSS, "@keyframes premium-panel-in")
        self.assertEqual(len(frames), 1, "expected exactly one panel keyframe")
        # A *moving* transform, not the word. The keyframe ends on
        # `transform:none`, so `assertIn("transform", ...)` passes with the
        # opening `translateY` deleted -- which is precisely the mutation this
        # assertion exists to catch, and it survived the first two attempts.
        moving = [v.strip() for v in re.findall(r"transform\s*:\s*([^;}]+)", frames[0])
                  if v.strip() not in ("none", "initial", "unset")]
        self.assertTrue(
            moving,
            "the panel keyframe animates no moving transform, so 'no fill "
            "mode' has quietly become 'no transform' -- the exact confusion "
            "the hand-port made")
        self.assertLess(len(frames[0]), 400,
                        "the keyframe block came out implausibly large, so the "
                        "brace matching is picking up its neighbours")


class TheThemeAndMotionRulesSurvive(unittest.TestCase):
    """Read out of the generated sheet, which is the thing v2 serves."""

    @classmethod
    def setUpClass(cls):
        cls.css = sync_v2_styles.TARGET.read_text(encoding="utf-8")

    def test_all_three_theme_states_are_present(self):
        self.assertRegex(self.css, r"(^|\})\s*:root\s*\{")
        self.assertIn("prefers-color-scheme", self.css)
        self.assertIn('[data-theme="dark"]', self.css)

    def test_the_reduced_motion_query_survives(self):
        self.assertIn("prefers-reduced-motion", self.css)

    def test_every_reduced_motion_block_is_the_boards_own(self):
        """Not a paraphrase, and not just the first one.

        premium_ui.py writes **two** reduced-motion blocks. The first version
        of this test matched one with a regex, which happened to be the small
        `.jb-select` one, so deleting the other left it passing. Every block
        premium_ui writes has to appear verbatim in what v2 serves."""
        blocks = blocks_named(premium_ui.CSS, "@media (prefers-reduced-motion")
        self.assertGreaterEqual(len(blocks), 2,
                                f"premium_ui has {len(blocks)} reduced-motion "
                                f"blocks; this test was written when it had 2, "
                                f"and a drop is what it exists to catch")
        for block in blocks:
            with self.subTest(block=block[:60]):
                self.assertIn(block, self.css)


if __name__ == "__main__":
    unittest.main()
