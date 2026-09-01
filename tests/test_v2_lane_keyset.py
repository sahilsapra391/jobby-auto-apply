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

    #: Per lane, so every lane gets rows by construction. The first version
    #: distributed them with `hash(legacy) % 6`, and Python randomises string
    #: hashing per process, so the fixture differed run to run and one lane
    #: came up empty -- a non-deterministic test for a bug about ordering.
    DATED = 9
    UNDATED = 4

    #: Found is a posting lane; the rest are application lanes, and the two
    #: are different objects with no link between them. See v2_lanes.
    #: Needs you is two statuses and the fixture has to contain both, or
    #: dropping either from the predicate passes.
    APP_STATUS = {"watch": ["watch"], "flight": ["inflight"],
                  "act": ["review", "active"], "dropped": ["dropped"]}

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

            # Found: postings with no decision row, dated and undated.
            found = []
            for i in range(self.DATED + self.UNDATED):
                posted = (None if i >= self.DATED
                          else NOW - timedelta(days=i % 4))   # ties on purpose
                legacy = f"found-{i:03d}"
                cur.execute(
                    """insert into postings (legacy_id, source, board_slug,
                                             external_id, title, employer_id,
                                             posted_at, last_seen_at, apply_url)
                       values (%s, 'greenhouse', 'acme', %s, %s, %s, %s, now(), %s)
                       returning id""",
                    (legacy, legacy, f"Role {legacy}", employer, posted,
                     f"https://example.com/{legacy}"))
                found.append((cur.fetchone()[0], posted))
            self.expected["found"] = found

            # A decision row so the Found predicate has something to exclude.
            cur.execute(
                """insert into postings (legacy_id, source, board_slug, external_id,
                                         title, employer_id, posted_at,
                                         last_seen_at, apply_url)
                   values ('decided', 'greenhouse', 'acme', 'decided', 'Decided',
                           %s, %s, now(), 'https://example.com/decided')
                   returning id""", (employer, NOW))
            self.decided = cur.fetchone()[0]
            cur.execute("""insert into job_decisions (account_id, posting_id,
                                                      state, decided_at)
                           values (%s, %s, 'watching', now())""",
                        (self.account, self.decided))

            # The application lanes. `updated_at` is NOT NULL, so these can
            # never have an undated row and the keyset's null branch is
            # unreachable here -- asserted below rather than left as a gap.
            for lane, statuses in self.APP_STATUS.items():
                rows = []
                for i in range(self.DATED):
                    status = statuses[i % len(statuses)]
                    cur.execute(
                        """insert into applications (account_id, legacy_id,
                                                     status, updated_at)
                           values (%s, %s, %s, %s) returning id, updated_at""",
                        (self.account, f"{lane}-{i:03d}", status,
                         NOW - timedelta(hours=i % 4)))     # ties on purpose
                    rows.append(tuple(cur.fetchone()))
                self.expected[lane] = rows

            # Submitted reads the history events, not a status. One of these
            # is retracted by a later `abandoned` and must not appear.
            done = []
            for i in range(self.DATED):
                cur.execute(
                    """insert into applications (account_id, legacy_id, status,
                                                 updated_at)
                       values (%s, %s, 'unknown', %s) returning id, updated_at""",
                    (self.account, f"done-{i:03d}", NOW - timedelta(hours=i % 4)))
                row = tuple(cur.fetchone())
                cur.execute("""insert into application_events
                               (account_id, application_id, kind, at, payload)
                               values (%s, %s, 'completed', %s, '{}')""",
                            (self.account, row[0], NOW - timedelta(hours=i % 4)))
                if i == 0:
                    # Retracted: a strictly later abandoned. Counted once
                    # before, and the role fell off the board entirely.
                    # Strictly later matters: the predicate is `r.at > h.at`,
                    # and the first version of this fixture wrote both at the
                    # same instant, so the retraction never applied and the
                    # test failed on its own timestamps rather than on the
                    # rule.
                    cur.execute("""insert into application_events
                                   (account_id, application_id, kind, at, payload)
                                   values (%s, %s, 'abandoned', %s, '{}')""",
                                (self.account, row[0], NOW + timedelta(hours=1)))
                else:
                    done.append(row)
            self.expected["done"] = done
            self.retracted = "done-000"

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
            # The cursor column differs by lane shape: postings order on
            # `posted_at` (index 6) and applications on `updated_at` (index 4).
            at = 6 if lane in v2_lanes.POSTING_LANES else 4
            for row in batch:
                seen.append((row[0], row[at]))
            after_id, after_at = batch[-1][0], batch[-1][at]
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

    def test_needs_you_contains_both_of_its_statuses(self):
        """The control for the predicate having two arms. A fixture with only
        `review` in it passes with `active` deleted from the lane."""
        with self.conn.cursor() as cur:
            cur.execute("""select distinct status from applications
                            where account_id = %s and legacy_id like 'act-%%'""",
                        (self.account,))
            self.assertEqual({r[0] for r in cur.fetchall()}, {"review", "active"})

    def test_only_posting_lanes_can_have_undated_rows(self):
        """Why the two assertions below are scoped to Found.

        `applications.updated_at` is NOT NULL, so an application lane cannot
        produce an undated row and the keyset's null branch is unreachable
        there. That is a property of the schema rather than of the fixture, so
        it is asserted: if the column ever becomes nullable, the scoping below
        becomes a hole and this fails first."""
        with self.conn.cursor() as cur:
            cur.execute("""select is_nullable from information_schema.columns
                            where table_name = 'applications'
                              and column_name = 'updated_at'""")
            self.assertEqual(cur.fetchone()[0], "NO")
            cur.execute("""select is_nullable from information_schema.columns
                            where table_name = 'postings'
                              and column_name = 'posted_at'""")
            self.assertEqual(cur.fetchone()[0], "YES",
                             "postings.posted_at stopped being nullable, so the "
                             "undated case this whole file is about is gone")

    def test_the_undated_rows_are_reachable_at_all(self):
        """The control for the two above. A keyset that stops at the first
        undated row still yields a correctly ordered prefix and would pass an
        ordering assertion; what it never does is arrive."""
        for lane in v2_lanes.POSTING_LANES:
            with self.subTest(lane=lane):
                expected_undated = sum(1 for _, at in self.expected[lane]
                                       if at is None)
                self.assertTrue(expected_undated, f"{lane} fixture has none")
                walked = self.walk(lane)
                got = sum(1 for _, at in walked if at is None)
                self.assertEqual(got, expected_undated,
                                 f"{lane} reached {got} of {expected_undated} "
                                 f"undated rows")

    def test_the_fixture_actually_has_undated_rows_in_every_posting_lane(self):
        """And the control for *that*: a fixture with no undated rows makes
        every assertion above vacuous, which is how this class of bug survives
        a test suite."""
        for lane in v2_lanes.POSTING_LANES:
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


class SubmittedReadsTheEventsNotAStatus(LaneCase):
    """A `completed` retracted by a later `abandoned` was counted once as
    submitted and the role fell off the board entirely -- neither submitted nor
    anywhere else. So the lane asks whether a completion is the application's
    last word, rather than trusting a status field to have been updated."""

    def test_a_retracted_completion_is_not_submitted(self):
        walked = {pid for pid, _ in self.walk("done")}
        with self.conn.cursor() as cur:
            cur.execute("select id from applications where legacy_id = %s",
                        (self.retracted,))
            retracted_id = cur.fetchone()[0]
        self.assertNotIn(retracted_id, walked)

    def test_control_an_unretracted_completion_is_submitted(self):
        """Without this, "the retracted one is absent" passes when the lane is
        empty. Its sibling has the same completion and no abandonment."""
        walked = {pid for pid, _ in self.walk("done")}
        with self.conn.cursor() as cur:
            cur.execute("select id from applications where legacy_id = 'done-001'")
            self.assertIn(cur.fetchone()[0], walked)

    def test_an_abandoned_BEFORE_the_completion_does_not_retract_it(self):
        """Order is the whole rule. An application abandoned and then later
        completed is submitted, and a predicate that only checked for the
        presence of an `abandoned` row would drop it."""
        with self.conn.cursor() as cur:
            cur.execute("""insert into applications (account_id, legacy_id,
                                                     status, updated_at)
                           values (%s, 'revived', 'unknown', %s) returning id""",
                        (self.account, NOW))
            app = cur.fetchone()[0]
            for kind, at in (("abandoned", NOW - timedelta(hours=3)),
                             ("completed", NOW - timedelta(hours=1))):
                cur.execute("""insert into application_events
                               (account_id, application_id, kind, at, payload)
                               values (%s, %s, %s, %s, '{}')""",
                            (self.account, app, kind, at))
        self.assertIn(app, {pid for pid, _ in self.walk("done")})

    def test_status_alone_would_have_given_a_different_answer(self):
        """The reason the lane reads events at all: every fixture row here has
        status 'unknown', so a status-based Submitted lane would return none of
        them."""
        with self.conn.cursor() as cur:
            cur.execute("""select count(*) from applications
                            where account_id = %s and status = 'submitted'""",
                        (self.account,))
            self.assertEqual(cur.fetchone()[0], 0)
        self.assertTrue(self.walk("done"), "the events lane found nothing either")


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


class FoundWalksByScoreToo(LaneCase):
    """The Found lane's other ordering, walked through the real keyset.

    **The tie-breaker is the row's identity, not the sort column's table.**
    Deriving it from the column alias worked while every lane sorted on its own
    table, and emitted `s.id` the moment Found sorted on `job_scores.total`,
    which has no id: the whole lane answered 500 with "column s.id does not
    exist". Nothing in the suite could see it, because nothing walked this
    ordering.
    """

    def setUp(self):
        super().setUp()
        # A score for every posting the Found lane can see, deliberately with
        # ties: a keyset over a non-unique column repeats or skips at the page
        # boundary without its tie-breaker, and a fixture of distinct values
        # cannot show that.
        with self.conn.cursor() as cur:
            cur.execute("select id from postings order by legacy_id")
            ids = [r[0] for r in cur.fetchall()]
            for index, posting in enumerate(ids):
                cur.execute(
                    "insert into job_scores (account_id, posting_id, total) "
                    "values (%s, %s, %s) on conflict do nothing",
                    (self.account, posting, 40 + (index % 5) * 10))
        self.scored = len(ids)

    def walk_by_score(self, page=2):
        sql = v2_lanes.page_sql("found", "score")
        seen, after_at, after_id = [], None, None
        for _ in range(500):
            with self.conn.cursor() as cur:
                cur.execute(sql, {"account": self.account, "limit": page,
                                  "after_at": after_at, "after_id": after_id})
                batch = cur.fetchall()
                at = [c.name for c in cur.description].index("score")
            if not batch:
                return seen
            for row in batch:
                seen.append((row[0], row[at]))
            after_id, after_at = batch[-1][0], batch[-1][at]
        raise AssertionError("the score keyset did not terminate")

    def test_the_fixture_actually_scored_something(self):
        """Without this every assertion below passes on an empty walk."""
        self.assertGreater(self.scored, 0)
        self.assertGreater(len(self.walk_by_score()), 0)

    def test_it_yields_the_same_set_as_the_date_ordering(self):
        by_score = {pid for pid, _ in self.walk_by_score()}
        by_date = {pid for pid, _ in self.walk("found")}
        self.assertEqual(by_score, by_date,
                         "the two orderings disagree about what is in the lane")

    def test_it_repeats_nothing_across_pages_despite_ties(self):
        walked = self.walk_by_score()
        ids = [pid for pid, _ in walked]
        self.assertEqual(len(ids), len(set(ids)), "a row appeared twice")

    def test_it_is_highest_first(self):
        totals = [total for _, total in self.walk_by_score()]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_the_ties_are_real(self):
        """The control for the two above: a walk over distinct values cannot
        show that the tie-breaker works."""
        totals = [total for _, total in self.walk_by_score()]
        self.assertLess(len(set(totals)), len(totals),
                        "no two rows share a score, so nothing here exercises "
                        "the tie-breaker")

    def test_the_predicate_never_names_the_score_tables_id(self):
        sql = v2_lanes.page_sql("found", "score")
        self.assertNotIn("s.id", sql)
        self.assertIn("p.id", sql)


if __name__ == "__main__":
    unittest.main()
