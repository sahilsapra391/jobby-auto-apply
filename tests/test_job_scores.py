"""The match score reaches Postgres, and the absences stay absences.

Without it the Found lane can only sort by date, which on a corpus that
ingests everything puts warehouse night shifts above the roles the reader
wants. 17,339 rows in an order nobody asked for is the lane being unusable
rather than a missing feature.
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import backfill  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402


def record(legacy, total=None, **score):
    out = {"id": legacy, "canonicalSource": "greenhouse",
           "canonicalUrl": f"https://boards.greenhouse.io/acme/jobs/{legacy}",
           "company": "Acme", "title": "Engineer",
           "firstSeenAt": "2026-08-01T00:00:00Z",
           "lastSeenAt": "2026-09-01T00:00:00Z"}
    if total is not None:
        out["score"] = dict({"total": total}, **score)
    return out


class JobScoreTests(pgtest.SchemaCase):
    prefix = "jobscore"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('s@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]

    def mirror(self, records):
        employers, _ = backfill.upsert_employers(self.conn, records)
        postings, _ = backfill.upsert_postings(self.conn, records, employers)
        written = backfill.upsert_scores(self.conn, records, postings,
                                         self.account)
        return postings, written

    def scores(self):
        with self.conn.cursor() as cur:
            cur.execute("select total, parts, gates, config_version "
                        "from job_scores order by total desc")
            return cur.fetchall()

    # -- it works ---------------------------------------------------------

    def test_a_score_reaches_the_table_whole(self):
        self.mirror([record("a1", 64, parts={"title": 18, "location": 12},
                            why={"title": "matched"}, gates=[],
                            unknowns=["keywords"], configVersion="v23")])
        rows = self.scores()
        self.assertEqual(len(rows), 1)
        total, parts, gates, version = rows[0]
        self.assertEqual(total, 64)
        self.assertEqual(parts, {"title": 18, "location": 12})
        self.assertEqual(gates, [])
        self.assertEqual(version, "v23")

    def test_a_rescore_updates_rather_than_duplicating(self):
        self.mirror([record("a1", 40)])
        self.mirror([record("a1", 71)])
        self.assertEqual(self.scores(), [(71, {}, [], None)])

    def test_scores_are_per_account(self):
        """The same posting scores differently for two readers, which is the
        whole reason this is not a column on `postings`."""
        postings, _ = self.mirror([record("a1", 64)])
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('t@example.com') "
                        "returning id")
            other = cur.fetchone()[0]
        backfill.upsert_scores(self.conn, [record("a1", 12)], postings, other)
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from job_scores")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute("select total from job_scores where account_id = %s",
                        (self.account,))
            self.assertEqual(cur.fetchone()[0], 64)

    # -- the absences -----------------------------------------------------

    def test_a_record_with_no_score_is_skipped_not_written_as_zero(self):
        """**A zero is a decision and an absence is not.** 22 of 21,890 pool
        records carry no score because the scorer has not reached them; writing
        0 would sort them below every genuine low scorer."""
        _, written = self.mirror([record("a1"), record("a2", 55)])
        self.assertEqual(written, 1)
        self.assertEqual(self.scores(), [(55, {}, [], None)])

    def test_a_non_numeric_total_is_skipped(self):
        _, written = self.mirror([record("a1", "high")])
        self.assertEqual(written, 0)
        self.assertEqual(self.scores(), [])

    def test_two_records_folding_onto_one_posting_do_not_break_the_insert(self):
        """Postgres refuses `on conflict do update` when one statement presents
        the same key twice, and a board-key fold produces exactly that: two
        pool records, one posting row."""
        records = [
            record("5f10861ba0ce8980", 70),
            dict(record("9c9cf0baa815c1eb", 30),
                 canonicalUrl="https://boards.greenhouse.io/acme/jobs/5f10861ba0ce8980"),
        ]
        records[0]["canonicalUrl"] = \
            "https://job-boards.greenhouse.io/acme/jobs/5f10861ba0ce8980"
        postings, written = self.mirror(records)
        self.assertLessEqual(written, 1,
                             "the folded pair must produce at most one score row")
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from job_scores")
            self.assertEqual(cur.fetchone()[0], written)

    def test_the_mirror_actually_calls_it(self):
        """A writer nothing calls is the write-path counter again."""
        source = (REPO / "scripts" / "dual_write.py").read_text(encoding="utf-8")
        self.assertIn("backfill.upsert_scores(", source,
                      "mirror_pool does not call upsert_scores, so no score "
                      "ever reaches Postgres")


if __name__ == "__main__":
    unittest.main()
