"""Tests for scripts/resume_build.py, the templated resume.

Two things have to hold. The template must reproduce a resume exactly, or the
layout gate that measures the render against the base will start failing for
reasons nobody can see. And the merge must be lossless, or a posting silently
goes out with the base wording instead of the tailored wording.

The third property is the one that is not about speed at all: employers, dates
and job titles live in the template, so a caller cannot change them. That was
previously a rule the verification step tried to catch afterwards.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "resume_build", ROOT / "scripts" / "resume_build.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

TEMPLATE = ROOT / "templates" / "resume.html"
BASE = ROOT / "templates" / "resume-content.json"

SAMPLE = """<style>.x{y:1}</style>
<div class="name">SAHIL SAPRA</div>
<div class="contact">Charlotte, NC | someone@example.com</div>
<div class="banner">PRODUCT MANAGER</div>
<div class="body summary">A summary with R&amp;D and a &rsquo; in it.</div>
<div class="sec">AREAS OF EXPERTISE</div>
<div class="aoe"><span class="b">&bull;</span>&nbsp;&nbsp;First area</div>
<div class="aoe"><span class="b">&bull;</span>&nbsp;&nbsp;Second area</div>
<div class="sec">TECHNICAL PROFICIENCIES</div>
<div class="tech"><b>Languages:</b> Python, SQL</div>
<div class="sec">EXPERIENCE</div>
<div class="org">CAMP Systems, Charlotte, NC<span class="when">2024 &ndash; 2026</span></div>
<div class="jobtitle">Technical Product Manager (2025 &ndash; 2026)</div>
<div class="body lede">Owned the tooling.</div>
<ul>
    <li>Built the generator.</li>
    <li>Raised coverage to 81%.</li>
  </ul>
<div class="pgno">1</div>
"""


class RoundTripTests(unittest.TestCase):
    def test_extract_then_render_reproduces_the_document(self):
        template, content = rb.extract(SAMPLE)
        self.assertEqual(rb.render(template, content), SAMPLE)

    def test_every_resume_ever_written_round_trips_exactly(self):
        # The real corpus, not a fixture. If the markup a run produces drifts
        # from what the template understands, this is where it shows.
        files = sorted((ROOT / "applications").glob("*/.resume.html"))
        if not files:
            self.skipTest("no rendered resumes on this machine")
        for f in files:
            with self.subTest(company=f.parent.name):
                src = f.read_text()
                template, content = rb.extract(src)
                self.assertEqual(rb.render(template, content), src)

    def test_the_frozen_parts_stay_in_the_template(self):
        template, content = rb.extract(SAMPLE)
        for frozen in ("SAHIL SAPRA", "CAMP Systems", "Technical Product Manager",
                       "2025 &ndash; 2026", "someone@example.com"):
            self.assertIn(frozen, template, frozen)
        # and are not something a caller can set
        self.assertNotIn("SAHIL SAPRA", json.dumps(content))
        self.assertNotIn("CAMP Systems", json.dumps(content))


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.base = {"banner": "B", "summary": "S",
                     "areasOfExpertise": ["a", "b"],
                     "technical": [{"label": "L", "items": "i"}],
                     "roles": [{"lede": "led", "bullets": ["one", "two", "three"]}]}

    def test_anything_not_mentioned_is_inherited(self):
        out = rb.merge(self.base, {"banner": "NEW"})
        self.assertEqual(out["banner"], "NEW")
        self.assertEqual(out["summary"], "S")
        self.assertEqual(out["roles"][0]["bullets"], ["one", "two", "three"])

    def test_a_single_bullet_can_be_replaced_by_index(self):
        out = rb.merge(self.base, {"roles": [{"bullets": {"1": "TWO!"}}]})
        self.assertEqual(out["roles"][0]["bullets"], ["one", "TWO!", "three"])

    def test_a_full_bullet_list_replaces_everything(self):
        out = rb.merge(self.base, {"roles": [{"bullets": ["only"]}]})
        self.assertEqual(out["roles"][0]["bullets"], ["only"])

    def test_the_base_is_not_mutated(self):
        rb.merge(self.base, {"roles": [{"bullets": {"0": "changed"}}]})
        self.assertEqual(self.base["roles"][0]["bullets"][0], "one")

    def test_overriding_a_bullet_that_does_not_exist_is_refused(self):
        with self.assertRaises(SystemExit):
            rb.merge(self.base, {"roles": [{"bullets": {"9": "nope"}}]})

    def test_adding_an_employer_is_refused(self):
        # Experience entries are frozen; only their wording changes.
        with self.assertRaises(SystemExit):
            rb.merge(self.base, {"roles": [{}, {"lede": "a job that is not his"}]})


class RenderGuardTests(unittest.TestCase):
    def test_a_short_list_is_refused_rather_than_silently_reflowed(self):
        template, content = rb.extract(SAMPLE)
        content["areasOfExpertise"] = ["only one"]
        with self.assertRaises(SystemExit) as ctx:
            rb.render(template, content)
        self.assertIn("areas of expertise", str(ctx.exception))

    def test_bare_ampersands_are_escaped_and_entities_left_alone(self):
        template, content = rb.extract(SAMPLE)
        content["banner"] = "R&D and Ops"
        out = rb.render(template, content)
        self.assertIn("R&amp;D and Ops", out)
        # the summary came out of the template already escaped; it must not be
        # escaped a second time into &amp;amp;
        self.assertIn("R&amp;D", out)
        self.assertNotIn("&amp;amp;", out)

    def test_markup_in_content_cannot_reach_the_page(self):
        template, content = rb.extract(SAMPLE)
        content["banner"] = "<script>alert(1)</script>"
        out = rb.render(template, content)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


class ShippedTemplateTests(unittest.TestCase):
    def test_the_shipped_template_and_base_render_together(self):
        if not (TEMPLATE.exists() and BASE.exists()):
            self.skipTest("template not present")
        out = rb.render(TEMPLATE.read_text(), json.loads(BASE.read_text()))
        self.assertIn("SAHIL SAPRA", out)
        self.assertNotIn("{{", out)

    def test_the_shipped_base_fits_the_shipped_template(self):
        if not (TEMPLATE.exists() and BASE.exists()):
            self.skipTest("template not present")
        import re
        t = TEMPLATE.read_text()
        base = json.loads(BASE.read_text())
        self.assertEqual(len(re.findall(r"\{\{aoe\d+\}\}", t)),
                         len(base["areasOfExpertise"]))
        # Technical rows are variable length: regrouping them under the
        # posting's names is part of the tailoring, and the hand-written
        # resumes used six, seven and eight.
        self.assertIn("{{technical}}", t)
        self.assertGreater(len(base["technical"]), 0)

class VariableTechnicalTests(unittest.TestCase):
    """Regrouping Technical Proficiencies is part of the tailoring.

    The resumes written by hand used six, seven and eight rows depending on the
    posting. A fixed count would have removed a row's worth of that range
    quietly, with everything still rendering.
    """

    def setUp(self):
        self.template = TEMPLATE.read_text() if TEMPLATE.exists() else None
        self.base = json.loads(BASE.read_text()) if BASE.exists() else None

    def rows(self, n):
        content = json.loads(json.dumps(self.base))
        content["technical"] = [{"label": f"Group {i}", "items": f"thing{i}"}
                                for i in range(n)]
        return rb.render(self.template, content)

    def test_six_seven_and_eight_rows_all_render(self):
        if not self.template:
            self.skipTest("template not present")
        for n in (6, 7, 8):
            with self.subTest(rows=n):
                out = self.rows(n)
                self.assertEqual(out.count('<div class="tech">'), n)
                self.assertIn(f"Group {n - 1}", out)

    def test_no_row_is_silently_dropped(self):
        if not self.template:
            self.skipTest("template not present")
        out = self.rows(8)
        for i in range(8):
            self.assertIn(f"thing{i}", out)

if __name__ == "__main__":
    unittest.main()
