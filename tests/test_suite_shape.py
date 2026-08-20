"""Tests about the test suite itself.

There is exactly one of these and it earns its place. `unittest.main()` sitting
anywhere but the end of a module means running that module directly executes
main() before the classes below it are defined, so those classes never run —
and the run reports a confident pass having tested less than it appeared to.

Under pytest everything collects either way, so the suite stays honest and the
gap is invisible until somebody runs one file directly to debug it. That is
§13 item 1 wearing a different hat: a green result that means nothing.

Eight modules had it, four of them predating the Discovery Engine.

Run from the repo root: python3 -m pytest tests/ -q
"""

import pathlib
import re
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
MAIN_BLOCK = re.compile(r'^if __name__ == "__main__":', re.M)
CLASS = re.compile(r"^class (\w+)", re.M)


class SuiteShapeTests(unittest.TestCase):
    def modules(self):
        return sorted(p for p in TESTS.glob("test_*.py"))

    def test_no_module_defines_a_test_class_after_its_main_block(self):
        offenders = {}
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            found = MAIN_BLOCK.search(text)
            if not found:
                continue
            after = CLASS.findall(text[found.end():])
            if after:
                offenders[path.name] = after
        self.assertEqual(offenders, {},
                         "these classes never run when the module is run directly")

    def test_the_suite_is_not_empty(self):
        # A guard against this file quietly passing because the glob broke.
        self.assertGreater(len(self.modules()), 15)


if __name__ == "__main__":
    unittest.main()
