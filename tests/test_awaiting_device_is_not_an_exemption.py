"""A v2 write the device has not answered yet is not a mirror divergence.

**And a v2 write the device NEVER answers still is.** That distinction is the
whole design. The obvious marker is a provenance flag saying v2 wrote the row,
and a provenance flag is permanent: it would excuse every v2 row for ever, and
the only v2 finding this project has ever had is precisely a row nothing acted
on. One real application holds `inflight` in Postgres against a
session file that has read `watch` since 2026-09-05T21:36:21Z, because v2 wrote
it at 2026-09-07T00:18:36Z and #206, which added the enqueue, landed two hours
later. A "v2 wrote this" exemption deletes that finding.

So the excusable state is "a work order is outstanding", which ends by itself
when the device answers, and past `AWAITING_SECONDS` the failure to end is the
finding.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import verify_parity  # noqa: E402

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def mismatch(field="session.status", ident="app-1", file="watch", pg="inflight"):
    return {"kind": "field_mismatch", "id": ident, "field": field,
            "file": file, "postgres": pg}


def pending(ident="app-1", order="apply", ago=60):
    return {ident: (order, NOW - timedelta(seconds=ago))}


class ClassifyTests(unittest.TestCase):
    def test_a_young_order_is_awaiting_the_device(self):
        kind, order, age = verify_parity.classify_awaiting(
            mismatch(), pending(ago=120), now=NOW)
        self.assertEqual(kind, "awaiting_device")
        self.assertEqual(order, "apply")
        self.assertEqual(age, 120)

    def test_an_old_order_is_overdue_and_stays_a_divergence(self):
        kind, _, age = verify_parity.classify_awaiting(
            mismatch(), pending(ago=verify_parity.AWAITING_SECONDS + 1), now=NOW)
        self.assertEqual(kind, "reconciliation_overdue")
        self.assertGreater(age, verify_parity.AWAITING_SECONDS)

    def test_a_row_with_no_order_is_untouched(self):
        """The motivating case. It predates the column, so it has no marker,
        so it stays a plain divergence -- which is the correct answer and the
        one every row-level exemption gets wrong."""
        self.assertIsNone(verify_parity.classify_awaiting(mismatch(), {}, now=NOW))

    def test_only_the_columns_v2_can_write_are_excusable(self):
        """Suppressing by row rather than by column would blind parity to the
        other sixteen compared columns on every v2-touched application."""
        for field in ("session.company", "session.blockers", "session.role",
                      "session.postingId"):
            self.assertIsNone(
                verify_parity.classify_awaiting(mismatch(field=field),
                                                pending(), now=NOW),
                f"{field} was excused, and v2 cannot write it")

    def test_the_excusable_columns_are_the_ones_the_route_writes(self):
        # Derived by reading the route rather than by trusting this list.
        route = (REPO / "apps" / "web" / "app" / "api" / "applications"
                 / "route.ts").read_text(encoding="utf-8")
        for column in verify_parity.AWAITING_COLUMNS:
            name = column.split(".", 1)[1]
            self.assertIn(f"body.{name}", route,
                          f"{column} is excusable here and route.ts never "
                          f"writes it")

    def test_only_a_field_mismatch_is_excusable(self):
        for kind in ("only_in_file", "only_in_postgres"):
            self.assertIsNone(verify_parity.classify_awaiting(
                dict(mismatch(), kind=kind), pending(), now=NOW))

    def test_an_uncomparable_timestamp_is_not_read_as_young(self):
        # A naive datetime is "could not compare", and could-not-look is never
        # is-not-there. Reading it as young would excuse the row silently.
        naive = {"app-1": ("apply", datetime(2026, 9, 8, 11, 59, 0))}
        self.assertIsNone(verify_parity.classify_awaiting(mismatch(), naive,
                                                          now=NOW))

    def test_an_order_with_no_timestamp_is_not_excused(self):
        self.assertIsNone(verify_parity.classify_awaiting(
            mismatch(), {"app-1": ("apply", None)}, now=NOW))


class ReportShapeTests(unittest.TestCase):
    def test_awaiting_is_an_aside_and_overdue_is_not(self):
        """An aside is a recorded fact; a divergence fails the run. The whole
        point of the age bound is that the second one exists."""
        source = (REPO / "scripts" / "verify_parity.py").read_text()
        block = source[source.index("ASIDES = ("):]
        block = block[:block.index(")") + 1]
        self.assertIn("awaiting_device", block)
        self.assertNotIn("reconciliation_overdue", block)

    def test_the_device_clears_the_marker_on_both_paths_it_can_answer(self):
        """**Clearing only in the session branch misses the control that
        matters most.** `apply_marks.apply_one` reads the session and appends
        history without writing the session, so a v2 `event: completed` reaches
        `_mirror_history` and takes `_application_id`'s `do nothing` branch;
        `sync_board` then deletes the session, so no later write clears it
        either. The marker would outlive its condition for ever on the one path
        that has no session at the end."""
        source = (REPO / "scripts" / "class_b.py").read_text()
        session = source[source.index("def _application_id"):]
        session = session[:session.index("\ndef ", 1)]
        self.assertIn("pending_order = null", session)

        history = source[source.index("def _mirror_history"):]
        history = history[:history.index("\ndef ", 1)]
        self.assertIn("pending_order = null", history,
                      "a history event does not clear the marker, so a "
                      "confirmed submission leaves it outstanding for ever")

    def test_the_marker_columns_are_outside_the_compared_set_without_an_exemption(self):
        """They were exempted first, and a guard refused it.

        `compared()` is `written()` minus the exemptions, and `written()` comes
        from the mirror's INSERT column list. `class_b` only ever *clears*
        these two, in a `do update`, so they are outside the write set and were
        never candidates. An exemption for a column the mirror does not write
        covers nothing, which is what
        `test_parity_compares_what_the_mirror_writes` exists to say.
        """
        import mirror_fields
        for column in ("pending_order", "pending_order_at"):
            self.assertNotIn(column, mirror_fields.written()["applications"])
            self.assertNotIn(column, mirror_fields.compared()["applications"])
            self.assertNotIn(("applications", column), mirror_fields.EXEMPT)

    def test_v2s_own_writers_are_gate_paths(self):
        """A second writer of the mirrored tables changes what parity compares
        exactly as `class_b` does."""
        import parity_clock
        self.assertIn("apps/web/app/api/applications/route.ts",
                      parity_clock.GATE_PATHS)
