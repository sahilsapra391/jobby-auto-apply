"""The parity checker records what it looked at, and cannot get quieter unnoticed.

**34 green parity runs on 2026-09-02 were honest about 12 fields and counted as
evidence about 30.** The timer runs the deployed copy of `verify_parity`,
production was 17 hours behind main, and its checker compared twelve
hand-written posting fields while the mirror wrote thirty. Nothing in the log
could have shown it: a run that compares less does not fail, it succeeds
faster.

These tests are written against the two things that must hold for that never to
recur silently -- a run carries its coverage, and the clock refuses to count a
run that covers less than this device has already seen -- plus the way down,
because a ratchet with no legitimate decrease is a ratchet somebody edits.
"""

import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import parity_clock      # noqa: E402
import parity_coverage   # noqa: E402
import parity_log        # noqa: E402


def measurement(**tables):
    """`{table: {compared, exempt, unseen}}` from a terse spec."""
    return {name: {"compared": sorted(c), "exempt": sorted(e), "unseen": sorted(u)}
            for name, (c, e, u) in tables.items()}


class TheThreeStatesAreReportedApart(unittest.TestCase):
    """`exempt` and `unseen` are different states and only one is a defect."""

    def test_a_column_with_a_reason_is_not_a_gap(self):
        m = measurement(postings=(["title"], ["legacy_id"], []))
        self.assertEqual(parity_coverage.totals(m),
                         {"compared": 1, "exempt": 1, "unseen": 0})

    def test_a_column_with_no_reason_is_counted_as_unseen(self):
        m = measurement(postings=(["title"], ["legacy_id"], ["body_r2_key"]))
        self.assertEqual(parity_coverage.totals(m)["unseen"], 1)

    def test_the_two_are_never_summed(self):
        """The control: 22 good reasons must not hide 16 real gaps. A single
        'not compared' number does exactly that, which is why the split is the
        feature rather than a presentation choice."""
        m = measurement(postings=(["title"], ["a", "b"], ["c"]))
        totals = parity_coverage.totals(m)
        self.assertNotEqual(totals["exempt"], totals["unseen"])
        self.assertNotIn("uncompared", totals)


class TheMarkOnlyRisesOnItsOwn(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_a_first_reading_sets_the_mark(self):
        data = parity_coverage.read_marks(self.root)
        m = measurement(postings=(["a", "b"], [], []))
        self.assertTrue(parity_coverage.raise_marks(m, data))
        self.assertEqual(data["marks"]["postings"]["compared"], ["a", "b"])

    def test_a_wider_reading_lifts_it(self):
        data = {"marks": {"postings": {"compared": ["a"]}}, "reductions": []}
        m = measurement(postings=(["a", "b"], [], []))
        parity_coverage.raise_marks(m, data)
        self.assertEqual(data["marks"]["postings"]["compared"], ["a", "b"])

    def test_a_narrower_reading_does_not_lower_it(self):
        data = {"marks": {"postings": {"compared": ["a", "b"]}}, "reductions": []}
        m = measurement(postings=(["a"], [], []))
        parity_coverage.raise_marks(m, data)
        self.assertEqual(data["marks"]["postings"]["compared"], ["a", "b"],
                         "observation lowered a mark, so a stale checker would "
                         "quietly reset the bar to its own narrower reading")

    def test_a_swap_does_not_earn_a_lower_bar(self):
        """A checker that gained one column and lost another has not earned
        forgiveness for the one it lost, so the mark is the union."""
        data = {"marks": {"postings": {"compared": ["a", "b"]}}, "reductions": []}
        m = measurement(postings=(["b", "c"], [], []))
        parity_coverage.raise_marks(m, data)
        self.assertEqual(data["marks"]["postings"]["compared"], ["a", "b", "c"])

    def test_below_names_the_columns_that_left(self):
        marks = {"postings": {"compared": ["a", "b", "c"]}}
        m = measurement(postings=(["a"], [], []))
        short = parity_coverage.below(m, marks)
        self.assertEqual(short[0]["missing"], ["b", "c"])
        self.assertEqual((short[0]["mark"], short[0]["now"]), (3, 1))


class TheWayDownNeedsBothHalves(unittest.TestCase):
    """`migrate.check_expand_only` is the precedent: a declaration that travels
    with the change, plus a flag on the command, and either alone is not
    enough."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        parity_coverage.write_marks(
            {"marks": {"postings": {"compared": ["gone", "kept", "quiet"]}},
             "reductions": []}, self.root)
        self.now = measurement(postings=(["kept"], ["gone"], []))
        # `gone` is exempt with a reason; `quiet` simply stopped being compared.
        self.live = {"postings": {"kept", "quiet"}}
        self.exempt = {("postings", "gone")}

    def _lower(self, reason, allow):
        return parity_coverage.lower(
            _FakeConn(self.live), reason, allow=allow, root=self.root,
            measurement=self.now, exempt=self.exempt)

    def test_the_flag_alone_is_refused_when_a_column_is_unaccounted(self):
        with self.assertRaises(parity_coverage.CoverageError) as caught:
            self._lower("tidying up", allow=True)
        self.assertIn("quiet", str(caught.exception))

    def test_a_reason_alone_is_refused(self):
        self.exempt |= {("postings", "quiet")}
        with self.assertRaises(parity_coverage.CoverageError) as caught:
            self._lower("both are exempt now", allow=False)
        self.assertIn("--allow-contract", str(caught.exception))

    def test_an_empty_reason_is_refused_even_with_the_flag(self):
        self.exempt |= {("postings", "quiet")}
        with self.assertRaises(parity_coverage.CoverageError) as caught:
            self._lower("   ", allow=True)
        self.assertIn("reason", str(caught.exception))

    def test_both_halves_and_every_column_accounted_for_lowers_it(self):
        self.exempt |= {("postings", "quiet")}
        out = self._lower("both were exempted in #999", allow=True)
        self.assertEqual(out["lowered"][0]["missing"], ["gone", "quiet"])
        data = parity_coverage.read_marks(self.root)
        self.assertEqual(data["marks"]["postings"]["compared"], ["kept"])

    def test_a_column_gone_from_the_schema_is_accounted_for(self):
        """A dropped column needs no exemption: it cannot be compared.

        `gone` stays in the schema and carries an exemption; `quiet` leaves the
        schema entirely. Both are accounted for and the verdicts differ, which
        is what says the two branches are distinguished rather than one
        swallowing the other."""
        self.live = {"postings": {"kept", "gone"}}   # `quiet` dropped
        out = self._lower("both columns were dropped in 0016", allow=True)
        whys = {c["column"]: c["why"] for c in out["columns"]}
        self.assertEqual(whys, {"gone": "exempted", "quiet": "gone"})

    def test_the_reduction_is_recorded_with_its_reason(self):
        self.exempt |= {("postings", "quiet")}
        self._lower("exempted in #999, see the note there", allow=True)
        recorded = parity_coverage.read_marks(self.root)["reductions"]
        self.assertEqual(len(recorded), 1)
        self.assertIn("#999", recorded[0]["reason"])
        self.assertEqual(recorded[0]["from"], 3)
        self.assertEqual(recorded[0]["to"], 1)

    def test_lowering_when_nothing_dropped_is_refused(self):
        """Otherwise `lower` is a way to write an arbitrary mark."""
        self.now = measurement(postings=(["gone", "kept", "quiet"], [], []))
        with self.assertRaises(parity_coverage.CoverageError):
            self._lower("no reason to", allow=True)


class _FakeConn:
    """The two reads `parity_coverage` makes of a live schema."""

    def __init__(self, live):
        self.live = live

    def cursor(self):
        return _FakeCursor(self.live)


class _FakeCursor:
    def __init__(self, live):
        self.live = live
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        tables = (params or [[]])[0]
        self.rows = [(t, c) for t in tables for c in sorted(self.live.get(t, ()))]

    def fetchall(self):
        return self.rows


class TheClockRefusesAShallowRun(unittest.TestCase):
    """The rule the whole file exists for: a run that looked at less than this
    device has seen is inconclusive, not green."""

    def entries(self, *coverage):
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        out = []
        for i, below in enumerate(coverage):
            entry = {"at": (base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "ok": True, "divergences": 0}
            if below:
                entry["coverage"] = {"compared": 12, "exempt": 22, "unseen": 16,
                                     "belowMark": [{"table": "postings", "mark": 30,
                                                    "now": 12, "missing": ["logo_src"]}]}
            else:
                entry["coverage"] = {"compared": 30, "exempt": 22, "unseen": 16,
                                     "belowMark": []}
            out.append(parity_log.with_coverage(entry))
        return out

    def test_a_full_run_still_counts(self):
        for entry in self.entries(False, False):
            self.assertTrue(entry.get("ok"))
            self.assertFalse(entry.get("inconclusive"))

    def test_a_shallow_run_is_inconclusive_rather_than_green(self):
        entry = self.entries(True)[0]
        self.assertFalse(entry.get("ok"))
        self.assertTrue(entry.get("inconclusive"))
        self.assertIn("postings", entry.get("reason", ""))

    def test_a_shallow_run_does_not_reset_the_clock(self):
        """It is an absence, not a failure. Resetting on it would punish a
        deploy lag with three more days rather than just not counting it."""
        self.assertFalse(parity_clock.diverged(self.entries(True)[0]))

    def test_a_shallow_run_contributes_no_covered_time(self):
        """The 34 greens, in miniature: they must stop counting.

        `now` is pinned because the clock reads the wall clock, and a fixture
        that drifts into the future reports a negative day and passes for the
        wrong reason."""
        now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        full = parity_clock.assess(self.entries(False, False, False), now=now)
        shallow = parity_clock.assess(self.entries(False, True, True), now=now)
        self.assertGreater(full["coveredDays"], shallow["coveredDays"],
                           "a shallow run still counted toward the gate")
        self.assertEqual((full["greenRuns"], full["inconclusiveRuns"]), (3, 0))
        self.assertEqual((shallow["greenRuns"], shallow["inconclusiveRuns"]),
                         (1, 2))

    def test_a_run_with_no_coverage_at_all_is_judged_as_before(self):
        """Every line written before this shipped has no `coverage` key, and
        rewriting history to insert one would be inventing readings. An absent
        key means the old rule, not a failure."""
        old = {"at": "2026-09-01T00:00:00Z", "ok": True, "divergences": 0}
        self.assertEqual(parity_log.with_coverage(dict(old)), old)


if __name__ == "__main__":
    unittest.main()
