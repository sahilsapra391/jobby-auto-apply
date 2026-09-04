"""No test reaches browser state directly without redirecting it first.

Read the title precisely, because a wider one would be false. This proves a
property about *direct* calls, and the limit is measured below.

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

**What this cannot see, measured rather than supposed.** A static reader finds
`browser.clear_slot()` in a test and nothing else. On 2026-08-28,
test_browser_slot_affinity created browser-artifacts/ in the home under a
redirected HOME while passing this guard, because it reaches the tree through
watch.reconcile_browser_slots -> watch.release_browser_slot -> browser.clear_slot,
and only the last of those three is a name this file knows. Adding the two
watch names would close that one path and leave the next one, since the
transitive closure of "reaches a writer eventually" is not something a call-name
list converges on.

So this is the cheap half of the property and not the whole of it. The other
half is tests/store_isolation.py (PR #116), which rebinds the constants at
runtime and therefore does not care how many frames deep the writer sits.
`redirected()` recognises it, so a module that hands its isolation to
store_isolation.begin() satisfies this file without also doing the work twice.
Neither mechanism subsumes the other: theirs covers indirect reach and every
constant including ones nobody has added yet, and this one fails a pull request
in CI rather than a sweep somebody has to remember to run.
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
    # server_config mkdirs the profile directory before the artifact root,
    # measured 2026-08-28 when test_apply_end_to_end still created
    # browser-profile/ in the home with only ARTIFACTS redirected.
    "artifact_root_for": {"ARTIFACTS"},
    "activity_path": {"ARTIFACTS"},
    "stage_application_uploads": {"ARTIFACTS"},
    # The deleting twins. These reach artifact_root_for and unlink files, so a
    # test that calls them without redirecting ARTIFACTS removes documents from
    # a live apply slot. Registered here because the map's own comment says it
    # is read off browser.py rather than remembered, and a delete is the one
    # kind of writer that cannot be noticed by a stray fixture appearing.
    "discard_application_uploads": {"ARTIFACTS"},
    "discard_staged": {"ARTIFACTS"},
    "server_config": {"ARTIFACTS", "PROFILE"},
    "server_configs": {"ARTIFACTS", "PROFILE"},
    "mcp_servers": {"ARTIFACTS", "PROFILE"},
    # serve mkdirs the chrome log under STATE/logs as well as the artifact root.
    "serve": {"STATE", "ARTIFACTS"},
    "clear_slot": {"ARTIFACTS"},
    "profile_for": {"PROFILE"},
    # providers.build_run_invocation calls config_path and server_configs on
    # whatever adapter it is handed, so handing it the real module is both.
    "browser_adapter=browser": {"STATE", "ARTIFACTS"},
}

# A test that must reach the real home, with the reason it must. Widening this
# is a decision someone makes on purpose; forgetting the redirect is not.
#
# Empty, and it was not always. test_apply_end_to_end.py sat here on the
# argument that driving a real Chrome needs the real everything. It does not:
# it needs the slot's CDP port, which port_for derives from the slot name, and
# nothing else in the artifact tree. While it was exempt it was writing
# uploads/endtoend/activity.json under a live slot's artifact root, and because
# it tears down through a clean stdin close it was the one caller reaching
# _write_activity at all. So the exempted test was depositing real-shaped
# records into the tree people measure real runs from, during the whole period
# real runs wrote nothing. An exemption is a claim that the real path is
# needed, and that claim is worth re-testing before it is granted.
REDIRECT_NOT_REQUIRED: dict[str, str] = {}


def _uses_store_isolation(tree):
    """Does this module hand its isolation to store_isolation.begin()?

    Both halves are required. The import alone is someone reading live_store();
    the call alone would match any unrelated begin().
    """
    imported = any(
        isinstance(node, ast.Import)
        and any(alias.name == "store_isolation" for alias in node.names)
        for node in ast.walk(tree)
    )
    if not imported:
        return False
    return any(
        isinstance(node, ast.Call)
        and ast.unparse(node.func) in
        ("store_isolation.begin", "store_isolation.isolate_store")
        for node in ast.walk(tree)
    )


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
    tree = ast.parse(path.read_text())
    if _uses_store_isolation(tree):
        # tests/store_isolation.py (PR #116) walks the module graph and rebinds
        # every scripts/ constant naming a path inside the real store, so it
        # covers all three of browser's at once and generalises to constants
        # nobody has added yet. Measured against this branch's browser.py on
        # 2026-08-28: after begin(), STATE, PROFILE and ARTIFACTS all resolve
        # under a jobby-store- tempdir, and end() puts all three back.
        #
        # Recognised here so the two mechanisms are not a merge hazard. Without
        # it, swapping a module's explicit patches for a begin() would make this
        # guard fail a module that had just become better isolated, which is a
        # guard punishing the fix.
        return set(CONSTANTS)
    moved = set()
    for node in ast.walk(tree):
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


class NoTestReachesBrowserStateWithoutRedirectingIt(unittest.TestCase):
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

    def test_the_shared_isolation_helper_counts_as_a_redirect(self):
        """Either mechanism satisfies the rule, so the merge order is free.

        PR #116 replaces a module's hand-written patches with a three-line
        setUpModule. Checked against synthetic sources rather than against
        tests/store_isolation.py, because that file is not on this branch yet
        and a guard that only works once the other branch lands is the coupling
        this is here to remove.
        """
        import tempfile
        cases = {
            "import store_isolation\ndef setUpModule():\n"
            "    store_isolation.begin()\n": set(CONSTANTS),
            "import store_isolation\ndef f(self):\n"
            "    store_isolation.isolate_store(self)\n": set(CONSTANTS),
            # Import with no call, and call with no import, are each not enough.
            "import store_isolation\nx = store_isolation.live_store()\n": set(),
            "def setUpModule():\n    begin()\n": set(),
        }
        with tempfile.TemporaryDirectory() as name:
            for source, expected in cases.items():
                probe = pathlib.Path(name) / "probe.py"
                probe.write_text(source)
                with self.subTest(source=source.replace("\n", " ")[:48]):
                    self.assertEqual(redirected(probe), expected)

    def test_no_exemption_outlives_the_need_for_it(self):
        """An exemption that is no longer load-bearing hides coverage silently.

        Two ways it goes dead: the file is gone, or the file has since started
        redirecting everything it touches. In the second case the guard is
        skipping a module that would now pass on its own merits, so it would
        stop noticing if the redirect were later removed, which is this
        module's whole subject one level up.
        """
        for name in REDIRECT_NOT_REQUIRED:
            with self.subTest(name=name):
                path = TESTS / name
                self.assertTrue(path.exists(), f"{name} is gone; drop its entry")
                needed = set().union(*(needs for _, _, needs in reaches_state(path)))
                self.assertTrue(
                    needed - redirected(path),
                    f"{name} now redirects everything it touches; "
                    "drop its exemption so the guard covers it",
                )

    def test_redirecting_state_alone_would_not_be_enough(self):
        """PROFILE and ARTIFACTS are bound from STATE at import, not derived.

        A redirect that sets only STATE leaves both of them addressing the real
        home, which is the half of this that reads as done and is not.
        """
        source = (ROOT / "scripts" / "browser.py").read_text()
        self.assertIn('PROFILE = STATE / "browser-profile"', source)
        self.assertIn('ARTIFACTS = STATE / "browser-artifacts"', source)
