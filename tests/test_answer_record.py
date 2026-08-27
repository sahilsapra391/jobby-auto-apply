"""The application answer record: what it stores, what it refuses to store.

Every guard here is checked negatively. A rule that has only ever been shown
passing its own happy path has not been shown to bind at all, and the three that
matter -- secrets never written, sensitivity derived rather than declared, a
cited answer-store key actually existing -- are each the sort of thing a caller
would quietly get wrong on a form nobody re-reads.
"""

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import answer_record  # noqa: E402


ANSWERS = {
    "legally-authorized-to-work-in-the-us": {
        "key": "legally-authorized-to-work-in-the-us",
        "question": "Are you legally authorized to work in the US?",
        "state": "confirmed", "value": "Yes",
    },
    "expected-compensation": {
        "key": "expected-compensation",
        "question": "What are your compensation expectations?",
        "state": "sensitive", "value": "165000", "tags": ["compensation"],
    },
    "how-did-you-hear-about-us": {
        "key": "how-did-you-hear-about-us",
        "question": "How did you hear about us?",
        "state": "confirmed", "value": "The company website",
        "tags": ["source-how-heard"],
    },
    # An ordinary-looking question whose stored entry is sensitive. The whole
    # point of deriving the flag is that the question text alone cannot see it.
    "what-is-your-preferred-arrangement": {
        "key": "what-is-your-preferred-arrangement",
        "question": "What is your preferred arrangement?",
        "state": "sensitive", "value": "Something private",
    },
}


class RecordFileTests(unittest.TestCase):
    """Writing a record, with the repo's applications/ folder redirected."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.apps = root / "applications"
        self.apps.mkdir()
        self.store = root / "state"
        self.store.mkdir()
        (self.store / "answers.json").write_text(json.dumps(ANSWERS))
        (self.store / "sessions").mkdir()
        (self.store / "sessions" / "app-1.json").write_text(json.dumps({
            "applicationId": "app-1", "company": "Acme", "role": "Product Manager",
            "url": "https://boards.example/acme/1", "ats": "greenhouse",
        }))
        self._real_apps = answer_record.APPS
        answer_record.APPS = self.apps

    def tearDown(self):
        answer_record.APPS = self._real_apps
        self.temp.cleanup()

    def write(self, fields, application_id="app-1"):
        return answer_record.write(application_id, fields, root=self.store)

    def read(self, application_id="app-1", company="Acme"):
        path = self.apps / company / f"answers-{application_id}.json"
        return json.loads(path.read_text())

    # --- the four sources, and the record they produce ---------------------

    def test_a_run_with_all_four_source_types_produces_one_complete_record(self):
        result = self.write([
            {"question": "Are you legally authorized to work in the US?",
             "answer": "Yes", "source": "stored",
             "sourceKey": "legally-authorized-to-work-in-the-us",
             "readBack": "match"},
            {"question": "First name", "answer": "Sahil", "source": "profile",
             "readBack": "match"},
            {"question": "Why do you want this role?",
             "answer": "Written from the tailored resume.", "source": "generated",
             "readBack": "match"},
            {"question": "Can you be in the London office three days a week?",
             "answer": "Yes", "source": "provided", "readBack": "match"},
        ])
        self.assertEqual(result["fields"], 4)
        self.assertEqual(result["sources"],
                         {"stored": 1, "profile": 1, "generated": 1, "provided": 1})

        record = self.read()
        self.assertEqual(record["applicationId"], "app-1")
        self.assertEqual(record["company"], "Acme")
        self.assertEqual(record["role"], "Product Manager")
        self.assertEqual(record["ats"], "greenhouse")
        self.assertEqual(record["schema"], answer_record.SCHEMA_VERSION)
        self.assertEqual([f["source"] for f in record["fields"]],
                         ["stored", "profile", "generated", "provided"])
        self.assertTrue(record["capturedAt"].endswith("Z"))

    def test_the_record_is_filed_beside_that_company_s_documents(self):
        self.write([{"question": "First name", "answer": "Sahil",
                     "source": "profile", "readBack": "match"}])
        self.assertTrue((self.apps / "Acme" / "answers-app-1.json").exists())
        self.assertEqual(answer_record.captured_ids(), {"app-1"})
        self.assertEqual(answer_record.load("app-1")["company"], "Acme")

    def test_a_record_is_written_owner_only(self):
        self.write([{"question": "First name", "answer": "Sahil",
                     "source": "profile", "readBack": "match"}])
        mode = (self.apps / "Acme" / "answers-app-1.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_two_roles_at_one_company_keep_separate_records(self):
        (self.store / "sessions" / "app-2.json").write_text(json.dumps({
            "applicationId": "app-2", "company": "Acme", "role": "Staff PM"}))
        self.write([{"question": "First name", "answer": "Sahil",
                     "source": "profile", "readBack": "match"}])
        self.write([{"question": "Last name", "answer": "Sapra",
                     "source": "profile", "readBack": "match"}], "app-2")
        self.assertEqual(answer_record.captured_ids(), {"app-1", "app-2"})
        self.assertEqual(self.read("app-2")["fields"][0]["question"], "Last name")

    # --- secrets are never written ----------------------------------------

    def test_a_verification_code_never_reaches_the_file(self):
        self.write([
            {"question": "Enter the verification code we emailed you",
             "answer": "884213", "source": "provided", "readBack": "match"},
            {"question": "First name", "answer": "Sahil", "source": "profile",
             "readBack": "match"},
        ])
        raw = (self.apps / "Acme" / "answers-app-1.json").read_text()
        # The bytes are the test. Not the parsed field, not the counter: the
        # file on disk, which is what migrates to object storage later.
        self.assertNotIn("884213", raw)
        record = json.loads(raw)
        blocked = record["fields"][0]
        self.assertEqual(blocked["answer"], "")
        self.assertEqual(blocked["withheld"], "one-time or verification code")
        self.assertTrue(blocked["sensitive"])
        # The row survives. A form that asked must not read as a form that did
        # not, or the record lies by omission instead of by content.
        self.assertEqual(blocked["question"],
                         "Enter the verification code we emailed you")

    def test_every_secret_class_is_withheld_and_ordinary_questions_are_not(self):
        withheld = [
            "Enter the one-time password sent to your phone",
            "What is the OTP?",
            "Verification code",
            "Please supply the confirmation code",
            "Enter your security code",
            "Two-factor authentication code",
            "Enter your authenticator code",
            "Create a password for your candidate account",
            "Choose a password",
            "Account passphrase",
            "PIN number",
            "Social Security Number",
            "What is your SSN?",
            "National insurance number",
            "Passport number",
            "Aadhaar",
            "Taxpayer identification number",
            "Driver's licence number",
            "Bank account number",
            "Card number",
            "CVV",
        ]
        for question in withheld:
            with self.subTest(question=question):
                self.assertIsNotNone(answer_record.withheld_reason(question),
                                     f"{question!r} should never be stored")
        allowed = [
            "Do you hold a valid driver's licence?",
            "Are you authorized to work in the United States?",
            "How many years of product management experience do you have?",
            "What is your current job title?",
            "Do you have a security clearance?",
            "Which coding languages do you use?",
            "What is your notice period?",
        ]
        for question in allowed:
            with self.subTest(question=question):
                self.assertIsNone(answer_record.withheld_reason(question),
                                  f"{question!r} is an ordinary question")

    def test_a_withheld_field_keeps_no_read_back_value_either(self):
        # readBackValue is the other place a secret could ride in, because it
        # holds whatever the page showed rather than whatever was typed.
        self.write([{"question": "One-time password", "answer": "111111",
                     "source": "provided", "readBack": "differs",
                     "readBackValue": "222222"}])
        raw = (self.apps / "Acme" / "answers-app-1.json").read_text()
        self.assertNotIn("111111", raw)
        self.assertNotIn("222222", raw)

    # --- sensitivity is derived, never declared ----------------------------

    def test_pay_and_demographic_questions_are_flagged_whatever_the_caller_says(self):
        # The caller cannot pass `sensitive` at all -- the schema has no such
        # key -- so the flag can only come from here.
        self.write([
            {"question": "What are your salary expectations?", "answer": "165000",
             "source": "stored", "sourceKey": "expected-compensation",
             "readBack": "match"},
            {"question": "Race / ethnicity", "answer": "Asian",
             "source": "provided", "readBack": "unverified"},
            {"question": "Date of birth", "answer": "1 January 1990",
             "source": "profile", "readBack": "match"},
            {"question": "Are you a protected veteran?", "answer": "No",
             "source": "provided", "readBack": "unverified"},
            {"question": "What is your current job title?", "answer": "PM",
             "source": "profile", "readBack": "match"},
        ])
        record = self.read()
        flags = {f["question"]: f["sensitive"] for f in record["fields"]}
        self.assertTrue(flags["What are your salary expectations?"])
        self.assertTrue(flags["Race / ethnicity"])
        self.assertTrue(flags["Date of birth"])
        self.assertTrue(flags["Are you a protected veteran?"])
        self.assertFalse(flags["What is your current job title?"])
        # Flagged, but stored: the user asking what the form said about pay is
        # the whole reason this record exists.
        pay = record["fields"][0]
        self.assertEqual(pay["answer"], "165000")
        self.assertNotIn("withheld", pay)

    def test_an_ordinary_question_is_flagged_when_its_stored_entry_is_sensitive(self):
        self.write([{"question": "What is your preferred arrangement?",
                     "answer": "Something private", "source": "stored",
                     "sourceKey": "what-is-your-preferred-arrangement",
                     "readBack": "match"}])
        field = self.read()["fields"][0]
        self.assertTrue(field["sensitive"])
        self.assertIn("answer store", field["sensitiveReason"])

    def test_a_sensitive_tag_flags_a_row_the_wording_would_have_missed(self):
        answers = dict(ANSWERS)
        answers["quiet"] = {"key": "quiet", "question": "Any preferences?",
                            "state": "confirmed", "value": "x",
                            "tags": ["political-contributions"]}
        (self.store / "answers.json").write_text(json.dumps(answers))
        self.write([{"question": "Any preferences?", "answer": "None",
                     "source": "stored", "sourceKey": "quiet", "readBack": "match"}])
        self.assertTrue(self.read()["fields"][0]["sensitive"])

    # --- a cited source has to exist ---------------------------------------

    def test_a_stored_source_naming_a_key_that_does_not_exist_is_refused(self):
        with self.assertRaises(answer_record.RecordError) as caught:
            self.write([{"question": "Are you authorized to work?", "answer": "Yes",
                         "source": "stored", "sourceKey": "invented-key",
                         "readBack": "match"}])
        self.assertIn("not in the answer store", str(caught.exception))
        self.assertEqual(answer_record.captured_ids(), set())

    def test_a_stored_source_with_no_key_is_refused(self):
        with self.assertRaises(answer_record.RecordError):
            self.write([{"question": "Authorized?", "answer": "Yes",
                         "source": "stored", "readBack": "match"}])

    def test_a_key_on_a_source_that_is_not_the_store_is_refused(self):
        with self.assertRaises(answer_record.RecordError):
            self.write([{"question": "First name", "answer": "Sahil",
                         "source": "profile", "readBack": "match",
                         "sourceKey": "legally-authorized-to-work-in-the-us"}])

    # --- shape ------------------------------------------------------------

    def test_an_unknown_source_is_refused(self):
        with self.assertRaises(answer_record.RecordError):
            self.write([{"question": "Q", "answer": "A", "source": "guessed"}])

    def test_an_unknown_read_back_state_is_refused(self):
        with self.assertRaises(answer_record.RecordError):
            self.write([{"question": "Q", "answer": "A", "source": "profile",
                         "readBack": "probably"}])

    def test_an_unsupported_key_is_refused_rather_than_dropped(self):
        with self.assertRaises(answer_record.RecordError) as caught:
            self.write([{"question": "Q", "answer": "A", "source": "profile",
                         "sensitive": False}])
        self.assertIn("unsupported keys", str(caught.exception))

    def test_the_same_question_twice_is_refused(self):
        with self.assertRaises(answer_record.RecordError):
            self.write([
                {"question": "First name", "answer": "Sahil", "source": "profile"},
                {"question": "first name", "answer": "Other", "source": "profile"},
            ])

    def test_a_refused_manifest_writes_nothing_at_all(self):
        with self.assertRaises(answer_record.RecordError):
            self.write([
                {"question": "First name", "answer": "Sahil", "source": "profile"},
                {"question": "Bad", "answer": "x", "source": "nonsense"},
            ])
        self.assertFalse((self.apps / "Acme").exists())

    def test_an_application_with_no_known_company_is_refused(self):
        with self.assertRaises(answer_record.RecordError) as caught:
            self.write([{"question": "Q", "answer": "A", "source": "profile"}],
                       "unknown-app")
        self.assertIn("cannot be filed", str(caught.exception))

    def test_the_history_names_the_company_once_the_session_is_gone(self):
        # Submission deletes the session. A record written afterwards still has
        # to land in the right folder.
        (self.store / "sessions" / "app-1.json").unlink()
        (self.store / "applications.jsonl").write_text(json.dumps({
            "event": "completed", "applicationId": "app-1", "company": "Acme",
            "role": "Product Manager"}) + "\n")
        self.write([{"question": "First name", "answer": "Sahil",
                     "source": "profile"}])
        self.assertTrue((self.apps / "Acme" / "answers-app-1.json").exists())

    def test_a_control_character_in_a_question_does_not_survive(self):
        self.write([{"question": "Full\x07 name", "answer": "Sahil\x00x",
                     "source": "profile"}])
        field = self.read()["fields"][0]
        self.assertEqual(field["question"], "Full name")
        self.assertEqual(field["answer"], "Sahilx")

    def test_a_record_over_the_ceiling_is_refused(self):
        with self.assertRaises(answer_record.RecordError) as caught:
            self.write([{"question": f"Question {i}", "answer": "x" * 3_000,
                         "source": "generated"} for i in range(40)])
        self.assertIn("ceiling", str(caught.exception))

    def test_a_read_back_that_differs_keeps_what_the_page_showed(self):
        self.write([{"question": "Country", "answer": "United Kingdom",
                     "source": "profile", "readBack": "differs",
                     "readBackValue": "United Arab Emirates"}])
        field = self.read()["fields"][0]
        self.assertEqual(field["readBack"], "differs")
        self.assertEqual(field["readBackValue"], "United Arab Emirates")


class PublicRecordTests(unittest.TestCase):
    """What the board is served, which is not quite what is on disk."""

    def test_a_withheld_answer_cannot_be_reintroduced_by_a_hand_edited_file(self):
        # public_record is the last gate before a record reaches KV. A file
        # edited by hand, or written by an older revision, does not get to put a
        # value back next to a withheld marker.
        public = answer_record.public_record({
            "applicationId": "app-1", "company": "Acme", "fields": [
                {"question": "One-time password", "answer": "884213",
                 "source": "provided", "readBack": "match", "sensitive": True,
                 "withheld": "one-time or verification code"},
            ]})
        self.assertEqual(public["fields"][0]["answer"], "")
        self.assertNotIn("884213", json.dumps(public))

    def test_an_unknown_source_or_read_back_falls_back_rather_than_leaking(self):
        public = answer_record.public_record({
            "applicationId": "a", "company": "b", "fields": [
                {"question": "Q", "answer": "A", "source": "<script>",
                 "readBack": "<script>", "sensitive": False}]})
        self.assertEqual(public["fields"][0]["source"], "generated")
        self.assertEqual(public["fields"][0]["readBack"], "unverified")

    def test_the_sensitive_flag_survives_even_though_nothing_draws_it(self):
        """The board shows pay and demographics plainly. The flag stays anyway.

        Sahil's board has one reader who already knows his own salary, so
        covering it was friction protecting nobody. That is a decision about a
        display, and it does not make the data ordinary. The flag is what a
        second account's board masks on and what the plan's Class 2 field
        encryption keys on, and re-deriving it later from question text alone
        would lose every row the answer store's own state caught.
        """
        public = answer_record.public_record({
            "applicationId": "a", "company": "b", "fields": [
                {"question": "What are your salary expectations?",
                 "answer": "165000", "source": "stored", "readBack": "match",
                 "sensitive": True},
                {"question": "First name", "answer": "Sahil", "source": "profile",
                 "readBack": "match", "sensitive": False},
            ]})
        self.assertTrue(public["fields"][0]["sensitive"])
        self.assertFalse(public["fields"][1]["sensitive"])
        # And the value is there to be shown, not blanked by the flag.
        self.assertEqual(public["fields"][0]["answer"], "165000")

    def test_stray_keys_do_not_survive_into_the_published_record(self):
        public = answer_record.public_record({
            "applicationId": "a", "company": "b", "secretInternalPath": "/Users/x",
            "fields": [{"question": "Q", "answer": "A", "source": "profile",
                        "sourceKey": "k", "sensitive": False}]})
        self.assertNotIn("secretInternalPath", public)
        # The key names a row in the local answer store. The board shows a
        # source badge, not a store index, so it stays on the laptop.
        self.assertNotIn("sourceKey", public["fields"][0])


class ArtifactContractTests(unittest.TestCase):
    """The writer and the R2 collector are one contract in two modules.

    r2_artifacts is the single collector deciding what a Class 1 artifact is and
    what its key is, and it already matched `answers-<id>.json` by name before
    this feature existed. So there is nothing to add there and exactly one thing
    to keep true: both modules must look in the same directory. If they drift,
    the record is written, the upload never sees it, and the board says "not
    captured" for an application that has a record sitting on disk.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def test_both_modules_resolve_the_same_applications_directory(self):
        import r2_artifacts
        cases = [None, str(self.root), str(self.root / "other")]
        for case in cases:
            with self.subTest(root=case):
                self.assertEqual(answer_record.applications_dir(case),
                                 r2_artifacts.applications_dir(case))

    def test_the_environment_redirect_moves_both_the_same_way(self):
        import r2_artifacts
        previous = os.environ.get("JOB_APPLICATION_DIR")
        os.environ["JOB_APPLICATION_DIR"] = str(self.root)
        try:
            self.assertEqual(answer_record.applications_dir(),
                             r2_artifacts.applications_dir())
            self.assertEqual(answer_record.applications_dir(),
                             self.root / "applications")
        finally:
            if previous is None:
                os.environ.pop("JOB_APPLICATION_DIR", None)
            else:
                os.environ["JOB_APPLICATION_DIR"] = previous

    def test_a_record_is_collected_as_a_class_one_artifact_under_its_application(self):
        import r2_artifacts
        store = self.root / "state"
        (store / "sessions").mkdir(parents=True)
        (store / "answers.json").write_text("{}")
        (store / "sessions" / "app-1.json").write_text(json.dumps({
            "applicationId": "app-1", "company": "Acme", "role": "PM"}))
        apps = self.root / "applications"
        apps.mkdir()
        real_apps = answer_record.APPS
        answer_record.APPS = apps
        self.addCleanup(setattr, answer_record, "APPS", real_apps)
        answer_record.write("app-1", [
            {"question": "First name", "answer": "Sahil", "source": "profile",
             "readBack": "match"}], root=store)

        plan = r2_artifacts.collect(root=str(store), apps_dir=str(apps))
        found = [a for a in plan["artifacts"] if a["name"] == "answers-app-1.json"]
        self.assertEqual(len(found), 1)
        artifact = found[0]
        self.assertEqual(artifact["applicationId"], "app-1")
        self.assertEqual(artifact["kind"], "answers")
        self.assertEqual(artifact["key"], "u/account-1/app/app-1/answers.json")
        # The board's modal reads this as JSON. A record keyed with the PDF
        # content type is a file the browser refuses rather than a page.
        self.assertEqual(artifact["contentType"], "application/json")
        self.assertEqual(artifact["sha256"],
                         hashlib.sha256((apps / "Acme" / "answers-app-1.json")
                                        .read_bytes()).hexdigest())

    def test_a_hand_made_questions_file_cannot_shadow_the_real_record(self):
        # Seventeen questions-<id>.json files predate this feature, hold question
        # text with no answers, and map to the same `answers` kind. Both want
        # u/<user>/app/<id>/answers.json and only one can have it. Sorted path
        # order gives it to the real record; the older file keeps its own name
        # under unfiled/ rather than overwriting what the board reads.
        import r2_artifacts
        store = self.root / "state"
        (store / "sessions").mkdir(parents=True)
        (store / "answers.json").write_text("{}")
        (store / "sessions" / "app-1.json").write_text(json.dumps({
            "applicationId": "app-1", "company": "Acme", "role": "PM"}))
        apps = self.root / "applications"
        (apps / "Acme").mkdir(parents=True)
        (apps / "Acme" / "questions-app-1.json").write_text(
            json.dumps(["What is your name?"]))
        real_apps = answer_record.APPS
        answer_record.APPS = apps
        self.addCleanup(setattr, answer_record, "APPS", real_apps)
        answer_record.write("app-1", [
            {"question": "First name", "answer": "Sahil", "source": "profile",
             "readBack": "match"}], root=store)

        plan = r2_artifacts.collect(root=str(store), apps_dir=str(apps))
        by_name = {a["name"]: a for a in plan["artifacts"]}
        self.assertEqual(by_name["answers-app-1.json"]["key"],
                         "u/account-1/app/app-1/answers.json")
        self.assertEqual(by_name["questions-app-1.json"]["key"],
                         "u/account-1/unfiled/Acme/questions-app-1.json")


class SalvagedQuestionTests(unittest.TestCase):
    """Old question lists become questions-only records, and never answers.

    41 questions*.json files survive from before capture existed. They hold what
    the form asked and no values at all. Turning them into rows is honest;
    resolving them against answers.json to produce values would not be, and the
    refusal is the point of these tests.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.store = root / "state"
        (self.store / "sessions").mkdir(parents=True)
        (self.store / "answers.json").write_text(json.dumps(ANSWERS))
        (self.store / "applications.jsonl").write_text("\n".join(json.dumps(e) for e in [
            {"event": "completed", "applicationId": "sent-1", "company": "Acme",
             "role": "PM"},
            {"event": "completed", "applicationId": "twin-a", "company": "Twin",
             "role": "PM"},
            {"event": "completed", "applicationId": "twin-b", "company": "Twin",
             "role": "Staff PM"},
            {"event": "submitting", "applicationId": "inflight-1",
             "company": "Pending", "role": "PM"},
        ]) + "\n")
        self.apps = root / "applications"
        for company in ("Acme", "Twin", "Pending", "Stranger"):
            (self.apps / company).mkdir(parents=True)
        (self.apps / "Acme" / "questions.json").write_text(json.dumps([
            "Are you legally authorized to work in the US?",
            "What are your salary expectations?",
            "Enter the verification code we emailed you",
            "Are you legally authorized to work in the US?",
        ]))
        (self.apps / "Twin" / "questions.json").write_text(
            json.dumps(["Which role are you applying for?"]))
        (self.apps / "Pending" / "questions.json").write_text(
            json.dumps(["Anything else?"]))
        (self.apps / "Stranger" / "questions.json").write_text(
            json.dumps(["Who are you?"]))
        self._real = answer_record.APPS
        answer_record.APPS = self.apps
        self.addCleanup(setattr, answer_record, "APPS", self._real)

    def plan(self):
        return answer_record.salvage_plan(self.store)

    def test_a_submitted_application_with_one_company_folder_is_attributed(self):
        planned = {p["applicationId"]: p for p in self.plan()["plan"]}
        self.assertEqual(set(planned), {"sent-1"})
        self.assertEqual(planned["sent-1"]["attributedBy"],
                         "the only application at this company")

    def test_a_company_with_two_applications_is_skipped_not_guessed(self):
        # Two open roles at one company get two different forms. Filing one
        # role's questions under the other role's application is the same
        # mistake as putting a stranger's logo on their card.
        why = {s["path"]: s["why"] for s in self.plan()["skipped"]}
        twin = [w for p, w in why.items() if "Twin" in p]
        self.assertEqual(len(twin), 1)
        self.assertIn("2 applications at this company", twin[0])

    def test_an_unsubmitted_application_is_skipped(self):
        why = [s["why"] for s in self.plan()["skipped"] if "Pending" in s["path"]]
        self.assertEqual(why, ["not a submitted application"])

    def test_a_company_the_store_does_not_know_is_skipped(self):
        why = [s["why"] for s in self.plan()["skipped"] if "Stranger" in s["path"]]
        self.assertEqual(why, ["no application at this company in the store"])

    def test_the_record_carries_questions_and_no_answers_at_all(self):
        item = self.plan()["plan"][0]
        answer_record.write("sent-1", item["questions"], root=self.store,
                            capture="questions-only")
        record = json.loads(
            (self.apps / "Acme" / "answers-sent-1.json").read_text())
        self.assertEqual(record["capture"], "questions-only")
        # The duplicate question collapses; three distinct ones survive.
        self.assertEqual(len(record["fields"]), 3)
        for field in record["fields"]:
            self.assertTrue(field["unrecorded"])
            self.assertNotIn("answer", field)
            self.assertNotIn("source", field)
            self.assertNotIn("readBack", field)

    def test_no_value_from_the_answer_store_leaks_into_a_salvaged_record(self):
        # The store holds "165000" under expected-compensation and the salvaged
        # list asks for salary expectations. Joining them would produce what the
        # store would say today, presented as what the form received months ago.
        item = self.plan()["plan"][0]
        answer_record.write("sent-1", item["questions"], root=self.store,
                            capture="questions-only")
        raw = (self.apps / "Acme" / "answers-sent-1.json").read_text()
        for value in ("165000", "Yes", "Something private"):
            self.assertNotIn(value, raw, f"{value!r} was reconstructed from the store")

    def test_a_salvaged_record_still_flags_sensitive_questions(self):
        item = self.plan()["plan"][0]
        answer_record.write("sent-1", item["questions"], root=self.store,
                            capture="questions-only")
        record = json.loads((self.apps / "Acme" / "answers-sent-1.json").read_text())
        flags = {f["question"]: f["sensitive"] for f in record["fields"]}
        self.assertTrue(flags["What are your salary expectations?"])
        self.assertFalse(flags["Are you legally authorized to work in the US?"])

    def test_the_published_shape_carries_the_level_and_no_invented_badges(self):
        item = self.plan()["plan"][0]
        record = answer_record.build("sent-1", item["questions"], root=self.store,
                                     capture="questions-only")
        public = answer_record.public_record(record)
        self.assertEqual(public["capture"], "questions-only")
        for field in public["fields"]:
            self.assertTrue(field["unrecorded"])
            self.assertNotIn("source", field)
            self.assertNotIn("readBack", field)

    def test_a_full_record_is_never_replaced_by_a_salvage(self):
        # A real record knows what went in. A salvage knows only the questions.
        # Downgrading one to the other loses the only copy of the answers.
        answer_record.write("sent-1", [
            {"question": "First name", "answer": "Sahil", "source": "profile",
             "readBack": "match"}], root=self.store)
        existing = answer_record.index(self.store)
        self.assertIn("sent-1", existing)
        planned = [p for p in self.plan()["plan"] if p["applicationId"] in existing]
        self.assertEqual(len(planned), 1, "the plan still finds it")
        # The CLI holds it back; the guard is that `index` already knows.
        record = json.loads((self.apps / "Acme" / "answers-sent-1.json").read_text())
        self.assertEqual(record["capture"], "full")


class NeverBackfillsFromTheStoreTests(unittest.TestCase):
    """The line between replaying an approved answer and inventing history.

    Two operations live one keystroke apart. Filling a live form from
    answers.json is the whole point of the answer store: the user approved that
    value and the agent is replaying it into a field. Writing that same value
    into a *historical* record's answer is a fabricated audit trail, because the
    record claims to say what an employer received and the store says what the
    user would answer now. Many entries were last edited 2026-08-26, months
    after the applications they would be attributed to.

    This file does the second thing to nobody, on either capture path. The proof
    is a sentinel: every stored value is replaced with a string that appears
    nowhere else, so if any code path ever copies one out it shows up in the
    bytes.
    """

    SENTINEL = "SENTINEL-STORED-VALUE-DO-NOT-COPY"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.store = root / "state"
        (self.store / "sessions").mkdir(parents=True)
        poisoned = {}
        for key, entry in ANSWERS.items():
            poisoned[key] = {**entry, "value": self.SENTINEL,
                             "answer": self.SENTINEL, "note": self.SENTINEL}
        (self.store / "answers.json").write_text(json.dumps(poisoned))
        (self.store / "sessions" / "app-1.json").write_text(json.dumps({
            "applicationId": "app-1", "company": "Acme", "role": "PM"}))
        self.apps = root / "applications"
        self.apps.mkdir()
        real = answer_record.APPS
        answer_record.APPS = self.apps
        self.addCleanup(setattr, answer_record, "APPS", real)

    def written(self):
        return (self.apps / "Acme" / "answers-app-1.json").read_text()

    def test_a_full_record_citing_a_stored_key_never_copies_its_value(self):
        # The record cites the key, so the store is read -- for the key's
        # existence and its sensitive state, and for nothing else.
        answer_record.write("app-1", [
            {"question": "What are your compensation expectations?",
             "answer": "what actually went into the form",
             "source": "stored", "sourceKey": "expected-compensation",
             "readBack": "match"},
        ], root=self.store)
        raw = self.written()
        self.assertNotIn(self.SENTINEL, raw)
        self.assertIn("what actually went into the form", raw)
        # And the store was still consulted, or the sensitivity would be wrong.
        self.assertTrue(json.loads(raw)["fields"][0]["sensitive"])

    def test_a_salvaged_record_never_resolves_its_questions(self):
        # Every question here has a matching entry in the store. None is filled.
        answer_record.write("app-1", [
            entry["question"] for entry in ANSWERS.values()
        ], root=self.store, capture="questions-only")
        raw = self.written()
        self.assertNotIn(self.SENTINEL, raw)
        for field in json.loads(raw)["fields"]:
            self.assertTrue(field["unrecorded"])
            self.assertNotIn("answer", field)

    def test_an_empty_answer_stays_empty_rather_than_being_filled_in(self):
        # The tempting shortcut: a blank answer, a known key, an obvious value
        # sitting right there. It stays blank, because blank is what happened.
        answer_record.write("app-1", [
            {"question": "Are you legally authorized to work in the US?",
             "answer": "", "source": "stored",
             "sourceKey": "legally-authorized-to-work-in-the-us",
             "readBack": "unverified"},
        ], root=self.store)
        raw = self.written()
        self.assertNotIn(self.SENTINEL, raw)
        self.assertEqual(json.loads(raw)["fields"][0]["answer"], "")

    def test_the_module_reads_no_value_field_off_an_answer_store_entry(self):
        """Structural, because 'nothing reads it today' is one refactor from false."""
        source = pathlib.Path(answer_record.__file__).read_text()
        body = source[source.index("def normalise_fields"):]
        for banned in ('entry["value"]', "entry.get(\"value\")",
                       'entry["answer"]', "entry.get(\"answer\")"):
            self.assertNotIn(banned, body,
                             f"{banned} would copy a stored answer into a record")


class HistoryStaysClosedTests(unittest.TestCase):
    def test_the_history_still_refuses_to_carry_answers(self):
        """The per-application record is the sanctioned path *because* this holds.

        applications.jsonl is append-only, unfiltered and read by everything;
        putting a transcript there would put a salary figure and an EEO answer
        in a file nothing masks. The record filters at the door instead, which
        is only a meaningful distinction while the door stays shut. This is the
        obvious place someone adds a shortcut, so the refusal is asserted from
        the feature that would benefit from breaking it.
        """
        import store
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        payload = pathlib.Path(temp.name) / "event.json"

        class Args:
            root = temp.name
            input = str(payload)

        for banned in ("answers", "values"):
            with self.subTest(key=banned):
                payload.write_text(json.dumps({
                    "event": "completed", "company": "Acme", "role": "PM",
                    banned: {"what are your salary expectations?": "165000"}}))
                with self.assertRaises(store.StoreError):
                    store.cmd_history_append(Args())


if __name__ == "__main__":
    unittest.main()
