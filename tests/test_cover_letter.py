"""Tests for the repository-owned cover-letter renderer and verifier."""

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cover_letter", ROOT / "scripts" / "cover_letter.py"
)
cover_letter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cover_letter
spec.loader.exec_module(cover_letter)


CONTENT = {
    "employer": "Northwind Analytics",
    "role": "Senior Product Manager",
    "date": "2026-08-23",
    "salutation": "Product Hiring Team",
    "opening": (
        "Northwind's focus on dependable analytics matches the product work I "
        "have led across data-heavy enterprise platforms."
    ),
    "evidence": [
        (
            "I owned an enterprise migration roadmap that moved 75 customers to "
            "a new aviation platform with zero operational downtime."
        ),
        (
            "I also automated a two-week data onboarding process, raising capacity "
            "to three customer migrations per day without adding headcount."
        ),
    ],
    "closing": (
        "I would bring that same mix of customer discovery, technical judgment, "
        "and measured delivery to Northwind's product team."
    ),
}

PROFILE = {
    "firstName": "Test",
    "lastName": "Candidate",
    "email": "candidate@example.com",
    "links": {
        "linkedin": "https://www.linkedin.com/in/test-candidate",
        "website": "https://example.com/portfolio",
    },
    "resumes": [
        {
            "key": "us",
            "default": True,
            "appliesTo": ["US"],
            "identity": {
                "location": "Springfield, IL",
                "phone": "5550101234",
                "fullAddress": {
                    "street": "9308 Private Test Street",
                    "city": "Springfield",
                    "state": "IL",
                    "postalCode": "62704",
                },
            },
        },
        {
            "key": "in",
            "default": False,
            "appliesTo": ["IN"],
            "identity": {
                "location": "Bengaluru, India",
                "phone": "+91 98765 43210",
                "fullAddress": {"street": "44 Private Test Road"},
            },
        },
    ],
}


class GreetingAndSignOffOnceTests(unittest.TestCase):
    """The template writes the greeting and the signature exactly once.

    Measured 2026-08-28 over the 39 rendered cover letters on this machine:
    16 read "Dear Dear ..." and 5 carried "Sincerely, <name>" twice, because
    the model put the greeting inside the salutation field and the sign-off
    inside the closing paragraph, and the fixed template adds both itself.
    Real employers received these letters.
    """

    def content(self, **overrides):
        merged = {**CONTENT, **overrides}
        return cover_letter.validate_content(merged)

    def test_a_salutation_that_already_says_dear_loses_it(self):
        got = self.content(salutation="Dear Justworks Hiring Team")
        self.assertEqual(got["salutation"], "Justworks Hiring Team")

    def test_even_a_double_dear_from_the_model_collapses(self):
        got = self.content(salutation="Dear Dear Hiring Team,")
        self.assertEqual(got["salutation"], "Hiring Team")

    def test_a_salutation_that_is_only_dear_falls_back_to_hiring_team(self):
        got = self.content(salutation="Dear,")
        self.assertEqual(got["salutation"], "Hiring Team")

    def test_a_closing_that_signs_off_keeps_its_prose_and_loses_the_signature(self):
        got = self.content(closing=(
            "Thank you for your consideration - I look forward to discussing "
            "how I can contribute. Sincerely, Alex Example"
        ))
        self.assertTrue(got["closing"].endswith("how I can contribute."))
        self.assertNotIn("Sincerely", got["closing"])

    def test_sincerely_yours_and_a_bare_regards_also_strip(self):
        got = self.content(closing=(
            "I would welcome the chance to help the team ship faster. "
            "Sincerely yours, Test Candidate"
        ))
        self.assertTrue(got["closing"].endswith("ship faster."))
        got = self.content(closing=(
            "I would welcome the chance to help the team ship faster. Regards,"
        ))
        self.assertTrue(got["closing"].endswith("ship faster."))

    def test_ordinary_gratitude_is_not_a_sign_off(self):
        kept = "I appreciate the team's time and consideration. Thank you."
        got = self.content(closing=kept)
        self.assertEqual(got["closing"], kept)

    def test_a_closing_that_is_only_a_sign_off_is_refused_by_name(self):
        with self.assertRaises(cover_letter.CoverLetterError) as caught:
            self.content(closing="Sincerely yours, Alex Example")
        self.assertIn("sign-off", str(caught.exception).lower())

    def test_render_refuses_unvalidated_duplicates_even_without_validate(self):
        # The belt behind the normaliser: feed render_html raw content the
        # way no current caller does, and it must refuse rather than render
        # "Dear Dear". This is the test that fails if the guard is removed.
        template = "<x>{{candidateName}}{{contactLine}}{{profileLinks}}{{documentTitle}}{{date}}{{employer}}{{role}}{{salutation}}{{body}}</x>"
        good = cover_letter.validate_content(dict(CONTENT))
        candidate = cover_letter.identity_for_region(PROFILE, "US")
        raw = dict(good)
        raw["salutation"] = "Dear Hiring Team"
        with self.assertRaises(cover_letter.CoverLetterError):
            cover_letter.render_html(template, raw, candidate)
        raw = dict(good)
        raw["closing"] = good["closing"] + " Sincerely, Test Candidate"
        with self.assertRaises(cover_letter.CoverLetterError):
            cover_letter.render_html(template, raw, candidate)
        rendered = cover_letter.render_html(template, good, candidate)
        self.assertNotIn("Dear Dear", rendered)


class DuplicationGateTests(unittest.TestCase):
    """No doubled words, no repeated paragraphs, no greeting in the body.

    All three gates sit in validate_content, behind every provider, because
    the duplication that shipped ("Dear Dear", a doubled signature) came out
    of whichever model happened to write the letter and the fix must not
    care which one that was. The thresholds are measured, not guessed:
    across all 79 rendered documents the only adjacent duplicated alphabetic
    word ever produced was "Dear", and the most similar genuine paragraph
    pair shares 18% of its words against the 60% ceiling here.
    """

    def content(self, **overrides):
        return cover_letter.validate_content({**CONTENT, **overrides})

    def test_a_word_typed_twice_in_a_row_is_refused_by_name(self):
        with self.assertRaises(cover_letter.CoverLetterError) as caught:
            self.content(opening=(
                "I am writing to to apply for this role because the team's "
                "work matches my background."
            ))
        self.assertIn("'to'", str(caught.exception))
        self.assertIn("opening", str(caught.exception))

    def test_repeated_digits_are_figures_not_artifacts(self):
        got = self.content(opening=(
            "I moved capacity from 3 3-person squads to a single team of "
            "twelve without slipping a release date."
        ))
        self.assertIn("3 3-person", got["opening"])

    def test_a_paragraph_written_twice_is_refused(self):
        paragraph = (
            "At Northwind Logistics I owned the migration roadmap for seventy five "
            "enterprise customers and landed it with zero downtime."
        )
        with self.assertRaises(cover_letter.CoverLetterError) as caught:
            self.content(evidence=[paragraph, paragraph])
        self.assertIn("repeats", str(caught.exception))

    def test_a_lightly_reworded_copy_is_still_a_repeat(self):
        paragraph = (
            "At Northwind Logistics I owned the migration roadmap for seventy five "
            "enterprise customers and landed it with zero downtime."
        )
        tweaked = paragraph.replace("landed", "delivered")
        with self.assertRaises(cover_letter.CoverLetterError):
            self.content(evidence=[paragraph, tweaked])

    def test_distinct_paragraphs_pass(self):
        got = self.content()
        self.assertEqual(len(got["evidence"]), len(CONTENT["evidence"]))

    def test_a_body_paragraph_may_not_open_with_a_greeting(self):
        with self.assertRaises(cover_letter.CoverLetterError) as caught:
            self.content(opening=(
                "Dear Hiring Team, I am writing to apply for this role with "
                "genuine enthusiasm for the mission."
            ))
        self.assertIn("greeting", str(caught.exception))


class ValidationTests(unittest.TestCase):
    def test_content_has_a_deliberately_small_schema(self):
        value = dict(CONTENT, model="some-model")
        with self.assertRaisesRegex(cover_letter.CoverLetterError,
                                    "unsupported content keys: model"):
            cover_letter.validate_content(value)

    def test_placeholder_text_is_rejected_in_every_authored_section(self):
        cases = [
            ("employer", "[Company Name]"),
            ("role", "{{role}}"),
            ("opening", "This is placeholder text for the finished opening paragraph."),
            ("closing", "TODO replace this finished closing paragraph later."),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                raw = json.loads(json.dumps(CONTENT))
                raw[field] = value
                with self.assertRaisesRegex(cover_letter.CoverLetterError,
                                            "placeholder text"):
                    cover_letter.validate_content(raw)

    def test_evidence_is_required_and_bounded(self):
        for evidence in ([], ["A supported paragraph with enough detail.                         "] * 4):
            with self.subTest(count=len(evidence)):
                with self.assertRaisesRegex(cover_letter.CoverLetterError,
                                            "one to three"):
                    cover_letter.validate_content(dict(CONTENT, evidence=evidence))

    def test_invalid_date_is_rejected(self):
        with self.assertRaisesRegex(cover_letter.CoverLetterError, "YYYY-MM-DD"):
            cover_letter.validate_content(dict(CONTENT, date="August 23"))


class IdentityAndHtmlTests(unittest.TestCase):
    def setUp(self):
        self.content = cover_letter.validate_content(CONTENT)
        self.template = (ROOT / "templates" / "cover-letter.html").read_text()

    def test_default_resume_identity_is_inherited(self):
        candidate = cover_letter.identity_for_region(PROFILE)
        self.assertEqual(candidate.name, "Test Candidate")
        self.assertEqual(candidate.location, "Springfield, IL")
        self.assertEqual(candidate.phone, "(555) 010-1234")

    def test_region_selects_its_own_resume_identity(self):
        candidate = cover_letter.identity_for_region(PROFILE, "IN")
        self.assertEqual(candidate.location, "Bengaluru, India")
        self.assertEqual(candidate.phone, "+91 98765 43210")

    def test_header_keeps_the_street_address_out(self):
        candidate = cover_letter.identity_for_region(PROFILE)
        rendered = cover_letter.render_html(self.template, self.content, candidate)
        self.assertIn("Springfield, IL", rendered)
        self.assertIn("candidate@example.com", rendered)
        self.assertNotIn("9308 Private Test Street", rendered)
        self.assertNotIn("62704", rendered)

    def test_authored_text_is_html_escaped(self):
        content = cover_letter.validate_content(
            dict(CONTENT, employer="Research & Development <Labs>")
        )
        rendered = cover_letter.render_html(
            self.template, content, cover_letter.identity_for_region(PROFILE)
        )
        self.assertIn("Research &amp; Development &lt;Labs&gt;", rendered)
        self.assertNotIn("<Labs>", rendered)

    def test_unresolved_template_token_is_rejected(self):
        with self.assertRaisesRegex(cover_letter.CoverLetterError, "unresolved tokens"):
            cover_letter.render_html(
                self.template + "{{unknownField}}",
                self.content,
                cover_letter.identity_for_region(PROFILE),
            )

    def test_generated_html_is_owner_readable_only(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "letter.html"
            cover_letter.private_write(target, "private")
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)


def toolchain_available():
    try:
        cover_letter.resume_pdf.find_chrome()
        cover_letter.resume_pdf.require_pymupdf()
        return True
    except cover_letter.resume_pdf.ToolError:
        return False


@unittest.skipUnless(toolchain_available(), "needs Chrome and pymupdf")
class RenderAndVerifyTests(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.content_path = self.directory / "content.json"
        self.profile_path = self.directory / "profile.json"
        self.html_path = self.directory / "letter.html"
        self.pdf_path = self.directory / "letter.pdf"
        self.content_path.write_text(json.dumps(CONTENT), encoding="utf-8")
        self.profile_path.write_text(json.dumps(PROFILE), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def render(self):
        return cover_letter.main([
            "render",
            "--content", str(self.content_path),
            "--profile", str(self.profile_path),
            "--out-html", str(self.html_path),
            "--out-pdf", str(self.pdf_path),
        ])

    def test_render_produces_a_verified_one_page_pdf(self):
        self.assertEqual(self.render(), 0)
        report = cover_letter.verify_pdf(
            self.pdf_path,
            cover_letter.validate_content(CONTENT),
            cover_letter.identity_for_region(PROFILE),
        )
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["pageCount"], 1)
        self.assertTrue(report["readable"])
        self.assertTrue(report["contentComplete"])
        self.assertFalse(report["overflowSignals"]["detected"])
        self.assertTrue(report["streetAddressOmitted"])
        self.assertEqual(os.stat(self.pdf_path).st_mode & 0o777, 0o600)

    def test_verify_cli_emits_json(self):
        self.assertEqual(self.render(), 0)
        process = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts" / "cover_letter.py"), "verify",
                "--pdf", str(self.pdf_path),
                "--content", str(self.content_path),
                "--profile", str(self.profile_path),
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertTrue(report["ok"])
        self.assertTrue(report["requiredFields"]["candidateName"])
        self.assertTrue(report["requiredFields"]["employer"])
        self.assertTrue(report["requiredFields"]["role"])

    def test_tampered_expected_role_fails_verification(self):
        self.assertEqual(self.render(), 0)
        changed = cover_letter.validate_content(dict(CONTENT, role="Director of Platforms"))
        report = cover_letter.verify_pdf(
            self.pdf_path, changed, cover_letter.identity_for_region(PROFILE)
        )
        self.assertFalse(report["ok"])
        self.assertFalse(report["requiredFields"]["role"])
