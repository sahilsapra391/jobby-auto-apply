"""No diagnostic path is discarded: the rule, enforced rather than remembered.

Three times now the component whose job is noticing has been the one that said
nothing. ping_health returned None on any network error, so an unreachable
Healthchecks URL was invisible from inside the watcher. The tick's STALE BOARD
branch was never reached across a 22-hour outage because the publish raised
first, so hours of pings named the publish and none named the day-old board.
And browser_mcp_proxy sent the Playwright child's stderr to DEVNULL, so a
backend that died during initialize destroyed its own explanation in the instant
it produced it, and every hand probe afterwards succeeded.

A rule that depends on the next person remembering is a rule already broken, so
this is the rule as a test. It reads source rather than behaviour on purpose:
the property is "the reason can be recovered afterwards", which has no runtime
of its own to observe.
"""

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

# Every directory holding product Python, not just the one that held it when
# this was written. `packages/` arrived with the v2 rebuild and `packages/runner`
# is where Phase 4 puts the process that launches Chrome and the provider CLIs,
# which is exactly the code this rule exists for. A guard scoped to the
# directory that happened to exist on the day it was written is a guard that
# stops applying the moment the tree grows, silently and while still passing.
AUDITED = (SCRIPTS, ROOT / "packages")


def audited_modules():
    for root in AUDITED:
        if root.is_dir():
            yield from sorted(root.rglob("*.py"))

LAUNCHERS = {"run", "Popen", "check_output", "call", "check_call"}

# Six inherit stderr, which reaches the service log and is therefore readable.
# Nothing is allowed to reach DEVNULL. If a new launch genuinely must discard
# its child's stderr, it is added here with the reason, not by widening the rule.
DISCARD_ALLOWED: dict[str, str] = {}


def launches(path):
    """Every subprocess launch in one module, with its stderr disposition."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in LAUNCHERS:
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
            continue
        stderr = next(
            (kw.value for kw in node.keywords if kw.arg == "stderr"), None
        )
        discarded = (
            isinstance(stderr, ast.Attribute) and stderr.attr == "DEVNULL"
        )
        yield node.lineno, discarded


class NoChildStderrIsThrownAway(unittest.TestCase):
    def test_no_subprocess_in_product_code_discards_its_stderr(self):
        discarded = [
            f"{path.name}:{line}"
            for path in audited_modules()
            for line, is_discarded in launches(path)
            if is_discarded and f"{path.name}:{line}" not in DISCARD_ALLOWED
        ]
        self.assertEqual(
            discarded, [],
            "stderr=DEVNULL destroys the reason a child died at the moment it is "
            "produced; capture it, or add it to DISCARD_ALLOWED with the reason",
        )

    def test_the_audit_actually_found_the_launches_it_claims_to_check(self):
        """A checker that matches nothing passes for the wrong reason."""
        total = sum(1 for path in audited_modules() for _ in launches(path))
        self.assertGreater(total, 50, f"only {total} subprocess launches found")

    def test_every_audited_directory_that_exists_is_actually_walked(self):
        """The floor above is met by scripts/ alone, so a `packages/` that
        silently stopped being walked would not move it. This is the half that
        notices."""
        walked = {p.parent for p in audited_modules()}
        for root in AUDITED:
            if not root.is_dir():
                continue
            has_python = any(root.rglob("*.py"))
            if has_python:
                self.assertTrue(
                    any(str(d).startswith(str(root)) for d in walked),
                    f"{root} holds Python and is not being audited")

    def test_it_would_catch_a_discarded_stderr(self):
        """The negative control, since the rule is the whole point."""
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            offender = pathlib.Path(name) / "bad.py"
            offender.write_text(
                "import subprocess\n"
                "subprocess.Popen(['x'], stderr=subprocess.DEVNULL)\n"
            )
            found = [line for line, discarded in launches(offender) if discarded]
            self.assertEqual(found, [2])


class TheFirewallReportsWhatItsBackendSaid(unittest.TestCase):
    """The instance this branch fixes. The other two are covered where they live.

    `ping_health` recording its own outcome and the tick carrying the staleness
    message past an earlier exception are on the staleness branch, tested
    behaviourally there rather than asserted here by name.
    """

    def test_the_backend_stderr_is_kept_and_reported(self):
        import browser_mcp_proxy
        self.assertTrue(hasattr(browser_mcp_proxy, "_report_backend_stderr"))
        self.assertGreater(browser_mcp_proxy.BACKEND_STDERR_LINES, 0)

    def test_what_it_reports_is_what_the_backend_actually_said(self):
        import browser_mcp_proxy
        import contextlib
        import io
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            browser_mcp_proxy._report_backend_stderr(
                browser_mcp_proxy.deque(["npm error notarget No matching version"]), 1
            )
        written = captured.getvalue()
        self.assertIn("No matching version", written)
        self.assertIn("exit 1", written)

    def test_a_backend_that_said_nothing_says_so_rather_than_nothing(self):
        import browser_mcp_proxy
        import contextlib
        import io
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            browser_mcp_proxy._report_backend_stderr(browser_mcp_proxy.deque(), 2)
        self.assertIn("wrote nothing to stderr", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
