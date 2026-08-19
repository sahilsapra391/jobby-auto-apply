"""Tests for topic matching in scripts/store.py.

Some questions are recognisable by a phrase rather than by overall wording. A
form buries "non-compete" in a paragraph of legalese, and overlap scoring cannot
see past the surrounding words: the real Vanguard political-contributions
question scored under the 0.75 bar against the stored one and resolved to
nothing, which would have parked an application over a question the user had
already answered.

The risk runs the other way too. These markers resolve a question to a stored
answer without the user present, so a marker that is too loose fills the wrong
field. Everything here is about keeping that boundary tight.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("store", ROOT / "scripts" / "store.py")
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


ANSWERS = {
    "post-emp": {"question": "post-employment obligations?", "value": "No",
                 "tags": ["legal-declaration", "post-employment-obligations"]},
    "political": {"question": "political contributions?", "value": "No",
                  "tags": ["legal-declaration", "political-contributions"]},
    "conflicts": {"question": "related to a client officer?", "value": "No",
                  "tags": ["legal-declaration", "relationships-conflicts"]},
    "comp": {"question": "expected compensation", "value": "$120,000",
             "state": "sensitive", "tags": ["compensation"]},
    "notice": {"question": "notice period", "value": "15 days", "tags": ["logistics"]},
}


class TopicMatchTests(unittest.TestCase):
    def find(self, question):
        return store.find_answer(ANSWERS, question)

    def test_the_real_vanguard_political_question_resolves(self):
        q = ("Securities industry regulations require investment advisory firms to collect "
             "data on political contributions made within the last 2 years. Have you made a "
             "political contribution(s) to any political official, candidate, party, or "
             "organization (e.g. Political Action Committee) within the last 2 years?")
        self.assertEqual(self.find(q)["value"], "No")

    def test_a_non_compete_buried_in_legalese_resolves(self):
        q = ("Do you have any post-employment obligations to any current or former employer "
             "(i.e., non-compete, non-solicitation, etc.) that may prohibit or restrict your "
             "employment for a position?")
        self.assertEqual(self.find(q)["value"], "No")

    def test_a_reworded_restrictive_covenant_question_resolves(self):
        self.assertEqual(self.find("Are you bound by any restrictive covenant?")["value"], "No")

    def test_a_conflict_of_interest_question_resolves(self):
        self.assertEqual(
            self.find("Please disclose any conflicts of interest with our clients.")["value"], "No")

    def test_each_marker_reaches_its_own_answer_not_a_neighbour(self):
        self.assertEqual(self.find("any non-solicitation agreement?")["topic"],
                         "post-employment-obligations")
        self.assertEqual(self.find("political action committee donations?")["topic"],
                         "political-contributions")
        self.assertEqual(self.find("are you a close associate of a director?")["topic"],
                         "relationships-conflicts")


class OverMatchTests(unittest.TestCase):
    """A marker that is too loose fills the wrong field, which is worse than
    parking. These are the questions that must NOT resolve to a standing No."""

    def find(self, question):
        return store.find_answer(ANSWERS, question)

    def test_compensation_is_untouched(self):
        self.assertNotEqual(self.find("What is your expected compensation?").get("value"), "No")

    def test_an_ordinary_employment_question_does_not_match(self):
        out = self.find("Why do you want to leave your current employment?")
        self.assertNotEqual(out.get("value"), "No")

    def test_criminal_history_is_not_swept_in(self):
        # Never given a standing answer, and must keep going to the user.
        out = self.find("Have you ever been convicted of a felony?")
        self.assertNotEqual(out.get("value"), "No")

    def test_a_question_about_political_science_does_not_match(self):
        out = self.find("Do you have a degree in political science?")
        self.assertNotEqual(out.get("value"), "No")

    def test_a_topic_with_no_stored_answer_returns_nothing(self):
        self.assertIsNone(store.topic_answer({}, "Do you have a non-compete?"))


if __name__ == "__main__":
    unittest.main()
