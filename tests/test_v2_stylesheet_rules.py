"""The board's stylesheet rules that CLAUDE.md names, checked in `board.css`.

`apps/web/app/board.css` used to be generated from the original Python board,
and this file compared v2 against that board's render. The board is gone and
`board.css` is the source now, so the oracle is the stylesheet itself plus the
markup v2 renders. Two kinds of check survive the move:

- **No class is invented.** Every class v2 puts on an element is one
  `board.css` styles, or one of a short, declared list of behavioural hooks.
  A class that is neither is a second design system growing beside the first.
- **The motion and theme rules hold.** Only `.appbar` is transition-named, no
  `.panel` animation holds its final frame, the panel keyframe still moves, and
  the three theme states and the reduced-motion query are present.

`tests/test_v2_styles_are_reachable.py` holds the converse: no class is styled
and rendered by nothing.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "apps" / "web" / "app"
CSS = WEB / "board.css"

#: Classes v2 renders that no rule selects, each a hook the markup needs for a
#: reason other than styling. Recorded when the generator was retired
#: (2026-10-20), when the original board emitted all seven itself.
HOOKS = {
    "answers-note": "the answers modal's note, found by its script",
    "bank-groups": "the Memory Bank's group container, found by its script",
    "finish": "the Needs you card's Open & finish control, a behaviour hook",
    "jb-actions": "the Found filter block's actions row, a layout marker",
    "retry": "the Retry control on a parked row, a behaviour hook",
    "skills": "a semantic marker on the card's skills list",
    "undrop": "the Dropped row's Undrop control, a behaviour hook",
}


def block_at(css, start):
    """The text of the brace-balanced block beginning at `start`.

    Regexes do not balance braces, and a lazy `.*?\\n\\s*\\}` looks like it
    does. The first version of this check used one to pull out
    `@keyframes premium-panel-in` and matched four thousand characters of
    unrelated rules, so an assertion that the keyframe animates a transform
    passed on somebody else's `transform` far below it.
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


def stylesheet():
    """`board.css` with comments removed, so prose cannot satisfy a check."""
    return re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)


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


class EveryClassV2UsesIsStyledOrDeclared(unittest.TestCase):
    def test_no_class_is_invented(self):
        selectors = set(re.findall(r"\.([A-Za-z][\w-]*)", stylesheet()))
        _, used = v2_markup()
        invented = sorted(c for c in used if c not in selectors and c not in HOOKS)
        self.assertEqual(
            invented, [],
            f"v2 uses classes board.css does not style: {invented}. Style it "
            f"in board.css, or add it to HOOKS with the reason it carries no "
            f"rule.")

    def test_every_hook_is_still_used_and_still_unstyled(self):
        """A hook that gained a rule, or left the markup, is a stale entry."""
        selectors = set(re.findall(r"\.([A-Za-z][\w-]*)", stylesheet()))
        _, used = v2_markup()
        for name, reason in HOOKS.items():
            with self.subTest(hook=name):
                self.assertTrue(reason)
                self.assertIn(name, used, f"{name} is no longer rendered")
                self.assertNotIn(name, selectors, f"{name} is styled now")

    def test_it_is_actually_reading_some_classes(self):
        """The control. An extractor that finds nothing passes the test above
        perfectly."""
        _, used = v2_markup()
        self.assertGreaterEqual(len(used), 150, f"only found {len(used)}")
        for expected in ("appbar", "wrap", "top", "segwrap", "seg", "tab", "panel"):
            self.assertIn(expected, used)


class TheMotionRulesHold(unittest.TestCase):
    """Each is a rule about a named element, read out of the stylesheet."""

    @classmethod
    def setUpClass(cls):
        cls.css = stylesheet()
        cls.source, cls.used = v2_markup()

    def test_only_the_app_bar_is_transition_named(self):
        """Home and Settings switch inside a view transition, and only the app
        bar may be named for it. A named `.panel` animated the height
        difference between two panels and stretched both snapshots."""
        named = []
        for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", self.css):
            decl = re.search(r"view-transition-name\s*:\s*([^;]+)", block.group(2))
            if not decl or decl.group(1).strip().startswith("none"):
                continue
            named.append(block.group(1).strip().split("\n")[-1].strip())
        self.assertEqual(named, [".appbar"], f"transition-named: {named}")
        self.assertIn("appbar", self.used)
        self.assertIn("panel", self.used)

    def test_no_panel_animation_holds_its_final_frame(self):
        """A transform held on a layer as tall as the Found lane exceeds the
        max texture and rasterizes blank, so no fill mode on a panel."""
        rules = re.findall(r"(?<![\w-])\.panel\s*\{([^}]*)\}", self.css)
        animated = [r for r in rules if re.search(r"animation\s*:", r)]
        self.assertGreaterEqual(len(animated), 1, "the panel is not animated any more")
        for rule in animated:
            shorthand = re.search(r"animation\s*:\s*([^;]+)", rule).group(1)
            for mode in ("forwards", "backwards", "both"):
                self.assertNotIn(mode, shorthand,
                                 "a panel animation holds its final frame")

    def test_the_panel_keyframe_still_moves(self):
        """The control: the rule is "no fill mode", not "no transform"."""
        frames = blocks_named(self.css, "@keyframes premium-panel-in")
        self.assertEqual(len(frames), 1, "expected exactly one panel keyframe")
        moving = [v.strip() for v in re.findall(r"transform\s*:\s*([^;}]+)", frames[0])
                  if v.strip() not in ("none", "initial", "unset")]
        self.assertTrue(moving, "the panel keyframe animates no moving transform")
        self.assertLess(len(frames[0]), 400,
                        "the keyframe block came out implausibly large, so the "
                        "brace matching is picking up its neighbours")


class TheThemeAndMotionQueriesSurvive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = stylesheet()

    def test_all_three_theme_states_are_present(self):
        self.assertRegex(self.css, r"(^|\})\s*:root\s*\{")
        self.assertIn("prefers-color-scheme", self.css)
        self.assertIn('[data-theme="dark"]', self.css)

    def test_the_reduced_motion_queries_survive(self):
        """Five blocks when the generator was retired; a drop is what this
        catches."""
        blocks = blocks_named(self.css, "@media (prefers-reduced-motion")
        self.assertGreaterEqual(len(blocks), 5, f"{len(blocks)} reduced-motion blocks")
