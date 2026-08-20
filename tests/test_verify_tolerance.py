"""Tests for the bottom-edge tolerance in scripts/resume_pdf.py verify.

`bottom` is measured to the last glyph on the page, so unlike the other edges it
is not a template setting: it moves with how much text there is, and a page
break can only land between lines. Holding it to the same 2pt tolerance as the
real margins made the check unsatisfiable, and agents re-rendered up to 13 times
trying to satisfy it. These tests pin the distinction, so the tolerance cannot
quietly go back to being uniform, and so the checks that do matter keep failing
when they should.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("resume_pdf", ROOT / "scripts" / "resume_pdf.py")
resume_pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume_pdf)


def page(**over):
    base = {"width": 612.0, "height": 792.0, "left": 54.0, "right": 54.0,
            "top": 36.0, "bottom": 40.0}
    base.update(over)
    return base


def doc(pages, colors=None, sizes=None):
    return {"pageCount": len(pages), "pages": pages,
            "sizes": sizes or [10.5], "fonts": ["Helvetica"],
            "colors": colors or {"#1f4e79": 4}}


class ToleranceTests(unittest.TestCase):
    def verify(self, source, built):
        """Run cmd_verify against two synthetic geometries."""
        original = resume_pdf.geometry
        order = [source, built]          # pop() takes the last, and
        # cmd_verify reads the built PDF first, then the source it is checked against
        resume_pdf.geometry = lambda _p: order.pop()
        try:
            args = types.SimpleNamespace(pdf="built.pdf", against="source.pdf")
            return resume_pdf.cmd_verify(args)
        finally:
            resume_pdf.geometry = original

    def test_a_bottom_within_one_line_passes(self):
        # 9.2pt was the median real difference and never meant anything wrong.
        out = self.verify(doc([page(bottom=40.0)]), doc([page(bottom=49.2)]))
        self.assertTrue(out["match"], out["differences"])

    def test_a_bottom_off_by_several_lines_still_fails(self):
        # A visibly half-empty page is several lines, not one, and must fail.
        out = self.verify(doc([page(bottom=40.0)]), doc([page(bottom=106.0)]))
        self.assertFalse(out["match"])
        self.assertTrue(any("bottom" in d for d in out["differences"]))

    def test_text_past_the_bottom_edge_is_reported_absolutely(self):
        # Negative bottom means the last line is off the page and clipped. That
        # is a failure whatever the source happens to measure.
        out = self.verify(doc([page(bottom=40.0)]), doc([page(bottom=-6.0)]))
        self.assertFalse(out["match"])
        self.assertTrue(any("past the bottom edge" in d for d in out["differences"]),
                        out["differences"])

    def test_the_real_margins_keep_the_tight_tolerance(self):
        # left/right/top/width/height are template settings and do hold still,
        # so relaxing bottom must not have relaxed them.
        for edge in ("left", "right", "top", "width", "height"):
            with self.subTest(edge=edge):
                built = page(**{edge: page()[edge] + 6.0})
                out = self.verify(doc([page()]), doc([built]))
                self.assertFalse(out["match"], f"{edge} drift of 6pt should fail")
                self.assertTrue(any(edge in d for d in out["differences"]))

    def test_page_count_change_still_fails(self):
        out = self.verify(doc([page()]), doc([page(), page()]))
        self.assertFalse(out["match"])
        self.assertTrue(any("page count" in d for d in out["differences"]))

    def test_a_missing_accent_colour_still_fails(self):
        out = self.verify(doc([page()], colors={"#1f4e79": 4}),
                          doc([page()], colors={"#000000": 4}))
        self.assertFalse(out["match"])
        self.assertTrue(any("colour" in d for d in out["differences"]))

    def test_an_identical_rebuild_matches(self):
        out = self.verify(doc([page(), page()]), doc([page(), page()]))
        self.assertTrue(out["match"], out["differences"])


if __name__ == "__main__":
    unittest.main()
