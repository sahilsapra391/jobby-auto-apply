"""A staged document does not outlive the run that staged it.

A file chooser hands the browser a path on a filesystem, so the two PDFs have
to exist locally at the moment of submission -- R2 cannot be the source there.
What does not have to happen is them staying afterwards, and until now they did:
151 documents were sitting in the tree with R2 already holding every one.

The guarantee is structural rather than remembered. Staging registers itself,
`main` discards in a `finally`, and `_run` returns from a dozen places -- the
runs whose documents matter most being the ones that took an early return.
"""

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import browser  # noqa: E402


class DiscardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.original = browser.artifact_root_for
        browser.artifact_root_for = lambda slot: self.root / slot
        browser._STAGED.clear()

    def tearDown(self):
        browser.artifact_root_for = self.original
        browser._STAGED.clear()
        self.tmp.cleanup()

    def staged(self, application_id="app123", slot="apply-1"):
        target = self.root / slot / "uploads" / application_id
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

    def test_staging_registers_itself_so_the_discard_needs_no_caller(self):
        browser._STAGED.append(("apply-1", "app123"))
        self.staged()
        results = browser.discard_staged()
        self.assertEqual(len(results), 1)
        self.assertEqual(browser._STAGED, [])

    def test_discard_staged_clears_every_registration(self):
        for n in (1, 2):
            self.staged(application_id=f"app{n}")
            browser._STAGED.append(("apply-1", f"app{n}"))
        removed = [f for r in browser.discard_staged() for f in r["removed"]]
        self.assertEqual(len(removed), 4)


class WiringTests(unittest.TestCase):
    def test_run_apply_discards_in_a_finally(self):
        """Not at the end of the happy path. `_run` returns from a dozen
        places, and a cleanup on the happy path misses exactly the runs whose
        documents matter most."""
        source = (ROOT / "scripts" / "run_apply.py").read_text()
        main = source.split("def main(argv=None):", 1)[1]
        self.assertIn("finally:", main)
        self.assertIn("browser.discard_staged()", main)
        self.assertLess(main.index("finally:"), main.index("discard_staged"))

    def test_staging_registers_only_after_the_copies_exist(self):
        """A failed staging must not schedule a delete of another run's
        directory."""
        source = (ROOT / "scripts" / "browser.py").read_text()
        body = source.split("def stage_application_uploads", 1)[1]
        self.assertLess(body.index("_STAGED.append"), body.index("except Exception"))


if __name__ == "__main__":
    unittest.main()
