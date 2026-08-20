"""Tests for required-experience extraction.

The whole difficulty is refusing the wrong number. Descriptions are full of
years that have nothing to do with what the candidate needs, and any one of
them taken as a requirement silently mis-scores the job and silently
mis-filters it — no error, just a role quietly ranked wrong or hidden.

So the negatives here matter more than the positives, and several of them came
off real postings during the supervised run rather than out of my head.

Run from the repo root: python3 -m pytest tests/ -q
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("extract", ROOT / "scripts" / "extract.py")
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)


def years(text):
    found = extract.years_required(text)
    return found["yearsText"] if found else None


class RequirementShapesTests(unittest.TestCase):
    def test_a_range(self):
        self.assertEqual(years("We want 3-5 years of product management experience."),
                         "3–5 years")
        self.assertEqual(years("3 to 5 years of relevant experience"), "3–5 years")
        self.assertEqual(years("you've spent 5–7 years in product marketing"), "5–7 years")

    def test_a_floor(self):
        self.assertEqual(years("You have 5+ years of experience shipping software."),
                         "5+ years")
        self.assertEqual(years("at least 2 years' experience managing a team"), "2+ years")
        self.assertEqual(years("Minimum of 4 years of relevant experience required."),
                         "4+ years")
        self.assertEqual(years("no less than 6 years of professional experience"), "6+ years")

    def test_a_bare_figure_next_to_the_right_words(self):
        self.assertEqual(years("Applicants must have 1 year of specialized experience "
                               "equivalent to the GS-12 level"), "1 year")

    def test_the_singular_reads_as_the_singular(self):
        self.assertEqual(years("1 year of experience"), "1 year")
        self.assertNotIn("1 years", years("1 year of experience"))

    def test_a_role_noun_qualifies_it(self):
        self.assertEqual(years("3+ years in a product ownership or similar role"), "3+ years")

    def test_the_floor_is_what_gets_stored(self):
        # A screen rejects on "you need at least five". Nobody is turned down
        # for having too few years above a stated ceiling.
        found = extract.years_required("5-8 years of experience")
        self.assertEqual(found["yearsMin"], 5)
        self.assertEqual(found["yearsMax"], 8)


class RefusalTests(unittest.TestCase):
    """What must NOT be read as a requirement."""

    def test_company_history_is_not_a_requirement(self):
        # Off a real posting: "PayPal has been revolutionizing commerce globally
        # for more than 25 years. Creating innovative experiences that…" — the
        # word "experiences" in the next sentence nearly qualified it.
        self.assertIsNone(years("PayPal has been revolutionizing commerce globally for "
                                "more than 25 years. Creating innovative experiences "
                                "that make moving money simple."))
        self.assertIsNone(years("We have been building this for 10 years of experience."))
        self.assertIsNone(years("Founded 12 years ago; we have deep experience."))
        self.assertIsNone(years("The company has 15 years of experience in the market."))
        self.assertIsNone(years("we've spent 8 years perfecting this experience"))

    def test_a_horizon_is_not_a_requirement(self):
        self.assertIsNone(years("A 3-year vision for the platform experience."))
        self.assertIsNone(years("Our roadmap covers the next 5 years of experience work."))

    def test_a_benefit_is_not_a_requirement(self):
        self.assertIsNone(years("Equity vests over 4 years of experience."))
        self.assertIsNone(years("Our average tenure is 6 years of experience."))

    def test_a_number_with_no_qualifying_language_is_ignored(self):
        self.assertIsNone(years("The office lease runs 7 years."))
        self.assertIsNone(years("We ship every 2 years."))

    def test_an_implausible_requirement_is_refused(self):
        self.assertIsNone(years("30 years of experience required"))
        self.assertEqual(extract.IMPLAUSIBLE_YEARS, 15)

    def test_nothing_stated_is_none_rather_than_a_guess(self):
        self.assertIsNone(years("We are hiring a Product Manager. Apply now."))
        self.assertIsNone(years("No experience required, we train you."))
        self.assertIsNone(years(""))
        self.assertIsNone(years(None))

    def test_a_reversed_range_is_refused(self):
        self.assertIsNone(years("9-3 years of experience"))


class ExtractShapeTests(unittest.TestCase):
    def test_the_result_always_has_every_key(self):
        # Absent and unknown are different, and the card draws an explicit chip.
        for text in ("3+ years of experience", "nothing here", ""):
            found = extract.extract(text)
            self.assertEqual(sorted(found), ["yearsMax", "yearsMin", "yearsText"])

    def test_a_runaway_description_is_not_scanned_forever(self):
        found = extract.extract("filler " * 20000 + " 5+ years of experience")
        self.assertIsNone(found["yearsMin"], "past the scan cap, so not found")
        self.assertEqual(extract.MAX_SCAN_CHARS, 40_000)


if __name__ == "__main__":
    unittest.main()
