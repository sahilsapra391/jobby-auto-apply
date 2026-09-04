"""The answer mirror may not empty the bank, and the tests must prove it ran.

On 2026-08-30 `class_b._mirror_answers` had neither a floor nor a ceiling while
`dual_write.resync` next door had both. Four payloads, four seeded answers, an
isolated scratch schema:

    {}                    -> ALL 4 DELETED
    None                  -> ALL 4 DELETED
    {"gender": "oops"}    -> ALL 4 DELETED
    {"gender": {...}}     -> 1 left, correct

On the real store that is 190 standing answers: relocation, visa, work
authorisation, the ones an employer's form asks and the agent answers from
memory. All four are tests here, asserting a surviving row count rather than an
exception type, because the exception is the mechanism and the row count is the
promise.

**Every refusal test is paired with a control that fails if the mirror stops
engaging at all.** A test asserting "nothing was deleted" passes perfectly when
the function has been commented out, and this repository has now paid five
times for a check that passed while measuring nothing.
"""

import sys
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import pgtest      # noqa: E402
import class_b     # noqa: E402

SEEDED = ("relocation", "visa-sponsorship", "work-authorisation", "notice-period")


class AnswerBankCase(pgtest.SchemaCase):
    prefix = "answerbank"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        with self.conn.cursor() as cur:
            # **The real schema, not a copy of it.** These two tables were
            # spelled out here, and migration 0014 adding `kind` and
            # `category` to `answer_bank` broke eight tests that were correct
            # about the mirror and stale about the table. A fixture that
            # restates a schema drifts from it exactly the way two
            # implementations of one query do.
            pgtest.apply_migrations(self.conn)
            cur.execute("insert into accounts (email) values (%s) returning id",
                        ("bank-test@example.com",))
            self.account = cur.fetchone()[0]

    def seed(self, keys=SEEDED):
        with self.conn.cursor() as cur:
            cur.execute("delete from answer_bank")
            for k in keys:
                cur.execute("insert into answer_bank (account_id, key, value) "
                            "values (%s, %s, %s)", (self.account, k, "a real answer"))

    def keys(self):
        with self.conn.cursor() as cur:
            cur.execute("select key from answer_bank where account_id = %s "
                        "order by key", (self.account,))
            return [r[0] for r in cur.fetchall()]

    def entry(self, value="yes"):
        return {"question": "?", "value": value, "state": "answered",
                "tags": [], "sensitive": False}


class TheFloorRefusesAnEmptyPayload(AnswerBankCase):
    def test_an_empty_dict_deletes_nothing(self):
        self.seed()
        with self.assertRaises(class_b.ClassBError):
            class_b._mirror_answers(self.conn, self.account, {})
        self.assertEqual(self.keys(), sorted(SEEDED))

    def test_none_deletes_nothing(self):
        self.seed()
        with self.assertRaises(class_b.ClassBError):
            class_b._mirror_answers(self.conn, self.account, None)
        self.assertEqual(self.keys(), sorted(SEEDED))

    def test_a_non_mapping_payload_deletes_nothing(self):
        self.seed()
        with self.assertRaises(class_b.ClassBError):
            class_b._mirror_answers(self.conn, self.account, ["relocation"])
        self.assertEqual(self.keys(), sorted(SEEDED))

    def test_force_does_not_defeat_the_floor(self):
        """**This is the test that makes the floor load-bearing**, and it was
        missing from the first version.

        Deleting the floor and running the suite passed, because for an unforced
        call the ceiling already refuses an empty payload: nothing survives it,
        so the doomed share is 100% and always over 25%. The two guards overlap
        everywhere except here. `force=True` is a caller saying "I meant that
        deletion", and it bypasses the ceiling by design -- so with the floor
        gone, a forced empty payload empties the bank with nothing left to stop
        it. Found by running the mutation rather than by reading the code."""
        self.seed()
        with self.assertRaises(class_b.ClassBError):
            class_b._mirror_answers(self.conn, self.account, {}, force=True)
        self.assertEqual(self.keys(), sorted(SEEDED))
        with self.assertRaises(class_b.ClassBError):
            class_b._mirror_answers(self.conn, self.account, None, force=True)
        self.assertEqual(self.keys(), sorted(SEEDED))

    def test_control_the_mirror_still_deletes_when_it_should(self):
        """Without this, every assertion above passes with the whole function
        deleted. One key drops out of a four-key payload, 25% and therefore at
        the ceiling rather than over it, and it must actually go."""
        self.seed()
        payload = {k: self.entry() for k in SEEDED if k != "notice-period"}
        class_b._mirror_answers(self.conn, self.account, payload)
        self.assertEqual(self.keys(), sorted(set(SEEDED) - {"notice-period"}),
                         "the mirror stopped deleting entirely, so the "
                         "refusal tests above prove nothing")


class AMalformedEntryDoesNotDeleteItsNeighbours(AnswerBankCase):
    """The worst of the four, because the payload looked valid enough to
    proceed. One unreadable entry produced an empty `rows`, and the emptiness
    was read as "the file lists no answers"."""

    def test_one_unreadable_entry_leaves_every_key_standing(self):
        self.seed()
        class_b._mirror_answers(self.conn, self.account,
                                {k: (self.entry() if k != "visa-sponsorship"
                                     else "oops") for k in SEEDED})
        self.assertEqual(self.keys(), sorted(SEEDED))

    def test_the_original_reproduction_deletes_nothing(self):
        """Verbatim: a single-key payload whose only entry is a string. Three
        of four keys are absent from it, which is 75% and over the ceiling, so
        it is refused rather than partly applied."""
        self.seed()
        with self.assertRaises(class_b.ClassBError):
            class_b._mirror_answers(self.conn, self.account, {"gender": "oops"})
        self.assertEqual(self.keys(), sorted(SEEDED))

    def test_control_an_unreadable_entry_is_not_silently_swallowed(self):
        """It is skipped for the upsert, so it must say so somewhere. The one
        place that fact is ever produced is this line."""
        import io
        from unittest import mock
        self.seed()
        stream = io.StringIO()
        with mock.patch.object(sys, "stderr", stream), \
             mock.patch("builtins.print") as printed:
            class_b._mirror_answers(
                self.conn, self.account,
                {k: (self.entry() if k != "visa-sponsorship" else "oops")
                 for k in SEEDED})
        self.assertIn("visa-sponsorship", stream.getvalue())
        printed.assert_not_called()


class TheCeilingBoundsALargeDeletion(AnswerBankCase):
    def test_deleting_most_of_the_bank_is_refused(self):
        self.seed()
        class_b._mirror_answers  # noqa
        with self.assertRaises(class_b.ClassBError) as caught:
            class_b._mirror_answers(self.conn, self.account,
                                    {"relocation": self.entry()})
        self.assertIn("ceiling", str(caught.exception))
        self.assertEqual(self.keys(), sorted(SEEDED))

    def test_force_is_how_a_caller_says_it_meant_it(self):
        self.seed()
        class_b._mirror_answers(self.conn, self.account,
                                {"relocation": self.entry()}, force=True)
        self.assertEqual(self.keys(), ["relocation"])

    def test_a_small_deletion_passes_under_the_ceiling(self):
        """The control for the ceiling. Eight keys, one removed: 12.5%, well
        under, and it must go through. Without this a ceiling of zero would
        pass every other test in this class."""
        eight = tuple(f"answer-{i}" for i in range(8))
        self.seed(eight)
        payload = {k: self.entry() for k in eight[:-1]}
        class_b._mirror_answers(self.conn, self.account, payload)
        self.assertEqual(self.keys(), sorted(eight[:-1]))

    def test_the_ceiling_matches_class_a(self):
        """Two numbers that were one asymmetry. If a future change moves one,
        this fails rather than letting them drift apart silently."""
        import dual_write
        self.assertEqual(class_b.ANSWER_DELETE_CEILING,
                         dual_write.RESYNC_DELETE_CEILING)


class TheMirrorStillDoesItsJob(AnswerBankCase):
    """Broad controls. Everything above is about refusing; these are about the
    function remaining a mirror."""

    def test_a_new_answer_is_inserted(self):
        self.seed()
        payload = {k: self.entry() for k in SEEDED}
        payload["new-key"] = self.entry("added")
        class_b._mirror_answers(self.conn, self.account, payload)
        self.assertIn("new-key", self.keys())

    def test_an_existing_answer_is_updated(self):
        self.seed()
        payload = {k: self.entry() for k in SEEDED}
        payload["relocation"] = self.entry("changed")
        class_b._mirror_answers(self.conn, self.account, payload)
        with self.conn.cursor() as cur:
            cur.execute("select value from answer_bank where account_id = %s "
                        "and key = 'relocation'", (self.account,))
            self.assertEqual(cur.fetchone()[0], "changed")

    def test_an_empty_bank_accepts_a_first_write(self):
        """The floor must not make a first run impossible: nothing held, so
        nothing can be deleted, and the payload should simply land."""
        with self.conn.cursor() as cur:
            cur.execute("delete from answer_bank")
        class_b._mirror_answers(self.conn, self.account,
                                {"relocation": self.entry()})
        self.assertEqual(self.keys(), ["relocation"])
