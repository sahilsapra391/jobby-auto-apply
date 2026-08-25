"""The onboarding taxonomy, and the two properties worth defending.

Most of these are about the taxonomy refusing to drift: from what employers
actually ask, and from holding no answers.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import questionnaire  # noqa: E402


def store_with(questions, tmp):
    payload = {}
    for index, entry in enumerate(questions):
        if isinstance(entry, str):
            entry = {"question": entry}
        payload[f"k{index}"] = {"question": entry["question"],
                                "tags": entry.get("tags", []),
                                "state": "confirmed"}
    (Path(tmp) / "answers.json").write_text(json.dumps(payload))
    return tmp


class TaxonomyShapeTests(unittest.TestCase):
    def setUp(self):
        self.payload = questionnaire.load()

    def test_the_shipped_taxonomy_is_well_formed(self):
        self.assertGreater(len(self.payload["topics"]), 20)

    def test_every_topic_says_why_it_is_there(self):
        # A topic without a reason is one nobody can argue with later.
        for topic in self.payload["topics"]:
            self.assertTrue(topic.get("why", "").strip(),
                            f"{topic['id']} has no rationale")

    def test_the_blocking_tier_stays_small_enough_to_finish(self):
        blocking = [t for t in self.payload["topics"] if t["tier"] == 1]
        self.assertLessEqual(len(blocking), 15,
                             "tier 1 is the twenty-minute budget; keep it short")

    def test_the_fields_no_application_completes_without_are_tier_one(self):
        blocking = {t["id"] for t in self.payload["topics"] if t["tier"] == 1}
        for required in ("work-authorisation", "visa-sponsorship", "phone",
                         "street-address", "notice-period"):
            self.assertIn(required, blocking)

    def test_country_varying_answers_are_marked_per_identity(self):
        # One profile row per user cannot hold a US and an India answer, and
        # picking the wrong one is a defect this project already fixed once.
        by_id = {t["id"]: t for t in self.payload["topics"]}
        for topic_id in ("street-address", "phone", "work-authorisation",
                         "compensation-expectation"):
            self.assertTrue(by_id[topic_id].get("perIdentity"),
                            f"{topic_id} differs by country and is not marked")

    def test_demographic_and_pay_topics_are_marked_sensitive(self):
        by_id = {t["id"]: t for t in self.payload["topics"]}
        for topic_id in ("self-identification", "compensation-expectation",
                         "current-compensation"):
            self.assertTrue(by_id[topic_id].get("sensitive"),
                            f"{topic_id} must be stored under per-field consent")

    def test_a_malformed_taxonomy_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "t.json"
            bad.write_text(json.dumps({"topics": [{"id": "x"}]}))
            with self.assertRaises(questionnaire.TaxonomyError):
                questionnaire.load(bad)
            bad.write_text("{not json")
            with self.assertRaises(questionnaire.TaxonomyError):
                questionnaire.load(bad)


class NoAnswersTests(unittest.TestCase):
    def test_the_shipped_taxonomy_carries_no_answers(self):
        # The property that lets this file ship in the repository and survive
        # the open-source publish.
        questionnaire.assert_no_answers(questionnaire.load())

    def test_an_answer_shaped_key_anywhere_is_refused(self):
        for intruder in ({"topics": [{"id": "a", "value": "yes"}]},
                         {"topics": [{"id": "a", "nested": {"answer": "no"}}]},
                         {"topics": [{"id": "a", "opts": [{"response": "x"}]}]}):
            with self.assertRaises(questionnaire.TaxonomyError):
                questionnaire.assert_no_answers(intruder)


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.topics = questionnaire.load()["topics"]

    def test_the_six_sponsorship_phrasings_collapse_to_one_topic(self):
        for phrasing in (
            "Do you require visa sponsorship?",
            "Will you now or in the future require visa sponsorship to work in the United States?",
            "Will you require sponsorship in order to be employed?",
            "Do you now or will you in the future require sponsorship for employment visa status?",
            "Will you need the company to sponsor an employment visa now or in the future?",
        ):
            topic = questionnaire.match_topic(phrasing, self.topics)
            self.assertIsNotNone(topic, phrasing)
            self.assertEqual(topic["id"], "visa-sponsorship", phrasing)

    def test_ten_demographic_phrasings_collapse_to_one_consented_choice(self):
        for phrasing in ("What is your gender?", "What is your ethnicity?",
                         "Are you a veteran?", "Do you have a disability?",
                         "How do you identify your sexual orientation?",
                         "Do you identify as transgender?", "Date of Birth",
                         "What is your age range?"):
            topic = questionnaire.match_topic(phrasing, self.topics)
            self.assertEqual(topic["id"], "self-identification", phrasing)

    def test_a_more_specific_phrase_beats_a_general_one(self):
        # "authorized ... work" and "sponsorship" both appear here; the
        # question is about sponsorship and should be labelled that way.
        topic = questionnaire.match_topic(
            "Are you legally authorized to work in this country without sponsorship?",
            self.topics)
        self.assertIn(topic["id"], {"work-authorisation", "visa-sponsorship"})

    def test_an_unrelated_question_matches_nothing(self):
        self.assertIsNone(questionnaire.match_topic(
            "What is your favourite colour?", self.topics))


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.payload = questionnaire.load()

    def test_an_unknown_question_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_with(["Do you require visa sponsorship?",
                        "What is your favourite colour?"], tmp)
            questions = questionnaire.observed_questions(tmp)
            _covered, orphans = questionnaire.coverage(questions, self.payload)
            self.assertEqual(orphans, ["What is your favourite colour?"])

    def test_policies_and_stories_are_not_counted_as_employer_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_with([
                {"question": "POLICY: how are visa questions answered?"},
                {"question": "a standing rule", "tags": ["policy"]},
                {"question": "Story: something I built"},
                {"question": "Do you require visa sponsorship?"},
            ], tmp)
            self.assertEqual(len(questionnaire.observed_questions(tmp)), 1)

    def test_exclusions_are_stated_with_a_reason(self):
        for rule in (self.payload.get("excluded") or {}).get("rules") or []:
            self.assertTrue(rule.get("why", "").strip())
            self.assertTrue(rule.get("match"))

    def test_an_excluded_entry_is_neither_covered_nor_an_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_with(["When may a tailored resume or cover letter be rebuilt "
                        "after it is created?"], tmp)
            questions = questionnaire.observed_questions(tmp)
            covered, orphans = questionnaire.coverage(questions, self.payload)
            self.assertEqual(orphans, [])
            self.assertEqual(covered, {})

    def test_a_missing_store_is_not_an_exception(self):
        self.assertEqual(questionnaire.observed_questions("/nonexistent/xyz"), [])


class PlanTests(unittest.TestCase):
    def test_blocking_topics_come_before_everything_else(self):
        ordered = questionnaire.plan(questionnaire.load())
        tiers = [topic["tier"] for topic in ordered]
        self.assertEqual(tiers, sorted(tiers))

    def test_sensitive_topics_come_last_within_their_tier(self):
        # Someone should meet the ordinary questions before being asked to
        # consent to anything.
        ordered = questionnaire.plan(questionnaire.load())
        for tier in (1, 2, 3):
            flags = [bool(t.get("sensitive")) for t in ordered if t["tier"] == tier]
            self.assertEqual(flags, sorted(flags))


class LiveCorpusTests(unittest.TestCase):
    """Against the real store when one exists. Skipped in CI, which has none."""

    def test_the_taxonomy_still_covers_every_question_really_asked(self):
        root = Path(os.environ.get("JOB_APPLICATION_DIR",
                                   "~/.job-application")).expanduser()
        if not (root / "answers.json").exists():
            self.skipTest("no answer store on this machine")
        payload = questionnaire.load()
        questions = questionnaire.observed_questions(root)
        _covered, orphans = questionnaire.coverage(questions, payload)
        self.assertEqual(orphans, [],
                         "employers are asking something the taxonomy has no topic for")


if __name__ == "__main__":
    unittest.main()
