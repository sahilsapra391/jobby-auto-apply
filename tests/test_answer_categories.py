"""What a Memory Bank entry is, what it is about, and what may share its row.

The three properties the Profile page reads. Each is measured against the real
store where the real store is the point, and against fixtures where the
property has to hold for records that do not exist yet.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ac = load("answer_categories")


def q(question, **over):
    entry = {"question": question, "value": over.pop("value", "Yes")}
    entry.update(over)
    return entry


class KindTests(unittest.TestCase):
    """Three kinds of record share one file, and only one of them is a question."""

    def test_a_policy_note_is_not_an_employer_question(self):
        self.assertEqual(ac.kind_of(q("POLICY: how are US visa sponsorship questions answered?")),
                         ac.KIND_POLICY)

    def test_a_story_bank_entry_is_not_either(self):
        self.assertEqual(ac.kind_of(q("Story: founder-scale ownership and ambiguity")),
                         ac.KIND_STORY)
        self.assertEqual(ac.kind_of(q("What motivates you?", tags=["story-bank"])),
                         ac.KIND_STORY)

    def test_the_policy_tag_does_not_make_a_question_a_policy(self):
        """Measured: 36 entries carry the `policy` tag and only 23 are notes.

        The tag means "this question has a standing answer", which is a property
        of an employer question. Keying the kind on it moved 18 real questions
        out of the question list, where nobody looking for them would find them.
        """
        entry = q("Are you willing to relocate?", tags=["policy", "location"])
        self.assertEqual(ac.kind_of(entry), ac.KIND_QUESTION)

    def test_an_ordinary_question_is_a_question(self):
        self.assertEqual(ac.kind_of(q("What is your notice period?")), ac.KIND_QUESTION)


class CategoryTests(unittest.TestCase):
    def test_each_category_claims_the_question_it_is_for(self):
        cases = {
            "What is your expected compensation?": "compensation",
            "Do you require visa sponsorship?": "work-authorisation",
            "Are you a protected veteran?": "demographics",
            "Have you ever been convicted of a felony?": "background-check",
            "Do you have a non-compete in place?": "legal",
            "Are you willing to relocate?": "location",
            "What is your highest degree?": "education",
            "What is your notice period?": "availability",
            "What is your LinkedIn profile URL?": "contact",
            "How did you hear about us?": "how-you-heard",
            "Have you previously worked at this company?": "this-employer",
            "Do you consent to your interview being recorded?": "consent",
            "How many years of product management experience do you have?": "skills",
            # Both captured from the live Axon TPM I form on 2026-08-28.
            "Would you meet the requirements for a deemed export license to"
            " access EAR - controlled technology?": "work-authorisation",
            "Do you have any contractual obligations, agreements or"
            " commitments that would interfere with joining?": "legal",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(ac.category_of(q(question)), expected, question)

    def test_work_authorisation_beats_location_when_a_question_names_both(self):
        """Order is load-bearing: an authorisation question names a country."""
        self.assertEqual(
            ac.category_of(q("Are you legally authorized to work in the United States?")),
            "work-authorisation")

    def test_categories_are_word_bounded(self):
        """Three afternoons lost to substrings: india/Indiana, age/average,
        race/embrace. These are the questions that must not be misfiled."""
        for question in ("What is your average deal size?",
                         "How do you manage a team?",
                         "Do you embrace ambiguity?",
                         "Describe a package you shipped"):
            with self.subTest(question=question):
                self.assertNotEqual(ac.category_of(q(question)), "demographics", question)

    def test_a_policy_or_story_has_no_category(self):
        self.assertIsNone(ac.category_of(q("POLICY: acknowledging a posted salary range")))
        self.assertIsNone(ac.category_of(q("Story: Example Labs architecture")))

    def test_other_stays_reachable(self):
        """A category set that cannot miss has stopped classifying."""
        self.assertEqual(ac.category_of(q("Do you prefer cats or dogs?")), ac.UNCATEGORISED)


class GroupingTests(unittest.TestCase):
    """Group for display. Never merge records. Keep every key."""

    def test_the_relocation_wordings_share_one_row(self):
        keys = {ac.group_key(q(text)) for text in (
            "Are you able to relocate for this position?",
            "Are you open to relocation?",
            "Are you willing to relocate?",
            "Would you be willing to relocate for this role?")}
        self.assertEqual(len(keys), 1, keys)

    def test_sponsorship_and_authorisation_never_share_a_row(self):
        """The trap, and the reason this is grouping and not merging.

        Both answer a bare Yes and they mean opposite facts. Requiring
        sponsorship and being authorised to work are not the same statement, and
        a merged row would fill one of them with the opposite of the truth on a
        real form under a real name.
        """
        needs = ac.group_key(q("Do you require visa sponsorship?", value="Yes"))
        authorised = ac.group_key(q("Are you legally authorized to work in the United States?",
                                    value="Yes"))
        self.assertIsNotNone(needs)
        self.assertIsNotNone(authorised)
        self.assertNotEqual(needs, authorised)

    def test_the_same_topic_with_different_answers_stays_two_rows(self):
        self.assertNotEqual(ac.group_key(q("Are you willing to relocate?", value="Yes")),
                            ac.group_key(q("Are you open to relocation?", value="No")))

    def test_an_unanswered_question_is_never_grouped(self):
        self.assertIsNone(ac.group_key(q("Are you willing to relocate?", value=None)))

    def test_a_question_on_no_known_topic_stands_alone(self):
        self.assertIsNone(ac.group_key(q("Why do you want to work here?")))

    def test_policies_and_stories_are_never_grouped(self):
        self.assertIsNone(ac.group_key(q("POLICY: how should relocation be answered?")))


class CategoryCoverageTests(unittest.TestCase):
    """Against a frozen sample of questions employers really asked.

    This read the live answer store, so it failed the moment somebody else's
    application produced a question the categoriser had no category for, and it
    was red for every session that ran the suite. A test that fails on data
    rather than on code teaches everyone to ignore red, which is the failure
    this repo has already paid for.

    The live-corpus question moved to scripts/lint_answer_categories.py, which
    warns. The gaps found at capture time are recorded in the fixture, so a
    real gap is a task rather than something deleted along with the test that
    found it.
    """

    FIXTURE = json.loads(
        (pathlib.Path(__file__).resolve().parent / "fixtures"
         / "answer_questions.json").read_text())

    def test_the_captured_questions_still_categorise(self):
        uncategorised = [e["question"] for e in self.FIXTURE["categorised"]
                         if ac.category_of(e) == ac.UNCATEGORISED]
        self.assertEqual(uncategorised, [],
                         "the categoriser lost a question it used to handle")

    def test_the_fixture_carries_no_answer_values(self):
        """It is committed, and an answer can be a salary figure or a
        demographic response. Questions and tags are what the categoriser
        reads; the value is not."""
        everything = (self.FIXTURE["categorised"]
                      + self.FIXTURE["knownGaps"]["englishQuestionsWithNoCategory"]
                      + self.FIXTURE["knownGaps"]["nonLatinScript"])
        for entry in everything:
            self.assertNotIn("value", entry)

    def test_the_recorded_gaps_are_still_gaps(self):
        """When one gets covered, take it out of the fixture. A list of stale
        claims is the failure the withdrawn-claims list exists to stop."""
        gaps = (self.FIXTURE["knownGaps"]["englishQuestionsWithNoCategory"]
                + self.FIXTURE["knownGaps"]["nonLatinScript"])
        covered = [e["question"] for e in gaps
                   if ac.category_of(e) != ac.UNCATEGORISED]
        self.assertEqual(covered, [],
                         "this gap is covered now: take it out of the fixture")

    def test_the_english_gaps_are_named_and_not_blamed_on_script(self):
        """Nine of the fourteen are ordinary English questions. Filing all of
        them under "non-Latin script" would hide the larger, easier problem
        behind the smaller, harder one."""
        english = [e["question"] for e in
                   self.FIXTURE["knownGaps"]["englishQuestionsWithNoCategory"]]
        self.assertGreater(len(english), 0)
        self.assertIn("Are you crypto-native?", english)
