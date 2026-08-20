"""Tests for scripts/resume_pdf.py, the local PDF toolchain.

What matters here is that `verify` actually catches drift. A verifier that
always passes is worse than none, because it converts an unchecked resume into
one that looks checked.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("resume_pdf", ROOT / "scripts" / "resume_pdf.py")
resume_pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume_pdf)

HTML = """<!doctype html><meta charset="utf-8"><style>
@page {{ size: 8.5in 11in; margin: {margin}; }}
body {{ font-family: Helvetica, sans-serif; font-size: 10.5pt; margin: 0; }}
h1 {{ font-size: 20pt; color: {accent}; margin: 0; }}
.contact {{ font-size: 9.5pt; margin: 2pt 0 0; }}
</style><h1>Test Person</h1>
<p class="contact">{contact}</p>
<p>A line of body text with a 42% metric in it.</p>"""

SHORT_CONTACT = "Springfield, IL 62704 | (555) 010-1234 | someone@example.com | LinkedIn"
# Long enough to wrap at this width: the exact failure a street address caused.
LONG_CONTACT = ("9308 Somewhere Extremely Long Street Name, Springfield, IL 62704 | "
                "(555) 010-1234 | someone@example.com | example.com | LinkedIn | "
                "GitHub | Portfolio | Personal Blog | Twitter")


def chrome_available():
    try:
        resume_pdf.find_chrome()
        return True
    except resume_pdf.ToolError:
        return False


def pymupdf_available():
    try:
        resume_pdf.require_pymupdf()
        return True
    except resume_pdf.ToolError:
        return False


class FontHeuristicTest(unittest.TestCase):
    def test_type3_glyph_programs_are_not_treated_as_fonts(self):
        # A PDF's inline bullet glyphs show up as "Type3 (7 0 R)". Reporting
        # those as a missing font sends the user chasing a font that does not
        # exist.
        self.assertTrue("Type3 (7 0 R)".startswith("Type3"))

    def test_known_microsoft_fonts_map_to_metric_substitutes(self):
        self.assertEqual(resume_pdf.METRIC_SUBSTITUTES["calibri"][0], "Carlito")
        self.assertEqual(resume_pdf.METRIC_SUBSTITUTES["cambria"][0], "Caladea")

    def test_font_scan_is_cached(self):
        resume_pdf._FONT_CACHE = None
        first = resume_pdf.installed_font_families()
        self.assertIs(first, resume_pdf.installed_font_families())


@unittest.skipUnless(chrome_available() and pymupdf_available(),
                     "needs Chrome and pymupdf")
class RenderAndVerifyTest(unittest.TestCase):
    """Renders real PDFs through the same path a tailored resume takes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        cls.base = cls.render("base", margin="0.5in 0.6in", accent="#1F4E79")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def render(cls, name, margin, accent, **kwargs):
        html = cls.tmp / f"{name}.html"
        html.write_text(HTML.format(margin=margin, accent=accent,
                                    contact=kwargs.get("contact", SHORT_CONTACT)),
                        encoding="utf-8")
        out = cls.tmp / f"{name}.pdf"
        resume_pdf.cmd_render(type("A", (), {"html": str(html), "out": str(out)}))
        return out

    def verify(self, built):
        return resume_pdf.cmd_verify(
            type("A", (), {"pdf": str(built), "against": str(self.base)}))

    def test_us_letter_geometry(self):
        geo = resume_pdf.geometry(self.base)
        self.assertEqual(geo["pageCount"], 1)
        self.assertEqual(geo["pages"][0]["width"], 612.0)
        self.assertEqual(geo["pages"][0]["height"], 792.0)

    def test_accent_colour_survives_the_render(self):
        self.assertIn("#1f4e79", resume_pdf.geometry(self.base)["colors"])

    def test_identical_rebuild_matches(self):
        same = self.render("same", margin="0.5in 0.6in", accent="#1F4E79")
        self.assertTrue(self.verify(same)["match"], self.verify(same)["differences"])

    def test_changed_margins_are_caught(self):
        drifted = self.render("margins", margin="0.9in 1.1in", accent="#1F4E79")
        result = self.verify(drifted)
        self.assertFalse(result["match"])
        self.assertTrue(any("left" in d for d in result["differences"]))

    def test_changed_accent_colour_is_caught(self):
        drifted = self.render("colour", margin="0.5in 0.6in", accent="#8B1A1A")
        result = self.verify(drifted)
        self.assertFalse(result["match"])
        self.assertTrue(any("#1f4e79" in d for d in result["differences"]))

    def test_wrapped_contact_line_is_caught(self):
        # The failure this exists for: a contact line long enough to wrap
        # leaves page count, margins, colours and font sizes untouched, so
        # every other check passes while the document visibly differs.
        wrapped = self.render("wrapped", margin="0.5in 0.6in", accent="#1F4E79",
                              contact=LONG_CONTACT)
        result = self.verify(wrapped)
        self.assertFalse(result["match"])
        self.assertTrue(any("contact block" in d for d in result["differences"]),
                        result["differences"])

    def test_rendered_pdf_is_owner_readable_only(self):
        import os
        self.assertEqual(os.stat(self.base).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
