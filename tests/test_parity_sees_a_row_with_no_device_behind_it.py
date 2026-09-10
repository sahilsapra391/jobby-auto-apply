"""An `applications` row the device knows nothing about is a divergence.

**`compare_class_b` skipped `only_in_postgres` for sessions entirely**, under a
comment whose two reasons were both true: `roles.json` also creates rows, and
`finish.py` deletes a session on submission. The conclusion drawn from them was
to skip the direction, and a session proved what that costs by writing
``legacy_id '!!probe-not-a-real-id!!'`` to production through the route. It
would have survived a green parity run. `outcomes` was in the same loop with
the same hole and no stated reason at all.

Both reasons are checkable, so the exemption is a condition rather than a skip:

    a Postgres-only session is a divergence unless
      (a) it has a live completed event
      (b) its legacy_id is in roles.json

**Against the session files, not the merged dict**, and that is the half worth
guarding. `class_b_file_state` merges `roles.json` into `sessions` for the
field comparison, so reading the orphan check off the merged set makes arm (b)
invisible, the set comes out empty, and the check certifies itself on a subject
0 rows wide. Measured against production 2026-09-10: **108** rows have no
session file, **103** by (a), **5** by (b), **0** by neither.

**What these cannot see, said rather than implied.** Every case here supplies
`completed` as a fixture value, so the query that fills it is under the test's
control and mutating that query cannot fail anything below -- verified by
emptying it and watching all seven pass. The query is covered by the census
above instead: 103 rows are excused by arm (a), and they can only be excused if
it returns them. A mutation applied to something the test controls proves
nothing, which this repository has already paid for once.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402
import verify_parity  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


def _files(session_files=(), roles=(), outcomes=()):
    """The device side, with the two writers kept apart the way the real one
    keeps them. `sessions` is the merged dict the field comparison uses."""
    merged = {k: {"status": "watch"} for k in (*session_files, *roles)}
    return {"sessions": merged, "events": {}, "answers": {}, "profile": None,
            "outcomes": {k: {"state": "ok"} for k in outcomes},
            "sessionFiles": set(session_files), "roleEntries": set(roles)}


def _database(sessions=(), outcomes=(), completed=()):
    return {"sessions": {k: {"status": "watch"} for k in sessions},
            "events": {}, "answers": {}, "profile": None,
            "outcomes": {k: {"state": "ok"} for k in outcomes},
            "pending": {}, "completed": set(completed)}


def _orphans(findings, field):
    return sorted(f["id"] for f in findings
                  if f.get("kind") == "only_in_postgres"
                  and f.get("field") == field)


class OrphanRowTests(unittest.TestCase):
    def test_the_probe_row_is_named(self):
        """The exact shape that reached production: no session file, no
        completed event, no roles entry."""
        findings = verify_parity.compare_class_b(
            _files(session_files=["real-one"]),
            _database(["real-one", "!!probe-not-a-real-id!!"]))
        self.assertEqual(_orphans(findings, "session"),
                         ["!!probe-not-a-real-id!!"])

    def test_arm_a_a_submitted_application_has_no_session_left(self):
        """`finish.py` deletes the session on submission. 103 of production's
        108 session-less rows are this."""
        findings = verify_parity.compare_class_b(
            _files(session_files=[]),
            _database(["submitted-and-swept"], completed=["submitted-and-swept"]))
        self.assertEqual(_orphans(findings, "session"), [])

    def test_arm_b_the_role_index_outlives_the_session(self):
        """5 of the 108. `roles.json` names the application and no session file
        does."""
        findings = verify_parity.compare_class_b(
            _files(session_files=[], roles=["named-by-roles-json"]),
            _database(["named-by-roles-json"]))
        self.assertEqual(_orphans(findings, "session"), [])

    def test_neither_arm_covers_a_row_with_nothing_behind_it(self):
        """The point of the pair. A row that satisfies one arm is excused and a
        row that satisfies neither is named, in the same comparison, so an
        exemption that quietly widened to "skip the direction" fails here."""
        findings = verify_parity.compare_class_b(
            _files(session_files=["has-a-file"], roles=["named-by-roles-json"]),
            _database(["has-a-file", "named-by-roles-json", "submitted", "ghost"],
                      completed=["submitted"]))
        self.assertEqual(_orphans(findings, "session"), ["ghost"])

    def test_the_two_arms_are_not_the_same_arm(self):
        """**Each arm has to cover a row the other does not**, or one of them
        is decoration and the check has a blind spot behind it.

        Written after a mutation that should have failed and did not. The first
        version of this file claimed the split between `sessionFiles` and the
        merged `sessions` dict was load-bearing, and it is not: `merged` is
        exactly `sessionFiles | roleEntries`, so subtracting one or the other
        yields the identical set and swapping them is a refactor. The split
        buys separate *counts*, not a different answer. This asserts the thing
        that is actually load-bearing instead."""
        # (a) alone: completed, and not in roles.json.
        only_a = verify_parity.compare_class_b(
            _files(session_files=[]),
            _database(["swept"], completed=["swept"]))
        self.assertEqual(_orphans(only_a, "session"), [],
                         "arm (a) does not cover a row only arm (a) can cover")
        # (b) alone: in roles.json, never completed.
        only_b = verify_parity.compare_class_b(
            _files(session_files=[], roles=["indexed"]),
            _database(["indexed"]))
        self.assertEqual(_orphans(only_b, "session"), [],
                         "arm (b) does not cover a row only arm (b) can cover")

    def test_an_orphan_edit_outcome_is_named(self):
        """The sibling hole, which had no stated reason at all."""
        findings = verify_parity.compare_class_b(
            _files(outcomes=["edit-1"]),
            _database(outcomes=["edit-1", "edit-ghost"]))
        self.assertEqual(_orphans(findings, "editOutcome"), ["edit-ghost"])

    def test_agreement_reports_nothing(self):
        """The anti-vacuity floor's other half: a check that names a row when
        the two sides agree is worse than one that names none."""
        both = ["a", "b", "c"]
        findings = verify_parity.compare_class_b(
            _files(session_files=both), _database(both))
        self.assertEqual(findings, [], f"unexpected findings: {findings}")
