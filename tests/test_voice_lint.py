"""Tests for scripts/voice_lint.py, the voice rules made binding.

The rules existed the whole time and letters shipped breaking them, because a
reminder is not a gate. Measured on 2026-08-25 against letters already on disk:
the Gemini letter opened "I am excited to apply", said "strongly resonates with
my background" and closed "thank you for your time and consideration"; the
Anthropic letters used an em-dash, which the rules ban outright.

Run from the repo root: python3 -m unittest discover -s tests
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import voice_lint  # noqa: E402


GOOD = (
    "I owned the migration of 75 enterprise customers onto TFBO Next-Gen. "
    "Manual onboarding was the bottleneck, so I automated the ETL pipeline and "
    "cut turnaround from two weeks per customer to three hours. I also built a "
    "PowerBI revenue digest and checked every figure against Snowflake before "
    "it reached an executive inbox."
)
EVIDENCE = (
    "CAMP Systems TFBO Next-Gen migration ETL pipeline Snowflake PowerBI "
    "Tableau attrition dashboards aviation"
)
POSTING = "We are hiring a program manager for enterprise customers and platforms."


class BannedConstructionTests(unittest.TestCase):
    def test_an_em_dash_fails(self):
        # The exact thing that shipped in the Anthropic letters.
        with self.assertRaises(voice_lint.VoiceError) as caught:
            voice_lint.check("I led the migration — it went well.")
        self.assertIn("em-dash", str(caught.exception))

    def test_an_en_dash_used_as_an_em_dash_fails(self):
        with self.assertRaises(voice_lint.VoiceError):
            voice_lint.check("I led the migration – it went well.")

    def test_the_excited_to_apply_opener_fails(self):
        with self.assertRaises(voice_lint.VoiceError) as caught:
            voice_lint.check("I am excited to apply for the role.")
        self.assertIn("banned phrasing", str(caught.exception))

    def test_the_resonates_line_fails(self):
        with self.assertRaises(voice_lint.VoiceError):
            voice_lint.check("Your focus strongly resonates with my background.")

    def test_the_thank_you_closer_fails(self):
        with self.assertRaises(voice_lint.VoiceError):
            voice_lint.check("Thank you for your time and consideration.")

    def test_the_banned_word_list_is_enforced(self):
        for word in ("delve", "tapestry", "pivotal", "empower", "testament to"):
            with self.subTest(word=word):
                with self.assertRaises(voice_lint.VoiceError):
                    voice_lint.check(f"This role is a {word} of the work.")

    def test_a_clean_letter_passes(self):
        report = voice_lint.check(GOOD, evidence=EVIDENCE, posting=POSTING)
        self.assertTrue(report["ok"])


class NamedSpecificsTests(unittest.TestCase):
    """A letter that only repeats the posting has said nothing about him."""

    def test_a_letter_of_only_posting_vocabulary_fails(self):
        echo = (
            "I am a program manager for enterprise customers and platforms. "
            "I would bring program management to your enterprise platforms."
        )
        with self.assertRaises(voice_lint.VoiceError) as caught:
            voice_lint.check(echo, evidence=EVIDENCE, posting=POSTING)
        self.assertIn("terms of his own", str(caught.exception))

    def test_a_letter_carrying_his_own_record_passes(self):
        report = voice_lint.check(GOOD, evidence=EVIDENCE, posting=POSTING)
        self.assertGreaterEqual(report["specificsCount"], voice_lint.MIN_SPECIFICS)
        self.assertTrue(
            {"snowflake", "powerbi"} & set(report["specifics"]), report["specifics"])

    def test_a_term_the_posting_supplied_is_not_his_own(self):
        found = voice_lint.specifics(
            "enterprise platforms snowflake", EVIDENCE, POSTING)
        self.assertNotIn("enterprise", found)
        self.assertNotIn("platforms", found)
        self.assertIn("snowflake", found)

    def test_the_specifics_half_is_skipped_with_no_evidence(self):
        # Skipped rather than faked. The banned half still binds, which is the
        # half that never depends on anything being supplied.
        self.assertTrue(voice_lint.check("A short plain letter.")["ok"])
        with self.assertRaises(voice_lint.VoiceError):
            voice_lint.check("A short letter — with a dash.")


class MarkupTests(unittest.TestCase):
    def test_it_reads_the_letter_and_not_its_stylesheet(self):
        # An em-dash inside CSS or a <style> block is not the letter's voice.
        html = (
            "<html><head><style>.a{content:'—';}</style></head>"
            f"<body><p>{GOOD}</p></body></html>"
        )
        self.assertTrue(
            voice_lint.check(html, evidence=EVIDENCE, posting=POSTING)["ok"])

    def test_an_em_dash_in_the_body_is_still_caught(self):
        html = f"<html><body><p>I led it — and it worked.</p></body></html>"
        with self.assertRaises(voice_lint.VoiceError):
            voice_lint.check(html)

    def test_an_html_entity_em_dash_is_caught(self):
        with self.assertRaises(voice_lint.VoiceError):
            voice_lint.check("<p>I led it &mdash; and it worked.</p>")
