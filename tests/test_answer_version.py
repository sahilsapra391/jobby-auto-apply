"""The version an answer edit is made against, mirrored so a board can send it.

**A bank that becomes uneditable as it is used is a bug with a delay on it.**
Every Memory Bank action carries the version it was made against and
`store.cmd_answer_edit` refuses a mismatch as `conflict`. That refusal is the
guarantee. What was missing until migration 0017 is the number to send: v2
reads Postgres, `class_b.answer_row` did not carry the version, so v2 sent 0.
Correct exactly once per entry, because the first edit that lands takes it to 1.

Measured 2026-09-06 over all 195 stored entries: 156 at version 0, 38 at 1, 1
at 3.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import class_b     # noqa: E402
import mirror_fields  # noqa: E402
import store       # noqa: E402


class TheTwoReadingsOfAVersionAreOne(unittest.TestCase):
    """`class_b._bank_version` and `store.bank_version` are one rule.

    They are written twice because the mirror does not import the store, and
    two readings of one rule that happen to agree today is the shape this
    repository has paid for five times. So they are compared over the values
    that actually distinguish them, rather than asserted to be similar.
    """

    #: Every shape a stored entry's version has been seen in, plus the ones
    #: that would separate a lenient reading from a strict one.
    SHAPES = [
        {},                         # never edited
        {"version": 0},
        {"version": 1},
        {"version": 3},
        {"version": None},          # written and cleared
        {"version": ""},            # a board sending an empty field
        {"version": "2"},           # a JSON round trip through a string
        {"version": 2.0},
        {"version": "not a number"},
        {"version": []},
        {"version": {"n": 1}},
    ]

    def test_they_agree_on_every_shape_including_the_broken_ones(self):
        for entry in self.SHAPES:
            with self.subTest(entry=entry):
                self.assertEqual(class_b._bank_version(entry),
                                 store.bank_version(entry),
                                 "the mirror and the store read this entry's "
                                 "version differently, so an edit made against "
                                 "what the board was shown would be refused")

    def test_absent_is_zero_which_is_what_the_store_compares_against(self):
        # Not a tautology: `cmd_answer_edit` does `bank_version(stored) != seen`
        # with `seen` defaulting to 0, so a never-edited entry has to read 0 on
        # both sides or a first edit from a board is refused on arrival.
        self.assertEqual(class_b._bank_version({}), 0)
        self.assertEqual(class_b._bank_version(None), 0)


class TheMirrorCarriesIt(unittest.TestCase):
    def test_the_row_has_the_version(self):
        row = class_b.answer_row({"question": "Q", "value": "V", "version": 4})
        self.assertEqual(row["version"], 4)

    def test_the_insert_writes_it_and_mirror_fields_finds_it_unaided(self):
        """Discovered from the INSERT, not from a list anyone maintains.

        This is the property the column was added under: `mirror_fields` reads
        the literal `insert into answer_bank (...)`, so parity compares the new
        column without a second declaration of it. A test that asserted the
        column name appeared in a list would be that second declaration.
        """
        written = mirror_fields.written()["answer_bank"]
        self.assertIn("version", written)
        # And the expected side of parity is the mirror's own transform, so the
        # two agree by construction. Checked rather than assumed, because the
        # module that ended the declared-twice class had an instance of it
        # inside itself.
        self.assertEqual(set(class_b.answer_row({})) | {"account_id", "key"},
                         written - {"updated_at"})


class AnEditIsMadeAgainstTheVersionTheBoardRead(unittest.TestCase):
    """The round trip, driven through the real store.

    The point is not that `cmd_answer_edit` works, which it did before. It is
    that the number a board reads out of the mirror is the number that edit
    has to carry, twice running.
    """

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

    def ns(self, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(root=self.root.name, **kw)

    def edit(self, value, version, edit_id):
        path = Path(self.root.name) / f"{edit_id}.json"
        path.write_text(json.dumps({
            "editId": edit_id, "question": "What is your notice period?",
            "value": value, "version": version}), encoding="utf-8")
        return store.cmd_answer_edit(self.ns(input=str(path)))

    def stored(self, key):
        answers = json.loads(
            (Path(self.root.name) / "answers.json").read_text(encoding="utf-8"))
        return answers[key]

    def test_a_second_edit_needs_the_version_the_first_one_produced(self):
        first = self.edit("two weeks", 0, "e1")
        self.assertTrue(first["applied"])
        key = first["key"]
        mirrored = class_b.answer_row(self.stored(key))
        self.assertEqual(mirrored["version"], 1,
                         "the mirror would show a board version 0 for an entry "
                         "that has moved to 1, and its next edit is refused")

        # What v2 does: send back what it read.
        second = self.edit("one month", mirrored["version"], "e2")
        self.assertTrue(second["applied"])
        self.assertEqual(class_b.answer_row(self.stored(key))["version"], 2)

    def test_sending_a_constant_zero_is_refused_on_the_second_edit(self):
        """The bug this column closes, reproduced.

        Mutation-proof for the fix: with the column absent a board can only
        send 0, and this is exactly what happens on the second edit of any row.
        """
        first = self.edit("two weeks", 0, "e1")
        again = self.edit("one month", 0, "e2")
        self.assertFalse(again["applied"])
        self.assertEqual(again["reason"], "conflict")
        self.assertEqual(again["version"], 1)
        self.assertEqual(self.stored(first["key"])["value"], "two weeks",
                         "the refused edit changed the stored answer, so the "
                         "conflict check is reporting rather than guarding")

    def test_the_same_edit_id_twice_is_a_replay_and_not_a_conflict(self):
        """A board re-sends an edit it remembers, and that must stay free.

        `edit_outcomes` records `pending` on claim and the queue dedupes by
        edit id, but a replay can still reach the store: the answer is
        `already applied`, which `watch.py` deliberately does not report as a
        refusal.
        """
        self.edit("two weeks", 0, "e1")
        replay = self.edit("two weeks", 0, "e1")
        self.assertFalse(replay["applied"])
        self.assertEqual(replay["reason"], "already applied")
