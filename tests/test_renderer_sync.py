import sys
import ast
import importlib.util
import json
import shutil
import subprocess
import tempfile
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


class BundleCompletenessTest(unittest.TestCase):
    """The bundle has to ship every local module it imports.

    deploy/renderer is a hand-listed copy of eleven files, and the deployed
    function puts that directory on sys.path and imports flat. A module the
    bundle imports but does not carry fails at import time on Vercel and every
    request 502s, while the same code renders perfectly on a machine where the
    rest of scripts/ happens to be reachable. clock.py was exactly that: added
    to scripts/, imported by both renderers, never added to FILES.
    """

    def local_imports(self, path):
        """Module-level imports only.

        That is the failure this models: an import at module scope runs when the
        handler imports the renderer, so a missing sibling 502s every request. An
        import inside a function only matters if that function is called, and
        store.cmd_session_save — the one lazy importer here — is never reached
        from a render.
        """
        found = set()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    def test_the_bundle_carries_everything_it_imports(self):
        available = {name.removesuffix(".py") for name in SYNC.FILES}
        local = {p.stem for p in SYNC.SOURCE.glob("*.py")}
        stdlib = set(sys.stdlib_module_names) | {"__future__"}
        missing = {}
        for name in SYNC.FILES:
            for module in self.local_imports(SYNC.SOURCE / name):
                if module in stdlib or module in available:
                    continue
                if module in local:
                    missing.setdefault(module, []).append(name)
        self.assertEqual(
            missing, {},
            "add these to sync_vercel_renderer.FILES or the deployment 502s: "
            + ", ".join(f"{m}.py (imported by {', '.join(u)})" for m, u in missing.items()))

    def test_the_bundle_imports_flat_the_way_the_handler_does(self):
        # deploy/api/page.py puts renderer/ on sys.path and imports `dashboard`,
        # not `renderer.dashboard`. Importing it the package way hides a missing
        # sibling, because the package directory is on the path either way.
        handler = (SYNC.TARGET.parent / "api" / "page.py").read_text(encoding="utf-8")
        self.assertIn('sys.path.insert(0, str(DEPLOY_ROOT / "renderer"))', handler)
        self.assertIn("import dashboard", handler)


class BundleDataFilesTest(unittest.TestCase):
    """The bundle has to ship every data file it reads, not just every module.

    BundleCompletenessTest models a missing import. This models the other half,
    which is what actually took the board down: brand.favicon_links() read
    favicon.ico and apple-touch-icon.png out of deploy/public/ whenever the
    brand pack copy was absent. public/ is Vercel's static output directory, so
    the file is there on any dev machine and gone inside the lambda. Every
    deployment from the Soft Premium renderer onward answered
    "The board state could not be rendered." while the identical code rendered
    fine locally and in every test.

    Rendering against a copy of the bundle with public/ removed is the only
    check that tells those two situations apart.
    """

    def render_from_bundle_layout(self, state):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # What Vercel puts in the function: the renderer and its assets.
            # Deliberately not public/ — that becomes static edge output.
            shutil.copytree(SYNC.TARGET, root / "renderer",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(SYNC.REPO / "deploy" / "assets", root / "assets")
            code = (
                "import json, sys\n"
                f"sys.path.insert(0, {str(root / 'renderer')!r})\n"
                "import dashboard\n"
                "page = dashboard.build(json.loads(sys.stdin.read()), 'now')\n"
                "print(len(page))\n"
            )
            return subprocess.run(
                [sys.executable, "-c", code], input=json.dumps(state),
                cwd=tmp, text=True, capture_output=True,
            )

    def test_the_board_renders_with_only_the_files_the_lambda_carries(self):
        state = {
            "profile": {"firstName": "A"},
            "sessions": [{"applicationId": "one", "company": "Example",
                          "role": "PM", "status": "watch", "region": "US",
                          "pendingFields": []}],
            "history": [], "roles": {}, "meta": {}, "jobs": {},
            "searchConfig": None, "providers": {},
        }
        result = self.render_from_bundle_layout(state)
        self.assertEqual(
            result.returncode, 0,
            "the deployed bundle reads a file it does not carry, so every "
            "request 502s with 'The board state could not be rendered':\n"
            + result.stderr,
        )
        self.assertGreater(int(result.stdout.strip()), 0)


if __name__ == "__main__":
    unittest.main()
