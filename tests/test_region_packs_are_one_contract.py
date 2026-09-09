"""`REGION_PACKS` is one contract in two languages, so it is compared, not restated.

**`db.savedSearch` read the stored locations with `.map(String)` over a list of
objects.** `search_config.validate` stores `[{"country": "US"},
{"regionPack": "EU"}]` and enforces that shape, so every entry stringified to
`"[object Object]"`: the chip would have read that, the picker would have shown
nothing ticked over a real saved search, and a `regionPack` would have been lost
outright rather than expanded into its thirty-two countries.

It was invisible because the stored list is empty today, and the commit that
makes the country boxes tick is the one that lets a reader fill it.

Expanding a pack means v2 now holds a copy of the table. `LogoContractTests` and
`test_r2_contract` are the pattern: **compare the outputs across all inputs, do
not restate the rule.** A test that asserted "both files mention EU" would pass
with a country deleted from one of them.
"""

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import search_config  # noqa: E402

TS = REPO / "apps" / "web" / "app" / "_ui" / "db.ts"


def _typescript_packs():
    """The packs as `db.ts` holds them, parsed from the literal."""
    source = TS.read_text()
    start = source.index("const REGION_PACKS")
    block = source[start:source.index("};", start)]
    packs = {}
    for match in re.finditer(r"(\w+):\s*\[([^\]]*)\]", block, re.S):
        name = match.group(1)
        codes = re.findall(r'"([A-Z]{2})"', match.group(2))
        packs[name] = codes
    return packs


class RegionPacksAgreeTests(unittest.TestCase):
    def test_the_two_copies_hold_the_same_packs(self):
        mine = _typescript_packs()
        self.assertTrue(mine, "no REGION_PACKS literal found in db.ts")
        self.assertEqual(
            sorted(mine), sorted(search_config.REGION_PACKS),
            "the two copies offer different packs. A pack v2 does not know is "
            "a saved search whose locations silently expand to nothing.")
        for name, codes in search_config.REGION_PACKS.items():
            self.assertEqual(
                sorted(mine[name]), sorted(codes),
                f"{name} differs between search_config.py and db.ts. A country "
                f"in one and not the other is a filter that includes different "
                f"places depending on which side of the board you ask.")

    def test_expansion_agrees_over_every_pack_and_a_mixed_config(self):
        """Outputs, over every input, rather than a claim about the lists.

        The one that matters is the mixed config: a `regionPack` beside a bare
        `country` is what a real saved search looks like once a reader has used
        both halves of the picker.
        """
        mine = _typescript_packs()

        def expand_like_v2(entries):
            out = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get("regionPack"), str):
                    out.extend(mine.get(entry["regionPack"].upper(), []))
                elif isinstance(entry.get("country"), str):
                    out.append(entry["country"].upper())
            return sorted(set(out))

        cases = [[{"regionPack": name}] for name in search_config.REGION_PACKS]
        cases.append([])
        cases.append([{"country": "US"}])
        cases.append([{"regionPack": "EU"}, {"country": "US"}, {"country": "CA"}])
        cases.append([{"regionPack": "SEA"}, {"regionPack": "MIDDLE_EAST"}])

        for entries in cases:
            expected = search_config.expand_locations({"locations": entries})
            self.assertEqual(
                expand_like_v2(entries), sorted(expected),
                f"the two expansions disagree for {json.dumps(entries)}")

    def test_the_comparison_has_a_subject(self):
        """A parser that has stopped parsing reports full agreement."""
        mine = _typescript_packs()
        self.assertGreaterEqual(len(mine), 3)
        self.assertGreaterEqual(sum(len(v) for v in mine.values()), 40)
