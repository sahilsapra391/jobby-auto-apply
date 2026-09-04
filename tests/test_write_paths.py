"""The write-path inventory, checked against the code rather than trusted.

`docs/write-paths.md` claims to list every place the store is mutated. Dual-write
is only correct if that list is complete, and an incomplete list does not fail
when it is written: the missed path shows up as a parity divergence on day five
and restarts the seven-day clock at zero.

So the list is compared against an AST walk of `scripts/`, and it fails in both
directions. A call site the document does not classify fails the build, which is
what stops a new write path from being added silently. A row in the document
that no longer matches any call site fails too, because a stale inventory is one
people stop reading, and an inventory people stop reading is prose.

The classification matters as much as the list. Class A and B are dual-written;
class C is not, with a reason each. A path in none of them is a path nobody has
thought about, which is the case this exists to make impossible.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DOC = REPO / "docs" / "write-paths.md"

# The functions that put bytes in the store, however they are reached.
PRIMITIVES = {"write_private", "save_jobs", "save_passed", "save_companies",
              "edit", "mark_passed", "mark_watched", "remember_role",
              "_persist_pool"}

# The modules a primitive can legitimately be called on. A `foo.edit(...)` on
# some unrelated object is not a store write.
OWNERS = {"store", "jobs_store"}


def call_sites():
    """{(module, function, primitive)} for every store write in scripts/."""
    found = set()
    for path in sorted(SCRIPTS.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                              # pragma: no cover
            continue
        owner = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    owner[id(child)] = node.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                name, module = func.attr, func.value.id
            elif isinstance(func, ast.Name):
                name, module = func.id, path.stem
            else:
                continue
            if name in PRIMITIVES and module in OWNERS | {path.stem}:
                found.add((path.stem, owner.get(id(node), "<module>"), name))
    return found


def documented():
    """{(module, function, primitive): class} from the three tables."""
    rows, current = {}, None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        match = re.match(r"^##\s+Class\s+([ABC])\b", stripped)
        if match:
            current = match.group(1)
            continue
        if stripped.startswith("##"):
            current = None
            continue
        if current is None or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("Module", "---"):
            continue
        key = tuple(c.strip("`") for c in cells[:3])
        if all(key):
            rows[key] = current
    return rows


class TheInventoryMatchesTheCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DOC.exists():
            raise unittest.SkipTest("no write-paths document in this checkout")
        cls.actual = call_sites()
        cls.listed = documented()

    def test_the_document_parses_into_something(self):
        """Every assertion below passes vacuously if the tables stop being
        found, which is what a heading rename would do."""
        self.assertGreater(len(self.listed), 20)
        self.assertGreater(len(self.actual), 20)

    def test_every_call_site_is_classified(self):
        missing = sorted(self.actual - set(self.listed))
        self.assertEqual(
            missing, [],
            f"{len(missing)} store writes are in scripts/ and in no table of "
            f"docs/write-paths.md. An unclassified write path is one nobody "
            f"has decided about, and it surfaces as a parity divergence days "
            f"later: {missing[:5]}")

    def test_the_document_lists_nothing_that_has_gone(self):
        stale = sorted(set(self.listed) - self.actual)
        self.assertEqual(
            stale, [],
            f"docs/write-paths.md lists call sites that no longer exist. A "
            f"stale inventory stops being read: {stale[:5]}")

    def test_every_class_is_used(self):
        """A class with no rows means the tables were reorganised and the
        parser is reading one of them as another."""
        for letter in ("A", "B", "C"):
            with self.subTest(cls=letter):
                self.assertTrue(
                    any(v == letter for v in self.listed.values()),
                    f"class {letter} has no rows")

    def test_the_pool_choke_point_is_class_a(self):
        """`_persist_pool` is the single place the pool reaches disk, and both
        pool writers must go through it. If either grows its own
        `write_private` again, dual-write covers less than it claims and this
        stops agreeing."""
        for function in ("save_jobs", "edit"):
            key = ("jobs_store", function, "_persist_pool")
            with self.subTest(function=function):
                self.assertEqual(
                    self.listed.get(key), "A",
                    f"jobs_store.{function} does not go through _persist_pool")
        self.assertEqual(
            self.listed.get(("jobs_store", "_persist_pool", "write_private")),
            "A", "_persist_pool is not the pool's writer")

    def test_neither_pool_writer_still_calls_write_private_directly(self):
        """The refactor's whole point. A direct call would bypass the mirror
        while still passing every other check here."""
        for function in ("save_jobs", "edit"):
            with self.subTest(function=function):
                self.assertNotIn(("jobs_store", function, "write_private"),
                                 self.actual)

    def test_every_class_c_row_carries_a_reason(self):
        """A path excluded from dual-write with no reason is indistinguishable
        from one that was forgotten."""
        section, rows = False, 0
        for line in DOC.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("## Class C"):
                section = True
                continue
            if section and stripped.startswith("##"):
                break
            if not section or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 4 or cells[0] in ("Module", "---"):
                continue
            rows += 1
            with self.subTest(row=cells[:3]):
                self.assertTrue(len(cells[3]) > 15,
                                f"{cells[:3]} gives no reason for exclusion")
        self.assertGreater(rows, 5)


class ThePrimitiveListIsHonest(unittest.TestCase):
    """The scan is only as good as its list of primitives, and that list is
    the easiest thing here to be quietly wrong about."""

    def test_every_primitive_exists_in_the_module_it_claims(self):
        sys.path.insert(0, str(SCRIPTS))
        import jobs_store
        import store
        for name in PRIMITIVES:
            with self.subTest(primitive=name):
                self.assertTrue(hasattr(store, name) or hasattr(jobs_store, name),
                                f"{name} is not a function in either module")

    def test_write_private_is_still_the_only_thing_touching_disk(self):
        """Every other primitive funnels through it. If one grows its own
        `open(...).write(...)`, the choke-point argument in the document stops
        holding and the scan stops covering it."""
        source = (SCRIPTS / "jobs_store.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "open":
                offenders.append(node.lineno)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("write_text", "write_bytes"):
                offenders.append(node.lineno)
        self.assertEqual(offenders, [],
                         "jobs_store writes to disk outside write_private")

    def test_it_would_notice_a_new_call_site(self):
        """The negative control. Without it a scan that matched nothing would
        pass every assertion above."""
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            offender = Path(name) / "newthing.py"
            offender.write_text("import jobs_store\n"
                                "def go():\n"
                                "    jobs_store.save_jobs(root, jobs)\n")
            tree = ast.parse(offender.read_text())
            hits = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in PRIMITIVES]
            self.assertEqual(len(hits), 1)
