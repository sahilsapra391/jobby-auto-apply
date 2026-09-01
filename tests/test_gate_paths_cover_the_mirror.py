"""The parity clock resets when the code that does the mirroring changes.

**It did not, and that is how two outages in one file left the clock running.**
`GATE_PATHS` named `dual_write.py` and not `packages/schema/backfill.py`, which
is the module `dual_write` calls for every class A upsert and where both of
2026-09-01's board freezes lived. Nine green runs stayed on the counter across
both fixes.

So this derives the modules the mirror actually writes through, rather than
restating the list. A new upsert module fails here until somebody decides
whether it belongs on the gate, which is the decision that was skipped.
"""
import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import parity_clock  # noqa: E402

MIRROR = REPO / "scripts" / "dual_write.py"
SEARCH = (REPO / "scripts", REPO / "packages" / "schema")


def modules_the_mirror_upserts_through():
    """Every module `dual_write` imports and calls an `upsert_*` on.

    Derived from the call sites, so it follows the code rather than a comment.
    """
    tree = ast.parse(MIRROR.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
    used = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in imported
                and node.func.attr.startswith("upsert_")):
            used.add(node.func.value.id)
    out = {}
    for name in used:
        for directory in SEARCH:
            candidate = directory / f"{name}.py"
            if candidate.is_file():
                out[name] = str(candidate.relative_to(REPO))
    return out


def imports_of(rel):
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
    out = {}
    for name in names:
        for directory in SEARCH:
            candidate = directory / f"{name}.py"
            if candidate.is_file():
                out[name] = str(candidate.relative_to(REPO))
    return out


class GatePathsCoverTheMirrorTests(unittest.TestCase):
    def test_every_module_the_mirror_upserts_through_is_a_gate_path(self):
        found = modules_the_mirror_upserts_through()
        self.assertTrue(found,
                        "no upsert call sites were found in dual_write.py; "
                        "the derivation stopped matching and this check is "
                        "vacuous")
        missing = sorted(rel for rel in found.values()
                         if rel not in parity_clock.GATE_PATHS)
        self.assertEqual(
            missing, [],
            f"the mirror writes through {missing} and a change there would "
            f"not restart the parity clock. Either add it to GATE_PATHS or "
            f"say in that comment why the evidence survives it.")

    def test_the_key_those_upserts_conflict_on_is_a_gate_path(self):
        """A change to the key derivation changes which records collide, which
        changes what the mirror stores, without the upsert moving."""
        upserts = modules_the_mirror_upserts_through()
        self.assertIn("backfill", upserts)
        keyed = imports_of(upserts["backfill"])
        self.assertIn("board_key", keyed,
                      "backfill no longer derives board keys; this check needs "
                      "to be pointed at whatever replaced it")
        self.assertIn(keyed["board_key"], parity_clock.GATE_PATHS)

    def test_the_clock_is_not_its_own_gate_path(self):
        """Over-inclusion has a cost: the whole import closure of the write
        path reaches the clock and its own coverage sidecar, and gating on
        those would restart the clock for editing the clock."""
        for never in ("scripts/parity_clock.py", "scripts/write_path_coverage.py"):
            self.assertNotIn(never, parity_clock.GATE_PATHS)


if __name__ == "__main__":
    unittest.main()
