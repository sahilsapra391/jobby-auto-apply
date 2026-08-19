"""Tests for scripts/ats_match.py, the keyword weaving pass.

The job is to say the posting's words for things the candidate has genuinely
done, and nothing else. So the two lists have to be right: `rewritable` licenses
a rewrite, `notClaimable` is the residue that stays missing. Getting the second
one wrong is how a resume starts claiming Kubernetes.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
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


if __name__ == "__main__":
    unittest.main()
