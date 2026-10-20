"""The card's score breakdown is drawn against the weights the device scores with.

`apps/web/app/rubric.ts` is hand-owned now, and two of its constants are not
free to change on their own: the Found card draws `earned/possible` for each
component, `earned` comes from the score the device stored, and `possible` is
`WEIGHTS`. If the two disagree the bars render against the wrong denominators,
silently. `scripts/match_score.py` is the one that computes the score, so it is
the authority and this compares the TypeScript against it.
"""

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import match_score  # noqa: E402

RUBRIC = REPO / "apps" / "web" / "app" / "rubric.ts"


def exported(name):
    """The JSON literal assigned to `export const <name>` in rubric.ts."""
    source = RUBRIC.read_text(encoding="utf-8")
    found = re.search(rf"export const {name}\b[^=]*=\s*(.+?)(?: as const)?;\n", source)
    if not found:
        raise AssertionError(f"rubric.ts no longer exports {name}")
    return json.loads(found.group(1))


class TheRubricIsTheScore(unittest.TestCase):
    def test_the_weights_are_the_ones_the_device_scores_with(self):
        self.assertEqual(exported("WEIGHTS"), dict(match_score.WEIGHTS))

    def test_the_components_are_the_scores_own_and_in_its_order(self):
        self.assertEqual(exported("COMPONENTS"), list(match_score.WEIGHTS))

    def test_the_comparison_reads_something(self):
        """The control: an empty dict on both sides would pass the first test."""
        self.assertGreaterEqual(len(match_score.WEIGHTS), 5)
        self.assertEqual(sum(match_score.WEIGHTS.values()), 100)
