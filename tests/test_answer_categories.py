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
        self.assertIsNone(ac.category_of(q("Story: SpecHawk architecture")))

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


class LiveCorpusTests(unittest.TestCase):
    """Against the real store when one exists. Skipped in CI, which has none."""

    def setUp(self):
        root = pathlib.Path("~/.job-application").expanduser()
        if not (root / "answers.json").exists():
            self.skipTest("no answer store on this machine")
        self.answers = json.loads((root / "answers.json").read_text())

    def test_every_employer_question_lands_in_a_category(self):
        questions = [v for v in self.answers.values() if ac.kind_of(v) == ac.KIND_QUESTION]
        uncategorised = [v.get("question") for v in questions
                         if ac.category_of(v) == ac.UNCATEGORISED]
        self.assertEqual(uncategorised, [],
                         "an employer question fits no category; name it or add one")

    def test_the_three_kinds_are_all_present_and_none_dominates(self):
        kinds = [ac.kind_of(v) for v in self.answers.values()]
        for kind in (ac.KIND_QUESTION, ac.KIND_POLICY, ac.KIND_STORY):
            self.assertGreater(kinds.count(kind), 0, kind)
        self.assertGreater(kinds.count(ac.KIND_QUESTION), kinds.count(ac.KIND_POLICY))

    def test_no_group_ever_spans_two_different_answers(self):
        """The property that makes grouping safe, checked over the real corpus
        rather than over the four fixtures above."""
        seen = {}
        for entry in self.answers.values():
            key = ac.group_key(entry)
            if not key:
                continue
            value = str(entry.get("value") or entry.get("answer") or "").strip().lower()
            seen.setdefault(key, set()).add(value)
        for key, values in seen.items():
            self.assertEqual(len(values), 1, f"{key} would collapse {values} into one row")


if __name__ == "__main__":
    unittest.main()
