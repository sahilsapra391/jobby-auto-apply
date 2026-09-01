"""A board key claimed twice must fold, wherever the other claimant is.

**This is the bug that froze the board for seventeen hours.** `postings` has
two unique constraints, `legacy_id` and `(source, board_slug, external_id)`.
`upsert_postings` resolved collisions *within a batch* in Python and then wrote
`on conflict (legacy_id)`, which covers the first constraint and not the
second. A record whose board key already belonged to a different stored row
therefore raised `UniqueViolation` inside a multi-row INSERT, aborting the
batch; class A dual-write fails the operation on a mirror error, so `save_jobs`
failed, the sweep could not persist, and every sweep after it failed the same
way. Measured on the real pool: **2 records of 20,835** were doing this.

Two of the three cases below are taken from the live pool as it stood on
2026-09-01, not invented:

    greenhouse braze   8139241        5f10861ba0ce8980  vs  9c9cf0baa815c1eb
    breezy     bitdeer eeab1edbc9db   1dc73a213adfc263  vs  3c12eb512130a4bb

Each test asserts which row survives *and* that the loser is reported. A fold
that drops a record silently is the same failure as the crash, moved somewhere
nobody looks: the record is in the file store, findable nowhere in Postgres,
and parity reports a divergence that is really a merge.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import backfill  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402


def record(legacy, url, source="greenhouse", company="Acme", title="Engineer"):
    return {"id": legacy, "canonicalSource": source, "canonicalUrl": url,
            "company": company, "title": title,
            "firstSeenAt": "2026-08-01T00:00:00Z",
            "lastSeenAt": "2026-09-01T00:00:00Z"}


class BoardKeyCollisionTests(pgtest.SchemaCase):
    prefix = "boardkey"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)

    # -- helpers ----------------------------------------------------------

    def upsert(self, records):
        """One mirror pass over `records`, returning (mapping, merged)."""
        values = list(records.values())
        employers, _contested = backfill.upsert_employers(self.conn, values)
        return backfill.upsert_postings(self.conn, values, employers)

    def stored(self):
        with self.conn.cursor() as cur:
            cur.execute("select legacy_id, id, title, merged_legacy_ids "
                        "from postings order by legacy_id")
            return cur.fetchall()

    # -- the three cases --------------------------------------------------

    def test_two_records_colliding_in_one_batch(self):
        """The Braze shape: one posting reached through two hostnames.

        Already handled before this change, and asserted here so the fix for
        the other two cannot regress it. One rule, both paths.
        """
        records = {
            "9c9cf0baa815c1eb": record(
                "9c9cf0baa815c1eb",
                "https://job-boards.greenhouse.io/braze/jobs/8139241"),
            "5f10861ba0ce8980": record(
                "5f10861ba0ce8980",
                "https://boards.greenhouse.io/braze/jobs/8139241"),
        }
        mapping, merged = self.upsert(records)

        rows = self.stored()
        self.assertEqual(len(rows), 1, "the two records must fold to one row")
        self.assertEqual(rows[0][0], "5f10861ba0ce8980",
                         "the lexicographically smallest legacy id wins")
        self.assertEqual(merged, ["9c9cf0baa815c1eb"],
                         "the loser is reported, never dropped quietly")
        self.assertEqual(rows[0][3], ["9c9cf0baa815c1eb"],
                         "the winner carries the loser's id")
        self.assertIn("5f10861ba0ce8980", mapping)

    def test_a_record_colliding_with_a_row_already_in_the_table(self):
        """**The case that raised.** The two records arrive in different
        passes, so the in-batch dedup never sees them together."""
        first = {"5f10861ba0ce8980": record(
            "5f10861ba0ce8980",
            "https://boards.greenhouse.io/braze/jobs/8139241")}
        self.upsert(first)
        before = self.stored()
        self.assertEqual(len(before), 1)
        uuid_before = before[0][1]

        second = {"9c9cf0baa815c1eb": record(
            "9c9cf0baa815c1eb",
            "https://job-boards.greenhouse.io/braze/jobs/8139241")}
        mapping, merged = self.upsert(second)

        rows = self.stored()
        self.assertEqual(len(rows), 1,
                         "the arriving record must fold into the stored row, "
                         "not raise and not create a second one")
        self.assertEqual(rows[0][0], "5f10861ba0ce8980")
        self.assertEqual(rows[0][1], uuid_before,
                         "the posting's uuid must not move: decisions point "
                         "at it and parity flaps if it changes")
        self.assertEqual(merged, ["9c9cf0baa815c1eb"],
                         "the folded record is reported")
        self.assertEqual(rows[0][3], ["9c9cf0baa815c1eb"])

    def test_a_retitle_colliding_with_its_own_earlier_self(self):
        """The Bitdeer shape, and the one that actually froze the board.

        The same posting is re-ingested under a new legacy id with a new title.
        Its board key is unchanged, so it collides with the row it replaces.
        The stored row keeps its uuid and takes the new title; the new legacy
        id is recorded as folded rather than becoming a second row.
        """
        first = {"1dc73a213adfc263": record(
            "1dc73a213adfc263", "https://bitdeer.breezy.hr/p/eeab1edbc9db",
            source="breezy", company="Bitdeer",
            title="Operations Technician (Mining Data Center-Night Shift)")}
        self.upsert(first)
        before = self.stored()
        uuid_before = before[0][1]

        second = {"3c12eb512130a4bb": record(
            "3c12eb512130a4bb", "https://bitdeer.breezy.hr/p/eeab1edbc9db",
            source="breezy", company="Bitdeer",
            title="Operations Technician, Night Shift")}
        mapping, merged = self.upsert(second)

        rows = self.stored()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "1dc73a213adfc263",
                         "the incumbent wins: it is lexicographically smaller")
        self.assertEqual(rows[0][1], uuid_before)
        self.assertEqual(merged, ["3c12eb512130a4bb"])
        self.assertEqual(rows[0][3], ["3c12eb512130a4bb"])

    def test_an_arriving_record_that_wins_takes_the_row_without_moving_it(self):
        """The other direction, which neither live case happened to be.

        Both live collisions had the incumbent winning, so a fix that simply
        said "the stored row always wins" would pass every test written from
        the pool and still be a second rule. `BOARD_KEY_WINNER` decides, over
        the incoming records and the stored row together, and the uuid stays
        put whichever way it goes.
        """
        first = {"bbbb00000000bbbb": record(
            "bbbb00000000bbbb",
            "https://boards.greenhouse.io/acme/jobs/77")}
        self.upsert(first)
        uuid_before = self.stored()[0][1]

        second = {"aaaa00000000aaaa": record(
            "aaaa00000000aaaa",
            "https://job-boards.greenhouse.io/acme/jobs/77")}
        mapping, merged = self.upsert(second)

        rows = self.stored()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "aaaa00000000aaaa",
                         "the arriving record is smaller, so it wins the key")
        self.assertEqual(rows[0][1], uuid_before,
                         "and it takes the existing row rather than a new one")
        self.assertEqual(merged, ["bbbb00000000bbbb"],
                         "the displaced incumbent is reported as folded")
        self.assertEqual(rows[0][3], ["bbbb00000000bbbb"])

    def test_the_same_pool_twice_changes_nothing(self):
        """Idempotency, and it is in this file because this change broke it.

        Once the stored row is a claimant for its own key, the obvious
        comparison folds every record into itself on the second pass: nothing
        is written, and the merged-ids update then sets an empty array on a NOT
        NULL column. Five existing tests caught that and none of the new ones
        did, which is the argument for writing the regression down here rather
        than relying on the same five to catch the next one.
        """
        records = {f"id{i}": record(
            f"id{i}", f"https://boards.greenhouse.io/acme/jobs/{i}")
            for i in range(4)}
        self.upsert(records)
        first = self.stored()
        mapping, merged = self.upsert(records)
        second = self.stored()
        self.assertEqual(first, second, "a second identical pass moved a row")
        self.assertEqual(merged, [], "an unchanged pool folds nothing")
        self.assertEqual(len(mapping), 4)
        for row in second:
            self.assertEqual(row[3], [],
                             "merged_legacy_ids must stay empty, not become "
                             "null and not absorb the row's own id")

    def test_a_clean_batch_still_writes_every_row(self):
        """The control. Without it every assertion above is satisfied by an
        upsert that folds everything into nothing."""
        records = {f"id{i}": record(
            f"id{i}", f"https://boards.greenhouse.io/acme/jobs/{i}")
            for i in range(5)}
        mapping, merged = self.upsert(records)
        self.assertEqual(len(self.stored()), 5)
        self.assertEqual(merged, [])
        self.assertEqual(len(mapping), 5)


if __name__ == "__main__":
    unittest.main()
