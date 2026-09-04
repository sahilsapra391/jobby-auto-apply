"""A retried application whose documents were evicted gets them back.

Eviction removes local artifacts once an application reaches a terminal
outcome, and drop.py --restore makes that outcome reversible. On 2026-08-28
application 6c3824319c68c547 was restored, retried, and refused with "the
tailored resume and cover letter are not both ready": the tick trusted the
document job's ready state, the runner checked the disk, both PDFs sat safely
in R2, and the click was consumed. The store already knew how to bring a file
back (`--get`); the apply path just never asked.

These cases cover the two halves: r2_sync.materialise_application restores
what the manifest attributes to one application, and
run_apply.restage_evicted_documents calls it before the refusal stands.
"""

import contextlib
import hashlib
import importlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import r2_sync  # noqa: E402
import run_apply  # noqa: E402


class FakeClient:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.gets = 0

    def get(self, key):
        self.gets += 1
        return self.objects.get(key)


def write_manifest(root, objects):
    path = r2_sync.manifest_path(str(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schemaVersion": 1, "objects": objects}),
                    encoding="utf-8")


def record(body, name):
    return {"name": name, "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body), "contentType": "application/pdf"}


class MaterialiseApplicationTests(unittest.TestCase):
    def setUp(self):
        # Other test modules in the suite replace sys.modules["r2"] and
        # ["r2_sync"] with their own copies as they load, so class identity
        # for R2Error depends on import order. Resolve r2_sync live and take
        # R2Error from the r2 module it actually raises from.
        self.r2_sync = importlib.import_module("r2_sync")
        self.error = self.r2_sync.r2.R2Error
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "store"
        self.folder = self.tmp / "applications" / "Acme"
        self.aid = "aa11bb22cc33dd44"
        self.resume = b"%PDF resume"
        self.cover = b"%PDF cover"
        write_manifest(self.root, {
            f"u/account-1/app/{self.aid}/resume.pdf": record(self.resume, "Acme_Resume.pdf"),
            f"u/account-1/app/{self.aid}/cover.pdf": record(self.cover, "Acme_CoverLetter.pdf"),
            f"u/account-1/app/{self.aid}/jd.txt": record(b"posting", "jd.txt"),
            "u/account-1/app/other0000000000/resume.pdf": record(b"%PDF other", "Other_Resume.pdf"),
        })
        self.client = FakeClient({
            f"u/account-1/app/{self.aid}/resume.pdf": self.resume,
            f"u/account-1/app/{self.aid}/cover.pdf": self.cover,
            f"u/account-1/app/{self.aid}/jd.txt": b"posting",
            "u/account-1/app/other0000000000/resume.pdf": b"%PDF other",
        })

    def restage(self):
        return self.r2_sync.materialise_application(
            self.aid, self.folder, str(self.root), "account-1", self.client)

    def test_restores_every_attributed_file_that_is_missing(self):
        restored = self.restage()
        self.assertEqual(sorted(item["name"] for item in restored),
                         ["Acme_CoverLetter.pdf", "Acme_Resume.pdf", "jd.txt"])
        self.assertEqual((self.folder / "Acme_Resume.pdf").read_bytes(), self.resume)
        self.assertEqual((self.folder / "Acme_CoverLetter.pdf").read_bytes(), self.cover)
        self.assertFalse((self.folder / "Other_Resume.pdf").exists(),
                         "another application's artifact must not be touched")

    def test_files_already_on_disk_are_left_alone(self):
        self.folder.mkdir(parents=True)
        (self.folder / "Acme_Resume.pdf").write_bytes(b"%PDF edited by hand")
        restored = self.restage()
        self.assertEqual(sorted(item["name"] for item in restored),
                         ["Acme_CoverLetter.pdf", "jd.txt"])
        self.assertEqual((self.folder / "Acme_Resume.pdf").read_bytes(),
                         b"%PDF edited by hand")

    def test_an_application_with_nothing_attributed_costs_no_network(self):
        out = self.r2_sync.materialise_application(
            "ffffffffffffffff", self.folder, str(self.root), "account-1",
            self.client)
        self.assertEqual(out, [])
        self.assertEqual(self.client.gets, 0)

    def test_a_changed_hash_refuses_rather_than_staging_corruption(self):
        self.client.objects[f"u/account-1/app/{self.aid}/resume.pdf"] = b"%PDF tampered"
        with self.assertRaises(self.error):
            self.restage()
        self.assertFalse((self.folder / "Acme_Resume.pdf").exists())

    def test_an_indexed_key_with_no_object_behind_it_refuses(self):
        del self.client.objects[f"u/account-1/app/{self.aid}/cover.pdf"]
        with self.assertRaises(self.error):
            self.restage()


class RunApplyRestageTests(unittest.TestCase):
    def setUp(self):
        self.summary = {"folder": "/tmp/apps/Acme", "documentsReady": False}
        # The helper resolves r2 and r2_sync through sys.modules when it runs,
        # and other test modules in this suite replace those entries as they
        # load. Patching the copies imported at the top of this file lands on
        # objects the helper never sees, so resolve the live ones now.
        self.r2 = importlib.import_module("r2")
        self.r2_sync = importlib.import_module("r2_sync")

    def call(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = run_apply.restage_evicted_documents("aa11bb22cc33dd44",
                                                      self.summary)
        return out, err.getvalue()

    def test_a_brief_with_no_folder_restages_nothing(self):
        self.assertEqual(
            run_apply.restage_evicted_documents("aa11bb22cc33dd44", {}), [])

    def test_unconfigured_r2_stays_quiet_and_the_refusal_stands(self):
        with mock.patch.object(self.r2, "configured", return_value=False):
            out, err = self.call()
        self.assertEqual(out, [])
        self.assertEqual(err, "")

    def test_a_restage_failure_is_reported_not_raised(self):
        with mock.patch.object(self.r2, "configured", return_value=True), \
             mock.patch.object(self.r2_sync, "materialise_application",
                               side_effect=self.r2.R2Error("bucket said no")):
            out, err = self.call()
        self.assertEqual(out, [])
        self.assertIn("restage_failed", err)
        self.assertIn("bucket said no", err)

    def test_restored_files_are_named_in_the_run_log(self):
        restored = [{"name": "Acme_Resume.pdf"}, {"name": "Acme_CoverLetter.pdf"}]
        with mock.patch.object(self.r2, "configured", return_value=True), \
             mock.patch.object(self.r2_sync, "materialise_application",
                               return_value=restored) as called:
            out, err = self.call()
        self.assertEqual(out, restored)
        called.assert_called_once_with("aa11bb22cc33dd44", "/tmp/apps/Acme")
        self.assertIn("documents_restaged", err)
        self.assertIn("Acme_CoverLetter.pdf", err)
