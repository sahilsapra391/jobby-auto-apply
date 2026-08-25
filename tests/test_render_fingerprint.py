"""The published board and the deployed code cannot drift apart silently.

The board is HTML rendered locally and stored in KV; the Vercel function serves
it. So there are two halves that must agree, and nothing forces them to. The
watcher republishes when its fingerprint changes, and that fingerprint named
three modules while the deployed bundle carries twelve. A change to premium_ui,
company_logos, match_score, jobs_store or store therefore altered every card on
the page and republished nothing: KV kept serving the previous render against
freshly deployed code, and no test, log line or health check reported it.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watch = load("watch")
sync = load("sync_vercel_renderer")


class RenderFingerprintTests(unittest.TestCase):
    def test_the_fingerprint_covers_every_deployed_module(self):
        """One source of truth, so the list cannot be right in only one place."""
        self.assertEqual(tuple(watch.render_modules()), tuple(sync.FILES))

    def test_every_deployed_module_actually_exists(self):
        for name in watch.render_modules():
            with self.subTest(module=name):
                self.assertTrue((ROOT / "scripts" / name).exists(), name)

    def test_changing_any_of_them_changes_the_fingerprint(self):
        """Not just the renderers. This is the case that was missed."""
        before = watch.render_version()
        for name in watch.render_modules():
            path = ROOT / "scripts" / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\n# fingerprint probe\n")
                with self.subTest(module=name):
                    self.assertNotEqual(watch.render_version(), before,
                                        f"a change to {name} would not republish")
            finally:
                path.write_bytes(original)
        self.assertEqual(watch.render_version(), before, "probe left a file changed")

    def test_the_fingerprint_is_stable_when_nothing_changes(self):
        self.assertEqual(watch.render_version(), watch.render_version())


if __name__ == "__main__":
    unittest.main()
