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

    def test_every_module_the_upserts_import_is_gated_or_exempt_with_a_reason(self):
        """The whole closure, not a hand-picked entry from it.

        **This test already computed the right set and then asserted one member
        of it.** `imports_of(backfill)` returns `board_key`, `company_logos`,
        `identity` and `migrate`; it checked `board_key` and let the other
        three through. `company_logos` decides `logo_alt` and `logo_domain`,
        #204 changed it on 2026-09-06, the clock did not reset, and 708
        divergences followed.

        That is the `GATE_PATHS` failure one level down, in the test written to
        prevent it. A check whose subject is one member of a derived set is a
        check about that member.

        Gated or exempt, and an exemption needs a sentence. An absence is
        ambiguous between "decides nothing" and "nobody looked", and the second
        reading is what cost two days.
        """
        upserts = modules_the_mirror_upserts_through()
        self.assertTrue(upserts, "the upsert derivation found nothing")
        closure = {}
        for rel in upserts.values():
            closure.update({v: k for k, v in imports_of(rel).items()})
        self.assertGreater(
            len(closure), 1,
            "the import derivation returned almost nothing, so this check is "
            "vacuous; it must reach at least the key module and one other")

        unaccounted = sorted(
            rel for rel in closure
            if rel not in parity_clock.GATE_PATHS
            and rel not in parity_clock.GATE_PATH_EXEMPT)
        self.assertEqual(
            unaccounted, [],
            f"the mirror's upsert modules import {unaccounted}, which are "
            f"neither gate paths nor exempt. A module that decides a column "
            f"the mirror writes is a gate path; one that does not needs a line "
            f"in GATE_PATH_EXEMPT saying so.")

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

    def test_the_module_that_decided_the_logo_columns_is_gated(self):
        """The specific regression, pinned by name.

        The derivation above would catch it again, and this says which one it
        was, so a future reader deleting the entry meets the incident rather
        than a list.
        """
        self.assertIn("scripts/company_logos.py", parity_clock.GATE_PATHS)

    def test_an_exemption_carries_a_reason_and_is_not_also_gated(self):
        for rel, reason in parity_clock.GATE_PATH_EXEMPT.items():
            self.assertNotIn(rel, parity_clock.GATE_PATHS,
                             f"{rel} is both gated and exempt")
            self.assertGreater(
                len(str(reason).split()), 6,
                f"{rel} is exempt with no real reason; an exemption without one "
                f"is an absence wearing a label")

    def test_an_exemption_names_a_file_that_exists(self):
        # An exemption for a module that has been deleted or renamed silently
        # stops covering anything, and reads as coverage.
        for rel in parity_clock.GATE_PATH_EXEMPT:
            self.assertTrue((REPO / rel).is_file(), f"{rel} does not exist")

    def test_the_clock_is_not_its_own_gate_path(self):
        """Over-inclusion has a cost: the whole import closure of the write
        path reaches the clock and its own coverage sidecar, and gating on
        those would restart the clock for editing the clock."""
        for never in ("scripts/parity_clock.py", "scripts/write_path_coverage.py"):
            self.assertNotIn(never, parity_clock.GATE_PATHS)
