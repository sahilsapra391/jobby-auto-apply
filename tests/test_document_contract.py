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
        "schemaVersion": 1,
        "applicationId": "acme-pm",
        "provider": "openai",
        "company": "Acme",
        "role": "Product Manager",
        "region": "US",
        "jobDescription": "Build a reliable product roadmap.",
        "profile": {"firstName": "Test", "lastName": "Candidate", "resumes": []},
        "storyFacts": "Shipped a real product.",
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
        return {"schemaVersion": 1, "applicationId": "acme-pm",
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


if __name__ == "__main__":
    unittest.main()
