"""A quarantined test may not rot into a file nobody can run.

**Quarantine is the honest response to a test whose failures stopped carrying
information, and it is one step from a graveyard.** A file that is not collected
is a file nothing tells you about: it can stop importing, its subject can be
deleted, its entry in the list can drift from what the file holds, and every one
of those is silent by construction.

So the arrangement checks itself. The list and the files must name the same
tests, every quarantined module must still import and still hold the test its
name promises, and every entry must say when and why. None of that runs the
quarantined test -- that is the point of quarantining it -- but it does keep the
quarantine a statement about a real test rather than a note about a file.
"""

import importlib
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"
LIST = REPO / "docs" / "quarantine.md"


def listed():
    """`{module: {test, ...}}` from the table in `docs/quarantine.md`."""
    out = {}
    for row in re.findall(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$",
                          LIST.read_text(encoding="utf-8"), re.M):
        dotted, since, why = row
        module, _, rest = dotted.partition(".")
        out[module] = {"test": rest, "since": since.strip(), "why": why.strip()}
    return out


def files():
    return {p.stem for p in TESTS.glob("quarantined_*.py")}


class TheQuarantineDescribesRealTests(unittest.TestCase):
    def test_the_list_and_the_files_agree(self):
        self.assertEqual(sorted(listed()), sorted(files()),
                         "docs/quarantine.md and tests/quarantined_*.py "
                         "disagree about what is quarantined")

    def test_every_quarantined_module_still_imports(self):
        """The cheapest way a quarantine rots: the code moves and nothing
        complains, because nothing was running it."""
        for module in sorted(files()):
            with self.subTest(module=module):
                importlib.import_module(f"tests.{module}")

    def test_every_listed_test_still_exists(self):
        """An entry naming a test that has been renamed or deleted is a claim
        about coverage that is no longer true either way."""
        for module, entry in sorted(listed().items()):
            with self.subTest(module=module):
                loaded = importlib.import_module(f"tests.{module}")
                cls, _, name = entry["test"].rpartition(".")
                target = getattr(loaded, cls, None)
                self.assertIsNotNone(target, f"{module} has no class {cls}")
                self.assertTrue(hasattr(target, name),
                                f"{cls} has no test {name}")

    def test_every_entry_says_when_and_why(self):
        for module, entry in sorted(listed().items()):
            with self.subTest(module=module):
                self.assertRegex(entry["since"], r"^\d{4}-\d{2}-\d{2}$",
                                 "an entry needs the date it was quarantined")
                self.assertGreater(len(entry["why"]), 60,
                                   "an entry needs a reason somebody can act on")

    def test_the_collected_suite_does_not_pick_these_up(self):
        """The control. If `discover` matched `quarantined_*.py` the whole
        arrangement would be decorative, and the flake would still be in the
        run it was moved out of."""
        import unittest.loader
        pattern = unittest.loader.TestLoader().testNamePatterns
        self.assertIsNone(pattern)
        for name in files():
            self.assertFalse(name.startswith("test"),
                             f"{name} starts with 'test' and would be collected")

    def test_something_is_actually_quarantined(self):
        """Every assertion above is vacuous over an empty set, which is how a
        list like this quietly stops being maintained."""
        self.assertTrue(files(), "no quarantined files; delete this file and "
                                 "docs/quarantine.md rather than leaving an "
                                 "empty arrangement that always passes")


if __name__ == "__main__":
    unittest.main()
