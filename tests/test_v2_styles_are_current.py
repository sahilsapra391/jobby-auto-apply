"""The v2 board's stylesheet is the board's stylesheet, byte for byte.

The requirement is that v2 looks one for one identical to the board it
replaces. A hand-port delivers that on the day it is written and drifts from
then on, silently, because nothing compares the two -- and the first attempt
here had already introduced a difference: it forbade `transform` in the panel
keyframe, which the real board uses. The rule is no *fill mode*, because a
transform held on a 50,000px layer exceeds the max texture; the port had turned
a rule about holding into a rule about transforms.

So it is generated, and this is the check that keeps it generated. Same shape
as the deployed-renderer check, and for the same reason: the working tree is
not the thing that ships.
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import sync_v2_styles  # noqa: E402


class TheGeneratedStylesheetIsCurrent(unittest.TestCase):
    def test_the_file_on_disk_matches_the_board(self):
        self.assertEqual(
            sync_v2_styles.main(["--check"]), 0,
            "apps/web/app/board.css is stale. Run "
            "'python3 scripts/sync_v2_styles.py' and commit it, or v2 stops "
            "matching the board it is replacing.")

    def test_it_carries_the_premium_ui_stylesheet_verbatim(self):
        """The half that makes v2 Soft Premium rather than merely dark.
        `premium_ui.CSS` is appended last so it overrides the four before it,
        and if it were dropped the page would still render, in the base
        stylesheet, looking like a different product."""
        import premium_ui
        self.assertIn(premium_ui.CSS.strip(),
                      sync_v2_styles.TARGET.read_text(encoding="utf-8"))

    def test_it_carries_the_other_four_sources_too(self):
        """One marker from each, so dropping any single source fails here
        rather than at a glance months later."""
        css = sync_v2_styles.TARGET.read_text(encoding="utf-8")
        for module, marker in (("dashboard", ".sysbanner"),
                               ("found_lane", ".jb-select"),
                               ("settings_panel", ".provider"),
                               ("profile_panel", ".profile-page")):
            with self.subTest(source=module):
                self.assertIn(marker, css,
                              f"nothing from {module} is in the generated "
                              f"stylesheet")

    def test_the_three_widths_survive_generation(self):
        """The shell, the reading column and the lane. A single width flattens
        the page into a wall, which is what the first v2 scaffold did."""
        css = sync_v2_styles.TARGET.read_text(encoding="utf-8")
        self.assertRegex(css, r"--shell\s*:\s*1800px")
        self.assertRegex(css, r"\.top\s*\{[^}]*max-width\s*:\s*1360px")
        self.assertRegex(css, r"\.segwrap\s*\{[^}]*max-width\s*:\s*1360px")
        # The lane takes the wrap's width, so it must NOT be capped at 1360.
        lane = re.search(r"(?<![\w-])\.panel\s*\{([^}]*)\}", css)
        self.assertIsNotNone(lane, "no .panel rule at all")
        self.assertNotIn("1360px", lane.group(1),
                         "the lane was capped to the reading width, which is "
                         "the difference that made v2 read as a document")


class TheGeneratorReadsNothingReal(unittest.TestCase):
    def test_the_synthetic_state_carries_no_store_data(self):
        """A generator that reads the real store is one that can leak it, and
        it does not need to: a fixed empty state produces byte-identical CSS."""
        state = sync_v2_styles.SYNTHETIC_STATE
        self.assertEqual(state["sessions"], {})
        self.assertEqual(state["jobs"], [])
        self.assertEqual(state["history"], [])
        self.assertEqual(state["profile"], {})

    def test_generation_does_not_touch_the_store(self):
        from unittest import mock
        with mock.patch("store.root_dir", side_effect=AssertionError(
                "the generator read the store root")):
            sync_v2_styles.board_css()

    def test_it_refuses_rather_than_guesses_if_the_head_gains_a_block(self):
        """Two <style> blocks and the generator must stop, not pick one. A
        stylesheet that is quietly half the page is the failure this whole
        file exists to prevent."""
        from unittest import mock
        import dashboard
        with mock.patch.object(dashboard, "build",
                               return_value="<style>a{}</style><style>b{}</style>"):
            with self.assertRaises(SystemExit):
                sync_v2_styles.board_css()
