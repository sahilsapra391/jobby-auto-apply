#!/usr/bin/env python3
"""What a posting says under its own headings, and the year figure that follows."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import extract  # noqa: E402
import posting_sections  # noqa: E402

# The posting that prompted this, reduced to the shape that mattered. The years
# figure sits in a requirements bullet whose neighbour mentions a roadmap.
PAYTM = """We're hiring at Paytm Payments Services Limited(PPSL)

About the Role-
As a Product Manager (Devices), you will own and lead the device charter.

Key Responsibilities-
- Define and own the device roadmap & vision aligned to Paytm's growth strategy.
- Mentor PMs/Associate PMs and build a strong device product culture.

Skills & Qualifications-
- Product experience, with at least 2 years owning device/IoT/fintech hardware products.
- Strong leadership & roadmap ownership skills, with CXO-level exposure.

Why join us?
- A collaborative output driven program.
"""


class SectionTests(unittest.TestCase):
    def test_both_halves_of_a_real_posting_are_found(self):
        out = posting_sections.split_sections(PAYTM)
        self.assertEqual(len(out["responsibilities"]), 3)
        self.assertEqual(len(out["requirements"]), 2)
        self.assertIn("at least 2 years", out["requirements"][0])

    def test_a_closing_heading_ends_the_section_rather_than_joining_it(self):
        out = posting_sections.split_sections(PAYTM)
        joined = " ".join(out["requirements"] + out["responsibilities"])
        self.assertNotIn("collaborative output driven", joined)

    def test_punctuation_and_case_in_a_heading_do_not_matter(self):
        # "Skills & Qualifications-" against "skills and qualifications" is what
        # failed first: the ampersand normalises on one side only unless both
        # sides go through the same function.
        for heading in ("Skills & Qualifications-", "SKILLS AND QUALIFICATIONS:",
                        "Skills / Qualifications", "  what you bring  "):
            out = posting_sections.split_sections(heading + "\n- 5 years of Python.\n")
            self.assertEqual(out["requirements"], ["5 years of Python."], heading)

    def test_a_posting_with_no_recognisable_heading_returns_nothing(self):
        out = posting_sections.split_sections("We are hiring.\nCome and work here.\n")
        self.assertEqual(out, {"responsibilities": [], "requirements": []})

    def test_a_sentence_starting_with_a_heading_word_is_not_a_heading(self):
        body = ("Requirements gathering is a large part of this role and you will "
                "spend time with stakeholders on it every week.\n- not a bullet\n")
        self.assertEqual(posting_sections.split_sections(body)["requirements"], [])


class YearsFromSectionsTests(unittest.TestCase):
    def test_a_neighbouring_bullet_no_longer_disqualifies_the_figure(self):
        # The window reached into "roadmap ownership skills" in the next bullet
        # and refused a real requirement. A disqualifier only counts on the
        # figure's own line now.
        self.assertEqual(extract.years_required(PAYTM)["yearsMin"], 2)

    def test_the_requirements_section_is_read_first(self):
        self.assertEqual(
            extract.years_from_sections(posting_sections.split_sections(PAYTM), PAYTM),
            {"yearsMin": 2, "yearsMax": 2, "yearsText": "2+ years"})

    def test_a_requirements_bullet_qualifies_without_the_word_experience(self):
        # "7+ years in growth" is a requirement. Only the section heading says
        # so, which is why the section pass does not need a requirement word.
        sections = {"requirements": ["7+ years in growth, account management."]}
        self.assertEqual(extract.years_from_sections(sections, "")["yearsMin"], 7)

    def test_that_relaxation_does_not_reach_the_whole_body(self):
        # The same sentence outside a requirements section stays scenery.
        body = "We have been building for 7+ years in growth, account management."
        self.assertIsNone(extract.years_required(body))

    def test_a_disqualifier_on_the_same_line_still_bites(self):
        sections = {"requirements": ["Our roadmap for the next 3 years is ambitious."]}
        self.assertIsNone(extract.years_from_sections(sections, ""))

    def test_a_body_that_is_one_long_line_is_not_made_worse(self):
        # No newlines means no line boundaries, so the clamp is what keeps the
        # disqualifier check from widening to the whole document.
        body = ("Minimum qualifications: 5 years of experience in product. "
                "Separately, our roadmap covers the next decade.")
        self.assertEqual(extract.years_required(body)["yearsMin"], 5)


if __name__ == "__main__":
    unittest.main()
