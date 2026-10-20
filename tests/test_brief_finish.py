"""Tests for scripts/brief.py and scripts/finish.py, the two consolidations.

These exist to remove round trips, and a consolidation is only worth having if
it keeps every check the calls it replaced were doing. So most of what is tested
here is refusal: that pre-submit still stops a cancelled or duplicate
application AND writes nothing when it does, that a completion without observed
confirmation is rejected, and that a test store can never publish over the real
board.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


brief = load("brief")


def run_finish(args, root):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "finish.py")] + args + ["--root", str(root)],
        capture_output=True, text=True)


class StoreTemp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-test-")
        self.root = pathlib.Path(self.dir)
        (self.root / "sessions").mkdir(parents=True)

    def session(self, aid, **over):
        entry = {"applicationId": aid, "company": "Acme", "role": "Product Manager",
                 "url": "https://example.com/jobs/1", "region": "US",
                 "ats": "greenhouse", "status": "inflight"}
        entry.update(over)
        (self.root / "sessions" / f"{aid}.json").write_text(json.dumps(entry))
        return entry

    def events(self):
        path = self.root / "applications.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class PreSubmitTests(StoreTemp):
    def test_a_clear_application_gets_the_write_ahead(self):
        self.session("acme-pm")
        out = json.loads(run_finish(["--id", "acme-pm", "--pre-submit"], self.root).stdout)
        self.assertTrue(out["proceed"])
        self.assertEqual([e["event"] for e in self.events()], ["submitting"])

    def test_a_cancelled_application_is_refused_and_nothing_is_written(self):
        # The user pressed Drop while the agent was filling the form. The old
        # sequence read the session in one call and logged the write-ahead in
        # another; folding them together must not lose the check between them.
        self.session("acme-pm", status="dropped")
        out = json.loads(run_finish(["--id", "acme-pm", "--pre-submit"], self.root).stdout)
        self.assertFalse(out["proceed"])
        self.assertIn("cancelled", out["reason"])
        self.assertEqual(self.events(), [])

    def test_a_missing_session_is_refused(self):
        out = json.loads(run_finish(["--id", "ghost-pm", "--pre-submit"], self.root).stdout)
        self.assertFalse(out["proceed"])
        self.assertEqual(self.events(), [])

    def test_an_already_submitted_role_is_refused_as_duplicate(self):
        self.session("acme-pm")
        (self.root / "applications.jsonl").write_text(json.dumps({
            "event": "completed", "applicationId": "acme-pm", "company": "Acme",
            "role": "Product Manager", "url": "https://example.com/jobs/1",
            "at": "2026-08-01T00:00:00Z", "eventId": "x"}) + "\n")
        out = json.loads(run_finish(["--id", "acme-pm", "--pre-submit"], self.root).stdout)
        self.assertFalse(out["proceed"])
        self.assertEqual(len(self.events()), 1, "refusal must not append anything")


class CompletedTests(StoreTemp):
    def nonce(self, aid="acme-pm"):
        result = run_finish(["--id", aid, "--pre-submit"], self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["submissionNonce"]

    def test_a_completion_without_observed_confirmation_is_refused(self):
        self.session("acme-pm")
        r = run_finish(["--id", "acme-pm", "--completed", "--confirmation", "   "], self.root)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("confirmation", (r.stdout + r.stderr).lower())
        self.assertEqual(self.events(), [])

    def test_documents_are_recorded_as_filenames_not_paths(self):
        # A full path here silently dropped the document from the publish
        # bundle and produced a dashboard link to a document that did not exist.
        self.session("acme-pm")
        nonce = self.nonce()
        out = json.loads(run_finish([
            "--id", "acme-pm", "--completed",
            "--nonce", nonce,
            "--confirmation", "Your application was successfully submitted.",
            "--resume", "/abs/where/Alex_Example_Acme_Resume.pdf",
            "--cover", "/abs/where/Alex_Example_Acme_CoverLetter.pdf"], self.root).stdout)
        self.assertEqual(out["resumeVariant"], "Alex_Example_Acme_Resume.pdf")
        self.assertEqual(out["coverLetter"], "Alex_Example_Acme_CoverLetter.pdf")
        event = [e for e in self.events() if e["event"] == "completed"][0]
        self.assertNotIn("/", event["resumeVariant"])
        self.assertEqual(event["confirmation"],
                         "Your application was successfully submitted.")

    def test_completion_before_pre_submit_is_refused(self):
        self.session("acme-pm")
        result = run_finish([
            "--id", "acme-pm", "--completed", "--nonce", "x" * 32,
            "--confirmation", "Application received",
        ], self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), [])

    def test_completion_nonce_is_one_use(self):
        self.session("acme-pm")
        nonce = self.nonce()
        args = [
            "--id", "acme-pm", "--completed", "--nonce", nonce,
            "--confirmation", "Application received",
        ]
        first = run_finish(args, self.root)
        second = run_finish(args, self.root)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(
            [event["event"] for event in self.events()],
            ["submitting", "completed"],
        )

    def test_generated_nonce_can_never_look_like_an_option(self):
        self.session("acme-pm")
        nonce = self.nonce()
        self.assertTrue(nonce.startswith("jobby_"))
        result = run_finish([
            "--id", "acme-pm", "--completed", "--nonce", nonce,
            "--confirmation", "Application received",
        ], self.root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonce_from_another_application_is_refused(self):
        self.session("acme-pm")
        self.session(
            "other-pm", company="Other", role="Engineer",
            url="https://example.com/jobs/2",
        )
        other_nonce = self.nonce("other-pm")
        own_nonce = self.nonce("acme-pm")
        self.assertNotEqual(other_nonce, own_nonce)
        result = run_finish([
            "--id", "acme-pm", "--completed", "--nonce", other_nonce,
            "--confirmation", "Application received",
        ], self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            [event["event"] for event in self.events()],
            ["submitting", "submitting"],
        )


class ParkTests(StoreTemp):
    def test_park_writes_pending_fields_the_store_accepts(self):
        # The store rejects a bare string, and rejects any field carrying a
        # value, because answers belong in the answer store.
        self.session("acme-pm")
        out = json.loads(run_finish([
            "--id", "acme-pm", "--park", "--pending", "Workday account",
            "--detail", "Create it, then press Retry"], self.root).stdout)
        self.assertTrue(out["parked"])
        saved = json.loads((self.root / "sessions" / "acme-pm.json").read_text())
        self.assertEqual(saved["status"], "review")
        self.assertEqual(saved["pendingFields"][0]["question"], "Workday account")
        self.assertNotIn("value", saved["pendingFields"][0])

    def answers(self):
        path = self.root / "answers.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def test_a_parked_question_is_recorded_for_next_time(self):
        """The grow-only loop, from the door the application opens.

        A question that stopped this application will stop the next one, so it
        goes into the Memory Bank the moment it stops this one, unanswered and
        attributed to the application that hit it.
        """
        self.session("acme-pm")
        out = json.loads(run_finish([
            "--id", "acme-pm", "--park",
            "--pending", "How many days a week can you be in the office?",
            "--detail", "Answer it and press Retry"], self.root).stdout)
        self.assertTrue(out["parked"])
        self.assertEqual(len(out["openedInBank"]), 1)
        entry = self.answers()[out["openedInBank"][0]]
        self.assertEqual(entry["state"], "missing")
        self.assertIn("unanswered", entry["tags"])
        self.assertEqual(entry["askedBy"], ["acme-pm"])

    def test_what_the_loop_decided_is_recorded_not_just_returned(self):
        """A silent loop failure has four indistinguishable causes otherwise.

        The agent tagged nothing, a guard refused it, the store errored, or the
        loop never ran: after the fact those all look identical, because the
        one field that said which was a return value nobody kept. It lands on
        the session, beside the blockers it describes.
        """
        self.session("acme-pm")
        run_finish([
            "--id", "acme-pm", "--park",
            "--pending", "How many days a week can you be in the office?",
            "--pending", "Solve the CAPTCHA",
            "--unanswered", "How many days a week can you be in the office?",
            "--detail", "Answer one, do the other"], self.root)
        saved = json.loads((self.root / "sessions" / "acme-pm.json").read_text())
        capture = saved["bankCapture"]
        self.assertEqual(capture["tagged"],
                         ["How many days a week can you be in the office?"])
        self.assertEqual(len(capture["opened"]), 1)
        self.assertTrue(capture["at"])
        # And the park itself is untouched by the diagnostic.
        self.assertEqual(saved["status"], "review")
        self.assertEqual(len(saved["pendingFields"]), 2)

    def test_the_record_says_why_when_nothing_was_banked(self):
        self.session("acme-pm")
        run_finish([
            "--id", "acme-pm", "--park",
            "--pending", "I authorize the Company to obtain a consumer report about me?",
            "--detail", "Read it yourself"], self.root)
        saved = json.loads((self.root / "sessions" / "acme-pm.json").read_text())
        self.assertEqual(saved["bankCapture"]["opened"], [])
        self.assertIn("refusal category", saved["bankCapture"]["notBanked"][0]["why"])

    def test_an_action_is_a_blocker_but_not_bank_material(self):
        """"Sign in to Workday" is not a question, and storing it would put an
        instruction in the bank where an answer belongs."""
        self.session("acme-pm")
        out = json.loads(run_finish([
            "--id", "acme-pm", "--park", "--pending", "Workday account",
            "--detail", "Create it, then press Retry"], self.root).stdout)
        self.assertTrue(out["parked"])
        self.assertEqual(out["openedInBank"], [])
        self.assertEqual(self.answers(), {})

    def test_the_agents_tagging_decides_when_it_is_given(self):
        self.session("acme-pm")
        out = json.loads(run_finish([
            "--id", "acme-pm", "--park",
            "--pending", "Tell us about a time you shipped something hard",
            "--pending", "Solve the CAPTCHA",
            "--unanswered", "Tell us about a time you shipped something hard",
            "--detail", "Answer the first, do the second"], self.root).stdout)
        self.assertEqual(len(out["openedInBank"]), 1)
        self.assertEqual(len(self.answers()), 1)

    def test_a_refusal_is_parked_and_never_banked(self):
        self.session("acme-pm")
        out = json.loads(run_finish([
            "--id", "acme-pm", "--park",
            "--pending", "I authorize the Company to obtain a consumer report about me?",
            "--detail", "Read it and decide yourself"], self.root).stdout)
        self.assertTrue(out["parked"], "the application must still park")
        self.assertEqual(out["openedInBank"], [])
        self.assertEqual(self.answers(), {})
        self.assertIn("refusal category", out["notBanked"][0]["why"])

    def test_a_bank_refusal_never_stops_the_park(self):
        """Failure honesty. Parking has to succeed even when the bank write does
        not, or a store problem leaves an application in flight with nobody
        told. What did not get recorded rides back in the result instead."""
        self.session("acme-pm")
        out = json.loads(run_finish([
            "--id", "acme-pm", "--park",
            "--pending", "What is the one-time code we sent you?",
            "--detail", "Read it off your phone yourself"], self.root).stdout)
        self.assertTrue(out["parked"])
        saved = json.loads((self.root / "sessions" / "acme-pm.json").read_text())
        self.assertEqual(saved["status"], "review")
        self.assertEqual(len(saved["pendingFields"]), 1)
        self.assertEqual(out["openedInBank"], [])
        self.assertIn("one-time code", out["notBanked"][0]["why"])

    def test_two_applications_asking_one_question_leave_one_entry(self):
        self.session("acme-pm")
        self.session("other-pm")
        question = "Why do you want to work here?"
        for aid in ("acme-pm", "other-pm"):
            run_finish(["--id", aid, "--park", "--pending", question,
                        "--detail", "Answer it"], self.root)
        answers = self.answers()
        self.assertEqual(len(answers), 1)
        self.assertEqual(list(answers.values())[0]["askedBy"], ["acme-pm", "other-pm"])

    def test_park_needs_a_named_blocker(self):
        self.session("acme-pm")
        r = run_finish(["--id", "acme-pm", "--park"], self.root)
        self.assertNotEqual(r.returncode, 0)


class ConcurrentCompanyTests(StoreTemp):
    def test_two_roles_at_one_company_get_separate_posting_files(self):
        # Ten companies already keep more than one role in a single folder and
        # five applications run at once. A shared jd.txt means the loser of the
        # race tailors a resume against the other role's posting.
        env = dict(os.environ, JOB_APPLICATION_DIR=str(self.root))
        written = []
        for aid, role in (("acme-pm-one", "Product Manager"),
                          ("acme-pm-two", "Senior Product Manager")):
            self.session(aid, role=role, extraction=f"Posting body for {role}")
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "brief.py"), "--id", aid],
                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            written.append(json.loads(r.stdout))

        self.assertNotEqual(written[0]["jdPath"], written[1]["jdPath"])
        for out, role in zip(written, ("Product Manager", "Senior Product Manager")):
            self.assertIn(role, pathlib.Path(out["jdPath"]).read_text())
        # Same folder, as intended. Only the posting file is per application.
        self.assertEqual(written[0]["folder"], written[1]["folder"])

    def test_a_retry_without_an_extraction_keeps_the_posting_already_on_disk(self):
        env = dict(os.environ, JOB_APPLICATION_DIR=str(self.root))
        self.session("acme-pm", extraction="The original posting body")
        first = json.loads(subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "brief.py"), "--id", "acme-pm"],
            capture_output=True, text=True, env=env).stdout)
        # The retry has lost the extraction; the file on disk is now the only
        # copy of the posting anywhere.
        self.session("acme-pm", extraction=None, status="review")
        second = json.loads(subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "brief.py"), "--id", "acme-pm"],
            capture_output=True, text=True, env=env).stdout)
        self.assertEqual(first["jdPath"], second["jdPath"])
        self.assertEqual(pathlib.Path(second["jdPath"]).read_text(),
                         "The original posting body")


class MissingSessionTests(StoreTemp):
    def test_a_completion_with_no_session_is_refused(self):
        # Without the company and role the event becomes a board row that
        # names no employer, which reads as a corrupted record of a real send.
        r = run_finish(["--id", "ghost-pm", "--completed",
                        "--confirmation", "Application submitted."], self.root)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.events(), [])


class GuardRegressionTests(StoreTemp):
    """The three ways this consolidation could have sent something it should not.

    Each of these was found by review after the code was written and before it
    was pushed, and each was reproducible.
    """

    def test_parking_does_not_resurrect_an_application_the_user_cancelled(self):
        # drop.py keeps the session and sets status "dropped". park() used to
        # overwrite that with "review", which puts it back in Needs you, and the
        # next Retry sends something the user called off.
        self.session("acme-pm", status="dropped")
        out = json.loads(run_finish([
            "--id", "acme-pm", "--park", "--pending", "Workday account"], self.root).stdout)
        self.assertFalse(out["parked"])
        saved = json.loads((self.root / "sessions" / "acme-pm.json").read_text())
        self.assertEqual(saved["status"], "dropped")

    def test_a_duplicate_is_refused_even_though_the_guard_exits_nonzero(self):
        # dupe_check exits 1 exactly when it finds a duplicate, so reading its
        # verdict from the exit code inverts the answer.
        self.session("acme-pm")
        (self.root / "applications.jsonl").write_text(json.dumps({
            "event": "completed", "applicationId": "other-id", "company": "Acme",
            "role": "Product Manager", "url": "https://example.com/jobs/1",
            "at": "2026-08-01T00:00:00Z", "eventId": "x"}) + "\n")
        out = json.loads(run_finish(["--id", "acme-pm", "--pre-submit"], self.root).stdout)
        self.assertFalse(out["proceed"])
        self.assertEqual(len(self.events()), 1, "a refusal must not append anything")

    def test_an_unreadable_history_blocks_rather_than_clears(self):
        # One truncated line used to read as "nothing has been applied to".
        self.session("acme-pm")
        (self.root / "applications.jsonl").write_text(
            json.dumps({"event": "completed", "applicationId": "z", "company": "A",
                        "role": "R", "at": "2026-08-01T00:00:00Z", "eventId": "y"})
            + "\n{truncated\n")
        out = json.loads(run_finish(["--id", "acme-pm", "--pre-submit"], self.root).stdout)
        self.assertFalse(out["proceed"])
        self.assertIn("could not read", out["reason"])

    def test_the_phase_update_also_carries_the_store_root(self):
        # phase.py republishes whenever --root is absent, so a test store would
        # have rendered its fixtures over the real board through this path.
        finish = load("finish")
        seen = []
        finish.call = lambda args, capture=True: seen.append([str(a) for a in args]) or \
            subprocess.CompletedProcess(args, 0, "{}", "")
        os.environ["JOB_APPLICATION_DIR"] = str(self.root)
        try:
            finish.set_phase("acme-pm", "Submitting", "x")
        finally:
            os.environ.pop("JOB_APPLICATION_DIR", None)
        self.assertIn("--root", seen[0])
        self.assertIn(str(self.root), seen[0])


class DocumentOwnershipTests(unittest.TestCase):
    def make(self, names):
        d = tempfile.mkdtemp(prefix="jobby-docs-")
        for n in names:
            (pathlib.Path(d) / n).write_bytes(b"%PDF-1.4")
        return pathlib.Path(d)

    def test_a_second_role_is_not_handed_the_first_roles_documents(self):
        # The folder is per company and three roles can share it. Ranking with
        # no floor returned whichever sorted first, brief reported
        # documentsReady, and the prompt forbids rebuilding, so the run would
        # have uploaded another role's resume.
        folder = self.make([
            "Alex_Example_Google_OperationsSolutions_Resume.pdf",
            "Alex_Example_Google_OperationsSolutions_CoverLetter.pdf",
            "Alex_Example_Google_AndroidAuto_Resume.pdf",
            "Alex_Example_Google_AndroidAuto_CoverLetter.pdf"])
        docs = brief.find_documents(folder, "Product Manager, Android Maps Reach")
        self.assertIsNone(docs["resume"])
        self.assertTrue(docs["ambiguous"])

    def test_the_only_pair_in_a_folder_is_reused_on_a_retry(self):
        # Filenames often carry only the company, so requiring a role match
        # everywhere would force a needless rebuild on every retry.
        folder = self.make(["Alex_Example_Plaid_Resume.pdf",
                            "Alex_Example_Plaid_CoverLetter.pdf"])
        docs = brief.find_documents(folder, "Staff Product Manager, Internal AI")
        self.assertTrue(docs["resume"].endswith("Plaid_Resume.pdf"))
        self.assertFalse(docs["ambiguous"])

    def test_an_exact_generated_pair_wins_over_an_older_generic_pair(self):
        folder = self.make([
            "Alex_Example_IFS_Resume.pdf",
            "Alex_Example_IFS_CoverLetter.pdf",
            "Alex_Example_IFS_Outbound_PM_Resume.pdf",
            "Alex_Example_IFS_Outbound_PM_CoverLetter.pdf",
        ])
        expected = {
            "resumePdf": "Alex_Example_IFS_Outbound_PM_Resume.pdf",
            "coverPdf": "Alex_Example_IFS_Outbound_PM_CoverLetter.pdf",
        }
        docs = brief.find_documents(folder, "Outbound PM", expected)
        self.assertTrue(docs["resume"].endswith(expected["resumePdf"]))
        self.assertTrue(docs["coverLetter"].endswith(expected["coverPdf"]))
        self.assertFalse(docs["ambiguous"])

    def test_one_roles_name_being_a_prefix_of_anothers_does_not_win(self):
        # "Android Maps Reach" against a file named AndroidAuto: the role words
        # overlap, the roles do not. Any scoring rule that picks here is one
        # coincidence away from uploading the wrong resume.
        folder = self.make([
            "Alex_Example_Google_AndroidAuto_Resume.pdf",
            "Alex_Example_Google_Operations_Resume.pdf",
            "Alex_Example_Google_AndroidAuto_CoverLetter.pdf",
            "Alex_Example_Google_Operations_CoverLetter.pdf"])
        docs = brief.find_documents(folder, "Product Manager, Android Maps Reach")
        self.assertIsNone(docs["resume"])
        self.assertTrue(docs["ambiguous"])


class BriefTests(unittest.TestCase):
    def test_an_existing_company_folder_is_reused_whatever_its_case(self):
        # A retry must write into the folder the first attempt made, not create
        # a second one beside it.
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "applications" / "Plaid").mkdir(parents=True)
            os.environ["JOB_APPLICATION_DIR"] = d
            try:
                self.assertEqual(brief.company_folder("plaid").name, "Plaid")
                self.assertEqual(brief.company_folder("PLAID").name, "Plaid")
            finally:
                os.environ.pop("JOB_APPLICATION_DIR", None)

    def test_the_base_resume_is_chosen_by_the_postings_country(self):
        profile = {"resumes": [
            {"key": "us", "appliesTo": ["US"], "default": True, "path": "/us.pdf"},
            {"key": "in", "appliesTo": ["IN"], "default": False, "path": "/in.pdf"}]}
        self.assertEqual(brief.pick_base(profile, "IN")["key"], "in")
        self.assertEqual(brief.pick_base(profile, "US")["key"], "us")

    def test_an_unknown_region_falls_back_to_the_default_resume(self):
        profile = {"resumes": [
            {"key": "us", "appliesTo": ["US"], "default": True, "path": "/us.pdf"},
            {"key": "in", "appliesTo": ["IN"], "default": False, "path": "/in.pdf"}]}
        self.assertEqual(brief.pick_base(profile, "EU")["key"], "us")

    def test_no_resumes_at_all_returns_nothing_rather_than_raising(self):
        self.assertIsNone(brief.pick_base({"resumes": []}, "US"))

    def test_documents_are_found_and_the_render_source_is_not_offered(self):
        with tempfile.TemporaryDirectory() as d:
            folder = pathlib.Path(d)
            (folder / "Alex_Example_Acme_Resume.pdf").write_bytes(b"%PDF-1.4")
            (folder / "Alex_Example_Acme_CoverLetter.pdf").write_bytes(b"%PDF-1.4")
            (folder / ".resume.html").write_text("<html></html>")
            docs = brief.find_documents(folder, "Product Manager")
            self.assertTrue(docs["resume"].endswith("Resume.pdf"))
            self.assertTrue(docs["coverLetter"].endswith("CoverLetter.pdf"))
            self.assertNotIn("html", json.dumps(docs))

    def test_identity_carries_the_profile_links(self):
        # A required LinkedIn field parked the Justworks application on
        # 2026-08-25 while profile.links.linkedin was set the whole time. The
        # prompt sends an agent to IDENTITY, so the links have to be in it.
        profile = {"firstName": "Alex", "links": {
            "linkedin": "https://www.linkedin.com/in/example-person/", "website": ""}}
        identity = brief.run_identity(profile, {"identity": {"phone": "5550001234"}})
        self.assertEqual(identity["links"]["linkedin"],
                         "https://www.linkedin.com/in/example-person/")
        self.assertNotIn("website", identity["links"], "an empty link is not a link")
        self.assertEqual(identity["phone"], "5550001234")

    def test_identity_carries_the_education_the_form_asks_for(self):
        # Greenhouse asks School, Degree and Discipline as required dropdowns.
        # The Workato application parked on the degree one on 2026-08-25 with
        # every one of these fields set in the profile.
        profile = {"education": [{"school": "Example State University",
                                  "degree": "BS Economics",
                                  "start": "2019-08", "end": "2023-05"}]}
        identity = brief.run_identity(profile, {})
        self.assertEqual(identity["education"][0]["school"], "Example State University")

    def test_a_profile_with_no_links_adds_nothing(self):
        self.assertIsNone(brief.run_identity({}, {}))
        self.assertEqual(brief.run_identity({}, {"identity": {"phone": "1"}}), {"phone": "1"})

    def test_an_empty_folder_reports_no_documents(self):
        with tempfile.TemporaryDirectory() as d:
            docs = brief.find_documents(pathlib.Path(d), "Product Manager")
            self.assertIsNone(docs["resume"])
            self.assertIsNone(docs["coverLetter"])


class PromotedFromFoundTests(StoreTemp):
    """A job promoted from the Found board has no extraction, and never did.

    promote.py builds the Watching session out of the job record before
    anything has read the posting, and nothing later filled the gap. On
    2026-08-25 that was 24 of 87 sessions, six of which had reached Apply and
    parked on `invalid_request` one second after their document job was
    created. brief is the one funnel the document contract reads through, so
    the recovery belongs here rather than in each caller.

    These stay offline. The job record names an ATS with no body fetcher, so
    posting_body falls straight through to the stored excerpt.
    """

    def jobs(self, **over):
        record = {
            "id": "acme-pm",
            "company": "Acme",
            "canonicalSource": "lever",
            "canonicalUrl": "https://jobs.lever.co/acme/1234",
            "descriptionExcerpt": "Own the roadmap for the billing platform.",
            "state": "watched",
        }
        record.update(over)
        (self.root / "jobs.json").write_text(json.dumps({"acme-pm": record}))

    def build(self, aid="acme-pm"):
        env = dict(os.environ, JOB_APPLICATION_DIR=str(self.root))
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "brief.py"), "--id", aid],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_a_session_with_no_extraction_recovers_the_posting_from_the_job(self):
        self.session("acme-pm", promotedFrom="found")
        self.jobs()
        out = self.build()
        self.assertIsNotNone(out["jdPath"])
        self.assertEqual(pathlib.Path(out["jdPath"]).read_text(),
                         "Own the roadmap for the billing platform.")

    def test_the_recovered_posting_is_cached_so_a_second_brief_stays_offline(self):
        # The file is the cache. Without it every brief for a promoted
        # application would go back out to the employer's board, and brief runs
        # more than once per application.
        self.session("acme-pm", promotedFrom="found")
        self.jobs()
        first = self.build()
        pathlib.Path(first["jdPath"]).write_text("cached posting body")
        (self.root / "jobs.json").write_text(json.dumps({}))
        second = self.build()
        self.assertEqual(first["jdPath"], second["jdPath"])
        self.assertEqual(pathlib.Path(second["jdPath"]).read_text(),
                         "cached posting body")

    def test_an_extraction_still_wins_over_the_job_record(self):
        self.session("acme-pm", extraction="The posting an agent actually read")
        self.jobs()
        out = self.build()
        self.assertEqual(pathlib.Path(out["jdPath"]).read_text(),
                         "The posting an agent actually read")

    def test_an_application_with_no_job_record_still_reports_no_posting(self):
        # The refusal has to survive. An application with nothing to tailor
        # against must fail the contract, not generate a resume from a title.
        self.session("acme-pm", promotedFrom="found")
        out = self.build()
        self.assertIsNone(out["jdPath"])
        self.assertIsNone(out["extraction"])



class RunIdentityTests(unittest.TestCase):
    """The three fields every application form opens with.

    A per-resume identity carries address, location and phone and never
    carried a name or an email, because those live once at the top of the
    profile. The prompt sends an agent to `identity` for the candidate's
    details, so it found none of the three, checked the answer store, found
    none there either, and parked on the easiest questions on the form. Two
    real applications were parked that way on 2026-08-25 with all three set in
    the profile the whole time.
    """

    PROFILE = {
        "firstName": "Test", "lastName": "Candidate",
        "email": "candidate@example.test",
    }

    def test_the_name_and_email_reach_the_agent(self):
        base = {"identity": {"phone": "5550001111", "location": "Boston, MA"}}
        identity = brief.run_identity(self.PROFILE, base)
        self.assertEqual(identity["firstName"], "Test")
        self.assertEqual(identity["lastName"], "Candidate")
        self.assertEqual(identity["email"], "candidate@example.test")
        # and nothing the resume already carried is lost
        self.assertEqual(identity["phone"], "5550001111")
        self.assertEqual(identity["location"], "Boston, MA")

    def test_a_resume_that_carries_its_own_value_keeps_it(self):
        # The resume being sent is the one that wins; this only fills gaps.
        base = {"identity": {"email": "regional@example.test"}}
        identity = brief.run_identity(self.PROFILE, base)
        self.assertEqual(identity["email"], "regional@example.test")

    def test_a_profile_missing_them_adds_nothing(self):
        identity = brief.run_identity({}, {"identity": {"phone": "5550001111"}})
        self.assertEqual(identity, {"phone": "5550001111"})

    def test_no_base_and_no_profile_is_still_none(self):
        self.assertIsNone(brief.run_identity({}, None))
