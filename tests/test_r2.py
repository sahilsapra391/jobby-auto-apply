"""Class 1 artifacts: where they are keyed, what gets uploaded, what is proven.

The expensive mistakes this guards against are all the same shape. An artifact
that quietly does not arrive. An upload that reports success and wrote nothing.
A verification pass that cannot see a corrupted object. A retry that spends the
operation budget re-uploading what is already there.

Run from the repo root: python3 -m unittest discover -s tests
"""

import contextlib
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


r2 = _load("r2")
r2_artifacts = _load("r2_artifacts")
r2_sync = _load("r2_sync")
STORE = ROOT / "scripts" / "store.py"


def store(root, *args, stdin=None):
    return subprocess.run(
        [sys.executable, str(STORE), "--root", str(root), *args],
        input=stdin, text=True, capture_output=True, check=True)


class FakeClient:
    """An R2 that keeps objects in a dict and counts what it was asked to do."""

    def __init__(self, bucket="jobby-artifacts", user_id="account-1"):
        self.bucket = bucket
        self.user_id = user_id
        self.objects = {}
        self.puts = 0
        self.heads = 0
        self.gets = 0
        self.lists = 0
        self.deletes = 0
        self.fail_on = None

    def head(self, key):
        self.heads += 1
        item = self.objects.get(key)
        if item is None:
            return None
        return {"bytes": len(item["body"]), "sha256": item["sha256"],
                "contentType": item["contentType"],
                "etag": hashlib.md5(item["body"]).hexdigest()}

    def get(self, key):
        self.gets += 1
        item = self.objects.get(key)
        return None if item is None else item["body"]

    def list_keys(self, prefix):
        self.lists += 1
        return sorted(k for k in self.objects if k.startswith(prefix))

    def delete(self, key):
        self.deletes += 1
        self.objects.pop(key, None)
        return True

    def put(self, key, body, content_type="application/octet-stream", sha256=None):
        if self.fail_on and self.fail_on in key:
            raise r2.R2Error(f"R2 PUT {key} failed: 403 forbidden")
        self.puts += 1
        self.objects[key] = {"body": body, "contentType": content_type,
                             "sha256": sha256 or hashlib.sha256(body).hexdigest()}
        return {"key": key, "bytes": len(body), "sha256": self.objects[key]["sha256"]}


class SigningTest(unittest.TestCase):
    """The presigned URL, pinned against a fixed clock and fixed credentials."""

    def setUp(self):
        import datetime
        self.now = datetime.datetime(2026, 8, 25, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.client = r2.Client("acct", "jobby-artifacts", "AKID", "SECRET")

    def test_the_url_addresses_the_bucket_and_the_key(self):
        url = self.client.presign_get("u/account-1/app/bc83/resume.pdf", now=self.now)
        self.assertTrue(url.startswith(
            "https://acct.r2.cloudflarestorage.com/jobby-artifacts/"
            "u/account-1/app/bc83/resume.pdf?"))

    def test_it_carries_an_expiry_and_a_signature(self):
        url = self.client.presign_get("u/account-1/app/bc83/resume.pdf", 300, now=self.now)
        self.assertIn("X-Amz-Expires=300", url)
        self.assertRegex(url, r"X-Amz-Signature=[0-9a-f]{64}$")

    def test_the_secret_is_never_in_the_url(self):
        url = self.client.presign_get("u/account-1/app/bc83/resume.pdf", now=self.now)
        self.assertNotIn("SECRET", url)
        self.assertIn("AKID", url)

    def test_changing_the_key_changes_the_signature(self):
        one = self.client.presign_get("u/account-1/app/a/resume.pdf", now=self.now)
        two = self.client.presign_get("u/account-1/app/b/resume.pdf", now=self.now)
        self.assertNotEqual(one.rsplit("=", 1)[1], two.rsplit("=", 1)[1])

    def test_response_headers_are_inside_the_signature(self):
        plain = self.client.presign_get("u/a/k.pdf", now=self.now)
        named = self.client.presign_get(
            "u/a/k.pdf", now=self.now,
            response_headers={"response-content-disposition": 'inline; filename="x.pdf"'})
        self.assertNotEqual(plain.rsplit("=", 1)[1], named.rsplit("=", 1)[1])
        self.assertIn("response-content-disposition=", named)

    def test_a_space_in_a_key_is_encoded_not_dropped(self):
        url = self.client.presign_get("u/account-1/unfiled/Keka HR/CV.pdf", now=self.now)
        self.assertIn("/unfiled/Keka%20HR/CV.pdf?", url)

    @contextlib.contextmanager
    def no_local_config(self):
        """Read no config.env. Otherwise this machine's real credentials answer.

        Without this the two tests below pass on a machine with no R2 set up and
        fail on the one that has it, which makes the suite's result a fact about
        the laptop rather than about the code.
        """
        original = r2.toolpath.settings
        r2.toolpath.settings = lambda: {}
        try:
            yield
        finally:
            r2.toolpath.settings = original

    def test_the_bucket_defaults_and_the_account_falls_back_to_the_queue_id(self):
        with self.no_local_config():
            values = r2.settings({"CF_ACCOUNT_ID": "abc", "R2_ACCESS_KEY_ID": "k",
                                  "R2_SECRET_ACCESS_KEY": "s"})
        self.assertEqual(values["R2_ACCOUNT_ID"], "abc")
        self.assertEqual(values["R2_BUCKET"], "jobby-artifacts")

    def test_a_missing_credential_is_named_rather_than_returning_none(self):
        # A None here would be read as "nothing to upload", which is how an
        # artifact fails to be stored anywhere while the run reports success.
        with self.no_local_config(), self.assertRaises(r2.R2Error) as caught:
            r2.client({"CF_ACCOUNT_ID": "abc", "R2_ACCESS_KEY_ID": "",
                       "R2_SECRET_ACCESS_KEY": ""})
        self.assertIn("R2_ACCESS_KEY_ID", str(caught.exception))

    def test_configured_is_false_with_no_credentials_anywhere(self):
        with self.no_local_config():
            self.assertFalse(r2.configured({"CF_ACCOUNT_ID": "abc"}))
            self.assertTrue(r2.configured({"CF_ACCOUNT_ID": "abc",
                                           "R2_ACCESS_KEY_ID": "k",
                                           "R2_SECRET_ACCESS_KEY": "s"}))


class LayoutTest(unittest.TestCase):
    """Which local file becomes which key, over a tree shaped like the real one."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        cls.store_root = cls.tmp / "store"
        store(cls.store_root, "init")

        cls.aid = "bc839847c93d9e46"
        session = {"applicationId": cls.aid, "company": "Acme Corp",
                   "role": "Product Manager", "status": "active",
                   "resumeVariant": "Sahil_Sapra_Acme_PM_Resume.pdf",
                   "coverLetter": "Sahil_Sapra_Acme_PM_CoverLetter.pdf"}
        payload = cls.tmp / "s.json"
        payload.write_text(json.dumps(session), encoding="utf-8")
        store(cls.store_root, "session-save", "--id", cls.aid, "--input", str(payload))

        cls.apps = cls.tmp / "applications"
        folder = cls.apps / "Acme Corp"
        folder.mkdir(parents=True)
        (folder / "Sahil_Sapra_Acme_PM_Resume.pdf").write_bytes(b"%PDF resume")
        (folder / "Sahil_Sapra_Acme_PM_CoverLetter.pdf").write_bytes(b"%PDF cover")
        (folder / f"jd-{cls.aid}.txt").write_text("the posting", encoding="utf-8")
        (folder / f"questions-{cls.aid}.json").write_text('{"q":"a"}', encoding="utf-8")
        (folder / f"confirm-{cls.aid}.png").write_bytes(b"\x89PNG capture")
        (folder / "posting.txt").write_text("scratch", encoding="utf-8")
        (folder / ".DS_Store").write_bytes(b"junk")
        (folder / "notes.md").write_text("not an artifact", encoding="utf-8")

        cls.plan = r2_artifacts.collect(str(cls.store_root), "account-1", str(cls.apps))
        cls.keys = {a["name"]: a["key"] for a in cls.plan["artifacts"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_generated_documents_are_keyed_by_application_and_kind(self):
        self.assertEqual(self.keys["Sahil_Sapra_Acme_PM_Resume.pdf"],
                         f"u/account-1/app/{self.aid}/resume.pdf")
        self.assertEqual(self.keys["Sahil_Sapra_Acme_PM_CoverLetter.pdf"],
                         f"u/account-1/app/{self.aid}/cover.pdf")

    def test_the_job_description_and_answers_ride_with_their_application(self):
        self.assertEqual(self.keys[f"jd-{self.aid}.txt"],
                         f"u/account-1/app/{self.aid}/jd.txt")
        self.assertEqual(self.keys[f"questions-{self.aid}.json"],
                         f"u/account-1/app/{self.aid}/answers.json")

    def test_a_confirmation_capture_lands_beside_them(self):
        self.assertEqual(self.keys[f"confirm-{self.aid}.png"],
                         f"u/account-1/app/{self.aid}/confirmation.png")

    def test_an_unattributable_file_keeps_its_company_and_name(self):
        # Not filed under an invented application id. A stranger's id on
        # someone's document is the same mistake as a stranger's logo.
        self.assertEqual(self.keys["posting.txt"],
                         "u/account-1/unfiled/Acme Corp/posting.txt")

    def test_every_key_is_under_the_one_prefix_a_deletion_would_sweep(self):
        for artifact in self.plan["artifacts"]:
            self.assertTrue(artifact["key"].startswith("u/account-1/"), artifact["key"])

    def test_junk_and_unknown_types_are_not_artifacts(self):
        self.assertNotIn(".DS_Store", self.keys)
        self.assertNotIn("notes.md", self.keys)
        self.assertEqual([s["path"].endswith("notes.md") for s in self.plan["skipped"]],
                         [True])

    def test_every_artifact_carries_its_hash_and_size(self):
        for artifact in self.plan["artifacts"]:
            body = pathlib.Path(artifact["path"]).read_bytes()
            self.assertEqual(artifact["sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(artifact["bytes"], len(body))

    def test_a_pdf_is_served_as_a_pdf(self):
        kinds = {a["name"]: a["contentType"] for a in self.plan["artifacts"]}
        self.assertEqual(kinds["Sahil_Sapra_Acme_PM_Resume.pdf"], "application/pdf")
        self.assertEqual(kinds[f"confirm-{self.aid}.png"], "image/png")

    def test_the_plan_is_stable_across_runs(self):
        again = r2_artifacts.collect(str(self.store_root), "account-1", str(self.apps))
        self.assertEqual([a["key"] for a in again["artifacts"]],
                         [a["key"] for a in self.plan["artifacts"]])

    def test_a_second_user_id_moves_the_whole_tree(self):
        other = r2_artifacts.collect(str(self.store_root), "account-2", str(self.apps))
        for artifact in other["artifacts"]:
            self.assertTrue(artifact["key"].startswith("u/account-2/"))


class DocIndexTest(unittest.TestCase):
    """The filename index, which is what keeps published /doc/ links working."""

    def test_a_name_that_occurs_once_is_indexed_to_its_key(self):
        index, ambiguous = r2_sync.doc_index([
            {"name": "A_Resume.pdf", "key": "u/account-1/app/a/resume.pdf",
             "sha256": "x", "bytes": 1, "contentType": "application/pdf"},
        ])
        self.assertEqual(index["A_Resume.pdf"]["key"], "u/account-1/app/a/resume.pdf")
        self.assertEqual(ambiguous, [])

    def test_a_name_that_occurs_twice_is_left_out_rather_than_guessed(self):
        # posting.txt exists in a dozen company folders and means a different
        # posting in each. Picking one serves one company's document under
        # another company's link.
        index, ambiguous = r2_sync.doc_index([
            {"name": "posting.txt", "key": "u/account-1/unfiled/A/posting.txt",
             "sha256": "x", "bytes": 1, "contentType": "text/plain"},
            {"name": "posting.txt", "key": "u/account-1/unfiled/B/posting.txt",
             "sha256": "y", "bytes": 2, "contentType": "text/plain"},
        ])
        self.assertNotIn("posting.txt", index)
        self.assertEqual(ambiguous, ["posting.txt"])


class SyncTest(unittest.TestCase):
    """Upload behaviour: idempotency, and what a failure does."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store_root = self.tmp / "store"
        store(self.store_root, "init")
        aid = "aa11bb22cc33dd44"
        session = {"applicationId": aid, "company": "Acme", "role": "PM",
                   "status": "active", "resumeVariant": "R.pdf", "coverLetter": "C.pdf"}
        payload = self.tmp / "s.json"
        payload.write_text(json.dumps(session), encoding="utf-8")
        store(self.store_root, "session-save", "--id", aid, "--input", str(payload))
        self.aid = aid
        self.apps = self.tmp / "applications" / "Acme"
        self.apps.mkdir(parents=True)
        (self.apps / "R.pdf").write_bytes(b"%PDF resume v1")
        (self.apps / "C.pdf").write_bytes(b"%PDF cover v1")
        self.apps_dir = str(self.tmp / "applications")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sync(self, client, **kw):
        return r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                            apply=True, client=client, **kw)

    def test_a_dry_run_writes_nothing_and_reports_what_would_move(self):
        client = FakeClient()
        result = r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                              apply=False, client=client)
        self.assertEqual(result["wouldUpload"], 2)
        self.assertEqual(result["wouldUploadBytes"], len(b"%PDF resume v1") + len(b"%PDF cover v1"))
        self.assertEqual(client.puts, 0)
        self.assertFalse(r2_sync.manifest_path(str(self.store_root)).exists())

    def test_the_first_run_uploads_everything(self):
        client = FakeClient()
        result = self.run_sync(client)
        self.assertEqual(result["uploaded"], 2)
        self.assertEqual(client.puts, 2)
        self.assertIn(f"u/account-1/app/{self.aid}/resume.pdf", client.objects)

    def test_a_second_run_over_an_unchanged_tree_costs_nothing(self):
        client = FakeClient()
        self.run_sync(client)
        client.puts = client.heads = 0
        result = self.run_sync(client)
        self.assertEqual(result["uploaded"], 0)
        self.assertEqual(result["skippedByManifest"], 2)
        self.assertEqual(client.puts, 0)
        self.assertEqual(client.heads, 0)

    def test_a_retry_after_a_lost_manifest_matches_rather_than_rewriting(self):
        # The same sha is already there, so the expensive class-A write is not
        # spent again and no duplicate object appears.
        client = FakeClient()
        self.run_sync(client)
        r2_sync.manifest_path(str(self.store_root)).unlink()
        client.puts = 0
        result = self.run_sync(client)
        self.assertEqual(result["matchedRemote"], 2)
        self.assertEqual(result["uploaded"], 0)
        self.assertEqual(client.puts, 0)
        self.assertEqual(len(client.objects), 2)

    def test_a_changed_file_is_uploaded_again_to_the_same_key(self):
        client = FakeClient()
        self.run_sync(client)
        (self.apps / "R.pdf").write_bytes(b"%PDF resume v2 which is longer")
        result = self.run_sync(client)
        self.assertEqual(result["uploaded"], 1)
        self.assertEqual(len(client.objects), 2)
        self.assertEqual(client.objects[f"u/account-1/app/{self.aid}/resume.pdf"]["body"],
                         b"%PDF resume v2 which is longer")

    def test_an_upload_failure_raises_rather_than_reporting_success(self):
        client = FakeClient()
        client.fail_on = "cover.pdf"
        with self.assertRaises(r2.R2Error):
            self.run_sync(client)

    def test_what_did_upload_before_the_failure_is_remembered(self):
        # Artifacts go up in sorted path order, so C.pdf lands and R.pdf is the
        # one that dies. The point is that the retry finishes rather than
        # starting over, which matters when the tree is 621 objects.
        client = FakeClient()
        client.fail_on = "resume.pdf"
        with self.assertRaises(r2.R2Error):
            self.run_sync(client)
        manifest = r2_sync.load_manifest(str(self.store_root))
        self.assertIn(f"u/account-1/app/{self.aid}/cover.pdf", manifest["objects"])
        self.assertNotIn(f"u/account-1/app/{self.aid}/resume.pdf", manifest["objects"])
        # And the retry finishes the job rather than starting over.
        client.fail_on = None
        client.puts = 0
        result = self.run_sync(client)
        self.assertEqual(result["uploaded"], 1)

    def test_the_manifest_is_owner_only(self):
        self.run_sync(FakeClient())
        path = r2_sync.manifest_path(str(self.store_root))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_sync_returns_the_index_the_board_will_serve_from(self):
        result = self.run_sync(FakeClient())
        self.assertEqual(result["index"]["R.pdf"]["key"],
                         f"u/account-1/app/{self.aid}/resume.pdf")


class VerifyTest(unittest.TestCase):
    """Verification has to catch a corrupted object, or it is not verification."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store_root = self.tmp / "store"
        store(self.store_root, "init")
        aid = "aa11bb22cc33dd44"
        payload = self.tmp / "s.json"
        payload.write_text(json.dumps({"applicationId": aid, "company": "Acme",
                                       "role": "PM", "status": "active",
                                       "resumeVariant": "R.pdf"}), encoding="utf-8")
        store(self.store_root, "session-save", "--id", aid, "--input", str(payload))
        self.aid = aid
        apps = self.tmp / "applications" / "Acme"
        apps.mkdir(parents=True)
        (apps / "R.pdf").write_bytes(b"%PDF the real resume")
        self.apps_dir = str(self.tmp / "applications")
        self.client = FakeClient()
        r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                     apply=True, client=self.client)
        self.key = f"u/account-1/app/{aid}/resume.pdf"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def verify(self, deep=True):
        return r2_sync.verify(str(self.store_root), "account-1", self.apps_dir,
                              deep=deep, client=self.client)

    def test_a_healthy_upload_verifies(self):
        result = self.verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["present"], 1)
        self.assertEqual(result["missing"], 0)

    def test_a_missing_object_is_reported(self):
        del self.client.objects[self.key]
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], 1)

    def test_a_deliberately_corrupted_body_is_caught(self):
        # Same length, same stamped metadata, different bytes. This is the case
        # a HEAD-only pass reports as healthy, which is why deep is the default.
        item = self.client.objects[self.key]
        item["body"] = b"%PDF the FAKE resume"
        self.assertEqual(len(item["body"]), len(b"%PDF the real resume"))
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["problems"][0]["problem"], "body sha256 differs")

    def test_the_quick_pass_catches_it_too_through_the_etag(self):
        self.client.objects[self.key]["body"] = b"%PDF the FAKE resume"
        result = self.verify(deep=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["problems"][0]["problem"], "ETag differs from local MD5")

    def test_a_truncated_object_is_caught_on_size_alone(self):
        self.client.objects[self.key]["body"] = b"%PDF"
        result = self.verify(deep=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["problems"][0]["problem"], "size differs")


class ListingTest(unittest.TestCase):
    """Reading a bucket listing, which is the only XML this project parses."""

    def test_an_ampersand_in_a_company_folder_survives_the_listing(self):
        # One real folder is "Dun & Bradstreet" and the listing returns its key
        # XML-escaped. Taking that literally reported three objects that exist
        # as missing and three that do not exist as orphans, which is the exact
        # opposite of what the tool is for.
        body = ("<ListBucketResult>"
                "<Contents><Key>u/account-1/unfiled/Dun &amp; Bradstreet/.cover.html</Key></Contents>"
                "<Contents><Key>u/account-1/unfiled/A &lt;b&gt;/x.pdf</Key></Contents>"
                "<Contents><Key>u/account-1/unfiled/O&apos;Reilly/y.pdf</Key></Contents>"
                "</ListBucketResult>")
        self.assertEqual(r2._between(body, "<Key>", "</Key>"), [
            "u/account-1/unfiled/Dun & Bradstreet/.cover.html",
            "u/account-1/unfiled/A <b>/x.pdf",
            "u/account-1/unfiled/O'Reilly/y.pdf",
        ])

    def test_an_escaped_entity_is_not_unescaped_twice(self):
        body = "<Key>a&amp;amp;b</Key>"
        self.assertEqual(r2._between(body, "<Key>", "</Key>"), ["a&amp;b"])

    def test_an_empty_listing_is_empty_rather_than_an_error(self):
        self.assertEqual(r2._between("<ListBucketResult/>", "<Key>", "</Key>"), [])


class PruneTest(unittest.TestCase):
    """Deleting what no local artifact maps to, and nothing else.

    Needed because a mapping rule can change. Broadening the sidecar pattern to
    accept slug application ids moved thirteen job descriptions to a new key,
    and without a sweep the old ones sit there forever.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store_root = self.tmp / "store"
        store(self.store_root, "init")
        self.aid = "aa11bb22cc33dd44"
        payload = self.tmp / "s.json"
        payload.write_text(json.dumps({"applicationId": self.aid, "company": "Acme",
                                       "role": "PM", "status": "active",
                                       "resumeVariant": "R.pdf"}), encoding="utf-8")
        store(self.store_root, "session-save", "--id", self.aid, "--input", str(payload))
        apps = self.tmp / "applications" / "Acme"
        apps.mkdir(parents=True)
        (apps / "R.pdf").write_bytes(b"%PDF resume")
        self.apps_dir = str(self.tmp / "applications")
        self.client = FakeClient()
        r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                     apply=True, client=self.client)
        self.live = f"u/account-1/app/{self.aid}/resume.pdf"
        self.stale = "u/account-1/unfiled/Acme/R.pdf"
        self.client.objects[self.stale] = {
            "body": b"%PDF resume", "contentType": "application/pdf",
            "sha256": hashlib.sha256(b"%PDF resume").hexdigest()}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def prune(self, apply):
        return r2_sync.prune(str(self.store_root), "account-1", self.apps_dir,
                             apply=apply, client=self.client)

    def test_a_dry_run_names_the_orphan_and_deletes_nothing(self):
        result = self.prune(apply=False)
        self.assertEqual(result["orphans"], 1)
        self.assertEqual(result["keys"], [self.stale])
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(self.client.deletes, 0)
        self.assertIn(self.stale, self.client.objects)

    def test_applying_removes_the_orphan_and_leaves_the_live_object(self):
        result = self.prune(apply=True)
        self.assertEqual(result["deleted"], 1)
        self.assertNotIn(self.stale, self.client.objects)
        self.assertIn(self.live, self.client.objects)

    def test_a_pruned_key_leaves_the_manifest_too(self):
        # Otherwise the manifest claims an object is uploaded that is not there,
        # and the next sync skips re-creating it.
        manifest = r2_sync.load_manifest(str(self.store_root))
        manifest["objects"][self.stale] = {"sha256": "x", "bytes": 1,
                                           "name": "R.pdf", "uploadedAt": "now"}
        r2_sync.save_manifest(manifest, str(self.store_root))
        self.prune(apply=True)
        self.assertNotIn(self.stale,
                         r2_sync.load_manifest(str(self.store_root))["objects"])

    def test_nothing_to_prune_is_not_a_failure(self):
        self.client.objects.pop(self.stale)
        result = self.prune(apply=True)
        self.assertEqual(result["orphans"], 0)
        self.assertEqual(self.client.deletes, 0)

    def test_it_only_ever_looks_under_this_user_prefix(self):
        self.client.objects["u/account-2/app/x/resume.pdf"] = {
            "body": b"x", "contentType": "application/pdf", "sha256": "y"}
        self.prune(apply=True)
        self.assertIn("u/account-2/app/x/resume.pdf", self.client.objects)


class SidecarAttributionTest(unittest.TestCase):
    """Application ids are mostly slugs, not hex, and the patterns must know it."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store_root = self.tmp / "store"
        store(self.store_root, "init")
        self.aid = "actively-ai-agent-product-manager"
        payload = self.tmp / "s.json"
        payload.write_text(json.dumps({"applicationId": self.aid, "company": "Actively AI",
                                       "role": "PM", "status": "active"}), encoding="utf-8")
        store(self.store_root, "session-save", "--id", self.aid, "--input", str(payload))
        folder = self.tmp / "applications" / "Actively AI"
        folder.mkdir(parents=True)
        (folder / f"jd-{self.aid}.txt").write_text("posting", encoding="utf-8")
        (folder / "jd-some-application-nobody-has.txt").write_text("orphan", encoding="utf-8")
        plan = r2_artifacts.collect(str(self.store_root), "account-1",
                                    str(self.tmp / "applications"))
        self.keys = {a["name"]: a["key"] for a in plan["artifacts"]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_slug_application_id_is_attributed(self):
        # Measured on 2026-08-25: only 37 of 187 ids are the 16-hex kind, and a
        # hex-shaped pattern left 13 of 39 job descriptions unattributable.
        self.assertEqual(self.keys[f"jd-{self.aid}.txt"],
                         f"u/account-1/app/{self.aid}/jd.txt")

    def test_an_id_the_store_does_not_know_is_not_invented(self):
        self.assertEqual(self.keys["jd-some-application-nobody-has.txt"],
                         "u/account-1/unfiled/Actively AI/jd-some-application-nobody-has.txt")


class KeyTiebreakTest(unittest.TestCase):
    """When two files claim one key, the winner is chosen, not alphabetical.

    answers-<id>.json is a captured record; questions-<id>.json is the bare
    list it was salvaged from. Both map to app/<id>/answers.json. Sorted path
    order happened to pick the record, and happening to be right is not being
    right: a peer session measuring the live bucket pointed out that a re-sync
    while the record was briefly absent would hand the key to the list, and the
    card's modal would fetch an array where it expects an object. It fails
    closed and silently, which is the worst way to find out.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store_root = self.tmp / "store"
        store(self.store_root, "init")
        self.aid = "aa11bb22cc33dd44"
        payload = self.tmp / "s.json"
        payload.write_text(json.dumps({"applicationId": self.aid, "company": "Acme",
                                       "role": "PM", "status": "active"}), encoding="utf-8")
        store(self.store_root, "session-save", "--id", self.aid, "--input", str(payload))
        self.folder = self.tmp / "applications" / "Acme"
        self.folder.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self):
        got = r2_artifacts.collect(str(self.store_root), "account-1",
                                   str(self.tmp / "applications"))
        return {a["name"]: a["key"] for a in got["artifacts"]}

    def test_the_record_takes_the_key_and_the_bare_list_does_not(self):
        (self.folder / f"answers-{self.aid}.json").write_text(
            json.dumps({"capture": "questions-only", "fields": [{"q": "why us"}]}))
        (self.folder / f"questions-{self.aid}.json").write_text(json.dumps(["why us"]))
        keys = self.plan()
        self.assertEqual(keys[f"answers-{self.aid}.json"],
                         f"u/account-1/app/{self.aid}/answers.json")
        self.assertEqual(keys[f"questions-{self.aid}.json"],
                         "u/account-1/unfiled/Acme/questions-" + self.aid + ".json")

    def test_the_bare_list_alone_still_gets_the_key(self):
        # Nothing better exists, so it is the best record of that application.
        (self.folder / f"questions-{self.aid}.json").write_text(json.dumps(["why us"]))
        self.assertEqual(self.plan()[f"questions-{self.aid}.json"],
                         f"u/account-1/app/{self.aid}/answers.json")

    def test_the_winner_does_not_depend_on_which_is_seen_first(self):
        """The one that has to bite, and the first version of it did not.

        It renamed the loser to aaa-questions-<id>.json so it would sort first.
        That name matches no SIDECARS pattern, so it claims no key, so there was
        no collision to resolve and the test asserted the right outcome about
        the wrong situation. Green under a _rank that returns 0 for everything.
        jobby-auto-apply-a7 hit the identical trap writing the mirror of this
        test and described it; the description is what sent me back to mine.

        Within one folder the case cannot be built: among the prefixes
        r2_artifacts recognises, "answers-" sorts before all of them, so the
        record is always visited first. The directory is the only lever, so the
        question list goes in a company folder that sorts earlier and rglob
        reaches it first. That makes this the displacement case: the list holds
        the key, the record arrives second and takes it.
        """
        early = self.tmp / "applications" / "AAA Staffing"
        early.mkdir(parents=True)
        (early / f"questions-{self.aid}.json").write_text(json.dumps(["why us"]))
        (self.folder / f"answers-{self.aid}.json").write_text(
            json.dumps({"capture": "questions-only", "fields": [{"q": "why us"}]}))

        visited = [p.name for p in sorted((self.tmp / "applications").rglob("*"))
                   if p.is_file()]
        self.assertLess(visited.index(f"questions-{self.aid}.json"),
                        visited.index(f"answers-{self.aid}.json"),
                        "the loser has to be visited first or this proves nothing")

        keys = self.plan()
        self.assertEqual(keys[f"answers-{self.aid}.json"],
                         f"u/account-1/app/{self.aid}/answers.json")
        self.assertEqual(keys[f"questions-{self.aid}.json"],
                         f"u/account-1/unfiled/AAA Staffing/questions-{self.aid}.json")

    def test_rank_orders_a_record_above_the_list_it_came_from(self):
        # Direct, so a reordering of FILENAME_PREFERENCE fails here rather than
        # only in whichever collision test happens to notice.
        self.assertLess(r2_artifacts._rank(f"answers-{self.aid}.json"),
                        r2_artifacts._rank(f"questions-{self.aid}.json"))
        self.assertLess(r2_artifacts._rank(f"questions-{self.aid}.json"),
                        r2_artifacts._rank("something-nobody-listed.json"))

    def test_an_unknown_prefix_never_displaces_a_known_record(self):
        (self.folder / f"answers-{self.aid}.json").write_text(json.dumps({"fields": []}))
        (self.folder / f"aardvark-{self.aid}.json").write_text(json.dumps([]))
        keys = self.plan()
        self.assertEqual(keys[f"answers-{self.aid}.json"],
                         f"u/account-1/app/{self.aid}/answers.json")
        self.assertTrue(keys[f"aardvark-{self.aid}.json"].startswith("u/account-1/unfiled/"))

    def test_the_demoted_file_is_still_stored_somewhere(self):
        # Losing the key must never mean losing the file.
        (self.folder / f"answers-{self.aid}.json").write_text(json.dumps({"fields": []}))
        (self.folder / f"questions-{self.aid}.json").write_text(json.dumps([]))
        keys = self.plan()
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys.values())), 2, "two files, two distinct keys")


class EvictionTest(unittest.TestCase):
    """Local files go only when R2 provably has them and the work is finished.

    The apply path needs a real file: the form agent uploads a PDF to the
    employer by path and the browser firewall pins that path by device, inode,
    size and sha256. So R2 cannot be the source for a file handed to a chooser,
    and eviction waits for the application to reach a terminal outcome.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store_root = self.tmp / "store"
        store(self.store_root, "init")
        self.done, self.live = "aa11bb22cc33dd44", "bb22cc33dd44ee55"
        for aid, company in ((self.done, "Doneco"), (self.live, "Liveco")):
            payload = self.tmp / f"{aid}.json"
            payload.write_text(json.dumps({"applicationId": aid, "company": company,
                                           "role": "PM", "status": "active",
                                           "resumeVariant": f"{company}_Resume.pdf"}))
            store(self.store_root, "session-save", "--id", aid, "--input", str(payload))
        event = self.tmp / "e.json"
        event.write_text(json.dumps({"applicationId": self.done, "event": "completed",
                                     "at": "2026-08-27T00:00:00Z", "company": "Doneco",
                                     "role": "PM"}))
        store(self.store_root, "history-append", "--input", str(event))
        self.apps = self.tmp / "applications"
        for company in ("Doneco", "Liveco"):
            (self.apps / company).mkdir(parents=True)
            (self.apps / company / f"{company}_Resume.pdf").write_bytes(
                f"%PDF {company}".encode())
        self.apps_dir = str(self.apps)
        self.client = FakeClient()
        r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                     apply=True, client=self.client)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_evict(self, apply):
        return r2_sync.evict(str(self.store_root), "account-1", self.apps_dir,
                             apply=apply, client=self.client)

    def path(self, company):
        return self.apps / company / f"{company}_Resume.pdf"

    def test_a_finished_application_is_evictable_and_a_live_one_is_not(self):
        result = self.run_evict(apply=False)
        self.assertEqual(result["removable"], 1)
        self.assertIn("the application has not finished", result["keptReasons"])

    def test_a_dry_run_removes_nothing(self):
        self.run_evict(apply=False)
        self.assertTrue(self.path("Doneco").exists())

    def test_applying_removes_the_finished_one_only(self):
        result = self.run_evict(apply=True)
        self.assertEqual(result["removed"], 1)
        self.assertFalse(self.path("Doneco").exists())
        self.assertTrue(self.path("Liveco").exists(),
                        "a live application still needs its PDF on disk to upload")

    def test_nothing_is_removed_when_r2_does_not_have_it(self):
        self.client.objects.clear()
        result = self.run_evict(apply=True)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(self.path("Doneco").exists())
        self.assertIn("no object in R2", result["keptReasons"])

    def test_nothing_is_removed_when_r2_holds_different_bytes(self):
        # The check re-downloads and hashes rather than trusting the manifest,
        # because the manifest records what we believe we sent.
        key = f"u/account-1/app/{self.done}/resume.pdf"
        self.client.objects[key]["body"] = b"%PDF something else entirely"
        result = self.run_evict(apply=True)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(self.path("Doneco").exists())

    def test_the_index_survives_eviction_so_the_board_keeps_serving(self):
        """The trap this had to avoid: file gone, link dead, object fine.

        The index used to be derived from the tree, so removing a local file
        would drop its entry on the next publish and turn a working board link
        into a 404 while the object sat safely in R2.
        """
        self.run_evict(apply=True)
        plan = r2_artifacts.collect(str(self.store_root), "account-1", self.apps_dir)
        index, _ = r2_sync.doc_index_from_manifest(str(self.store_root), plan["artifacts"])
        self.assertEqual(index["Doneco_Resume.pdf"]["key"],
                         f"u/account-1/app/{self.done}/resume.pdf")
        self.assertNotIn("Doneco_Resume.pdf",
                         {a["name"] for a in plan["artifacts"]},
                         "the file really is gone from disk")

    def test_an_evicted_artifact_comes_back_byte_identical(self):
        original = self.path("Doneco").read_bytes()
        self.run_evict(apply=True)
        back = self.tmp / "restored.pdf"
        r2_sync.materialise("Doneco_Resume.pdf", back, str(self.store_root),
                            client=self.client)
        self.assertEqual(back.read_bytes(), original)

    def test_materialise_refuses_a_name_it_does_not_know(self):
        with self.assertRaises(r2.R2Error):
            r2_sync.materialise("never-stored.pdf", self.tmp / "x.pdf",
                                str(self.store_root), client=self.client)

    def test_materialise_refuses_content_that_does_not_hash_right(self):
        key = f"u/account-1/app/{self.done}/resume.pdf"
        self.client.objects[key]["body"] = b"%PDF tampered"
        with self.assertRaises(r2.R2Error):
            r2_sync.materialise("Doneco_Resume.pdf", self.tmp / "x.pdf",
                                str(self.store_root), client=self.client)

    def test_pushing_one_application_uploads_only_that_one(self):
        # Both files change; only the named application's is pushed. Changing
        # the content rather than clearing the bucket, because the manifest is
        # what suppresses a re-upload and clearing the bucket alone leaves it
        # believing the object is there -- which is the real behaviour, and the
        # first version of this test tripped on it.
        self.path("Doneco").write_bytes(b"%PDF Doneco revised")
        self.path("Liveco").write_bytes(b"%PDF Liveco revised")
        self.client.puts = 0
        out = r2_sync.push_application(self.done, str(self.store_root),
                                       apps_dir=self.apps_dir, client=self.client)
        self.assertEqual(out["objects"], 1)
        self.assertEqual(out["uploaded"], 1)
        self.assertEqual(self.client.puts, 1, "the live application must not be touched")
        self.assertEqual(self.client.objects[f"u/account-1/app/{self.done}/resume.pdf"]["body"],
                         b"%PDF Doneco revised")

    def test_pushing_an_application_with_nothing_on_disk_is_not_an_error(self):
        out = r2_sync.push_application("no-such-application", str(self.store_root),
                                       apps_dir=self.apps_dir, client=self.client)
        self.assertEqual(out["objects"], 0)


class PreferenceSurvivesEvictionTest(unittest.TestCase):
    """Two correct operations whose composition destroyed content in R2.

    answers-<id>.json and questions-<id>.json both claim app/<id>/answers.json,
    and the collector prefers the record. Evict the record -- correctly, it is
    safely in R2 -- and the question list is the only claimant left, so the next
    sync sees a changed sha for that key and writes a bare array over the
    record. Neither step is wrong on its own.

    It happened on the live bucket: two records overwritten, 2,655 bytes to 838
    and 1,118 to 136, found by jobby-auto-apply-a7 sampling R2 rather than by
    any test here. Both were recovered from the pre-eviction snapshot.

    The preference was deliberate within one scan from #96. This makes it
    durable across scans, because once the winner is off disk the manifest is
    the only memory that it ever won.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.store_root = self.tmp / "store"
        store(self.store_root, "init")
        self.aid = "aa11bb22cc33dd44"
        payload = self.tmp / "s.json"
        payload.write_text(json.dumps({"applicationId": self.aid, "company": "Acme",
                                       "role": "PM", "status": "active"}))
        store(self.store_root, "session-save", "--id", self.aid, "--input", str(payload))
        self.folder = self.tmp / "applications" / "Acme"
        self.folder.mkdir(parents=True)
        self.record = self.folder / f"answers-{self.aid}.json"
        self.questions = self.folder / f"questions-{self.aid}.json"
        self.record.write_text(json.dumps(
            {"capture": "questions-only", "fields": [{"q": f"q{n}"} for n in range(18)]}))
        self.questions.write_text(json.dumps([f"q{n}" for n in range(18)]))
        self.apps_dir = str(self.tmp / "applications")
        self.key = f"u/account-1/app/{self.aid}/answers.json"
        self.client = FakeClient()
        r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                     apply=True, client=self.client)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stored(self):
        return json.loads(self.client.objects[self.key]["body"])

    def test_the_record_wins_the_key_while_both_are_on_disk(self):
        self.assertIsInstance(self.stored(), dict)

    def test_the_question_list_cannot_take_the_key_once_the_record_is_gone(self):
        """The regression. Without the guard this overwrites and loses data."""
        self.record.unlink()
        result = r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                              apply=True, client=self.client)
        self.assertEqual(result.get("refusedDowngrade"), 1)
        self.assertIsInstance(self.stored(), dict, "R2 must still hold the record")
        self.assertEqual(len(self.stored()["fields"]), 18)

    def test_the_refusal_says_which_file_was_kept_and_which_was_turned_away(self):
        self.record.unlink()
        result = r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                              apply=True, client=self.client)
        self.assertEqual(result["downgrades"], [{
            "key": self.key, "stored": f"answers-{self.aid}.json",
            "refused": f"questions-{self.aid}.json"}])

    def test_a_better_file_still_displaces_a_worse_stored_one(self):
        """The guard must not freeze the key against genuine improvement.

        This is the repair path: after the damage, the manifest recorded the
        question list, and putting the record back had to be allowed.
        """
        self.record.unlink()
        r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                     apply=True, client=self.client)
        self.questions.unlink()
        self.record.write_text(json.dumps({"capture": "full", "fields": [{"q": "back"}]}))
        result = r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                              apply=True, client=self.client)
        self.assertEqual(result["uploaded"], 1)
        self.assertEqual(self.stored()["capture"], "full")

    def test_the_same_file_updating_itself_is_never_a_downgrade(self):
        self.record.write_text(json.dumps({"capture": "full", "fields": [{"q": "revised"}]}))
        result = r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                              apply=True, client=self.client)
        self.assertEqual(result.get("refusedDowngrade", 0), 0)
        self.assertEqual(self.stored()["capture"], "full")

    def test_the_guard_is_reachable_at_all(self):
        """It was not, for one commit, and a green run hid it.

        The first version called _rank unqualified. That NameError only fires on
        the branch the guard exists for, so a sync where no name differed passed
        cleanly and proved nothing. Same shape as every other green-and-
        meaningless check this week.
        """
        self.record.unlink()
        result = r2_sync.sync(str(self.store_root), "account-1", self.apps_dir,
                              apply=True, client=self.client)
        self.assertGreater(result.get("refusedDowngrade", 0), 0,
                           "the guard's own branch has to execute in some test")
