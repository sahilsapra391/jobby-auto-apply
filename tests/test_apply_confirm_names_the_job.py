"""The Apply confirmation names the employer and the role, or it names nothing.

**Both props were `undefined` on every Found card.** `Rows.tsx` passed
`row.company` and `row.role`; the Found select produces `p.title` and
`e.canonical_name as employer` and neither of those. The naming was copied from
the watch lane, which does alias both, so it reads correctly and is wrong here.

What a reader saw was "Apply to this posting?" -- the bare are-you-sure that
`ApplyWithConfirm`'s own docstring rejects as catching nothing, on the one
control that hands a real application to an agent that fills a real employer's
form under a real name.

The general form is a census: every `row.X` a lane's rows read has to be a
column that lane's SELECT produces. This pins the pair that matters, because a
prompt that cannot name the job is the failure with a cost attached.
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import v2_lanes  # noqa: E402

ROWS = REPO / "apps" / "web" / "app" / "_ui" / "Rows.tsx"


def _found_columns():
    """Every name the Found select yields: aliases where given, else the column."""
    names = set()
    for line in v2_lanes.POSTING_COLUMNS.splitlines():
        for part in line.split(","):
            part = part.strip().rstrip(",")
            if not part:
                continue
            alias = re.search(r"\bas\s+(\w+)$", part)
            if alias:
                names.add(alias.group(1))
                continue
            bare = re.match(r"^[a-z]+\.(\w+)$", part)
            if bare:
                names.add(bare.group(1))
    return names


class TheApplyPromptNamesTheJobTests(unittest.TestCase):
    def test_the_columns_were_found_at_all(self):
        """A parser that has stopped parsing reports full compliance."""
        columns = _found_columns()
        self.assertGreaterEqual(
            len(columns), 10,
            f"only parsed {len(columns)} columns out of POSTING_COLUMNS")
        self.assertIn("employer", columns)
        self.assertIn("title", columns)

    def test_apply_with_confirm_reads_columns_found_actually_selects(self):
        source = ROWS.read_text()
        block = source[source.index("<ApplyWithConfirm"):]
        block = block[:block.index("/>")]
        reads = set(re.findall(r"row\.(\w+)", block))
        self.assertTrue(reads, "ApplyWithConfirm reads no row fields at all")
        columns = _found_columns()
        missing = sorted(r for r in reads if r not in columns and r != "id")
        self.assertEqual(
            missing, [],
            f"ApplyWithConfirm reads {missing}, which the Found select does "
            f"not produce, so the prompt falls back to a bare "
            f"'Apply to this posting?' on an irreversible control. Found "
            f"yields {sorted(columns)}.")

    def test_it_passes_both_halves_rather_than_one(self):
        """A prompt naming the company and not the role is half a prompt."""
        source = ROWS.read_text()
        block = source[source.index("<ApplyWithConfirm"):]
        block = block[:block.index("/>")]
        for prop in ("company", "role"):
            self.assertRegex(
                block, rf"{prop}=\{{",
                f"ApplyWithConfirm no longer passes {prop}")
