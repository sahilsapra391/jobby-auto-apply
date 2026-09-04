"""A staged document does not outlive the run that staged it.

A file chooser hands the browser a path on a filesystem, so the two PDFs have
to exist locally at the moment of submission -- R2 cannot be the source there.
What does not have to happen is them staying afterwards, and until now they did:
151 documents were sitting in the tree with R2 already holding every one.

The guarantee is structural rather than remembered. Staging registers itself,
`main` discards in a `finally`, and `_run` returns from a dozen places -- the
runs whose documents matter most being the ones that took an early return.
"""

import hashlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import browser  # noqa: E402


class DiscardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        # ARTIFACTS, not a lambda over artifact_root_for. The guard in
        # test_tests_do_not_write_production_state parses for a redirect of the
        # constant, and a monkeypatched function is invisible to it: this
        # module was isolated in fact and unprotected in principle, so dropping
        # the patch in a later refactor would have gone unnoticed.
        self.patch = mock.patch.object(browser, "ARTIFACTS", self.root)
        self.patch.start()
        browser._STAGED.clear()

    def tearDown(self):
        self.patch.stop()
        browser._STAGED.clear()
        self.tmp.cleanup()

    def staged(self, application_id="app123", slot="apply-1"):
        target = browser.artifact_root_for(slot) / "uploads" / application_id
        target.mkdir(parents=True)
        (target / "resume.pdf").write_bytes(b"%PDF-1.4 resume")
        (target / "cover.pdf").write_bytes(b"%PDF-1.4 cover")
        return target

    def test_the_documents_go(self):
        target = self.staged()
        out = browser.discard_application_uploads("apply-1", "app123")
        self.assertEqual(sorted(f["name"] for f in out["removed"]),
                         ["cover.pdf", "resume.pdf"])
        self.assertEqual(list(target.glob("*.pdf")), [])

    def test_the_firewall_record_stays(self):
        """activity.json lives in the same directory and is what tells a
        capability failure apart from a real park. Deleting it would trade a
        duplicate PDF for the ability to say why a run stopped."""
        target = self.staged()
        (target / "activity.json").write_text('{"browser_find": 1}')
        browser.discard_application_uploads("apply-1", "app123")
        self.assertTrue((target / "activity.json").exists())

    def test_only_named_document_types_are_deleted(self):
        """An allowlist, not "everything that is not activity.json". The next
        file written here should have to be named before anything removes it."""
        target = self.staged()
        (target / "notes.sqlite").write_bytes(b"x")
        browser.discard_application_uploads("apply-1", "app123")
        self.assertTrue((target / "notes.sqlite").exists())

    def test_a_missing_directory_is_not_an_error(self):
        out = browser.discard_application_uploads("apply-1", "never-staged")
        self.assertEqual(out["removed"], [])
        self.assertNotIn("error", out)

    def test_it_never_raises_even_on_a_hostile_directory(self):
        """Losing a staged duplicate is not worth failing a run over; R2 is the
        record. It reports instead, so a directory that will not go away is
        visible rather than silently accumulating."""
        link = self.root / "apply-2" / "uploads"
        link.mkdir(parents=True)
        (link / "app9").symlink_to("/nowhere")
        out = browser.discard_application_uploads("apply-2", "app9")
        self.assertEqual(out["removed"], [])

    def test_discard_staged_drains_every_registration(self):
        """The registry holds resolved paths, so this appends paths."""
        for n in (1, 2):
            browser._STAGED.append(self.staged(application_id=f"app{n}"))
        removed = [f for r in browser.discard_staged() for f in r["removed"]]
        self.assertEqual(len(removed), 4)
        self.assertEqual(browser._STAGED, [])


class WiringTests(unittest.TestCase):
    """Behavioural, not source-text.

    The first versions of these asserted on `index()` positions in the source.
    An agent moved `_STAGED.append` to the first line of the try -- exactly
    what the test claimed to prevent -- and the module still passed 21 of 21.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(browser, "ARTIFACTS",
                                       pathlib.Path(self.tmp.name))
        self.patch.start()
        browser._STAGED.clear()

    def tearDown(self):
        self.patch.stop()
        browser._STAGED.clear()
        self.tmp.cleanup()

    def artifacts(self, name="r.pdf"):
        source = pathlib.Path(self.tmp.name) / name
        source.write_bytes(b"%PDF-1.4 x")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return {"path": str(source), "size": source.stat().st_size, "sha256": digest}

    def test_staging_then_discarding_removes_the_files_it_staged(self):
        """The chain nothing exercised: stage, register, discard."""
        art = {"resume": self.artifacts("resume.pdf"),
               "coverLetter": self.artifacts("cover.pdf")}
        browser.stage_application_uploads("apply-1", "app1", art)
        staged = browser.artifact_root_for("apply-1") / "uploads" / "app1"
        self.assertEqual(len(list(staged.glob("*.pdf"))), 2)
        self.assertEqual(len(browser._STAGED), 1)
        browser.discard_staged()
        self.assertEqual(list(staged.glob("*.pdf")), [])
        self.assertEqual(browser._STAGED, [])

    def test_a_failed_staging_registers_nothing(self):
        """A registration made before the copies land would schedule a delete
        of a directory this run does not own."""
        with self.assertRaises((ValueError, OSError, TypeError)):
            browser.stage_application_uploads("apply-1", "app2", {"resume": {}})
        self.assertEqual(browser._STAGED, [])

    def test_the_registry_holds_a_resolved_path_not_a_slot_and_id(self):
        """So a staging done under one ARTIFACTS cannot be discarded against
        another later in the same process, which deleted a real document."""
        art = {"resume": self.artifacts("resume.pdf"),
               "coverLetter": self.artifacts("cover.pdf")}
        browser.stage_application_uploads("apply-1", "app3", art)
        entry = browser._STAGED[0]
        self.assertIsInstance(entry, pathlib.Path)
        # resolve() on both sides: macOS reports /var as /private/var, and the
        # staging path is already resolved.
        self.assertTrue(
            str(entry).startswith(str(pathlib.Path(self.tmp.name).resolve())),
            f"{entry} is not under the redirected root")

    def test_a_later_redirect_cannot_move_where_the_discard_deletes(self):
        art = {"resume": self.artifacts("resume.pdf"),
               "coverLetter": self.artifacts("cover.pdf")}
        browser.stage_application_uploads("apply-1", "app4", art)
        staged = browser.artifact_root_for("apply-1") / "uploads" / "app4"
        elsewhere = tempfile.TemporaryDirectory()
        try:
            with mock.patch.object(browser, "ARTIFACTS", pathlib.Path(elsewhere.name)):
                browser.discard_staged()
            self.assertEqual(list(staged.glob("*.pdf")), [],
                             "the discard followed the redirect instead of the path")
        finally:
            elsewhere.cleanup()
