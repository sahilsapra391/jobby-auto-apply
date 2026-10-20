"""Tests for scripts/ats_match.py, the keyword weaving pass.

The job is to say the posting's words for things the candidate has genuinely
done, and nothing else. So the two lists have to be right: `rewritable` licenses
a rewrite, `notClaimable` is the residue that stays missing. Getting the second
one wrong is how a resume starts claiming Kubernetes.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ats_match", ROOT / "scripts" / "ats_match.py")
ats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ats)


class EvidenceTests(unittest.TestCase):
    def base(self, text):
        return ats.tokens(text)

    def test_a_direct_hit_is_evidence(self):
        self.assertEqual(ats.evidence_for("roadmap", self.base("owned the roadmap")), "roadmap")

    def test_a_known_synonym_is_evidence(self):
        # The posting says "experimentation"; the resume says LaunchDarkly.
        # Same work, and the posting's word is the one an ATS scores.
        found = ats.evidence_for("experimentation", self.base("rolled out LaunchDarkly flags"))
        self.assertIsNotNone(found)

    def test_a_term_with_nothing_behind_it_is_not_evidence(self):
        self.assertIsNone(ats.evidence_for("kubernetes", self.base("product manager, roadmap, sql")))

    def test_a_bigram_whose_halves_are_both_evidenced_counts(self):
        found = ats.evidence_for("data pipeline", self.base("built a data warehouse and a pipeline"))
        self.assertIsNotNone(found)


class SuggestTests(unittest.TestCase):
    JD = ("We need a product manager for experimentation and observability on a "
          "developer platform. Kubernetes and Terraform required. You will own the "
          "roadmap and run discovery with customers.")
    BASE = ("Product manager. Rolled out LaunchDarkly feature flags. Built monitoring "
            "and logging dashboards. Owned the roadmap. Ran user research interviews. "
            "Internal tooling for developers.")

    def suggest(self, resume=""):
        return ats.suggest(self.JD, resume, self.BASE, "", 40)

    def test_synonyms_land_in_rewritable(self):
        terms = {r["term"] for r in self.suggest()["rewritable"]}
        self.assertIn("experimentation", terms)
        self.assertIn("observability", terms)

    def test_tools_he_has_never_used_stay_unclaimable(self):
        out = self.suggest()
        # The posting's own spelling is preserved, capitals and all, because
        # these get woven into a resume verbatim.
        blocked = {t.lower() for t in out["notClaimable"]}
        self.assertIn("kubernetes", blocked)
        self.assertIn("terraform", blocked)
        self.assertNotIn("kubernetes", {r["term"].lower() for r in out["rewritable"]})

    def test_suggestions_are_spelled_the_way_the_posting_spells_them(self):
        # Matching is done on stems, so "management" collapses to "manag". These
        # are woven in verbatim, and "manag" in Areas of Expertise is worse than
        # nothing.
        out = ats.suggest("Stakeholder management and roadmap ownership.", "",
                          "Cross functional partner alignment. Owned the roadmap.", "", 20)
        offered = [r["term"] for r in out["rewritable"]] + out["notClaimable"]
        for term in offered:
            self.assertFalse(term.endswith("manag"), f"{term!r} is a stem, not a word")
            self.assertNotIn(".", term)

    def test_rewriting_raises_the_reachable_coverage(self):
        out = self.suggest()
        self.assertGreater(out["coverageIfRewritten"], out["coverageNow"])

    def test_a_term_already_in_the_resume_is_not_offered_again(self):
        out = self.suggest(resume="experimentation and observability on a developer platform")
        terms = {r["term"] for r in out["rewritable"]}
        self.assertNotIn("experimentation", terms)
        self.assertIn("experimentation", out["alreadyCovered"])

    def test_noise_words_never_reach_the_resume(self):
        out = ats.suggest("We have four gaps and a fit for the right person. Roadmap matters.",
                          "", self.BASE, "", 40)
        offered = {r["term"] for r in out["rewritable"]} | set(out["notClaimable"])
        for junk in ("four", "gaps", "fit"):
            self.assertNotIn(junk, offered)

    def test_the_ceiling_is_reported_rather_than_hidden(self):
        # A posting listing real requirements he lacks should not read as 100%.
        out = self.suggest()
        self.assertLess(out["coverageIfRewritten"], 100)
        self.assertTrue(out["notClaimable"])

class RenderSourceTests(unittest.TestCase):
    """The content gate reads the HTML the PDF renders from.

    It used to read PDFs only, so every wording fix had to go through Chrome
    before it could be checked: 42 of 141 re-renders across 84 runs existed for
    that reason alone.
    """

    def test_html_is_read_as_text_with_no_markup_or_entities(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / ".resume.html"
            path.write_text(
                "<style>.x{color:red}</style>"
                "<div class='org'>Northwind Logistics &amp; Co</div>"
                "<script>var a=1</script>"
                "<ul><li>Raised coverage from 62% to 81%&rsquo;s peak</li></ul>")
            text = ats.document_text(path)
            self.assertIn("Northwind Logistics & Co", text)
            self.assertIn("62%", text)
            self.assertNotIn("<div", text)
            self.assertNotIn("&amp;", text)
            # style and script bodies are not resume content
            self.assertNotIn("color:red", text)
            self.assertNotIn("var a=1", text)

    def test_a_pdf_still_goes_through_the_pdf_reader(self):
        # The final check runs against the file that is actually uploaded, so
        # the PDF path must keep working.
        calls = []
        original = ats.pdf_text
        ats.pdf_text = lambda p: calls.append(p) or "pdf text"
        try:
            out = ats.document_text(pathlib.Path("/nowhere/resume.pdf"))
        finally:
            ats.pdf_text = original
        self.assertEqual(out, "pdf text")
        self.assertEqual(len(calls), 1)


class StoryBankDefaultTests(unittest.TestCase):
    """The honesty gate has to see every sanctioned source, or it cries wolf.

    A claim may trace to the base resume, the answer store, or the story bank.
    The gate only read the story bank when handed --facts, and the runs were not
    handing it over: across nine applications where the posting was still on
    disk, 8 of 9 reported a non-empty unsourcedTerms list without it and none
    did with it. Those resumes were fine. The gate was wrong, and a gate that is
    usually wrong is one people learn to ship past.
    """

    def test_the_story_bank_is_found_without_being_named(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "story-facts.txt").write_text("He led the rollout to the CEO.")
            os.environ["JOB_APPLICATION_DIR"] = str(root)
            try:
                found = ats.default_facts()
            finally:
                os.environ.pop("JOB_APPLICATION_DIR", None)
            self.assertIsNotNone(found)
            self.assertIn("rollout", found.read_text())

    def test_a_store_with_no_story_bank_returns_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["JOB_APPLICATION_DIR"] = d
            try:
                self.assertIsNone(ats.default_facts())
            finally:
                os.environ.pop("JOB_APPLICATION_DIR", None)
