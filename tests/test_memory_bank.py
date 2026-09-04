"""The Profile page's store: base resumes, and the Memory Bank's two doors.

The bank is the thing that makes Jobby worth more the longer it runs, and it
only works if two different writers converge on one record. The board edits an
answer directly. An application that parks on a question it could not answer
opens that same question, unanswered, from the other side. A question answered
through both doors must leave one entry behind and not two.

Everything else in here is a boundary. What must never enter the bank, what an
edit must never be able to make some *other* question resolve to, and what a
replayed click must never do twice.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import itertools
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load("store")
dashboard = load("dashboard")
watch = load("watch")


def ns(**kw):
    kw.setdefault("root", None)
    return SimpleNamespace(**kw)


class BankTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = str(pathlib.Path(self.tmp.name) / "store")
        store.cmd_init(ns(root=self.root))
        self._serial = itertools.count()

    def write(self, payload):
        path = pathlib.Path(self.tmp.name) / f"in-{next(self._serial)}.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def answers(self):
        return store.cmd_answer_list(ns(root=self.root))

    def edit(self, **payload):
        payload.setdefault("editId", f"edit-{next(self._serial)}")
        return store.cmd_answer_edit(ns(root=self.root, input=self.write(payload)))

    def open_question(self, question, application="app-1"):
        return store.cmd_answer_open(ns(root=self.root, input=self.write(
            {"question": question, "applicationId": application})))

    def find(self, question):
        return store.cmd_answer_find(ns(root=self.root, question=question))

    def put(self, question, value, state="confirmed", tags=None, remember=True):
        """The path a real run uses, so fixtures look like the real store."""
        payload = {"question": question, "value": value, "state": state}
        if tags:
            payload["tags"] = tags
        return store.cmd_answer_put(ns(root=self.root, input=self.write(payload),
                                       remember_sensitive=remember))


class EditVersioningTests(BankTest):
    """The board re-sends clicks it remembers. So does it re-send edits."""

    def test_an_edit_applies_and_moves_the_version(self):
        out = self.edit(question="What is your notice period?", value="15 days", version=0)
        self.assertTrue(out["applied"])
        self.assertEqual(out["version"], 1)
        self.assertEqual(self.find("What is your notice period?")["value"], "15 days")

    def test_replaying_the_same_edit_changes_nothing(self):
        first = self.edit(editId="edit-abc", question="Notice period?", value="15 days",
                          version=0)
        again = store.cmd_answer_edit(ns(root=self.root, input=self.write(
            {"editId": "edit-abc", "question": "Notice period?", "value": "15 days",
             "version": 0})))
        self.assertTrue(first["applied"])
        self.assertFalse(again["applied"])
        self.assertEqual(again["reason"], "already applied")
        # And crucially the version did not move twice, so a third replay is
        # still a no-op rather than becoming a conflict.
        self.assertEqual(again["version"], 1)

    def test_a_different_edit_at_a_stale_version_is_refused_not_applied(self):
        self.edit(question="Notice period?", value="15 days", version=0)
        stale = self.edit(question="Notice period?", value="30 days", version=0)
        self.assertFalse(stale["applied"])
        self.assertEqual(stale["reason"], "conflict")
        # Refused, not clobbered: the store cannot un-lose an answer, and the
        # board can always re-read and try again.
        self.assertEqual(self.find("Notice period?")["value"], "15 days")

    def test_edits_chain_when_each_carries_the_version_it_saw(self):
        self.edit(question="Notice period?", value="15 days", version=0)
        second = self.edit(question="Notice period?", value="30 days", version=1)
        self.assertTrue(second["applied"])
        self.assertEqual(self.find("Notice period?")["value"], "30 days")

    def test_a_delete_needs_the_version_it_saw(self):
        self.edit(question="Notice period?", value="15 days", version=0)
        stale = store.cmd_answer_delete(
            ns(root=self.root, key="notice-period", version=0, edit_id="d1"))
        self.assertFalse(stale["deleted"])
        gone = store.cmd_answer_delete(
            ns(root=self.root, key="notice-period", version=1, edit_id="d2"))
        self.assertTrue(gone["deleted"])
        self.assertNotIn("notice-period", self.answers())

    def test_an_edit_clears_the_legacy_answer_field(self):
        """Four real entries put their answer in `answer`, not `value`.

        An edit that wrote `value` and left `answer` behind would show the new
        text on the page and fill the old one on the form.
        """
        path = pathlib.Path(self.root) / "answers.json"
        path.write_text(json.dumps({"rate-yourself-on-sql-1-10-highest": {
            "key": "rate-yourself-on-sql-1-10-highest",
            "question": "Rate yourself on SQL 1-10, 10 highest",
            "answer": "9", "state": "confirmed"}}))
        self.edit(key="rate-yourself-on-sql-1-10-highest",
                  question="Rate yourself on SQL 1-10, 10 highest", value="8", version=0)
        stored = self.answers()["rate-yourself-on-sql-1-10-highest"]
        self.assertEqual(stored["value"], "8")
        self.assertNotIn("answer", stored)


class GrowOnlyLoopTests(BankTest):
    """A question that stopped one application must not stop the next one."""

    def test_an_opened_question_is_tracked_but_is_not_an_answer(self):
        opened = self.open_question("How many days a week can you be in the office?")
        self.assertTrue(opened["opened"])
        entry = self.answers()[opened["key"]]
        self.assertEqual(entry["state"], "missing")
        self.assertIn("unanswered", entry["tags"])
        self.assertEqual(entry["askedBy"], ["app-1"])

        # The whole point of the state: it is a question the bank knows about
        # and still cannot answer. If the lookup treated it as an answer it
        # would fill a field with nothing and never ask again.
        found = self.find("How many days a week can you be in the office?")
        self.assertEqual(found["match"], "none")
        self.assertTrue(found["open"])

    def test_answering_from_the_bank_closes_it_for_the_next_application(self):
        question = "How many days a week can you be in the office?"
        opened = self.open_question(question, "app-1")
        self.edit(key=opened["key"], question=question, value="Three", version=0)

        entry = self.answers()[opened["key"]]
        self.assertNotIn("unanswered", entry.get("tags") or [])
        self.assertTrue(entry.get("answeredAt"))

        # A second employer, wording it differently, resolves without parking.
        later = self.find("How many days per week can you be in the office?")
        self.assertNotEqual(later.get("match"), "none")
        self.assertEqual(later["value"], "Three")

    def test_two_applications_asking_the_same_thing_make_one_entry(self):
        question = "What interests you about working here?"
        first = self.open_question(question, "app-1")
        second = self.open_question(question, "app-2")
        self.assertTrue(first["opened"])
        self.assertFalse(second["opened"])
        self.assertTrue(second["joined"])
        self.assertEqual(len(self.answers()), 1)
        self.assertEqual(self.answers()[first["key"]]["askedBy"], ["app-1", "app-2"])

    def test_the_two_doors_converge_on_one_record(self):
        """The case the loop exists to get right.

        A question answered on the Profile page and a question answered by
        going back to the parked application are the same question. Two entries
        would mean the second application still parks on something the user has
        already answered.
        """
        question = "Describe a product you shipped end to end."
        self.open_question(question, "app-1")
        # Door one: the board.
        self.edit(question=question, value="I shipped the payments rebuild.", version=0)
        # Door two: the run that parked, answering it the way a run does.
        self.put(question, "I shipped the payments rebuild.")
        self.assertEqual(len(self.answers()), 1)

    def test_opening_a_question_already_answered_does_nothing(self):
        self.put("What is your notice period?", "15 days")
        out = self.open_question("What is your notice period?")
        self.assertFalse(out["opened"])
        self.assertEqual(out["reason"], "already answered")
        self.assertEqual(self.find("What is your notice period?")["value"], "15 days")


class RefusalBoundaryTests(BankTest):
    """Neither door may put an answer behind an act the product refuses."""

    REFUSALS = (
        "I authorize the Company to obtain a consumer report about me for employment purposes.",
        "Please acknowledge the FCRA disclosure and sign to permit a background check.",
        "Have you ever been convicted of a felony?",
        "Please solve the CAPTCHA below to continue.",
        "Create a password for your candidate account.",
    )

    def test_a_parked_refusal_never_becomes_a_bank_entry(self):
        for question in self.REFUSALS:
            with self.subTest(question=question[:40]):
                out = self.open_question(question)
                self.assertFalse(out["opened"], question)
                self.assertEqual(self.answers(), {})

    def test_an_edit_cannot_create_an_answer_to_one_either(self):
        for question in self.REFUSALS:
            with self.subTest(question=question[:40]):
                with self.assertRaises(store.StoreError):
                    self.edit(question=question, value="Yes", version=0)

    def test_willingness_is_not_authorisation_and_stays_storable(self):
        """The line §11 draws, and the one this guard most easily gets wrong.

        Agreeing to be checked later is a statement of intent and is a stored
        standing answer. Authorising the check is an act performed on the
        user's behalf. A guard that could not tell them apart would delete a
        policy the user has relied on for months.
        """
        out = self.edit(question="Would you be willing to undergo a background check?",
                        value="Yes", version=0)
        self.assertTrue(out["applied"])

    def test_an_edit_that_would_shadow_a_refusal_is_refused_by_name(self):
        """The subtler failure: not answering the refusal, but making some
        other entry answer it by overlap."""
        probe = ("Please acknowledge the FCRA disclosure and sign to permit a "
                 "background check to be conducted.")
        self.assertEqual(self.find(probe)["match"], "none")
        with self.assertRaises(store.StoreError) as caught:
            # The indirect route, and the one a category guard cannot see. This
            # question names no refusal: no FCRA, no authorisation, nothing to
            # match on. It just happens to share enough words with the probe to
            # clear the 0.75 overlap bar, so storing an answer to it would put
            # an answer behind the disclosure without ever mentioning it.
            self.edit(question="Please acknowledge the disclosure and sign to permit "
                               "a check to be conducted.",
                      value="Confirmed", version=0)
        self.assertIn("refuses", str(caught.exception))
        self.assertEqual(self.find(probe)["match"], "none")
        self.assertEqual(self.answers(), {})

    def test_every_refusal_probe_resolves_the_same_before_and_after_an_edit(self):
        self.put("What is your notice period?", "15 days")
        before = {p: store._resolved(self.answers(), p) for p in store.REFUSAL_PROBES}
        self.edit(question="What are you looking for in your next role?",
                  value="Ownership of a real product surface.", version=0)
        after = {p: store._resolved(self.answers(), p) for p in store.REFUSAL_PROBES}
        self.assertEqual(before, after)

    def test_a_grandfathered_entry_stays_editable(self):
        """The store holds a policy note titled "background check, willingness
        versus authorisation", whose whole subject is this distinction. A guard
        reading the words rather than the situation would make it uneditable."""
        title = "POLICY: background check, willingness versus authorisation"
        self.put(title, "Willing yes. Authorising no.", tags=["policy"])
        version = store.bank_version(self.answers()[store.answer_key(title)])
        out = self.edit(key=store.answer_key(title), question=title,
                        value="Willing yes. Authorising never.", version=version)
        self.assertTrue(out["applied"])


class LoopEntryBoundaryTests(BankTest):
    """The loop as an entry point, which is the door the boundary suite did not
    used to have.

    Everything the answer system refuses is refused at lookup. The grow-only
    loop opens a second door: it *writes* questions, unprompted, from whatever
    an employer put on a form. If a refusal could arrive through it, the bank
    would end up holding the question, the Profile page would invite an answer,
    and the refusal would have been routed around rather than enforced.
    """

    def opened(self, question):
        return self.open_question(question)["opened"]

    def test_a_consumer_report_authorisation_cannot_become_a_bank_question(self):
        self.assertFalse(self.opened(
            "I authorize the Company to obtain a consumer report about me for "
            "employment purposes."))
        self.assertEqual(self.answers(), {})

    def test_an_fcra_disclosure_cannot(self):
        self.assertFalse(self.opened(
            "Please acknowledge the FCRA disclosure and sign to permit a background "
            "check to be conducted."))
        self.assertEqual(self.answers(), {})

    def test_a_captcha_cannot(self):
        self.assertFalse(self.opened("Solve the reCAPTCHA to continue."))
        self.assertEqual(self.answers(), {})

    def test_a_degree_in_x_cannot(self):
        """Not a refused act, and that is the point of listing it separately.

        A person may answer this deliberately from the Profile page. What must
        not happen is Jobby planting it unprompted, because it sits one word
        from the political-contributions topic that already has a standing
        answer, and §10 keeps it as a tested boundary.
        """
        self.assertFalse(self.opened("Do you have a degree in political science?"))
        self.assertEqual(self.answers(), {})

    def test_criminal_history_cannot(self):
        self.assertFalse(self.opened("Have you ever been convicted of a felony?"))
        self.assertEqual(self.answers(), {})

    def test_a_one_time_code_cannot(self):
        self.assertFalse(self.opened("Enter the verification code we sent you."))
        self.assertEqual(self.answers(), {})

    def test_the_whole_refusal_set_leaves_the_bank_empty(self):
        """Swept together, because the failure that matters is any one of them
        getting through, not a particular one."""
        for question in (
            "I authorize the Company to obtain a consumer report about me.",
            "I acknowledge the FCRA disclosure and authorise a background check.",
            "Have you ever been convicted of a crime?",
            "Please complete the CAPTCHA below.",
            "Create a password for your candidate account.",
            "Do you have a degree in political science?",
            "Enter the one-time code from your authenticator app.",
        ):
            self.open_question(question, "app-1")
        self.assertEqual(self.answers(), {},
                         "a refusal reached the bank through the park loop")

    def test_an_ordinary_question_still_gets_through(self):
        """The guard has to be narrow or the loop stops being worth having."""
        self.assertTrue(self.opened("How many days a week can you be in the office?"))
        self.assertEqual(len(self.answers()), 1)


class OpenedSensitivityTests(BankTest):
    """A question's subject is known when it is recorded and nowhere else.

    Without this, a parked "What is your expected compensation?" opened as an
    ordinary entry, the Profile page offered it as an ordinary answer, and a
    salary was stored unflagged and rendered unmasked.
    """

    def test_a_compensation_question_is_born_sensitive(self):
        out = self.open_question("What is your expected compensation?")
        entry = self.answers()[out["key"]]
        self.assertEqual(entry["state"], "sensitive")
        self.assertTrue(entry["sensitive"])
        self.assertEqual(entry["dataClass"], "class-2")

    def test_a_demographic_question_is_born_sensitive(self):
        for question in ("Are you a protected veteran?",
                         "What is your gender?",
                         "What is your date of birth?",
                         "Do you require visa sponsorship?"):
            with self.subTest(question=question):
                out = self.open_question(question)
                self.assertEqual(self.answers()[out["key"]]["state"], "sensitive", question)

    def test_an_ordinary_question_is_not(self):
        for question in ("How many days a week can you be in the office?",
                         "What is your average deal size?",
                         "How do you manage a team?",
                         "Describe a package you shipped end to end.",
                         "What is your notice period?"):
            with self.subTest(question=question):
                out = self.open_question(question)
                self.assertEqual(self.answers()[out["key"]]["state"], "missing", question)

    def test_answering_a_born_sensitive_entry_keeps_it_sensitive(self):
        out = self.open_question("What is your expected compensation?")
        self.edit(key=out["key"], question="What is your expected compensation?",
                  value="$185,000", state="sensitive", version=0)
        entry = self.answers()[out["key"]]
        self.assertEqual(entry["state"], "sensitive")
        self.assertEqual(entry["dataClass"], "class-2")

    def test_the_classifier_reads_words_not_substrings(self):
        """Measured on 2026-08-26: a substring list flagged "average", "manage"
        and "embrace" as sensitive, because "age" and "race" live inside them.
        A classifier that masks half the bank is worse than no classifier."""
        for question in ("What is your average deal size?",
                         "How do you manage a team?",
                         "Do you embrace ambiguity?",
                         "Describe a package you shipped"):
            self.assertFalse(store.sensitive_question(question), question)


class ResolverGuardTests(BankTest):
    """An unanswered entry must not satisfy a lookup through *any* resolver.

    Guarded once in find_answer rather than inside each resolver. Guarding them
    one at a time is how this broke: the loop added the rule, topic_answer was
    taught it, and the next merge brought three more resolvers from another
    branch, each iterating the answers with no idea a rule existed.
    """

    def test_no_resolver_can_return_an_entry_with_no_answer(self):
        # Every tag the merged resolvers key on, all on valueless entries.
        answers = {}
        for key, tag, question in (
            ("ack", "acknowledgement-standing", "Do you acknowledge the terms?"),
            ("pay", "compensation", "What is your expected compensation?"),
            ("noncompete", "post-employment-obligations", "Any non-compete?"),
        ):
            answers[key] = {"key": key, "question": question, "value": None,
                            "state": "missing", "tags": [tag]}
        for probe in ("Do you acknowledge and agree to the terms above?",
                      "What compensation are you looking for?",
                      "Do you have a non-compete in place?",
                      "Please acknowledge the privacy notice."):
            with self.subTest(probe=probe):
                found = store.find_answer(answers, probe)
                self.assertEqual(found.get("match"), "none", probe)
                self.assertIsNone(found.get("value"), probe)


class OneTimeCodeTests(BankTest):
    """A code is worthless a minute later and dangerous for as long as it is
    written down. It is not bank material through any door."""

    CODES = (
        "Enter the one-time code we sent to your phone",
        "Verification code",
        "Please enter your 2FA passcode",
        "Enter the six-digit security code",
    )

    def test_a_code_question_is_never_opened_by_a_park(self):
        for question in self.CODES:
            with self.subTest(question=question):
                out = self.open_question(question)
                self.assertFalse(out["opened"])
                self.assertIn("one-time code", out["reason"])

    def test_a_code_question_is_never_saved_by_an_edit(self):
        for question in self.CODES:
            with self.subTest(question=question):
                with self.assertRaises(store.StoreError):
                    self.edit(question=question, value="482913", version=0)

    def test_a_bare_code_pasted_into_an_ordinary_box_is_refused(self):
        """The second door. The question looks innocent; the answer does not."""
        with self.assertRaises(store.StoreError) as caught:
            self.edit(question="Anything else we should know?", value="482913", version=0)
        self.assertIn("bare numeric code", str(caught.exception))

    def test_an_ordinary_number_is_not_mistaken_for_one(self):
        out = self.edit(question="How many years of product management do you have?",
                        value="6 years", version=0)
        self.assertTrue(out["applied"])


class SensitiveTests(BankTest):
    def test_a_sensitive_entry_is_marked_for_the_encryption_that_is_not_built(self):
        """Class 2 in the multi-tenant plan: per-user envelope encryption,
        unwrapped only when a device runner needs the field. None of that
        exists yet. Marking the records means the work has something to select
        on rather than re-deriving the set from tag spellings later."""
        self.edit(question="What compensation are you looking for?", value="$185,000",
                  state="sensitive", version=0)
        entry = self.answers()[store.answer_key("What compensation are you looking for?")]
        self.assertTrue(entry["sensitive"])
        self.assertEqual(entry["dataClass"], "class-2")

    def test_there_is_no_status_field_pretending_to_track_that_work(self):
        """`encryption` used to sit here holding the constant "pending", written
        by nobody and read by nothing. A status with one value and no writer is
        a promise, not a status, and it ages into a lie."""
        self.edit(question="What compensation are you looking for?", value="$185,000",
                  state="sensitive", version=0)
        entry = self.answers()[store.answer_key("What compensation are you looking for?")]
        self.assertNotIn("encryption", entry)

    def test_an_ordinary_entry_is_not(self):
        self.edit(question="What is your notice period?", value="15 days", version=0)
        entry = self.answers()["notice-period"]
        self.assertFalse(entry["sensitive"])
        self.assertEqual(entry["dataClass"], "class-1")

    def test_a_value_declined_at_fill_time_is_not_an_open_question(self):
        self.put("Expected CTC", "70 LPA", state="sensitive", remember=False)
        out = self.open_question("Expected CTC")
        self.assertFalse(out["opened"])
        self.assertIn("chose not to store", out["reason"])


class HostileTextTests(BankTest):
    """Questions arrive off employer forms and answers are free text. Neither
    is trusted, and neither is ever concatenated into markup."""

    NASTY = '<script>alert(1)</script> & "quotes" \'and\' <img src=x onerror=1>'

    def test_markup_in_a_question_survives_storage_as_text(self):
        out = self.edit(question=f"Why us? {self.NASTY}", value="Because.", version=0)
        self.assertTrue(out["applied"])
        stored = self.answers()[out["key"]]
        self.assertIn("<script>", stored["question"])

    def test_the_key_is_slug_shaped_whatever_the_question_contains(self):
        out = self.edit(question=f"Why us? {self.NASTY}", value="Because.", version=0)
        self.assertRegex(out["key"], r"^[a-z0-9-]+$")

    def test_an_oversized_answer_is_refused_rather_than_stored(self):
        with self.assertRaises(store.StoreError):
            self.edit(question="Tell us about yourself", value="x" * 5000, version=0)


class BaseResumeTests(BankTest):
    def profile(self, **over):
        base = {
            "firstName": "Sahil",
            "resumes": [
                {"key": "us", "path": "/r/us.pdf", "default": True, "appliesTo": ["US"],
                 "identity": {"phone": "5550001111", "location": "Charlotte, NC"}},
                {"key": "in", "path": "/r/in.pdf", "default": False, "appliesTo": ["IN"],
                 "identity": {"phone": "5550002222", "location": "Navi Mumbai, MH"}},
            ],
        }
        base.update(over)
        store.cmd_profile_replace(ns(root=self.root, input=self.write(base)))
        return base

    def session(self, aid, status, region):
        path = pathlib.Path(self.root) / "sessions" / f"{aid}.json"
        path.write_text(json.dumps({"applicationId": aid, "company": "Acme",
                                    "role": "PM", "status": status, "region": region}))

    def test_the_default_can_be_swapped(self):
        self.profile()
        out = store.cmd_resume_default(ns(root=self.root, key="in"))
        self.assertEqual(out["default"], "in")
        resumes = store.cmd_profile_get(ns(root=self.root))["resumes"]
        self.assertEqual([e["key"] for e in resumes if e["default"]], ["in"])

    def test_removing_the_default_is_refused(self):
        self.profile()
        with self.assertRaises(store.StoreError) as caught:
            store.cmd_resume_remove(ns(root=self.root, key="us"))
        self.assertIn("default", str(caught.exception))

    def test_removing_the_last_base_for_a_region_with_open_work_is_refused(self):
        self.profile()
        self.session("app-in", "review", "IN")
        with self.assertRaises(store.StoreError) as caught:
            store.cmd_resume_remove(ns(root=self.root, key="in"))
        self.assertIn("IN", str(caught.exception))

    def test_removing_it_is_allowed_once_that_work_is_finished(self):
        self.profile()
        self.session("app-in", "dropped", "IN")
        out = store.cmd_resume_remove(ns(root=self.root, key="in"))
        self.assertEqual(out["resumes"], ["us"])


class BaseResumeSelectionTests(BankTest):
    """A base is content plus a rule for when it applies, and the rule has to be
    explainable or a wrong resume on a posting is undiagnosable: the document is
    plausible, nothing errors, and the only evidence is a person noticing."""

    def profile(self, resumes):
        store.cmd_profile_replace(ns(root=self.root, input=self.write({
            "firstName": "Sahil", "resumes": resumes})))

    def base(self, key, countries, default=False, priority=0):
        return {"key": key, "path": f"/r/{key}.pdf", "default": default,
                "priority": priority,
                "selector": {"countries": countries},
                "identity": {"phone": "5550001111", "location": "Somewhere"}}

    def test_a_country_selects_the_base_that_claims_it(self):
        resumes = [self.base("us", ["US"], default=True), self.base("in", ["IN"])]
        chosen, why = store.select_resume(resumes, "IN")
        self.assertEqual(chosen["key"], "in")
        self.assertIn("IN is claimed by in", why)

    def test_an_unclaimed_country_falls_to_the_default_and_says_so(self):
        resumes = [self.base("us", ["US"], default=True), self.base("in", ["IN"])]
        chosen, why = store.select_resume(resumes, "GB")
        self.assertEqual(chosen["key"], "us")
        self.assertIn("no base claims GB", why)

    def test_priority_breaks_an_overlap_deterministically(self):
        resumes = [self.base("eu", ["GB", "DE"], priority=10),
                   self.base("gb", ["GB"], priority=20),
                   self.base("us", ["US"], default=True)]
        chosen, why = store.select_resume(resumes, "GB")
        self.assertEqual(chosen["key"], "gb")
        self.assertIn("outranks", why)

    def test_selection_does_not_depend_on_list_order(self):
        a = [self.base("eu", ["GB"], priority=10), self.base("gb", ["GB"], priority=20),
             self.base("us", ["US"], default=True)]
        b = list(reversed(a))
        self.assertEqual(store.select_resume(a, "GB")[0]["key"],
                         store.select_resume(b, "GB")[0]["key"])

    def test_a_profile_written_before_selectors_still_works(self):
        """`appliesTo` is what the store held before this existed."""
        resumes = [{"key": "us", "path": "/r/us.pdf", "default": True, "appliesTo": ["US"],
                    "identity": {"phone": "1", "location": "x"}},
                   {"key": "in", "path": "/r/in.pdf", "default": False, "appliesTo": ["IN"],
                    "identity": {"phone": "2", "location": "y"}}]
        self.assertEqual(store.select_resume(resumes, "IN")[0]["key"], "in")

    def test_overlaps_are_reported_rather_than_resolved_silently(self):
        resumes = [self.base("eu", ["GB", "DE"]), self.base("gb", ["GB"])]
        self.assertEqual(store.resume_overlaps(resumes), {"GB": ["eu", "gb"]})

    def test_adding_a_base_copies_an_existing_one(self):
        self.profile([self.base("us", ["US"], default=True)])
        out = store.cmd_resume_add(ns(root=self.root, input=self.write(
            {"key": "eu", "label": "Europe", "countries": ["gb", "de"], "copyFrom": "us"})))
        self.assertEqual(out["added"], "eu")
        self.assertEqual(out["countries"], ["DE", "GB"])
        resumes = store.cmd_profile_get(ns(root=self.root))["resumes"]
        eu = next(r for r in resumes if r["key"] == "eu")
        self.assertEqual(eu["identity"], resumes[0]["identity"])
        self.assertFalse(eu["default"])
        self.assertEqual(store.select_resume(resumes, "DE")[0]["key"], "eu")

    def test_an_uploaded_document_becomes_the_new_base_pdf(self):
        """Uploading changes what the base is built from, not what it says.

        The PDF is the source text the model reads and what the layout is
        measured against. The wording still comes from the copied base, because
        resume_build.extract matches the exact markup Jobby's template emits
        and cannot read an arbitrary document.
        """
        self.profile([self.base("us", ["US"], default=True)])
        uploaded = pathlib.Path(self.tmp.name) / "eu.pdf"
        uploaded.write_bytes(b"%PDF-1.7 pretend")
        out = store.cmd_resume_add(ns(root=self.root, input=self.write(
            {"key": "eu", "countries": ["GB"], "copyFrom": "us",
             "documentPath": str(uploaded)})))
        self.assertEqual(out["document"], "uploaded")
        resumes = store.cmd_profile_get(ns(root=self.root))["resumes"]
        eu = next(r for r in resumes if r["key"] == "eu")
        self.assertEqual(eu["path"], str(uploaded))
        # Contact details still come from the base it copied.
        self.assertEqual(eu["identity"], resumes[0]["identity"])

    def test_without_an_upload_the_base_keeps_using_the_copied_document(self):
        self.profile([self.base("us", ["US"], default=True)])
        out = store.cmd_resume_add(ns(root=self.root, input=self.write(
            {"key": "eu", "countries": ["GB"], "copyFrom": "us"})))
        self.assertEqual(out["document"], "copied")
        resumes = store.cmd_profile_get(ns(root=self.root))["resumes"]
        self.assertEqual(next(r for r in resumes if r["key"] == "eu")["path"],
                         resumes[0]["path"])

    def test_a_missing_or_non_pdf_document_is_refused(self):
        self.profile([self.base("us", ["US"], default=True)])
        with self.assertRaises(store.StoreError):
            store.cmd_resume_add(ns(root=self.root, input=self.write(
                {"key": "eu", "countries": ["GB"], "documentPath": "/nope/missing.pdf"})))
        notpdf = pathlib.Path(self.tmp.name) / "eu.txt"
        notpdf.write_text("not a pdf")
        with self.assertRaises(store.StoreError):
            store.cmd_resume_add(ns(root=self.root, input=self.write(
                {"key": "eu2", "countries": ["GB"], "documentPath": str(notpdf)})))

    def test_a_new_base_needs_a_country_and_a_clean_key(self):
        self.profile([self.base("us", ["US"], default=True)])
        with self.assertRaises(store.StoreError):
            store.cmd_resume_add(ns(root=self.root, input=self.write(
                {"key": "eu", "countries": []})))
        with self.assertRaises(store.StoreError):
            store.cmd_resume_add(ns(root=self.root, input=self.write(
                {"key": "../etc", "countries": ["GB"]})))

    def test_a_duplicate_key_is_refused(self):
        self.profile([self.base("us", ["US"], default=True)])
        with self.assertRaises(store.StoreError):
            store.cmd_resume_add(ns(root=self.root, input=self.write(
                {"key": "us", "countries": ["GB"]})))

    def test_updating_changes_what_a_base_claims(self):
        self.profile([self.base("us", ["US"], default=True), self.base("eu", ["GB"])])
        store.cmd_resume_update(ns(root=self.root, input=self.write(
            {"key": "eu", "countries": ["GB", "DE", "FR"], "priority": 5})))
        resumes = store.cmd_profile_get(ns(root=self.root))["resumes"]
        self.assertEqual(store.select_resume(resumes, "FR")[0]["key"], "eu")
        self.assertEqual(store.resume_priority(
            next(r for r in resumes if r["key"] == "eu")), 5)


class ResumeWordingTests(BankTest):
    BASE = {
        "banner": "PRODUCT MANAGER",
        "summary": "A summary.",
        "areasOfExpertise": ["One", "Two", "Three"],
        "technical": [{"label": "Languages", "items": "Python, SQL"}],
        "roles": [{"lede": "Led things.", "bullets": ["First", "Second"]}],
    }

    def seed(self):
        """Write the override directly, because a put validates against the
        shipped template and this fixture is deliberately smaller than it."""
        (pathlib.Path(self.root) / "resume-content.json").write_text(json.dumps(self.BASE))

    def test_the_shipped_template_is_the_fallback(self):
        content = store.cmd_resume_content_get(ns(root=self.root))
        # The real one, because there is no override on a fresh store.
        self.assertIn("areasOfExpertise", content)

    def test_rewording_is_accepted(self):
        self.seed()
        reworded = json.loads(json.dumps(self.BASE))
        reworded["roles"][0]["bullets"][0] = "First, said better"
        out = store.cmd_resume_content_put(ns(root=self.root, input=self.write(reworded)))
        self.assertTrue(out["saved"])
        self.assertEqual(
            store.cmd_resume_content_get(ns(root=self.root))["roles"][0]["bullets"][0],
            "First, said better")

    def test_a_list_that_grows_is_refused_by_name(self):
        """The template has a fixed number of slots and the layout gate measures
        the render against the base. A list that grows does not produce a worse
        resume, it produces one that does not render, and refusing here names
        the field instead of a slot count."""
        self.seed()
        longer = json.loads(json.dumps(self.BASE))
        longer["areasOfExpertise"].append("Four")
        with self.assertRaises(store.StoreError) as caught:
            store.cmd_resume_content_put(ns(root=self.root, input=self.write(longer)))
        self.assertIn("areasOfExpertise", str(caught.exception))

    def test_a_role_that_loses_a_bullet_is_refused_by_name(self):
        self.seed()
        shorter = json.loads(json.dumps(self.BASE))
        shorter["roles"][0]["bullets"].pop()
        with self.assertRaises(store.StoreError) as caught:
            store.cmd_resume_content_put(ns(root=self.root, input=self.write(shorter)))
        self.assertIn("role 1", str(caught.exception))


class PublishedShapeTests(BankTest):
    """What leaves the device, and what must not reach the page."""

    def state(self):
        return {
            "profile": {"firstName": "Sahil", "resumes": [
                {"key": "us", "path": "/home/someone/Resumes/Sahil Resume.pdf",
                 "default": True, "appliesTo": ["US"],
                 "identity": {"phone": "5550001111", "location": "Charlotte, NC",
                              "fullAddress": {"oneLine": "1 Main St, Charlotte NC"}}}]},
            "sessions": [], "history": [], "roles": {}, "meta": {}, "jobs": {},
            "searchConfig": None, "providers": {},
            "answers": {
                "notice-period": {"key": "notice-period", "question": "Notice period?",
                                  "value": "15 days", "state": "confirmed"},
                "expected-comp": {"key": "expected-comp",
                                  "question": "Expected compensation?",
                                  "value": "$185,000", "state": "sensitive"},
                "office-days": {"key": "office-days", "question": "Days in office?",
                                "value": None, "state": "missing",
                                "tags": ["unanswered"], "askedBy": ["app-1"]},
            },
            "resumeContent": {"banner": "PRODUCT MANAGER"},
        }

    def test_the_published_state_carries_the_bank_and_names_its_account(self):
        published = dashboard.publishable_state(self.state())
        self.assertEqual(published["userId"], dashboard.USER_ID)
        self.assertEqual(published["memoryBank"]["userId"], dashboard.USER_ID)
        self.assertEqual(len(published["memoryBank"]["answers"]), 3)

    def test_the_legacy_answer_field_is_normalised_before_anything_reads_it(self):
        """One normalisation, on the machine that owns the store.

        Doing it in the API route instead put "is this answered" in three
        places in two languages, and the browser's copy disagreed: measured
        against the real store, it counted four answered entries as open and
        badged the nav with a number nothing could clear.
        """
        state = self.state()
        state["answers"]["rate-sql"] = {"key": "rate-sql",
                                        "question": "Rate yourself on SQL",
                                        "answer": "9", "state": "confirmed"}
        published = dashboard.publishable_state(state)
        entry = next(a for a in published["memoryBank"]["answers"]
                     if a["key"] == "rate-sql")
        self.assertEqual(entry["value"], "9")
        self.assertNotIn("answer", entry)
        self.assertEqual(published["meta"]["memoryBank"]["unanswered"], 1)

    def test_every_answer_is_published_including_the_sensitive_ones(self):
        """Reverses the withholding shipped in #73, on the owner's instruction
        after seeing it live.

        He does not want a hidden category on his own board. It is his data
        behind his own password, and a single user reading their own
        compensation is a different question from a store holding other
        people's. **This reverses when a second account exists**, and issue #82
        is the work that reverses it.
        """
        published = dashboard.publishable_state(self.state())
        entry = next(a for a in published["memoryBank"]["answers"]
                     if a["key"] == "expected-comp")
        self.assertEqual(entry["value"], "$185,000")
        self.assertNotIn("heldOnDevice", entry)

    def test_the_classification_still_travels_untouched(self):
        """The display decision must not disturb the classification.

        `sensitive` and `dataClass` are what the multi-tenant envelope
        encryption selects on. If showing the value quietly cleared the flag,
        #82 would later encrypt the wrong set and nobody would notice until it
        was someone else's compensation.
        """
        entry = next(a for a in dashboard.publishable_state(self.state())["memoryBank"]["answers"]
                     if a["key"] == "expected-comp")
        self.assertTrue(entry["sensitive"])
        self.assertEqual(entry["dataClass"], "class-2")

    def test_a_sensitive_question_nobody_has_answered_is_still_open(self):
        state = self.state()
        state["answers"]["comp-open"] = {
            "key": "comp-open", "question": "What compensation do you want?",
            "value": None, "state": "sensitive", "tags": ["unanswered"]}
        published = dashboard.publishable_state(state)
        self.assertEqual(published["meta"]["memoryBank"]["unanswered"], 2)

    def test_a_dismissed_question_leaves_the_unanswered_count(self):
        """The number the nav badge draws. It could not reach zero before.

        Five Korean-language questions from one already-dropped application sat
        in Needs you with no way to clear them but to answer them, and the user
        was never going to. `tests/test_dismissed_questions.py` is the rest of
        this; here it is only the published number.
        """
        state = self.state()
        state["answers"]["gone"] = {
            "key": "gone", "question": "Do you require a Korea work visa?",
            "value": None, "state": "dismissed", "tags": []}
        counts = dashboard.publishable_state(state)["meta"]["memoryBank"]
        self.assertEqual(counts["unanswered"], 1)
        self.assertEqual(counts["dismissed"], 1)
        self.assertEqual(counts["total"], 4)

    def test_the_counts_ride_in_meta_and_the_values_do_not(self):
        published = dashboard.publishable_state(self.state())
        counts = published["meta"]["memoryBank"]
        self.assertEqual(counts, {"total": 3, "unanswered": 1, "dismissed": 0,
                                  "sensitive": 1, "identities": 1, "questions": 3,
                                  "policies": 0, "stories": 0})

    def test_an_identity_publishes_the_document_name_not_the_path(self):
        """A path is a directory layout on one person's machine and says
        nothing a reader of the board needs."""
        identity = dashboard.publishable_state(self.state())["memoryBank"]["identities"][0]
        self.assertEqual(identity["resumeRef"], "Sahil Resume.pdf")
        self.assertNotIn("/home/someone", json.dumps(identity))
        self.assertTrue(identity["isDefault"])
        self.assertEqual(identity["countryScope"], ["US"])

    def test_no_answer_and_no_question_reaches_the_page(self):
        """The measurement that made this a fetch rather than an embed, and the
        privacy property that falls out of it. Nothing here is in a saved copy
        of the page, a cache of it, or its view-source."""
        published = dashboard.publishable_state(self.state())
        page = dashboard.build(published, "test")
        # Still fetched rather than embedded. That was always a page-weight
        # measurement first, and it holds whatever the display rules are.
        for absent in ("$185,000", "15 days", "Expected compensation?",
                       "Notice period?", "5550001111", "1 Main St"):
            self.assertNotIn(absent, page, absent)

    def test_the_page_carries_the_counts_it_is_allowed_to(self):
        published = dashboard.publishable_state(self.state())
        page = dashboard.build(published, "test")
        self.assertIn("data-profile-open", page)
        self.assertIn('data-primary-view="profile"', page)


class WatcherEditTests(unittest.TestCase):
    """The claimed queue entry, and the store call it becomes."""

    def test_a_save_becomes_an_answer_edit_carrying_its_version(self):
        argv, stdin = watch._bank_command({
            "applicationId": "ans-1", "action": "save", "key": "notice-period",
            "question": "Notice period?", "value": "15 days", "version": 2})
        self.assertEqual(argv[0], "answer-edit")
        payload = json.loads(stdin)
        self.assertEqual(payload["editId"], "ans-1")
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["value"], "15 days")

    def test_a_delete_carries_the_version_it_saw(self):
        argv, stdin = watch._bank_command({
            "applicationId": "del-1", "action": "delete", "key": "notice-period",
            "version": 3})
        self.assertEqual(argv, ["answer-delete", "--key", "notice-period",
                                "--version", "3", "--edit-id", "del-1"])
        self.assertIsNone(stdin)

    def test_a_delete_with_no_key_is_not_turned_into_a_command(self):
        argv, _ = watch._bank_command({"applicationId": "del-2", "action": "delete"})
        self.assertIsNone(argv)

    def test_the_resume_actions_map_to_their_commands(self):
        self.assertEqual(watch._resume_command(
            {"action": "default", "key": "in"})[0], ["resume-default", "--key", "in"])
        self.assertEqual(watch._resume_command(
            {"action": "remove", "key": "in"})[0], ["resume-remove", "--key", "in"])
        argv, stdin = watch._resume_command({"action": "content", "content": {"banner": "X"}})
        self.assertEqual(argv[0], "resume-content-put")
        self.assertEqual(json.loads(stdin), {"banner": "X"})

    def test_an_unknown_action_produces_no_command_rather_than_a_guess(self):
        self.assertIsNone(watch._resume_command({"action": "delete-everything"})[0])
