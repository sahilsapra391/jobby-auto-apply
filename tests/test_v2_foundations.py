"""Phase 0's own deliverables, checked the way any other invariant is.

Phase 0 exists to make the later phases safe to attempt, and every part of it is
the kind of thing that rots quietly: a directory nobody puts anything in, an ADR
for eight of nine decisions, a policy document describing a rule the code does
not enforce. None of those fails anything at the time.

The one worth explaining is the ADR count. §2 of the plan is a table of settled
decisions, and the instruction attached to it is that the next agent should not
relitigate them. That only works if every row has a record, so this counts the
rows in the plan and compares, rather than asserting a number somebody has to
remember to update. A tenth decision added to §2 fails this until it has an ADR.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "docs" / "plans" / "jobby-v2-rebuild-plan.md"
ADR = REPO / "docs" / "adr"


class TheLayoutExists(unittest.TestCase):
    """§9 Phase 0: apps/web, apps/worker, apps/scanner, packages/runner,
    packages/schema, docs/plans."""

    EXPECTED = ("apps/web", "apps/worker", "apps/scanner",
                "packages/runner", "packages/schema", "docs/plans")

    def test_every_directory_is_present(self):
        for rel in self.EXPECTED:
            with self.subTest(directory=rel):
                self.assertTrue((REPO / rel).is_dir(), f"{rel} is missing")

    def test_every_scaffold_says_which_phase_fills_it(self):
        """An empty directory with no README is indistinguishable from one
        somebody forgot to delete."""
        for rel in ("apps/web", "apps/worker", "apps/scanner",
                    "packages/runner"):
            with self.subTest(directory=rel):
                readme = REPO / rel / "README.md"
                self.assertTrue(readme.exists(), f"{rel}/README.md is missing")
                self.assertRegex(readme.read_text(encoding="utf-8"),
                                 r"Phase \d",
                                 f"{rel}/README.md names no phase")

    def test_the_schema_package_is_not_a_scaffold(self):
        """Phase 0 fills this one, so it holds working code rather than a
        promise."""
        self.assertTrue((REPO / "packages/schema/migrate.py").exists())
        self.assertTrue((REPO / "packages/schema/flags.py").exists())
        migrations = sorted((REPO / "packages/schema/migrations").glob("*.sql"))
        self.assertGreater(len(migrations), 0, "no migrations")

    def test_the_running_product_has_not_been_moved(self):
        """The three launchd services execute scripts/ by absolute path, and
        run the KV helpers in deploy/ from that directory. Moving either stops
        production to gain a tidier tree."""
        self.assertTrue((REPO / "scripts" / "watch.py").exists())
        self.assertTrue((REPO / "deploy" / "queue.mjs").exists())


class ThePlanIsInTheRepository(unittest.TestCase):
    def test_the_plan_is_checked_in(self):
        self.assertTrue(PLAN.exists(),
                        "the plan lives in a Downloads folder, where the next "
                        "agent cannot read it")

    def test_the_migration_policy_is_written_down(self):
        doc = REPO / "docs" / "migrations.md"
        self.assertTrue(doc.exists())
        text = doc.read_text(encoding="utf-8").lower()
        for phrase in ("expand", "contract", "jobby:contract"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


class EverySettledDecisionHasARecord(unittest.TestCase):
    def setUp(self):
        if not PLAN.exists():
            self.skipTest("the plan is not in this checkout")
        self.plan = PLAN.read_text(encoding="utf-8")

    def decision_rows(self):
        """The body rows of the §2 table.

        Located by the section heading rather than by finding every markdown
        table, because the plan has several and only this one is the register
        of settled decisions.
        """
        start = self.plan.index("## 2. Decisions made")
        end = self.plan.index("## 3.", start)
        rows = []
        for line in self.plan[start:end].splitlines():
            line = line.strip()
            if not line.startswith("|") or line.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and cells[0].lower() == "decision":
                continue                                  # header
            if cells:
                rows.append(cells[0])
        return rows

    def test_the_table_is_found_at_all(self):
        """If the heading moves, every assertion below passes vacuously."""
        self.assertGreaterEqual(len(self.decision_rows()), 5)

    def test_there_is_one_adr_per_decision(self):
        records = sorted(ADR.glob("0*.md"))
        self.assertEqual(
            len(records), len(self.decision_rows()),
            f"{len(self.decision_rows())} decisions in §2 and "
            f"{len(records)} records in docs/adr/")

    def test_every_record_states_a_status_and_names_its_source(self):
        for path in sorted(ADR.glob("0*.md")):
            with self.subTest(record=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, r"- Status: \w+")
                self.assertIn("§2", text)

    def test_every_record_says_what_was_turned_down(self):
        """A record with no rejected alternative is a note, not a decision.
        The point of the directory is that the next agent can see the option
        was considered rather than missed."""
        for path in sorted(ADR.glob("0*.md")):
            with self.subTest(record=path.name):
                self.assertIn("## Rejected",
                              path.read_text(encoding="utf-8"))

    def test_the_index_lists_every_record(self):
        index = (ADR / "README.md").read_text(encoding="utf-8")
        for path in sorted(ADR.glob("0*.md")):
            with self.subTest(record=path.name):
                self.assertIn(path.name, index)

    def test_the_index_lists_nothing_that_does_not_exist(self):
        index = (ADR / "README.md").read_text(encoding="utf-8")
        linked = set(re.findall(r"\((\d{4}-[a-z0-9-]+\.md)\)", index))
        present = {p.name for p in ADR.glob("0*.md")}
        self.assertEqual(linked - present, set(),
                         "the index links records that are not there")
