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

import pathlib
import re
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
MAIN_BLOCK = re.compile(r'^if __name__ == ["\']__main__["\']:', re.M)
CLASS = re.compile(r"^class \w+", re.M)


class SuiteShapeTests(unittest.TestCase):
    def test_no_test_class_is_defined_below_the_main_block(self):
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            body = path.read_text()
            main = MAIN_BLOCK.search(body)
            if not main:
                continue
            for found in CLASS.finditer(body):
                if found.start() > main.start():
                    offenders.append(f"{path.name}: {found.group(0)} after the main block")
        self.assertEqual(
            offenders, [],
            "these classes never run when the file is executed directly:\n"
            + "\n".join(offenders))

    def test_the_suite_is_not_empty(self):
        # Every check in this file is a loop over a glob, and a loop over an
        # empty glob passes. From the discovery branch's version of this guard.
        self.assertGreater(len(list(TESTS.glob("test_*.py"))), 15)

    def test_the_main_block_is_the_last_thing_in_the_file(self):
        for path in sorted(TESTS.glob("test_*.py")):
            body = path.read_text().rstrip()
            main = MAIN_BLOCK.search(body)
            if not main:
                continue
            with self.subTest(file=path.name):
                tail = body[main.start():]
                self.assertLessEqual(
                    len(tail.splitlines()), 3,
                    f"{path.name} has code after unittest.main()")


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


if __name__ == "__main__":
    unittest.main()
