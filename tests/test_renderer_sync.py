import importlib.util
import subprocess
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SYNC_PATH = REPO / "scripts" / "sync_vercel_renderer.py"
SPEC = importlib.util.spec_from_file_location("sync_vercel_renderer", SYNC_PATH)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class RendererBundleTest(unittest.TestCase):
    def test_deployment_renderer_matches_source(self):
        for name in SYNC.FILES:
            with self.subTest(name=name):
                self.assertEqual(
                    (SYNC.SOURCE / name).read_bytes(),
                    (SYNC.TARGET / name).read_bytes(),
                    f"run python3 scripts/sync_vercel_renderer.py after changing {name}",
                )
        for name in SYNC.BRAND_ASSETS:
            with self.subTest(asset=name):
                self.assertEqual(
                    (SYNC.REPO / "assets" / "brand" / name).read_bytes(),
                    (SYNC.REPO / "deploy" / "assets" / "brand" / name).read_bytes(),
                )

    def test_sanitized_state_renders_exactly_like_local_state(self):
        code = """
import sys
sys.path.insert(0, 'scripts')
import dashboard
state = {
    'profile': {'firstName': 'A', 'email': 'private@example.com'},
    'sessions': [{'applicationId': 'one', 'company': 'Example', 'role': 'PM',
                  'status': 'watch', 'region': 'US', 'pendingFields': [],
                  'privateAnswer': 'never publish'}],
    'history': [], 'roles': {}, 'meta': {}, 'jobs': {}, 'searchConfig': None,
    'providers': {},
}
public = dashboard.publishable_state(state)
assert 'email' not in public['profile']
assert 'privateAnswer' not in public['sessions'][0]
assert dashboard.build(state, 'now') == dashboard.build(public, 'now')
"""
        result = subprocess.run(
            ["python3", "-c", code], cwd=REPO, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
