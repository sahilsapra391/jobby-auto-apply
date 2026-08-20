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



class ConsentTopicTests(unittest.TestCase):
    """Interview recording, and the line under background checks.

    Being willing to undergo a background check is a statement of intent and the
    user's to give. Authorising one, signing the consent that lets a third party
    pull his records, is a different act and still goes to him. The marker keys
    on the willingness, never on the words "background check", or it would tick
    an FCRA consent form.
    """

    ANSWERS = {
        "willing": {"question": "willing to submit a background check?", "value": "Yes",
                    "tags": ["legal-declaration", "background-check-willingness"]},
        "recording": {"question": "consent to interview recording?", "value": "Yes",
                      "tags": ["consent", "interview-recording"]},
    }

    def find(self, question):
        return store.find_answer(self.ANSWERS, question)

    def test_a_named_vendor_resolves(self):
        q = ("At Remote we use brighthire.ai to record and auto-transcript our interviews. "
             "Do you consent to us using this tool during the interviewing process?")
        self.assertEqual(self.find(q)["value"], "Yes")

    def test_an_unnamed_notetaker_resolves(self):
        self.assertEqual(
            self.find("We use an AI notetaker in interviews. Do you consent?")["value"], "Yes")

    def test_willingness_to_be_checked_resolves(self):
        self.assertEqual(
            self.find("Would you be willing to undergo a background check?")["value"], "Yes")

    def test_authorising_a_consumer_report_does_NOT_resolve(self):
        # The one that must keep going to the user. Signing this is what lets a
        # third party pull his records; agreeing to be checked later is not.
        out = self.find("I authorize the Company to obtain a consumer report about me "
                        "for employment purposes.")
        self.assertNotEqual(out.get("value"), "Yes")

    def test_an_fcra_disclosure_does_NOT_resolve(self):
        out = self.find("Please acknowledge the FCRA disclosure and sign to permit a "
                        "background check to be conducted.")
        self.assertNotEqual(out.get("value"), "Yes")


class SourceQuestionTests(unittest.TestCase):
    """"How did you hear about us" carries the company name, so no two forms
    word it the same and overlap scoring never matches it."""

    ANSWERS = {
        "source": {"question": "how did you hear about us?", "value": "Company website",
                   "tags": ["sourcing", "source-how-heard"]},
        "referral": {"question": "who referred you?", "value": "",
                     "tags": ["sourcing"]},
    }

    def find(self, q):
        return store.find_answer(self.ANSWERS, q)

    def test_the_company_name_in_the_question_does_not_break_it(self):
        for q in ("How did you hear about Bevi? (required)",
                  "How did you find out about this opportunity at Datadog?",
                  "Where did you hear about this role?"):
            self.assertEqual(self.find(q)["value"], "Company website", q)

    def test_an_unrelated_question_is_not_swept_in(self):
        self.assertNotEqual(self.find("How many years of experience do you have?")
                            .get("value"), "Company website")


class SignatureAndCertificationTests(unittest.TestCase):
    """Typing a name is signing, and a certification covers the whole page.

    These resolve to an answer, but the value carries a condition rather than a
    bare yes: the agent still has to check that nothing on the form was inferred,
    and read what the block above it actually grants.
    """

    ANSWERS = {
        "sig": {"question": "type your name", "value": "Sahil Sapra",
                "tags": ["signature", "signature-name"]},
        "cert": {"question": "certify true and correct", "value": "Agree, match the field",
                 "tags": ["attestation", "accuracy-attestation"]},
        "range": {"question": "agree this range aligns", "value": "Yes",
                  "tags": ["compensation", "salary-range-acknowledgement"]},
        "excl": {"question": "excluded or debarred", "value": "No",
                 "tags": ["legal-declaration", "exclusion-debarment"]},
        "comp": {"question": "expected salary for US roles", "value": "$120,000",
                 "state": "sensitive", "tags": ["compensation"]},
    }

    def find(self, q):
        return store.find_answer(self.ANSWERS, q)

    def test_a_certification_signature_block_resolves_to_the_name(self):
        q = ("I HAVE READ THE CERTIFICATION ABOVE AND UNDERSTAND AND AGREE TO ITS TERMS. "
             "Please type your name and the date.")
        self.assertEqual(self.find(q)["value"], "Sahil Sapra")

    def test_an_accuracy_certification_resolves(self):
        q = ("By submitting my application, I certify that the information provided "
             "(including in my resume/CV) is true and correct.")
        self.assertIn("Agree", self.find(q)["value"])

    def test_acknowledging_a_posted_range_resolves_to_yes(self):
        q = ("Our base salary range for this role is $70,000 - $90,000. Do you acknowledge "
             "and agree that this range aligns with your compensation expectations?")
        self.assertEqual(self.find(q)["value"], "Yes")

    def test_asking_for_a_salary_FIGURE_never_returns_yes(self):
        # One word apart from the acknowledgement, and "Yes" in a salary field is
        # the worst mis-fill this store can produce.
        for q in ("What are your compensation expectations?",
                  "Desired salary", "What is your expected CTC?"):
            self.assertNotEqual(self.find(q).get("value"), "Yes", q)

    def test_a_sensitive_figure_is_never_reached_by_fuzzy_or_topic_match(self):
        out = self.find("What compensation are you looking for?")
        self.assertNotEqual(out.get("value"), "$120,000")

    def test_exclusion_and_debarment_resolves_to_no(self):
        q = ("Are you currently, or have you ever been, excluded from, debarred or otherwise "
             "ineligible to participate in state or federally funded programs or federal "
             "healthcare programs as defined in 42 USC 1320a-7b(f)?")
        self.assertEqual(self.find(q)["value"], "No")

    def test_criminal_history_is_not_swept_into_the_exclusion_answer(self):
        out = self.find("Have you ever been convicted of a crime?")
        self.assertNotEqual(out.get("value"), "No")


if __name__ == "__main__":
    unittest.main()
