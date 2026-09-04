"""Tests for the encrypted document worker's plaintext boundary."""

import base64
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "document_contract", ROOT / "scripts" / "document_contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def bundle():
    pdf = b"%PDF-1.4\nbase"
    return {
        "schemaVersion": 2,
        "applicationId": "acme-pm",
        "provider": "openai",
        "company": "Acme",
        "role": "Product Manager",
        "region": "US",
        "jobDescription": "Build a reliable product roadmap.",
        "profile": {"firstName": "Test", "lastName": "Candidate", "resumes": []},
        "storyFacts": "Shipped a real product.",
        "resumeContent": {
            "banner": "PRODUCT MANAGER | PRIVATE WORDING",
            "summary": "Wording the Profile page saved, not the shipped template.",
            "areasOfExpertise": ["Product Analytics", "Customer Discovery"],
            "technical": [{"label": "Languages", "items": "Python, TypeScript"}],
            "roles": [{"lede": "Payments", "bullets": ["Moved 75 customers."]}],
        },
        "writingSamples": ["A short professional note."],
        "baseResume": {
            "name": "base-resume.pdf",
            "sha256": __import__("hashlib").sha256(pdf).hexdigest(),
            "contentBase64": base64.b64encode(pdf).decode(),
        },
        "outputs": {
            "resumeHtml": ".Test_Acme_PM.resume.html",
            "resumePdf": "Test_Acme_PM_Resume.pdf",
            "coverHtml": ".Test_Acme_PM.cover.html",
            "coverPdf": "Test_Acme_PM_CoverLetter.pdf",
        },
    }


class BundleTests(unittest.TestCase):
    def test_company_and_role_alone_collide_three_ways(self):
        """The reason the id tag exists, stated as the cases it covers.

        All 205 applications in the store produce 205 distinct names, so this
        contract has never collided in production. That is a property of the
        corpus, not of the rule -- and the rule had already failed once, before
        it owned naming: three Glance applications to three different roles all
        claim `Sahil_Sapra_Glance_Resume.pdf`, so KV holds one object for three
        tailored resumes and nothing can say which application received which.
        """
        profile = {"firstName": "Sahil", "lastName": "Sapra"}
        name = lambda role: contract.output_names(  # noqa: E731
            profile, "Acme", role)["resumePdf"]
        long_a = ("Senior Product Manager, Platform Infrastructure and "
                  "Developer Experience A")
        long_b = long_a[:-1] + "B"
        for label, first, second in (
                ("punctuation is dropped by the slug",
                 "Senior PM, AI", "Senior PM - AI"),
                ("the role is capped at 55 characters", long_a, long_b),
                ("the same title twice", "Product Manager", "Product Manager")):
            with self.subTest(label):
                self.assertEqual(name(first), name(second),
                                 "this case is expected to collide without the "
                                 "id tag; if it no longer does, the tag may be "
                                 "carrying less than this test claims")

    def test_the_id_tag_separates_every_one_of_them(self):
        profile = {"firstName": "Sahil", "lastName": "Sapra"}
        made = [contract.output_names(profile, "Acme", "Product Manager", aid)
                for aid in ("glance-product-analyst-ii", "dc9c8137b240216e",
                            "04f960acfe932252")]
        for key in ("resumePdf", "coverPdf", "resumeHtml", "coverHtml"):
            names = [m[key] for m in made]
            self.assertEqual(len(set(names)), len(names),
                             f"{key} is not unique across applications that "
                             f"agree on company and role: {names}")

    def test_the_tag_is_stable_and_absent_without_an_id(self):
        """Stable, because a name that changes between runs orphans the file
        written under the last one. Absent without an id, because
        `r2_artifacts` still has to resolve everything written before the tag
        existed.
        """
        profile = {"firstName": "Sahil", "lastName": "Sapra"}
        once = contract.output_names(profile, "Acme", "PM", "abc123")["resumePdf"]
        twice = contract.output_names(profile, "Acme", "PM", "abc123")["resumePdf"]
        self.assertEqual(once, twice)
        bare = contract.output_names(profile, "Acme", "PM")["resumePdf"]
        self.assertEqual(bare, "Sahil_Sapra_Acme_PM_Resume.pdf")
        self.assertNotEqual(bare, once)

    def test_output_names_always_include_the_role(self):
        names = contract.output_names(
            {"firstName": "Test", "lastName": "Candidate"},
            "Acme & Co", "Staff Product Manager")
        self.assertIn("Staff_Product_Manager", names["resumePdf"])
        self.assertNotIn(" ", names["resumePdf"])

    def test_reduced_profile_removes_street_and_postal_code(self):
        profile = {"firstName": "T", "lastName": "C", "email": "t@example.test"}
        selected = {"identity": {
            "phone": "5550001111", "location": "Boston, MA 02110",
            "taxId": "must-never-leave-the-mac",
            "fullAddress": {"street": "1 Private Way", "city": "Boston",
                            "state": "MA", "postalCode": "02110", "country": "US"},
        }}
        reduced = contract._reduced_profile(profile, selected)
        packed = json.dumps(reduced)
        self.assertNotIn("Private Way", packed)
        self.assertNotIn("02110", packed)
        self.assertNotIn("taxId", packed)
        self.assertIn("Boston", packed)

    def test_materialize_writes_private_endpoint_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = contract.materialize(bundle(), tmp)
            self.assertEqual(pathlib.Path(runtime["baseResumePath"]).read_bytes()[:4], b"%PDF")
            self.assertEqual(pathlib.Path(runtime["taskRoot"]).stat().st_mode & 0o777, 0o700)
            self.assertEqual(pathlib.Path(runtime["storeRoot"], "profile.json").stat().st_mode & 0o777,
                             0o600)

    def test_materialize_carries_the_profile_wording_into_the_task_store(self):
        """The defect this file did not catch, measured on 2026-08-28.

        `materialize` wrote `profile.json` and `story-facts.txt` and stopped, so
        the worker's task store never held the wording and every resume fell
        back to `templates/resume-content.json`. The old
        `test_materialize_writes_private_endpoint_files` passed throughout,
        because it asserted the files that were written rather than the ones a
        render needs.
        """
        with tempfile.TemporaryDirectory() as tmp:
            runtime = contract.materialize(bundle(), tmp)
            written = pathlib.Path(runtime["storeRoot"], "resume-content.json")
            self.assertTrue(written.is_file())
            self.assertEqual(written.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(written.read_text()),
                             bundle()["resumeContent"])
            self.assertEqual(runtime["resumeContentPath"], str(written))

    def test_the_wording_is_a_file_beside_the_profile_not_a_runtime_field(self):
        """Same rule the profile and the story facts follow.

        task.json is written to disk unencrypted for the length of the run.
        Content that has a file goes in the file; duplicating it into the
        runtime would put the same paragraphs in two places and make the next
        person guess which one a reader is looking at.
        """
        with tempfile.TemporaryDirectory() as tmp:
            runtime = contract.materialize(bundle(), tmp)
            self.assertNotIn("resumeContent", runtime)
            packed = json.dumps(runtime)
            self.assertNotIn("not the shipped template", packed)

    def test_a_bundle_with_no_resume_wording_is_refused(self):
        task = bundle()
        del task["resumeContent"]
        with self.assertRaises(contract.DocumentContractError):
            contract.validate_bundle(task)

    def test_resume_wording_of_the_wrong_shape_is_refused(self):
        """Named per field, because "invalid bundle" sends nobody anywhere."""
        cases = {
            "banner": 7,
            "summary": None,
            "areasOfExpertise": ["fine", 3],
            "technical": [{"label": "Languages"}],
            "roles": [],
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                task = bundle()
                task["resumeContent"][field] = value
                with self.assertRaises(contract.DocumentContractError) as caught:
                    contract.validate_bundle(task)
                self.assertIn("base resume wording", str(caught.exception))

    def test_wording_shape_does_not_count_list_lengths(self):
        """`store._same_shape` counts slots; this endpoint has nothing to count
        against, and `resume_build.render` is the check that holds the template.
        """
        task = bundle()
        task["resumeContent"]["areasOfExpertise"] = ["only one"]
        task["resumeContent"]["roles"][0]["bullets"] = ["a", "b", "c"]
        self.assertIsNone(
            contract._resume_content_problem(task["resumeContent"]))

    def test_tampered_base_resume_is_rejected(self):
        task = bundle()
        task["baseResume"]["contentBase64"] = base64.b64encode(b"%PDF-changed").decode()
        with self.assertRaises(contract.DocumentContractError):
            contract.validate_bundle(task)


class ResultTests(unittest.TestCase):
    def make_result(self):
        files = {}
        for key, name in bundle()["outputs"].items():
            data = b"%PDF-1.4\nresult" if key.endswith("Pdf") else b"<!doctype html>result"
            files[key] = {
                "name": name,
                "sha256": __import__("hashlib").sha256(data).hexdigest(),
                "contentBase64": base64.b64encode(data).decode(),
            }
        return {"schemaVersion": 2, "applicationId": "acme-pm",
                "provider": "openai", "files": files}

    def test_install_is_atomic_and_returns_both_pdf_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = contract.install_result(self.make_result(), tmp, "acme-pm")
            self.assertTrue(pathlib.Path(written["resumePdf"]).is_file())
            self.assertTrue(pathlib.Path(written["coverPdf"]).is_file())
            self.assertEqual(pathlib.Path(written["coverPdf"]).stat().st_mode & 0o777, 0o600)

    def test_a_result_for_another_application_is_rejected(self):
        result = self.make_result()
        result["applicationId"] = "other-role"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(contract.DocumentContractError):
                contract.install_result(result, tmp, "acme-pm")

    def test_unsafe_result_filename_is_rejected(self):
        result = self.make_result()
        result["files"]["resumePdf"]["name"] = "../resume.pdf"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(contract.DocumentContractError):
                contract.install_result(result, tmp, "acme-pm")

    def test_all_files_are_validated_before_any_are_installed(self):
        result = self.make_result()
        result["files"]["coverPdf"]["contentBase64"] = base64.b64encode(
            b"not a PDF"
        ).decode()
        result["files"]["coverPdf"]["sha256"] = __import__("hashlib").sha256(
            b"not a PDF"
        ).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(contract.DocumentContractError):
                contract.install_result(result, tmp, "acme-pm")
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])
