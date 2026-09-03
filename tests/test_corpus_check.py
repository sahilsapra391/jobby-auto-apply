"""`corpus_check.MODULES` is a list, so something has to derive what it must cover.

Four lists in this repository have drifted the same way: `GATE_PATHS` named
the caller and not the writer, `verify_parity.FIELDS` compared 11 of 28
columns, the migrations table list went stale against the migrations, and
`sync_v2_styles --check` walked a set of generated files that had grown. The
direction is always the same. Nobody deletes an entry; somebody adds a case
the reader never hears about.

So the list is named in `corpus_check` -- a runner has to be able to say what
it ran -- and what it must *cover* is derived here from the skips themselves.

**The derivation reads an AST rather than the text, and that is the point of
this file as much as the coverage is.** A scanner over source is a scanner
over text, and text about a thing looks exactly like the thing: a `<button>`
inside a sentence explaining that buttons must be disabled failed the
control scan, `"india" in "Indiana"` filed a US state under India, and
`marker in text` has cost three separate afternoons here. A parser does not
have that problem, because it never sees a comment or a docstring at all --
`ast.walk` yields calls, and a call in prose is not a call.
"""

import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"
sys.path.insert(0, str(REPO / "scripts"))

import corpus_check  # noqa: E402

#: A skip reason saying "this machine does not have the thing". Substrings
#: with no word boundary needed: each is a whole phrase an author wrote on
#: purpose, not a category marker being matched against arbitrary prose, which
#: is the case the boundary rule exists for.
CORPUS_REASONS = (
    "on this machine",
    "cannot run here",
    "needs Chrome",
    "no production database",
)

#: Nine modules, measured 2026-09-03. A floor rather than an equality, for the
#: reason `GATE_PATHS` learned: an equality test on a derived list defends the
#: list from being corrected, and would have failed the fix rather than the
#: drift. If the derivation stops matching, this fails instead of passing with
#: an empty set, which is the half that actually rots.
FLOOR = 8


def _literal(node):
    """The literal text of a string argument, f-string parts included."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return ""


def modules_with_a_corpus_skip():
    found = set()
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
            if name not in ("skipTest", "skipUnless", "skipIf", "SkipTest"):
                continue
            text = " ".join(_literal(a) for a in node.args)
            if any(marker in text for marker in CORPUS_REASONS):
                found.add(f"tests.{path.stem}")
    return found


class TheListCoversWhatItClaims(unittest.TestCase):
    def test_the_derivation_still_matches_something(self):
        found = modules_with_a_corpus_skip()
        self.assertGreaterEqual(
            len(found), FLOOR,
            f"the skip-reason derivation found only {len(found)} modules. A "
            f"matcher that has stopped matching reports full coverage, which "
            f"is the same green as real coverage.")

    def test_every_module_with_a_corpus_skip_is_run(self):
        missing = sorted(modules_with_a_corpus_skip()
                         - set(corpus_check.MODULES)
                         - set(corpus_check.EXCLUDED))
        self.assertEqual(
            missing, [],
            f"these modules hold a test that can only run where the real "
            f"corpus lives, and the hourly check does not run them: {missing}. "
            f"CI skips them too, so nothing anywhere is running them.")

    def test_every_module_it_runs_exists(self):
        for name in corpus_check.MODULES:
            with self.subTest(module=name):
                self.assertTrue((TESTS / f"{name.split('.')[-1]}.py").exists())


    def test_every_exclusion_carries_a_reason(self):
        """An exemption without a reason is an exemption nobody can review,
        and the reason is the only thing separating it from a test that was
        inconvenient. Same rule the parity exemptions are held to."""
        for name, why in corpus_check.EXCLUDED.items():
            with self.subTest(module=name):
                self.assertGreater(len(why), 40, f"{name} is excluded on no "
                                                 f"stated grounds")

    def test_nothing_is_both_run_and_excluded(self):
        both = set(corpus_check.MODULES) & set(corpus_check.EXCLUDED)
        self.assertEqual(both, set())


class TheScannerReadsCodeAndNotProse(unittest.TestCase):
    """The property the AST buys, pinned so nobody 'simplifies' it to a grep."""

    def test_a_skip_reason_quoted_in_a_comment_is_not_a_skip(self):
        tree = ast.parse('# self.skipTest("no profile on this machine")\n'
                         'x = 1\n')
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.Call)], [])

    def test_a_skip_reason_quoted_in_a_docstring_is_not_a_skip(self):
        tree = ast.parse('"""Explains that skipTest(\'on this machine\') is '
                         'the pattern."""\n')
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.Call)], [])


class TheCeilingIsMeasured(unittest.TestCase):
    def test_the_skip_ceiling_leaves_little_headroom(self):
        """55 against a measured 50. A ceiling generous enough never to fire
        is a ceiling that notices nothing, which is the failure mode of every
        guard written up in CLAUDE.md."""
        self.assertLessEqual(corpus_check.MAX_SKIPPED, 60)
        self.assertGreaterEqual(corpus_check.MAX_SKIPPED, 50)


class ItDoesNotMakeTheWatcherLookLikeATestRunner(unittest.TestCase):
    """`watch.py` imports this module, and two guards in this repository decide
    what to do by asking whether `unittest` is in `sys.modules`.

    `provider_usage.record` refuses to write the device's own ledger under a
    test runner, and `migrate.device_url_refusal` refuses the device database.
    Both are correct and both are backstops for real damage that has already
    happened once. A module-level `import unittest` in `corpus_check` would
    give the running daemon a test runner's signature, and the failure would be
    silent in the worst direction: the watcher would stop recording what
    applications cost, and the number would simply be smaller.

    So the import lives inside `run()`, and this is the check that keeps it
    there. Measured by importing `watch` in a clean interpreter, not by reading
    the import block, because a transitive import would not show up there.
    """

    def test_importing_watch_does_not_import_unittest(self):
        import subprocess
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import watch; "
             "print('unittest' in sys.modules, 'pytest' in sys.modules)"
             % str(REPO / "scripts")],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.strip(), "False False", out.stdout)


if __name__ == "__main__":
    unittest.main()
