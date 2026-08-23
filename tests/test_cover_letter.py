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
                "location": "Charlotte, NC",
                "phone": "5550101234",
                "fullAddress": {
                    "street": "9308 Private Test Street",
                    "city": "Charlotte",
                    "state": "NC",
                    "postalCode": "28202",
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
        self.assertEqual(candidate.location, "Charlotte, NC")
        self.assertEqual(candidate.phone, "(555) 010-1234")

    def test_region_selects_its_own_resume_identity(self):
        candidate = cover_letter.identity_for_region(PROFILE, "IN")
        self.assertEqual(candidate.location, "Bengaluru, India")
        self.assertEqual(candidate.phone, "+91 98765 43210")

    def test_header_keeps_the_street_address_out(self):
        candidate = cover_letter.identity_for_region(PROFILE)
        rendered = cover_letter.render_html(self.template, self.content, candidate)
        self.assertIn("Charlotte, NC", rendered)
        self.assertIn("candidate@example.com", rendered)
        self.assertNotIn("9308 Private Test Street", rendered)
        self.assertNotIn("28202", rendered)

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


if __name__ == "__main__":
    unittest.main()
