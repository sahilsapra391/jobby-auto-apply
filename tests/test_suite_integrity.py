"""Every test file runs the same set whether you run it directly or discover it.

`unittest.main()` under `if __name__ == "__main__"` executes at that point in
the file. A class defined after it does not exist yet when it runs, so
`python3 tests/test_thing.py` silently skips it while
`python3 -m unittest discover` runs it. The direct invocation reports a green
suite that is missing tests.

This happened here: appending a class to the end of a file with `cat >>` put it
below the main block in three files, hiding 8 tests from direct invocation. The
totals reported from `discover` were right, which is exactly why nobody noticed.

(The discovery branch carries a similar guard. If both land, keep one.)

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


if __name__ == "__main__":
    unittest.main()
