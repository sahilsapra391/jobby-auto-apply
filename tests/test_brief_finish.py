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
        out = json.loads(run_finish([
            "--id", "acme-pm", "--completed",
            "--confirmation", "Your application was successfully submitted.",
            "--resume", "/abs/where/Sahil_Sapra_Acme_Resume.pdf",
            "--cover", "/abs/where/Sahil_Sapra_Acme_CoverLetter.pdf"], self.root).stdout)
        self.assertEqual(out["resumeVariant"], "Sahil_Sapra_Acme_Resume.pdf")
        self.assertEqual(out["coverLetter"], "Sahil_Sapra_Acme_CoverLetter.pdf")
        event = [e for e in self.events() if e["event"] == "completed"][0]
        self.assertNotIn("/", event["resumeVariant"])
        self.assertEqual(event["confirmation"],
                         "Your application was successfully submitted.")


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


class PublishGuardTests(unittest.TestCase):
    def test_a_redirected_store_never_publishes(self):
        # Publishing a board built from a test store would overwrite the real
        # one, which has happened once and looked exactly like every
        # application disappearing.
        finish = load("finish")
        seen = {}
        def fake_call(args, capture=True):
            seen["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")
        finish.call = fake_call
        os.environ["JOB_APPLICATION_DIR"] = "/tmp/whatever"
        try:
            finish.sync_board()
        finally:
            os.environ.pop("JOB_APPLICATION_DIR", None)
        self.assertIn("--no-publish", [str(a) for a in seen["args"]])


class BriefTests(unittest.TestCase):
    def test_an_existing_company_folder_is_reused_whatever_its_case(self):
        # A retry must write into the folder the first attempt made, not create
        # a second one beside it.
        found = brief.company_folder("plaid")
        self.assertEqual(found.name, "Plaid")

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
            (folder / "Sahil_Sapra_Acme_Resume.pdf").write_bytes(b"%PDF-1.4")
            (folder / "Sahil_Sapra_Acme_CoverLetter.pdf").write_bytes(b"%PDF-1.4")
            (folder / ".resume.html").write_text("<html></html>")
            docs = brief.find_documents(folder, "Product Manager")
            self.assertTrue(docs["resume"].endswith("Resume.pdf"))
            self.assertTrue(docs["coverLetter"].endswith("CoverLetter.pdf"))
            self.assertNotIn("html", json.dumps(docs))

    def test_an_empty_folder_reports_no_documents(self):
        with tempfile.TemporaryDirectory() as d:
            docs = brief.find_documents(pathlib.Path(d), "Product Manager")
            self.assertIsNone(docs["resume"])
            self.assertIsNone(docs["coverLetter"])


if __name__ == "__main__":
    unittest.main()
