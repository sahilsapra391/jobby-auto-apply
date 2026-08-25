"""The published board and the deployment that renders it cannot silently drift.

The two halves are deployed by different routes: state is published from the
user's machine every few minutes, code only when main is merged. Between them
there is a window where new state meets an older renderer.

The envelope already carried a rendererSchemaVersion for this, but it was the
literal 1 in the publisher, in the KV reader and in the Vercel function, and it
had never been incremented. It could only ever agree with itself, so a genuine
mismatch fell through to dashboard.build, threw, and surfaced as a bare 502
reading "The board state could not be rendered" — which says neither what is
wrong nor that it clears on its own.

A hash cannot be forgotten the way a constant can.

Run from the repo root: python3 -m unittest discover -s tests
"""

import hashlib
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


publish = load("publish", ROOT / "scripts" / "publish.py")
page = load("page_fn", ROOT / "deploy" / "api" / "page.py")


class FingerprintAgreementTests(unittest.TestCase):
    def test_both_sides_hash_the_same_tree_to_the_same_value(self):
        """The publisher hashes scripts/, the function hashes its own bundle."""
        self.assertEqual(publish.renderer_fingerprint(), page._renderer_fingerprint())

    def test_the_publisher_covers_every_deployed_module(self):
        deployed = sorted(p.name for p in (ROOT / "deploy" / "renderer").glob("*.py")
                          if p.name != "__init__.py")
        self.assertEqual(sorted(publish.renderer_files()), deployed)

    def test_changing_any_renderer_module_changes_the_fingerprint(self):
        before = publish.renderer_fingerprint()
        for name in publish.renderer_files():
            path = ROOT / "scripts" / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\n# probe\n")
                with self.subTest(module=name):
                    self.assertNotEqual(publish.renderer_fingerprint(), before)
            finally:
                path.write_bytes(original)
        self.assertEqual(publish.renderer_fingerprint(), before)


class MismatchReportingTests(unittest.TestCase):
    def test_matching_fingerprints_render(self):
        self.assertIsNone(page._renderer_mismatch(page._renderer_fingerprint()))

    def test_a_different_fingerprint_is_reported_rather_than_thrown(self):
        found = page._renderer_mismatch("0000deadbeef0000")
        self.assertIsNotNone(found)
        self.assertEqual(found["publishedBy"], "0000deadbeef0000")
        self.assertEqual(found["deployed"], page._renderer_fingerprint())

    def test_state_without_a_fingerprint_still_renders(self):
        """Older publishers wrote no such field. Do not block the board on a
        value that is still rolling out."""
        self.assertIsNone(page._renderer_mismatch(""))


if __name__ == "__main__":
    unittest.main()
