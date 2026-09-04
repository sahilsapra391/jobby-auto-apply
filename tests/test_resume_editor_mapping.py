"""The editor edits the real document, so the document and the content must agree.

The Profile page renders the actual resume in an iframe and makes its text
regions editable, then reads the edits back by the same selectors
`resume_build.extract` matches on. That only works while the rendered DOM and
the content file correspond one to one.

If a template change adds a bullet, or renames a class, or splits a section, the
editor keeps working and starts writing to the wrong field. Nothing errors. The
resume just quietly becomes wrong, and the first evidence is a person reading
their own resume on an employer's site.

So the correspondence is asserted here, where a template change fails loudly
instead.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resume_build = load("resume_build")
TEMPLATE = ROOT / "templates" / "resume.html"
CONTENT = ROOT / "templates" / "resume-content.json"

# The selectors the editor uses, which are the ones extract() matches on. Kept
# as literal patterns rather than imported, because the point is to notice when
# the two drift apart rather than to make them share a definition and drift
# together.
SELECTORS = {
    "banner": r'<div class="banner"',
    "summary": r'<div class="body summary"',
    "aoe": r'<div class="aoe"',
    "tech": r'<div class="tech"',
    "lede": r'<div class="body lede"',
    "list": r"<ul>",
}


class RenderedDocumentMatchesContentTests(unittest.TestCase):
    def setUp(self):
        if not TEMPLATE.exists() or not CONTENT.exists():
            self.skipTest("no resume template on this machine")
        self.content = json.loads(CONTENT.read_text(encoding="utf-8"))
        self.html = resume_build.render(
            TEMPLATE.read_text(encoding="utf-8"), self.content)

    def count(self, key):
        return len(re.findall(SELECTORS[key], self.html))

    def test_every_slot_is_filled(self):
        """A leftover {{token}} means the editor would offer a placeholder as
        text and save it as the user's own words."""
        leftover = re.findall(r"\{\{[a-z0-9]+\}\}", self.html)
        self.assertEqual([t for t in leftover if t not in ("{{name}}", "{{contact}}")], [])

    def test_the_singular_regions_appear_exactly_once(self):
        self.assertEqual(self.count("banner"), 1)
        self.assertEqual(self.count("summary"), 1)

    def test_areas_of_expertise_correspond_one_to_one(self):
        self.assertEqual(self.count("aoe"), len(self.content["areasOfExpertise"]))

    def test_technical_rows_correspond_one_to_one(self):
        self.assertEqual(self.count("tech"), len(self.content["technical"]))

    def test_roles_correspond_one_to_one(self):
        """Ledes and bullet lists are paired by position, which is what the
        document itself does and what extract() relies on."""
        roles = self.content["roles"]
        self.assertEqual(self.count("list"), len(roles))
        self.assertLessEqual(self.count("lede"), len(roles))

    def test_each_role_keeps_its_own_bullet_count(self):
        lists = re.findall(r"<ul>(.*?)</ul>", self.html, re.S)
        self.assertEqual(len(lists), len(self.content["roles"]))
        for index, (block, role) in enumerate(zip(lists, self.content["roles"])):
            with self.subTest(role=index):
                self.assertEqual(len(re.findall(r"<li>", block)), len(role["bullets"]))

    def test_an_area_carries_its_bullet_glyph_outside_the_editable_text(self):
        """The glyph is the template's, not the user's words.

        Measured in the browser on 2026-08-27: reading the whole `.aoe` node
        put the bullet and its two non-breaking spaces into the content, so a
        save with no edits at all drew a second bullet on the next render, and
        a third on the save after that. The editor strips `.b` before reading,
        and this asserts the structure that makes that necessary and possible.
        """
        area = re.search(r'<div class="aoe"[^>]*>(.*?)</div>', self.html, re.S)
        self.assertIsNotNone(area)
        self.assertIn('class="b"', area.group(1))
        self.assertIn("&bull;", area.group(1))

    def test_the_round_trip_through_extract_is_still_exact(self):
        """The property the whole editor rests on: what render writes, extract
        reads back, unchanged."""
        template, content = resume_build.extract(self.html)
        self.assertEqual(resume_build.render(template, content), self.html)
