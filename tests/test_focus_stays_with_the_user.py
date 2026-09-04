"""The agent works in the background; the user's own clicks move the user.

`open_application` runs on every apply. It ended with `/json/activate`, which
raises the slot's window, so an afternoon of applications took the screen away
from the user again and again -- and at eight slots, eight times as often as it
needed to. The call was also doing nothing else: the lines above it close every
other page, so the posting is the only tab in the window and already selected.

`focus` is the opposite case and keeps its activate. Somebody clicked Open
& finish and wants to be taken there.
"""

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import browser  # noqa: E402

SOURCE = (ROOT / "scripts" / "browser.py").read_text()
TREE = ast.parse(SOURCE)


def body_of(name):
    """One function as code, with comments and its docstring removed.

    Through the AST rather than by slicing text, because the first version
    matched the comment explaining why the call is not there and failed on the
    fix it was written to protect. A test that greps prose tests the prose.
    """
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            stripped = ast.FunctionDef(
                name=node.name, args=node.args, decorator_list=[], returns=None,
                type_params=[],
                body=[b for b in node.body
                      if not (isinstance(b, ast.Expr)
                              and isinstance(b.value, ast.Constant)
                              and isinstance(b.value.value, str))] or [ast.Pass()],
            )
            return ast.unparse(ast.fix_missing_locations(stripped))
    raise LookupError(name)


def every_function():
    return [n.name for n in TREE.body if isinstance(n, ast.FunctionDef)]


class NoAutomatedPathRaisesAWindowTests(unittest.TestCase):
    def test_opening_an_application_does_not_activate(self):
        self.assertNotIn("/json/activate", body_of("open_application"))

    def test_opening_an_application_does_not_reach_for_the_window_server(self):
        opening = body_of("open_application")
        self.assertNotIn("osascript", opening)
        self.assertNotIn("bringToFront", opening)

    def test_it_still_leaves_the_posting_as_the_only_tab(self):
        """Why the activate was redundant, not merely unwanted."""
        opening = body_of("open_application")
        self.assertIn("/json/close/", opening)
        self.assertIn("!= target", opening)

    def test_a_click_by_the_user_still_brings_chrome_forward(self):
        """The refusal must not be blanket; this is what it protects."""
        focusing = body_of("focus")
        self.assertIn("/json/activate", focusing)
        self.assertIn("osascript", focusing)

    def test_no_other_automated_helper_activates(self):
        """A new caller is the way this comes back, so check all of them."""
        allowed = {"focus"}
        offenders = [
            name for name in every_function()
            if name not in allowed
            and ("/json/activate" in body_of(name) or "osascript" in body_of(name))
        ]
        self.assertEqual(
            offenders, [],
            "these raise a window without a user asking: " + ", ".join(offenders),
        )

    def test_the_checker_can_actually_see_an_activate(self):
        """A matcher that matches nothing passes for the wrong reason."""
        self.assertIn("/json/activate", body_of("focus"))
        self.assertGreater(len(every_function()), 20)
