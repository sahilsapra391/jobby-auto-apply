"""A column that stops being populated is visible without a second store.

**When the file store is deleted, parity retires and takes two checks with it**
-- `mirror_fields` describes the mirror and `parity_coverage` measures parity,
so neither has a subject afterwards. What survives checks *shape*:
`schema_drift` answers "does this column exist", which was true on both sides of
every divergence parity has ever caught.

Measured against the real one: 469 postings were inserted with `logo_domain`
NULL because production ran a `posting_row` without `_logo_columns`, and
`schema_drift` saw 3 of 3 logo columns present throughout.

So this is the same ratchet `parity_coverage` uses, pointed at content instead
of at coverage: per column, what share of rows is populated, against the highest
share this database has shown. It needs no second store and fails in the loud
direction -- a writer that stops writing reports a smaller number.

**It is deliberately not a replacement for parity and must not be described as
one.** It cannot see a value that is wrong rather than absent, and it cannot see
a row that should exist and does not. Both are written down in
`docs/what-parity-alone-catches.md` rather than implied away.
"""

import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import population  # noqa: E402


def reading(**columns):
    """`{table: {column: (populated, total)}}` from a terse spec."""
    out = {}
    for dotted, pair in columns.items():
        table, _, column = dotted.partition("__")
        out.setdefault(table, {})[column] = pair
    return out


class TheShareIsWhatIsMeasured(unittest.TestCase):
    def test_an_empty_table_is_not_a_regression(self):
        """Zero of zero is no evidence, and reading it as 0% would fire on
        every fresh database."""
        self.assertIsNone(population.share(0, 0))

    def test_a_full_column_is_one(self):
        self.assertEqual(population.share(10, 10), 1.0)

    def test_the_share_is_of_rows_present(self):
        self.assertEqual(population.share(3, 4), 0.75)


class TheMarkOnlyRises(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_a_first_reading_sets_the_mark(self):
        data = population.read_marks(self.root)
        lifted = population.raise_marks(reading(postings__logo=(97, 100)), data)
        self.assertTrue(lifted)
        self.assertEqual(data["marks"]["postings"]["logo"], 0.97)

    def test_a_better_reading_lifts_it(self):
        data = {"marks": {"postings": {"logo": 0.9}}, "reductions": []}
        population.raise_marks(reading(postings__logo=(97, 100)), data)
        self.assertEqual(data["marks"]["postings"]["logo"], 0.97)

    def test_a_worse_reading_does_not_lower_it(self):
        data = {"marks": {"postings": {"logo": 0.976}}, "reductions": []}
        population.raise_marks(reading(postings__logo=(84, 100)), data)
        self.assertEqual(data["marks"]["postings"]["logo"], 0.976,
                         "observation lowered a mark, so a writer that stopped "
                         "writing would quietly reset the bar to its own worse "
                         "reading")


class TheLogoErasureIsWhatItCatches(unittest.TestCase):
    """The case this exists for, run as the numbers actually were."""

    def setUp(self):
        self.marks = {"marks": {"postings": {"logo_domain": 0.976}},
                      "reductions": []}

    def test_the_erasure_is_reported(self):
        # 22,466 rows at 97.5% plus 469 inserted blank.
        now = reading(postings__logo_domain=(21911, 22935))
        short = population.below(now, self.marks["marks"])
        self.assertEqual(len(short), 1)
        self.assertEqual(short[0]["column"], "logo_domain")
        self.assertLess(short[0]["now"], short[0]["mark"])

    def test_a_healthy_reading_is_not(self):
        """The control. Without it, a check that always fires would pass this
        file's other test and be useless."""
        now = reading(postings__logo_domain=(22376, 22935))
        self.assertEqual(population.below(now, self.marks["marks"]), [])

    def test_noise_does_not_fire(self):
        """A share moves a little as the corpus turns over, and an alarm that
        fires on that is an alarm nobody keeps. The threshold is a real drop,
        not any drop."""
        now = reading(postings__logo_domain=(22350, 22935))   # 97.4% vs 97.6%
        self.assertEqual(population.below(now, self.marks["marks"]), [],
                         "a two-tenths-of-a-point move fired the alarm")


class TheWayDownNeedsBothHalves(unittest.TestCase):
    """The same shape `parity_coverage` uses, and `migrate.check_expand_only`
    before it: a reason, plus a flag, and neither alone."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        population.write_marks(
            {"marks": {"postings": {"logo_domain": 0.976}}, "reductions": []},
            self.root)
        self.now = reading(postings__logo_domain=(11000, 22935))

    def test_a_reason_alone_is_refused(self):
        with self.assertRaises(population.PopulationError) as caught:
            population.lower(self.now, "the feed stopped carrying it",
                             allow=False, root=self.root)
        self.assertIn("--allow-drop", str(caught.exception))

    def test_the_flag_alone_is_refused(self):
        with self.assertRaises(population.PopulationError) as caught:
            population.lower(self.now, "  ", allow=True, root=self.root)
        self.assertIn("reason", str(caught.exception))

    def test_both_lowers_it_and_records_why(self):
        population.lower(self.now, "Adzuna stopped sending logos in #999",
                         allow=True, root=self.root)
        data = population.read_marks(self.root)
        self.assertAlmostEqual(data["marks"]["postings"]["logo_domain"],
                               11000 / 22935, places=4)
        self.assertIn("#999", data["reductions"][0]["reason"])

    def test_lowering_when_nothing_dropped_is_refused(self):
        """Otherwise `lower` is a way to write an arbitrary mark."""
        with self.assertRaises(population.PopulationError):
            population.lower(reading(postings__logo_domain=(22376, 22935)),
                             "no reason to", allow=True, root=self.root)
