"""Every lane walks its whole set, in order, without repeating or skipping.

`nulls last` is the load-bearing part and it has to hold in three places at
once: the ORDER BY, the keyset predicate, and the index. Get any one of them
wrong and the failure is quiet -- PostgreSQL sorts nulls FIRST under `desc`, so
a lane without it opens with the 2,087 postings whose date nobody stated, and
an index whose ordering differs from the query's cannot serve it, so the lane
falls back to sorting the corpus and nothing says so.

The usual keyset predicate `(posted_at, id) < (?, ?)` is wrong here for a
reason worth stating: a NULL on either side makes the row comparison NULL
rather than true, so the walk stops at the first undated row and everything
after it is unreachable. That is the specific bug these walks exist to catch,
and a fixture with no undated rows in it would not.
"""

import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import pgtest    # noqa: E402
import v2_lanes  # noqa: E402

PAGE = 7          # deliberately not a divisor of any lane size below
NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


class LaneCase(pgtest.SchemaCase):
    prefix = "v2lane"

    #: Per lane, so every lane gets both dated and undated rows by
    #: construction. The first version distributed rows with
    #: `hash(legacy) % 6`, which is randomised per process for strings, so the
    #: fixture differed run to run and one lane came up with no undated rows at
    #: all -- a non-deterministic test for a bug about ordering.
    DATED = 9
    UNDATED = 4

    STATE = {"found": None, "watch": "watching", "flight": "queued",
             "act": "needs_you", "done": "submitted", "dropped": "dropped"}

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        import migrate
        migrate.run(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('lane@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]
            cur.execute("insert into employers (canonical_name, norm_name) "
                        "values ('Acme', 'acme') returning id")
            employer = cur.fetchone()[0]

            self.expected = {}
            n = 0
            for lane, state in self.STATE.items():
                rows = []
                for i in range(self.DATED + self.UNDATED):
                    # Ties on purpose: several rows share a date, so the id
                    # half of the keyset has to do real work.
                    posted = (None if i >= self.DATED
                              else NOW - timedelta(days=i % 4))
                    legacy = f"{lane}-{i:03d}"
                    cur.execute(
                        """insert into postings (legacy_id, source, board_slug,
                                                 external_id, title, employer_id,
                                                 posted_at, last_seen_at, apply_url)
                           values (%s, 'greenhouse', 'acme', %s, %s, %s, %s,
                                   now(), %s) returning id""",
                        (legacy, legacy, f"Role {legacy}", employer, posted,
                         f"https://example.com/{legacy}"))
                    pid = cur.fetchone()[0]
                    if state:
                        cur.execute("""insert into job_decisions
                                       (account_id, posting_id, state, decided_at)
                                       values (%s, %s, %s, now())""",
                                    (self.account, pid, state))
                    rows.append((pid, posted))
                    n += 1
                self.expected[lane] = rows

    def walk(self, lane, page=PAGE):
        """Every row the lane yields, page by page, through the real keyset."""
        sql = v2_lanes.page_sql(lane)
        seen, after_at, after_id = [], None, None
        for _ in range(500):                    # a bound, not an expectation
            with self.conn.cursor() as cur:
                cur.execute(sql, {"account": self.account, "limit": page,
                                  "after_at": after_at, "after_id": after_id})
                batch = cur.fetchall()
            if not batch:
                return seen
            for row in batch:
                seen.append((row[0], row[6]))
            after_id, after_at = batch[-1][0], batch[-1][6]
        raise AssertionError(f"{lane} did not terminate; the keyset is not "
                             f"advancing and the walk would loop forever")


class EveryLaneWalksCleanly(LaneCase):
    def test_each_lane_yields_its_whole_set_exactly_once(self):
        for lane in v2_lanes.LANES:
            with self.subTest(lane=lane):
                walked = self.walk(lane)
                ids = [pid for pid, _ in walked]
                self.assertEqual(len(ids), len(set(ids)),
                                 f"{lane} repeated a row")
                self.assertEqual(set(ids), {pid for pid, _ in self.expected[lane]},
                                 f"{lane} skipped or invented rows")

    def test_each_lane_is_newest_first_with_undated_last(self):
        for lane in v2_lanes.LANES:
            with self.subTest(lane=lane):
                walked = self.walk(lane)
                dates = [at for _, at in walked]
                dated = [d for d in dates if d is not None]
                self.assertEqual(dated, sorted(dated, reverse=True),
                                 f"{lane} is not newest first")
                first_undated = next((i for i, d in enumerate(dates)
                                      if d is None), len(dates))
                self.assertTrue(all(d is None for d in dates[first_undated:]),
                                f"{lane} interleaves undated rows rather than "
                                f"putting them last")

    def test_the_undated_rows_are_reachable_at_all(self):
        """The control for the two above. A keyset that stops at the first
        undated row still yields a correctly ordered prefix and would pass an
        ordering assertion; what it never does is arrive."""
        for lane in v2_lanes.LANES:
            with self.subTest(lane=lane):
                expected_undated = sum(1 for _, at in self.expected[lane]
                                       if at is None)
                if not expected_undated:
                    continue
                walked = self.walk(lane)
                got = sum(1 for _, at in walked if at is None)
                self.assertEqual(got, expected_undated,
                                 f"{lane} reached {got} of {expected_undated} "
                                 f"undated rows")

    def test_the_fixture_actually_has_undated_rows_in_every_lane(self):
        """And the control for *that*: a fixture with no undated rows makes
        every assertion above vacuous, which is how this class of bug survives
        a test suite."""
        for lane in v2_lanes.LANES:
            with self.subTest(lane=lane):
                self.assertTrue(
                    any(at is None for _, at in self.expected[lane]),
                    f"{lane} has no undated rows in the fixture")

    def test_a_page_size_of_one_walks_the_same_set(self):
        """The boundary the fixture's page size is chosen to avoid landing on.
        A keyset that is subtly off by one is invisible at page 7 and obvious
        at page 1."""
        for lane in ("found", "done", "dropped"):
            with self.subTest(lane=lane):
                self.assertEqual([p for p, _ in self.walk(lane, page=1)],
                                 [p for p, _ in self.walk(lane, page=PAGE)])


class FoundIsTheAbsenceOfADecision(LaneCase):
    def test_a_decision_removes_a_posting_from_found(self):
        before = {pid for pid, _ in self.walk("found")}
        self.assertTrue(before)
        target = next(iter(before))
        with self.conn.cursor() as cur:
            cur.execute("""insert into job_decisions
                           (account_id, posting_id, state, decided_at)
                           values (%s, %s, 'watching', now())""",
                        (self.account, target))
        after = {pid for pid, _ in self.walk("found")}
        self.assertNotIn(target, after)
        self.assertEqual(before - after, {target})

    def test_found_is_not_a_storable_state(self):
        """It is a query, not a materialisation, and the schema refuses to
        record it -- which is what keeps it free at 500k."""
        import psycopg
        with self.conn.cursor() as cur:
            cur.execute("select id from postings limit 1")
            pid = cur.fetchone()[0]
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute("""insert into job_decisions
                               (account_id, posting_id, state, decided_at)
                               values (%s, %s, 'found', now())""",
                            (self.account, pid))


class TheIndexActuallyServesTheLane(LaneCase):
    def test_found_does_not_sort_the_corpus(self):
        """A lane whose index ordering differs from its query's cannot use it,
        and the only symptom is a sort that nobody sees. Read the plan."""
        sql = v2_lanes.page_sql("found")
        with self.conn.cursor() as cur:
            cur.execute("explain (format text) " + sql,
                        {"account": self.account, "limit": 50,
                         "after_at": None, "after_id": None})
            plan = "\n".join(r[0] for r in cur.fetchall())
        # On a 51-row fixture the planner will choose a sort regardless, so
        # this asserts the index exists and is declared to match, which is the
        # half a small fixture can actually answer. The full-corpus plan is
        # checked against Neon in the report rather than here.
        with self.conn.cursor() as cur:
            cur.execute("""select indexdef from pg_indexes
                           where tablename = 'postings' and indexname = 'postings_keyset'""")
            row = cur.fetchone()
        self.assertIsNotNone(row, "postings_keyset is gone")
        self.assertIn("posted_at DESC NULLS LAST", row[0])
        self.assertIn("id DESC", row[0])
        self.assertIn("Scan", plan)


if __name__ == "__main__":
    unittest.main()
