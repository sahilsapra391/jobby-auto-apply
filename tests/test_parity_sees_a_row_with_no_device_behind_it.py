"""An `applications` row the device knows nothing about is a divergence.

**`compare_class_b` skipped `only_in_postgres` for sessions entirely**, under a
comment whose two reasons were both true: `roles.json` also creates rows, and
`finish.py` deletes a session on submission. The conclusion drawn from them was
to skip the direction, and a peer session proved what that costs by writing
``legacy_id '!!probe-not-a-real-id!!'`` to production through the route. It
would have survived a green parity run.

`outcomes` was in the same loop with the same hole and no reason at all.

The exemption the finding proposed -- excuse a Postgres-only row that has a
completed event -- is deliberately **not** here. Measured 2026-09-09 against
production: `roles.json` holds 208 entries against 208 rows and 100 session
files, nothing prunes it, and the Postgres-only set is empty, so the exemption
would have explained 0 of 0 rows while permanently blinding the one direction
that had just let a row through.
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


def _files(sessions=(), outcomes=()):
    return {"sessions": {k: {"status": "watch"} for k in sessions},
            "events": {}, "answers": {}, "profile": None,
            "outcomes": {k: {"state": "ok"} for k in outcomes}}


def _database(sessions=(), outcomes=()):
    return {"sessions": {k: {"status": "watch"} for k in sessions},
            "events": {}, "answers": {}, "profile": None,
            "outcomes": {k: {"state": "ok"} for k in outcomes},
            "pending": {}}


def _kinds(findings, field):
    return sorted(f["id"] for f in findings
                  if f.get("kind") == "only_in_postgres"
                  and f.get("field") == field)


class OrphanRowTests(unittest.TestCase):
    def test_the_probe_row_is_named(self):
        """The exact shape that reached production."""
        findings = verify_parity.compare_class_b(
            _files(["real-one"]),
            _database(["real-one", "!!probe-not-a-real-id!!"]))
        self.assertEqual(_kinds(findings, "session"),
                         ["!!probe-not-a-real-id!!"])

    def test_an_orphan_edit_outcome_is_named(self):
        """The sibling hole, which had no stated reason at all."""
        findings = verify_parity.compare_class_b(
            _files(outcomes=["edit-1"]),
            _database(outcomes=["edit-1", "edit-ghost"]))
        self.assertEqual(_kinds(findings, "editOutcome"), ["edit-ghost"])

    def test_a_roles_only_id_is_not_a_divergence(self):
        """`class_b_file_state` merges the role index into `sessions`, so an
        application named only by `roles.json` is already on the file side.
        That is the reason the exemption was written for, and it is handled a
        layer earlier than the exemption would have been."""
        findings = verify_parity.compare_class_b(
            _files(["from-roles-json"]), _database(["from-roles-json"]))
        self.assertEqual(_kinds(findings, "session"), [])

    def test_agreement_reports_nothing(self):
        """The anti-vacuity floor's other half: a check that names a row when
        the two sides agree is worse than one that names none."""
        both = ["a", "b", "c"]
        findings = verify_parity.compare_class_b(_files(both), _database(both))
        self.assertEqual([f for f in findings
                          if f.get("kind") == "only_in_postgres"], [])
        self.assertEqual(findings, [], f"unexpected findings: {findings}")
