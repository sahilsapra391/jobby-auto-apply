"""No test writes the real ~/.job-application.

`browser.config_path` re-renders `browser-mcp-<slot>.json` for whatever slot it
is handed, and it does not care that its caller is a test. Measured on
2026-08-28: the production config for apply-1 held a fixture application id, two
upload paths under a tempdir that had already been cleaned up, and a
`browser_mcp_proxy.py` inside a git worktree rather than the production
checkout. Three rewrites were watched happening, at 00:18, 00:23 and 00:35,
from concurrent sessions running the suite; the tempdir in it changed each time.

Nothing read it, so nothing broke. That is the whole reason to write this down
now: the file is derived on every real run, so today the damage is zero, and the
only thing standing between here and a test that reaches a real browser slot is
one refactor by someone who has no way to know this seam exists. A production
config naming a worktree's scripts/ is also the exact shape the worktree
discipline in CLAUDE.md exists to prevent, and that one has cost three outages.

This reads source rather than behaviour because the property is "the module
redirects its state before any test runs", which has no runtime an assertion
could sit in: by the time a test body executes, the writes have already gone
wherever they were going to go.
"""

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

# Every browser.py entry point that creates or rewrites something under the real
# home, mapped to the module constant a caller has to redirect before using it.
# Read off browser.py rather than remembered, which is how the read-sounding
# names got here: server_config reaches artifact_root_for, and that mkdirs.
#
# The mapping has to be per-constant, not a blanket "redirect STATE". PROFILE
# and ARTIFACTS are bound from STATE at import, so a caller of clear_slot
# redirects ARTIFACTS and nothing else, correctly, and a rule demanding STATE
# would fail it for a fault it does not have.
CONSTANTS = ("STATE", "PROFILE", "ARTIFACTS")

WRITERS = {
    "write_config": {"STATE"},
    "config_path": {"STATE"},
    "write_gemini_settings": {"STATE"},
    "artifact_root_for": {"ARTIFACTS"},
    "activity_path": {"ARTIFACTS"},
    "stage_application_uploads": {"ARTIFACTS"},
    "server_config": {"ARTIFACTS"},
    "server_configs": {"ARTIFACTS"},
    "mcp_servers": {"ARTIFACTS"},
    "serve": {"ARTIFACTS"},
    "clear_slot": {"ARTIFACTS"},
    "profile_for": {"PROFILE"},
    # providers.build_run_invocation calls config_path and server_configs on
    # whatever adapter it is handed, so handing it the real module is both.
    "browser_adapter=browser": {"STATE", "ARTIFACTS"},
}

# A test that must reach the real home, with the reason it must. Widening this
# is a decision someone makes on purpose; forgetting the redirect is not.
REDIRECT_NOT_REQUIRED = {
    "test_apply_end_to_end.py":
        "drives a real Chrome on a real slot on purpose, and takes its "
        "candidates from watch.free_apply_slots() so it cannot collide with a "
        "live application. Excluded from CI beside the resume render tests.",
}


def _receiver_is_the_browser_module(attribute):
    """`browser.x`, and also `self.browser.x` for a module held on a fixture."""
    value = attribute.value
    if isinstance(value, ast.Name):
        return value.id == "browser"
    return isinstance(value, ast.Attribute) and value.attr == "browser"


def reaches_state(path):
    """Every call in one test module that would write the real home."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in WRITERS:
            if _receiver_is_the_browser_module(func):
                yield node.lineno, f"{ast.unparse(func)}()", WRITERS[func.attr]
        for keyword in node.keywords:
            if (keyword.arg == "browser_adapter"
                    and isinstance(keyword.value, ast.Name)
                    and keyword.value.id == "browser"):
                yield (node.lineno, "browser_adapter=browser",
                       WRITERS["browser_adapter=browser"])


def redirected(path):
    """Which constants a module actually moves.

    Parsed, not matched as text. The first version of this read the source as a
    string, which counted a commented-out `patch.object(browser, "STATE", ...)`
    as a redirect and passed the negative test it was written to fail. Both
    idioms in the suite are covered: patch.object, and assignment onto a module
    the fixture holds (`self.browser.STATE = root`).
    """
    moved = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            if not ast.unparse(node.func).endswith("patch.object"):
                continue
            if len(node.args) < 2:
                continue
            target, attribute = node.args[0], node.args[1]
            if not (isinstance(target, ast.Name) and target.id == "browser"):
                continue
            if isinstance(attribute, ast.Constant) and attribute.value in CONSTANTS:
                moved.add(attribute.value)
        elif isinstance(node, ast.Assign):
            for assigned in node.targets:
                if (isinstance(assigned, ast.Attribute)
                        and assigned.attr in CONSTANTS
                        and _receiver_is_the_browser_module(assigned)):
                    moved.add(assigned.attr)
    return moved


class NoTestWritesTheRealJobApplicationDirectory(unittest.TestCase):
    def test_every_test_reaching_browser_state_redirects_it_first(self):
        offenders = []
        inspected = 0
        reaching = 0
        for path in sorted(TESTS.glob("test_*.py")):
            inspected += 1
            calls = list(reaches_state(path))
            if not calls:
                continue
            reaching += 1
            if path.name in REDIRECT_NOT_REQUIRED:
                continue
            moved = redirected(path)
            offenders.extend(
                f"{path.name}:{line} {what} needs {sorted(needs - moved)} redirected"
                for line, what, needs in calls
                if needs - moved
            )
        self.assertEqual(
            offenders, [],
            "these write the real ~/.job-application; redirect browser.STATE, "
            "PROFILE and ARTIFACTS to a temp dir first: " + "; ".join(offenders),
        )
        # A matcher that quietly stops matching has to fail rather than pass.
        self.assertGreater(inspected, 50, "the test glob stopped finding modules")
        self.assertGreaterEqual(
            reaching, 4, "no test reaches browser state any more, or WRITERS is stale"
        )

    def test_every_writer_named_here_still_exists_in_browser(self):
        """WRITERS is a hand-kept list against another file, so pin it.

        A writer that gets renamed in browser.py leaves a dead entry here and
        the guard quietly stops covering that call, which is the failure this
        whole module exists to prevent, one level up.
        """
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import browser
        missing = [
            name for name in WRITERS
            if name != "browser_adapter=browser" and not hasattr(browser, name)
        ]
        self.assertEqual(missing, [], f"renamed or gone from browser.py: {missing}")

    def test_the_exemptions_still_name_files_that_exist(self):
        for name in REDIRECT_NOT_REQUIRED:
            with self.subTest(name=name):
                self.assertTrue((TESTS / name).exists(), f"{name} is gone; drop its entry")

    def test_redirecting_state_alone_would_not_be_enough(self):
        """PROFILE and ARTIFACTS are bound from STATE at import, not derived.

        A redirect that sets only STATE leaves both of them addressing the real
        home, which is the half of this that reads as done and is not.
        """
        source = (ROOT / "scripts" / "browser.py").read_text()
        self.assertIn('PROFILE = STATE / "browser-profile"', source)
        self.assertIn('ARTIFACTS = STATE / "browser-artifacts"', source)


if __name__ == "__main__":
    unittest.main()
