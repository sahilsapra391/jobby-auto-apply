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

A hash cannot be forgotten the way a constant can. But it also cannot be made
to agree everywhere: there is one jobby:board-state and many deployments, and
every open PR gets a preview whose renderer hashes differently from main's.
The laptop can only stamp the branch it is standing on. So the hash reports,
and only a render that actually failed acts on the report.

Run from the repo root: python3 -m unittest discover -s tests
"""

import base64
import hashlib
import importlib.util
import os
import pathlib
import unittest
from unittest import mock

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


class Recorder(page.handler):
    """The handler with its socket removed.

    Overriding _send captures the decision — which status, which headers —
    without standing up http.server just to read it back out again.
    """

    def __init__(self, authorized=True):
        self.headers = {"Authorization": "Basic " + AUTH} if authorized else {}
        self.command = "GET"
        self.captured = None

    def _send(self, status, body, content_type, extra=None):
        self.captured = (status, body, dict(extra or {}))


STATE = {
    "profile": {"firstName": "A"}, "sessions": [], "history": [], "roles": {},
    "meta": {}, "jobs": {}, "searchConfig": None, "providers": {},
}
AUTH = base64.b64encode(b"jobby:secret").decode()
FOREIGN = "0000deadbeef0000"


class DriftDoesNotBlockAWorkingBoardTests(unittest.TestCase):
    """A preview must draw, or previews are worthless.

    Refusing to render on a hash mismatch 503s every preview of every PR that
    touches a renderer module, permanently, because the laptop stamps main and
    the preview is not main. Reviewing a UI change on its preview is the whole
    reason the preview exists.
    """

    def run_page(self, fingerprint, build):
        recorder = Recorder()
        with mock.patch.dict(os.environ, {"DASHBOARD_PASSWORD": "secret"}, clear=False), \
             mock.patch.object(page, "_state", return_value=(STATE, "2026-08-25T00:00:00Z", fingerprint)), \
             mock.patch.object(page.dashboard, "build", build):
            recorder._page()
        return recorder.captured

    def test_a_preview_whose_renderer_drifted_still_serves_the_board(self):
        status, body, extra = self.run_page(FOREIGN, lambda *a: "<html>board</html>")
        self.assertEqual(status, 200)
        self.assertIn(b"board", body)

    def test_both_fingerprints_ride_out_on_every_rendered_page(self):
        _, _, extra = self.run_page(FOREIGN, lambda *a: "<html>board</html>")
        self.assertEqual(extra["X-Jobby-State-Fingerprint"], FOREIGN)
        self.assertEqual(extra["X-Jobby-Renderer-Fingerprint"], page._renderer_fingerprint())

    def test_state_with_no_fingerprint_reports_none_rather_than_blank(self):
        _, _, extra = self.run_page("", lambda *a: "<html>board</html>")
        self.assertEqual(extra["X-Jobby-State-Fingerprint"], "none")

    def test_a_render_that_fails_under_drift_says_which_half_is_behind(self):
        def explode(*a):
            raise KeyError("postedAt")
        status, body, extra = self.run_page(FOREIGN, explode)
        self.assertEqual(status, 503)
        self.assertIn(b"different version of the renderer", body)
        self.assertEqual(extra.get("Retry-After"), "60")

    def test_a_render_that_fails_without_drift_is_the_plain_502(self):
        def explode(*a):
            raise KeyError("postedAt")
        status, body, _ = self.run_page(page._renderer_fingerprint(), explode)
        self.assertEqual(status, 502)
        self.assertIn(b"could not be rendered", body)


if __name__ == "__main__":
    unittest.main()
