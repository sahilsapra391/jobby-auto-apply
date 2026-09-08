"""The device can read its own decisions back, and the mapping is derived.

**`jobs_store` contains zero SELECTs, which is why the file store cannot be
deleted.** Every decision the device has ever made is mirrored into
`job_decisions` and nothing has read one back. Measured 2026-09-03: an absent
pool reads as empty, the next sweep repopulates from the connectors, and 16
watched, 100 applied and 11 passed vanish with no error anywhere.

This is the first Phase 4 module and it changes no authority. The file still
decides; this proves the read agrees with it.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "scripts"))

import backfill  # noqa: E402
import decisions  # noqa: E402


class Cursor:
    def __init__(self, rows): self.rows = rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, args=None): self.sql = sql
    def fetchall(self): return list(self.rows)
    def fetchone(self): return self.rows[0] if self.rows else (0,)


class Conn:
    def __init__(self, rows): self.rows = rows
    def cursor(self): return Cursor(self.rows)


class MappingTests(unittest.TestCase):
    def test_it_is_inverted_from_the_mirror_rather_than_restated(self):
        """A second copy of a field map is the shape this repo has paid for
        five times. A state added to `STATE_MAP` has to arrive here on its
        own."""
        expected = {decision: legacy
                    for legacy, (_c, decision) in backfill.STATE_MAP.items()
                    if decision}
        self.assertEqual(decisions.legacy_state(), expected)
        self.assertTrue(expected, "STATE_MAP yielded no decisions; vacuous")

    def test_the_two_directions_are_inverses(self):
        forward, back = decisions.decision_state(), decisions.legacy_state()
        for legacy, decision in forward.items():
            self.assertEqual(back[decision], legacy)
        self.assertEqual(len(forward), len(back),
                         "two pool states map to one decision, so the reverse "
                         "read cannot be unambiguous")

    def test_only_the_account_scoped_states_are_decisions(self):
        """`new` is the absence of a decision and `expired` is a fact about the
        posting, mirrored as `postings.closed_at`. Two accounts share the
        second and disagree about the first, which is why one table is
        account-scoped and the other is not."""
        mapped = set(decisions.decision_state())
        self.assertNotIn("new", mapped)
        self.assertNotIn("expired", mapped)
        self.assertEqual(mapped, {"watched", "passed", "applied"})

    def test_every_state_the_constraint_allows_can_be_read(self):
        """The migration's own check constraint is the authority on what the
        column can hold. A value it allows and this reader cannot map is a
        decision that would read as no decision."""
        sql = (REPO / "packages" / "schema" / "migrations"
               / "0002_corpus_and_decisions.sql").read_text(encoding="utf-8")
        block = sql[sql.index("job_decisions_state_is_known"):]
        block = block[:block.index(")")]
        allowed = {piece.strip().strip("',\n ")
                   for piece in block.split("'") if piece.strip(", \n()")}
        # `isalpha()` here silently dropped `needs_you`, which is the exact
        # failure this case exists to catch: a state the constraint allows,
        # invisible to the check that is supposed to enumerate them.
        allowed = {a for a in allowed
                   if a and a.replace("_", "").isalpha()
                   and a not in ("state", "in", "check")}
        self.assertTrue(allowed, "no states parsed out of the constraint")
        unreadable = sorted(allowed - set(decisions.legacy_state()))
        # `queued`, `applying` and `needs_you` are legal and nothing writes
        # them; v2_lanes.py says why. They are named rather than assumed.
        self.assertEqual(
            unreadable, ["applying", "needs_you", "queued"],
            f"the constraint allows {unreadable} and the reader maps none of "
            f"them; either the mirror can now write one, or this list moved")


class ReadTests(unittest.TestCase):
    def test_it_keys_on_the_pool_id_the_device_actually_holds(self):
        conn = Conn([("abc123", "submitted"), ("def456", "watching")])
        self.assertEqual(decisions.for_account(conn, "acct"),
                         {"abc123": "applied", "def456": "watched"})

    def test_an_unmappable_state_raises_rather_than_reading_as_no_decision(self):
        """The caller is about to treat an absent key as "no decision", which
        is the one reading that is always wrong. Silence here would make a
        schema change look like a user who had decided nothing."""
        conn = Conn([("abc123", "queued")])
        with self.assertRaises(ValueError) as caught:
            decisions.for_account(conn, "acct")
        self.assertIn("queued", str(caught.exception))
        self.assertIn("STATE_MAP", str(caught.exception))

    def test_a_decision_about_a_deleted_posting_is_counted_not_guessed(self):
        # The join is what makes the key resolvable, so an unjoinable decision
        # cannot be returned. "None matched" and "none exist" stay different.
        self.assertEqual(decisions.orphans(Conn([(3,)]), "acct"), 3)

    def test_from_pool_takes_the_three_decision_states_and_no_others(self):
        pool = {"a": {"state": "applied"}, "b": {"state": "new"},
                "c": {"state": "expired"}, "d": {"state": "watched"},
                "e": {}, "f": {"state": "passed"}}
        self.assertEqual(decisions.from_pool(pool),
                         {"a": "applied", "d": "watched", "f": "passed"})


class ComparisonTests(unittest.TestCase):
    def test_it_reports_the_sets_and_never_only_their_sizes(self):
        """Counts agreeing is the trap. Six Found filters each moved the count
        and one moved the rows."""
        out = decisions.compare({"a": "applied", "b": "watched"},
                                {"a": "applied", "c": "watched"})
        self.assertEqual(out["onlyOnDevice"], ["b"])
        self.assertEqual(out["onlyInPostgres"], ["c"])
        self.assertEqual(out["agreed"], 1)

    def test_a_state_disagreement_is_reported_with_both_values(self):
        out = decisions.compare({"a": "applied"}, {"a": "passed"})
        self.assertEqual(out["disagree"],
                         [{"id": "a", "device": "applied", "postgres": "passed"}])
        self.assertEqual(out["agreed"], 0)

    def test_two_sets_of_equal_size_that_share_nothing_are_not_agreement(self):
        out = decisions.compare({"a": "applied"}, {"b": "applied"})
        self.assertEqual(out["agreed"], 0)
        self.assertEqual(len(out["onlyOnDevice"]), 1)
        self.assertEqual(len(out["onlyInPostgres"]), 1)
