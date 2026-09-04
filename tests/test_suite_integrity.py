"""Every test file runs the same set whether you run it directly or discover it.

`unittest.main()` under `if __name__ == "__main__"` executes at that point in
the file. A class defined after it does not exist yet when it runs, so
`python3 tests/test_thing.py` silently skips it while
`python3 -m unittest discover` runs it. The direct invocation reports a green
suite that is missing tests.

This happened here: appending a class to the end of a file with `cat >>` put it
below the main block in three files, hiding 8 tests from direct invocation. The
totals reported from `discover` were right, which is exactly why nobody noticed.

Both branches wrote one of these. This is the one that survived: it also
catches anything below the main block, not only a class.

Run from the repo root: python3 -m unittest discover -s tests
"""

import ast
import pathlib
import re
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
MAIN_BLOCK = re.compile(r'^if __name__ == ["\']__main__["\']:', re.M)
CLASS = re.compile(r"^class \w+", re.M)


class SuiteShapeTests(unittest.TestCase):
    def test_no_test_file_has_a_main_block(self):
        """**The hazard is removed rather than detected.**

        Two rules used to live here: that no class sat below the main block,
        and that nothing followed `unittest.main()`. Both were correct and both
        fired only in CI, because they are tests. Appending a class to a file
        whose last lines are `if __name__ == "__main__"` therefore passed
        locally, ran under `discover`, and never ran on a direct execution of
        the file -- twice in one session, same cause, and the second time was
        after resolving to be careful about the first.

        A pre-commit hook would not have caught it either. `.githooks` is
        wired through `core.hooksPath`, which is per-clone, and it is set in
        the production checkout and unset in the worktrees where all agent
        work happens. A guard whose installation is per-clone is a guard the
        next worktree does not have.

        So the block is gone from all 139 test files instead. `discover` never
        needed it, `python3 -m unittest tests.test_foo` still runs one module,
        and code appended to any of them now runs however it is invoked.
        Measured before removing them: 138 of 139 had one and not one did
        anything other than call `unittest.main()`.

        **The two rules it replaces would both have become vacuous**, because
        each skipped a file with no main block, and a loop that skips
        everything passes. This one fails when a block comes back.

        It walks an AST rather than matching text, because the marker it looks
        for appears in this file's own docstring and in the regex the old rules
        used. A parser never sees either.
        """
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                if (isinstance(test, ast.Compare)
                        and isinstance(test.left, ast.Name)
                        and test.left.id == "__name__"):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "a main block is back in these files. Anything appended after one "
            "runs under discover and not under a direct run of the file, which "
            "is how two classes shipped unexecuted:\n" + "\n".join(offenders))

    def test_the_suite_is_not_empty(self):
        # Every check in this file is a loop over a glob, and a loop over an
        # empty glob passes. From the discovery branch's version of this guard.
        self.assertGreater(len(list(TESTS.glob("test_*.py"))), 15)

    def test_no_test_case_inherits_another_test_cases_tests(self):
        """Subclassing a TestCase to reuse its setUp re-runs all of its tests.

        Two classes in test_seen.py did that, so eleven tests ran three times
        over and the suite reported 313 where it had 305. Nothing failed; the
        number was just wrong, and a count nobody can trust is worse than a
        smaller one. Put shared setup in a plain mixin instead.
        """
        import ast
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            tree = ast.parse(path.read_text())
            classes = {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)}
            for name, node in classes.items():
                for base in node.bases:
                    parent = classes.get(getattr(base, "id", None))
                    if not parent:
                        continue
                    inherited = [f.name for f in parent.body
                                 if isinstance(f, ast.FunctionDef)
                                 and f.name.startswith("test_")]
                    if inherited:
                        offenders.append(
                            f"{path.name}: {name} re-runs {len(inherited)} tests "
                            f"inherited from {parent.name}")
        self.assertEqual(offenders, [], "\n".join(offenders))
