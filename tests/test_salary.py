"""Tests for scripts/salary.py, the compensation figure.

When a posting states a band and then asks what the candidate expects, the answer
is the middle of that band. The arithmetic looks trivial and is not: bands arrive
with K suffixes, en dashes, the word "to", Indian lakh grouping, LPA, and only
one side. A decimal point in the wrong place on this field is not a small error.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("salary", ROOT / "scripts" / "salary.py")
salary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(salary)


class MidpointTests(unittest.TestCase):
    def mid(self, band):
        value, detail = salary.midpoint(band)
        return value, (detail["formatted"] if isinstance(detail, dict) else detail)

    def test_the_users_own_examples(self):
        self.assertEqual(self.mid("$70,000 - $90,000"), (80000.0, "$80,000"))
        self.assertEqual(self.mid("$140,000 - $180,000"), (160000.0, "$160,000"))

    def test_k_suffixes(self):
        self.assertEqual(self.mid("$140K - $180K")[0], 160000.0)
        self.assertEqual(self.mid("90k to 110k")[0], 100000.0)

    def test_a_suffix_on_only_the_second_half(self):
        # "140 - 180K" means both are thousands, not 140 dollars.
        self.assertEqual(self.mid("140 - 180K")[0], 160000.0)

    def test_en_and_em_dashes(self):
        self.assertEqual(self.mid("$100,000–$120,000")[0], 110000.0)
        self.assertEqual(self.mid("$100,000—$120,000")[0], 110000.0)

    def test_lpa(self):
        value, pretty = self.mid("25-35 LPA")
        self.assertEqual(value, 3000000.0)
        self.assertEqual(pretty, "30 LPA")

    def test_indian_lakh_grouping_is_preserved(self):
        value, pretty = self.mid("Rs. 25,00,000 - Rs. 35,00,000")
        self.assertEqual(value, 3000000.0)
        self.assertEqual(pretty, "₹30,00,000")

    def test_a_one_sided_band_uses_the_stated_figure(self):
        value, pretty = self.mid("up to $150,000")
        self.assertEqual(value, 150000.0)
        self.assertEqual(pretty, "$150,000")

    def test_currency_symbols_are_carried_through(self):
        self.assertEqual(self.mid("£60,000 - £75,000")[1], "£67,500")
        self.assertEqual(self.mid("€80,000 - €90,000")[1], "€85,000")

    def test_a_reversed_band_still_gives_the_midpoint(self):
        self.assertEqual(self.mid("$90,000 - $70,000")[0], 80000.0)

    def test_an_odd_midpoint_rounds_rather_than_showing_cents(self):
        # A salary field with a decimal in it looks like a mistake.
        self.assertEqual(self.mid("$70,000 - $85,000")[1], "$77,500")
        pretty = self.mid("$70,001 - $80,000")[1]   # midpoint is 75000.5
        self.assertNotIn(".", pretty)
        self.assertIn(pretty, ("$75,000", "$75,001"))

    def test_no_band_is_reported_rather_than_guessed(self):
        self.assertIsNone(self.mid("")[0])
        self.assertIsNone(self.mid("competitive")[0])
        self.assertIsNone(self.mid("depends on experience")[0])


class GroupingTests(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(salary.indian_grouping(3000000), "30,00,000")
        self.assertEqual(salary.indian_grouping(120000), "1,20,000")
        self.assertEqual(salary.indian_grouping(900), "900")
