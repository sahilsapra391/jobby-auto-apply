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



class TheWinnerIsTheLiveRecord(unittest.TestCase):
    """A fold keeps the posting that is still open.

    Measured 2026-09-03 over every fold the store held: all three kept the
    record the sweep had already expired and filed the live one under it, so
    `postings` held "Operations Manager, $65,000-$85,000, closed" where the
    live posting at the same Breezy id was "3rd Shift Electronics Operations
    Supervisor, $50,000-$55,000". v2 reads Postgres, so it showed three open
    postings as closed, at the previous title and the previous pay.

    **These need no database, on purpose.** The collision decision is pure
    logic over the arriving records and whatever already holds their keys, and
    the machine that holds the real corpus has no Postgres on it -- 51 of the
    corpus check's skips are exactly that. So the loop is driven through a stub
    connection: the real `upsert_postings`, the real sort, the real branch, and
    a cursor that answers the incumbent query and records the statements.
    """

    def test_live_beats_closed_and_then_the_id_decides(self):
        self.assertLess(backfill.winner_key("zzzz", None),
                        backfill.winner_key("aaaa", "2026-09-02T00:00:00Z"))
        self.assertLess(backfill.winner_key("aaaa", None),
                        backfill.winner_key("bbbb", None))
        self.assertLess(backfill.winner_key("aaaa", "2026-09-02T00:00:00Z"),
                        backfill.winner_key("bbbb", "2026-09-02T00:00:00Z"))

    def test_the_record_carries_its_own_closedness_into_the_sort(self):
        live = {"id": "ffff", "closedAt": None}
        shut = {"id": "aaaa", "closedAt": "2026-09-02T00:00:00Z"}
        self.assertEqual(sorted([shut, live], key=backfill.BOARD_KEY_WINNER),
                         [live, shut])

    # -- the real loop, no database ---------------------------------------

    class _Cursor:
        def __init__(self, incumbents, log):
            self.incumbents, self.log, self._pending = incumbents, log, []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params=None):
            self.log.append((" ".join(sql.split()), list(params or [])))
            if "from postings where (source" in " ".join(sql.split()):
                self._pending = self.incumbents
            else:
                self._pending = []

        def fetchall(self):
            return self._pending

    class _Conn:
        def __init__(self, incumbents):
            self.incumbents, self.log = incumbents, []

        def cursor(self):
            return TheWinnerIsTheLiveRecord._Cursor(self.incumbents, self.log)

    def _drive(self, arriving, incumbents):
        conn = self._Conn(incumbents)
        employers = {backfill.norm_company("Acme"): "employer-1"}
        mapping, merged = backfill.upsert_postings(conn, arriving, employers)
        renames = [params for sql, params in conn.log
                   if sql.startswith("update postings set legacy_id")]
        return merged, renames, conn.log

    def test_a_live_record_displaces_a_closed_incumbent_that_sorts_lower(self):
        """**The case that decides whether the fix is real.**

        The closed incumbent's id sorts *below* the live arriving record's, so
        under the old id-only rule the incumbent wins and the live posting is
        folded away -- which is exactly what production did, three times out of
        three. It is also the case that a half-applied fix passes: leave the
        call site building `{"id": holder}` and the incumbent reads as live,
        the smaller id wins again, and every test drawn from the pool is still
        green.
        """
        live = record("ffff5555", "https://boards.greenhouse.io/acme/jobs/900",
                      title="Supervisor")
        arriving = [live]
        incumbents = [("greenhouse", "acme", "900", "aaaa1111",
                       "2026-09-02T15:47:07Z")]
        merged, renames, _log = self._drive(arriving, incumbents)

        self.assertEqual(merged, ["aaaa1111"],
                         "the closed incumbent should have been folded into "
                         "the live arriving record, not the other way round")
        self.assertEqual(renames, [["aaaa1111", "ffff5555"]],
                         "the live record must take the stored row rather than "
                         "get a new one; the uuid is what decisions point at")

    def test_a_closed_incumbent_still_wins_against_another_closed_record(self):
        """Closedness is the first key, not the only one. With nothing live,
        the id decides exactly as before, which is what keeps a winner from
        moving on every pass."""
        later = record("ffff5555", "https://boards.greenhouse.io/acme/jobs/900")
        later["closedAt"] = "2026-09-03T00:00:00Z"
        incumbents = [("greenhouse", "acme", "900", "aaaa1111",
                       "2026-09-02T15:47:07Z")]
        merged, renames, _log = self._drive([later], incumbents)
        self.assertEqual(merged, ["ffff5555"])
        self.assertEqual(renames, [])

    def test_a_live_incumbent_is_not_displaced_by_a_closed_arrival(self):
        shut = record("aaaa1111", "https://boards.greenhouse.io/acme/jobs/900")
        shut["closedAt"] = "2026-09-03T00:00:00Z"
        incumbents = [("greenhouse", "acme", "900", "ffff5555", None)]
        merged, renames, _log = self._drive([shut], incumbents)
        self.assertEqual(merged, ["aaaa1111"],
                         "the closed arrival folds into the live stored row")
        self.assertEqual(renames, [])

    def test_the_incumbent_query_actually_asks_for_closedness(self):
        """Edit 2 of four, pinned. Without `closed_at` in the select the
        unpack raises, but a future rewrite could drop it and default the
        value, which is the same inertness one layer down."""
        live = record("ffff5555", "https://boards.greenhouse.io/acme/jobs/900")
        _merged, _renames, log = self._drive([live], [])
        selects = [sql for sql, _ in log if "from postings where (source" in sql]
        self.assertTrue(selects)
        self.assertIn("closed_at", selects[0])

    def test_a_displaced_incumbent_is_reported_once_not_twice(self):
        """`run()` prints `len(report["merged"])` and joins the first five, so
        a doubled id is a wrong number in front of a person.

        The incumbent loses its key to the live arrival and then arrives in the
        same batch itself, which appends it on both branches. `main` has the
        same double whenever a displaced incumbent is in the batch; it was rare
        there because displacement needed a lucky id sort, and a live record
        now displaces a closed one on every retitle.
        """
        live = record("ffff5555", "https://boards.greenhouse.io/acme/jobs/900")
        shut = record("aaaa1111", "https://boards.greenhouse.io/acme/jobs/900")
        shut["closedAt"] = "2026-09-02T15:47:07Z"
        merged, renames, _log = self._drive([live, shut],
                                            [("greenhouse", "acme", "900",
                                              "aaaa1111", "2026-09-02T15:47:07Z")])
        self.assertEqual(merged, ["aaaa1111"])
        self.assertEqual(renames, [["aaaa1111", "ffff5555"]])

if __name__ == "__main__":
    unittest.main()
