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




class SkillRatingTests(unittest.TestCase):
    """A 1-to-10 self-rating is answered per skill, never per topic.

    The same sentence with a different noun is a different question. Measured
    before this existed: a Python rating fuzzy-matched the stored Excel entry at
    0.67, which is the shape of an answer that would have been wrong rather than
    missing. An unrecognised skill returns nothing so the run asks.
    """

    def rating(self, question):
        found = store.skill_rating(question)
        return found["answer"] if found else None

    def test_the_users_wording_resolves(self):
        self.assertEqual(self.rating("Rate yourself on SQL? 1 to 10-Highest"), "9")
        self.assertEqual(self.rating("Rate yourself on Excel? 1 to 10-Highest"), "10")

    def test_other_wordings_of_the_same_question_resolve(self):
        self.assertEqual(self.rating("Rate your SQL skills from 1 to 10"), "9")
        self.assertEqual(
            self.rating("On a scale of 1-10, how would you rate your Excel proficiency?"),
            "10")

    def test_an_unknown_skill_gets_no_answer(self):
        self.assertIsNone(self.rating("Rate yourself on Python? 1 to 10"))
        self.assertIsNone(self.rating("Rate yourself on Kubernetes, 1 to 10"))

    def test_two_skills_in_one_question_get_no_answer(self):
        self.assertIsNone(self.rating("Rate your SQL and Excel skills 1 to 10"))

    def test_a_question_that_is_not_a_rating_gets_no_answer(self):
        self.assertIsNone(self.rating("How many years of SQL have you used?"))
        self.assertIsNone(self.rating("Do you know Excel?"))

class AnyCountryWorkAuthorisationTests(unittest.TestCase):
    """Sponsorship and work authorisation answer the same in every country.

    They used to answer only for the US, and a separate stored answer said
    sponsorship WAS required everywhere else. Two forms from the same standing
    instruction could therefore contradict each other, and the one that went
    out was whichever country the posting happened to be in.
    """

    def topic(self, question):
        found = store.topic_answer(self.answers, question)
        return (found or {}).get("topic")

    def setUp(self):
        self.answers = {
            "a": {"question": "sponsorship anywhere", "value": "No",
                  "tags": ["sponsorship-any-country"]},
            "b": {"question": "authorised anywhere", "value": "Yes",
                  "tags": ["work-authorisation-any-country"]},
        }

    def test_sponsorship_questions_match_in_any_country(self):
        for question in (
            "Do you require visa sponsorship to work in the United Kingdom?",
            "Will you now or in the future require sponsorship to work in Canada?",
            "Do you need sponsorship for employment in Singapore?",
            "Will you require the company to sponsor a visa?",
        ):
            with self.subTest(question=question):
                self.assertEqual(self.topic(question), "sponsorship-any-country")

    def test_authorisation_questions_match_in_any_country(self):
        for question in (
            "Are you legally authorized to work in Germany?",
            "Do you have the right to work in Australia?",
            "Are you eligible to work in Ireland?",
        ):
            with self.subTest(question=question):
                self.assertEqual(self.topic(question),
                                 "work-authorisation-any-country")

    def test_an_unrelated_question_matches_neither(self):
        for question in ("What is your notice period?",
                         "Have you worked with SQL?"):
            with self.subTest(question=question):
                self.assertIsNone(self.topic(question))





STANDING_ANSWERS = {
    "linkedin": {"question": "What is your LinkedIn profile URL?",
                 "value": "https://www.linkedin.com/in/example-person/",
                 "tags": ["links", "linkedin-profile-url"]},
    "post-emp": {"question": "post-employment obligations?", "value": "No",
                 "tags": ["legal-declaration", "post-employment-obligations"]},
    "prior": {"question": "worked here before?", "value": "No",
              "tags": ["prior-employment-with-this-employer"]},
    "office": {"question": "office attendance requirement", "value": "Yes",
               "tags": ["in-office-acknowledgement"]},
    "docs": {"question": "documentation of resume history", "value": "Yes",
             "tags": ["resume-history-documentation"]},
    "source": {"question": "how did you hear about us?", "value": "Company website",
               "tags": ["sourcing", "source-how-heard"]},
    "conflicts": {"question": "related to an employee?", "value": "No",
                  "tags": ["legal-declaration", "relationships-conflicts"]},
    "comp": {"question": "expected compensation", "value": "$120,000",
             "state": "sensitive", "tags": ["compensation"]},
}

# The questions that parked Justworks and GitLab on 2026-08-25, shared by the
# resolve tests and the over-match tests so both read the same fixture.


class StandingAnswerTopicTests(unittest.TestCase):
    """The four topics that parked two real applications on 2026-08-25.

    Justworks and GitLab were both filled to the last field and then parked:
    Justworks on a LinkedIn URL, a flexwork acknowledgement and a
    resume-documentation confirmation; GitLab on a post-employment restriction
    and on whether he had worked there before. Every one of those had an answer
    the user considers standing, and none of them resolved.

    Each question below is the wording the real form used, taken from the
    parked session's own pendingFields.
    """

    ANSWERS = STANDING_ANSWERS

    def find(self, question):
        return store.find_answer(self.ANSWERS, question)

    def test_the_real_gitlab_restriction_question_resolves(self):
        q = ("Are you subject to any employment agreements and/or post-employment "
             "restrictions with your current employer or a past employer?")
        self.assertEqual(self.find(q)["value"], "No")

    def test_the_real_gitlab_prior_employment_question_resolves(self):
        self.assertEqual(
            self.find("Have you previously worked at or consulted for GitLab?")["value"], "No")

    def test_the_real_justworks_flexwork_question_resolves(self):
        q = ("Justworks has a flexwork policy which requires employees to work in the "
             "office at least 2 days per week. Does this work for you?")
        self.assertEqual(self.find(q)["value"], "Yes")

    def test_the_real_justworks_documentation_question_resolves(self):
        q = ("Are you able to provide documentation of the employment and education "
             "history listed on your resume if requested?")
        self.assertEqual(self.find(q)["value"], "Yes")

    def test_a_bare_linkedin_label_resolves_to_the_url(self):
        for q in ("LinkedIn Profile URL", "LinkedIn URL", "Link to your LinkedIn page"):
            self.assertTrue(self.find(q)["value"].endswith("/example-person/"), q)

    def test_the_company_name_in_the_question_does_not_break_prior_employment(self):
        for q in ("Have you previously worked for Datadog?",
                  "Have you previously been employed by Stripe?",
                  "Are you a former employee of this organisation?"):
            self.assertEqual(self.find(q)["value"], "No", q)

    def test_any_number_of_office_days_still_resolves_to_yes(self):
        for q in ("This role requires 3 days per week in the office. Are you able to meet that?",
                  "Our return to office policy requires 4 days onsite per week. Does this work?"):
            self.assertEqual(self.find(q)["value"], "Yes", q)


class StandingAnswerOverMatchTests(unittest.TestCase):
    """The neighbours each new topic must not claim.

    Every one of these was a real possibility while writing the markers, and
    each is a question whose answer is either different or not the agent's to
    give.
    """

    ANSWERS = STANDING_ANSWERS

    def find(self, question):
        return store.find_answer(self.ANSWERS, question)

    def test_a_relatives_question_is_not_answered_as_his_own_history(self):
        # A relative working there is the conflicts topic, which is scanned
        # first, and it is a question about someone else either way.
        out = self.find("Do you have any relatives who are current or former "
                        "employees of GitLab?")
        self.assertNotEqual(out.get("topic"), "prior-employment-with-this-employer")

    def test_having_worked_at_a_kind_of_company_is_not_swept_in(self):
        out = self.find("Have you ever worked at a startup?")
        self.assertNotEqual(out.get("value"), "No")

    def test_the_work_setup_preference_is_not_the_office_acknowledgement(self):
        # He accepts whatever a posting requires; that is not the same as
        # saying which setup he wants, which has its own answer.
        out = self.find("Which work setup do you prefer: remote, hybrid or onsite?")
        self.assertNotEqual(out.get("value"), "Yes")

    def test_a_source_question_mentioning_linkedin_keeps_its_own_answer(self):
        out = self.find("How did you hear about this role? (LinkedIn, Indeed, referral)")
        self.assertEqual(out["value"], "Company website")

    def test_documentation_does_not_become_a_background_check_authorisation(self):
        out = self.find("I authorize the Company to obtain a consumer report about me "
                        "for employment purposes.")
        self.assertNotEqual(out.get("value"), "Yes")

    def test_compensation_is_still_untouched(self):
        self.assertNotEqual(self.find("What is your expected compensation?").get("value"), "Yes")


if __name__ == "__main__":
    unittest.main()
