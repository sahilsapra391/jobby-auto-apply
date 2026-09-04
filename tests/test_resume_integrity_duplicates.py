"""The resume integrity gate refuses doubled words, whoever generated them.

test_resume_build renders the real base resume and is excluded from CI, so
the duplication half of assert_integrity gets its own hermetic module: pure
dict fixtures, no store, no Chrome. The rule under test is shared with the
cover letter (cover_letter imports resume_build.doubled_word), so both
documents answer to one definition and a provider swap cannot reintroduce
"Dear Dear" or "led led" through either door.

Measured 2026-08-28 across all 79 rendered documents on this machine: the
only adjacent duplicated alphabetic word any provider produced was "Dear",
sixteen times, and digits legitimately repeat 264 times, which is why the
rule is alphabetic-only.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "resume_build", ROOT / "scripts" / "resume_build.py")
resume_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume_build)


def content(bullet):
    return {
        "banner": "TEST CANDIDATE",
        "summary": "Product manager with measured outcomes.",
        "areasOfExpertise": ["Roadmaps"],
        "technical": [],
        "roles": [{"org": "Acme", "jobtitle": "PM", "bullets": [bullet]}],
    }


class DoubledWordTests(unittest.TestCase):
    def test_an_adjacent_repeated_word_is_named(self):
        self.assertEqual(resume_build.doubled_word("led led the team"), "led")
        self.assertEqual(resume_build.doubled_word("Dear Dear Hiring Team"), "Dear")

    def test_case_differences_do_not_hide_the_repeat(self):
        self.assertEqual(resume_build.doubled_word("The the roadmap"), "The")

    def test_digits_and_distinct_words_pass(self):
        self.assertIsNone(resume_build.doubled_word("grew 0 0.5 to 3 3-person squads"))
        self.assertIsNone(resume_build.doubled_word("had a plan that led the team"))

    def test_hyphenated_and_possessive_words_are_single_tokens(self):
        self.assertIsNone(resume_build.doubled_word("feature-parity feature work"))
        self.assertEqual(resume_build.doubled_word("the team's team's charter"), "team's")


class IntegrityGateTests(unittest.TestCase):
    def test_a_doubled_word_in_a_tailored_bullet_fails_integrity(self):
        base = content("Led the migration of 75 customers.")
        bad = content("Led the the migration of 75 customers.")
        with self.assertRaises(resume_build.ResumeIntegrityError) as caught:
            resume_build.assert_integrity(base, bad)
        message = str(caught.exception)
        self.assertIn("'the'", message)
        self.assertIn("roles[1]", message)

    def test_a_clean_tailored_resume_passes(self):
        base = content("Led the migration of 75 customers.")
        resume_build.assert_integrity(base, content("Led the migration of 75 customers."))

    def test_a_technical_row_printed_twice_fails_integrity(self):
        base = content("Led the migration of 75 customers.")
        bad = content("Led the migration of 75 customers.")
        row = {"label": "Languages", "items": "Python, TypeScript"}
        bad["technical"] = [dict(row), dict(row)]
        base["technical"] = [dict(row), {"label": "Cloud", "items": "AWS"}]
        with self.assertRaises(resume_build.ResumeIntegrityError) as caught:
            resume_build.assert_integrity(base, bad)
        self.assertIn("same line twice", str(caught.exception))

    def test_a_repeated_expertise_chip_fails_integrity(self):
        base = content("Led the migration of 75 customers.")
        bad = content("Led the migration of 75 customers.")
        bad["areasOfExpertise"] = ["Roadmaps", "roadmaps"]
        base["areasOfExpertise"] = ["Roadmaps", "Discovery"]
        with self.assertRaises(resume_build.ResumeIntegrityError):
            resume_build.assert_integrity(base, bad)


class ShippedTemplateIsCleanTests(unittest.TestCase):
    """The template must satisfy the same gates it feeds.

    The duplicated technical row lived in templates/resume-content.json
    itself, so every tailored resume inherited it and the integrity gate
    could never have been added without failing everything. This pins the
    template clean so the duplicate cannot come back through the file the
    whole pipeline starts from.
    """

    def test_the_template_passes_every_duplication_gate(self):
        import json
        template = json.loads(
            (ROOT / "templates" / "resume-content.json").read_text())
        resume_build.assert_integrity(template, template)
