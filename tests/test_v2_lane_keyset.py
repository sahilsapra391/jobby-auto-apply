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
import json
import re
import unittest

#: The Found lane's filter parameters, all off. Every generated Found query
#: names them now, and psycopg raises on a missing key rather than defaulting,
#: which is the behaviour to want: a walk that silently filtered would report a
#: smaller lane as correct.
NO_FILTERS = {"q": None, "within": None, "direct": None}
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

            # The application lanes, each populated in the column its own
            # ordering walks.
            #
            # **This used to write `updated_at` and nothing else**, which was
            # right while every application lane ordered by it and became a
            # fixture that could not see the ordering the moment three of them
            # stopped. Worse, it made the keyset's null branch unreachable for
            # every application lane, because `updated_at` is NOT NULL -- and
            # `dropped_at` and `posted_at` are both nullable, so that branch is
            # now live in production and the fixture has to reach it.
            for lane, statuses in self.APP_STATUS.items():
                rows = []
                for i in range(self.DATED):
                    status = statuses[i % len(statuses)]
                    # Ties on purpose in every column, so the id tie-breaker is
                    # exercised rather than assumed.
                    #
                    # **Every column is a different permutation of the same
                    # ties, and that is the load-bearing part.** The first
                    # version derived them all from `i % 4`, so ordering by
                    # `posted_at` and ordering by `updated_at` produced the
                    # same sequence -- and reverting Watching, Dropped and
                    # Submitted to `updated_at` passed every test in this file.
                    # A fixture that makes two orderings agree cannot tell them
                    # apart, which is the whole question here.
                    when = NOW - timedelta(hours=i % 4)
                    other = (i * 3) % 4                 # 0,3,2,1,0,3,2,1,0
                    # Nullable for two lanes, and left null on a third of the
                    # rows so "undated last" has something to order.
                    absent = i % 3 == 2
                    posted = None if absent else NOW - timedelta(days=other)
                    dropped = None if absent else NOW - timedelta(hours=other)
                    # Needs you orders on how many blockers a row has, fewest
                    # first. Three distinct counts, so an ordering that ignored
                    # them could not pass by luck.
                    blockers = json.dumps([{"question": f"q{n}"}
                                           for n in range(i % 3 + 1)])
                    cur.execute(
                        """insert into applications (account_id, legacy_id,
                                                     status, updated_at,
                                                     posted_at, dropped_at,
                                                     blockers)
                           values (%s, %s, %s, %s, %s, %s, %s)
                           returning id, updated_at""",
                        (self.account, f"{lane}-{i:03d}", status, when,
                         posted, dropped, blockers))
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
                # A different permutation from `updated_at` again, so a lane
                # reverted to `updated_at` cannot pass by producing the same
                # sequence. See the note in the application-lane loop.
                cur.execute("""insert into application_events
                               (account_id, application_id, kind, at, payload)
                               values (%s, %s, 'completed', %s, '{}')""",
                            (self.account, row[0],
                             NOW - timedelta(hours=(i * 3) % 4)))
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

    def cursor_key(self, lane):
        """The row key this lane's ordering walks, from `v2_lanes` itself.

        **Asked, not counted.** This was two hardcoded column indexes -- 6 for
        postings and 4 for applications -- which is the ORDER BY restated in a
        second language, and it broke the moment three lanes changed what they
        order by. It did not break usefully either: reading `updated_at` as the
        cursor of a lane ordered by blocker count yields a walk that terminates
        and reports a wrong set.
        """
        return (v2_lanes.SORTS["new"][0].split(".")[-1]
                if lane in v2_lanes.POSTING_LANES
                else v2_lanes.application_order(lane)[1])

    def rows(self, lane, page, after_at=None, after_id=None):
        """One page, as `(id, cursor)` pairs with the cursor found by name."""
        with self.conn.cursor() as cur:
            cur.execute(v2_lanes.page_sql(lane),
                        {"account": self.account, "limit": page,
                         "after_at": after_at, "after_id": after_id,
                         **NO_FILTERS})
            names = [d.name for d in cur.description]
            batch = cur.fetchall()
        key = self.cursor_key(lane)
        self.assertIn(key, names,
                      f"{lane} orders by {key} and does not return it, so no "
                      f"cursor can be cut from its own rows")
        at = names.index(key)
        return [(row[0], row[at]) for row in batch]

    def full(self, lane):
        """The whole lane in one page, which is the ordering with no keyset.

        The control for `walk`: both share the ORDER BY, so a disagreement
        between them is the keyset predicate and nothing else.
        """
        return self.rows(lane, 10000)

    def walk(self, lane, page=PAGE):
        """Every row the lane yields, page by page, through the real keyset."""
        seen, after_at, after_id = [], None, None
        for _ in range(500):                    # a bound, not an expectation
            batch = self.rows(lane, page, after_at, after_id)
            if not batch:
                return seen
            seen.extend(batch)
            after_id, after_at = batch[-1][0], batch[-1][1]
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

    def test_each_lane_is_highest_first_with_absent_last(self):
        """Descending on the lane's own cursor, absent values last.

        **Not "newest first", which three lanes are not.** Needs you leads with
        the row closest to done and walks a negated blocker count; Watching and
        Dropped walk dates that are not `updated_at`. What every lane does have
        is one ordering, descending, `nulls last` -- that is what `order_by`
        emits and it is the property worth asserting, because reading it as
        "newest" is what made this test encode one lane's choice as a rule.
        """
        for lane in v2_lanes.LANES:
            with self.subTest(lane=lane):
                values = [at for _, at in self.walk(lane)]
                present = [v for v in values if v is not None]
                self.assertEqual(present, sorted(present, reverse=True),
                                 f"{lane} is not ordered highest first")
                first_absent = next((i for i, v in enumerate(values)
                                     if v is None), len(values))
                self.assertTrue(all(v is None for v in values[first_absent:]),
                                f"{lane} interleaves rows with no value rather "
                                f"than putting them last")

    def test_the_keyset_walk_agrees_with_the_unpaged_ordering(self):
        """The control the ordering assertion cannot be: a keyset that stops
        early, or restarts, still yields a correctly ordered prefix.

        Both sides share the ORDER BY, so any disagreement is the keyset
        predicate. This is what catches a cursor cut from the wrong column: it
        does not error, it silently walks a different sequence.
        """
        for lane in v2_lanes.LANES:
            with self.subTest(lane=lane):
                self.assertEqual([i for i, _ in self.walk(lane)],
                                 [i for i, _ in self.full(lane)],
                                 f"{lane} pages in a different order from the "
                                 f"order it sorts in")

    def test_needs_you_contains_both_of_its_statuses(self):
        """The control for the predicate having two arms. A fixture with only
        `review` in it passes with `active` deleted from the lane."""
        with self.conn.cursor() as cur:
            cur.execute("""select distinct status from applications
                            where account_id = %s and legacy_id like 'act-%%'""",
                        (self.account,))
            self.assertEqual({r[0] for r in cur.fetchall()}, {"review", "active"})

    def test_the_null_branch_is_reachable_from_more_than_one_lane(self):
        """The keyset's null arms are live code, and which lanes reach them.

        This used to assert the opposite -- that no application lane could
        produce a null cursor, because `updated_at` is NOT NULL -- and used
        that to scope the undated assertions to Found. Migration 0015 and the
        Watching ordering ended it: `dropped_at` and `posted_at` are both
        nullable, so three lanes now walk a column that can be absent and the
        branch that used to be unreachable outside Found is the common case.

        Asked of the walk rather than of a list, so a lane that stops producing
        nulls is a failure here rather than a silently vacuous assertion
        somewhere else."""
        reached = {lane for lane in v2_lanes.LANES
                   if any(v is None for _, v in self.full(lane))}
        self.assertGreaterEqual(len(reached), 3,
                                f"only {sorted(reached)} produce a null cursor; "
                                f"the keyset's null arms are barely exercised")
        with self.conn.cursor() as cur:
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
                         "after_at": None, "after_id": None, **NO_FILTERS})
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
                                  "after_at": after_at, "after_id": after_id,
                                  **NO_FILTERS})
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


class EveryOrderingBreaksTiesOnItsOwnRow(unittest.TestCase):
    """**The general form, because Found-by-score is only the first instance.**

    Until a lane sorted on another table, deriving the keyset tie-breaker from
    the sort column's alias was always right: every lane ordered on its own
    table, so the alias and the row's identity were the same letter. The
    six-lane walk could not see the defect because the defect did not exist
    yet, and the test written for the instance would not see the next one
    either. Any lane that later sorts on something other than its own row has
    the same fault waiting.

    So this reads every `(lane, sort)` the generator emits and asserts the
    ordering breaks ties on the lane's *own* row, whatever it sorts by. It
    needs no database.

    The identity is derived from each query's own FROM clause rather than
    restated here, so a lane built on a third table is covered the day it
    exists.
    """

    def pairs(self):
        for lane in v2_lanes.LANES:
            sorts = (v2_lanes.SORTS if lane in v2_lanes.POSTING_LANES
                     else {"new": None})
            for sort in sorts:
                yield lane, sort, v2_lanes.page_sql(lane, sort)

    def identity(self, sql):
        """The alias of the table the lane is a lane *of*, from its own FROM.

        **The statement's own FROM, not the first one in the text.** It was the
        first, until the application lanes grew correlated subqueries in the
        SELECT list: `from application_events h` then appears before
        `from applications a`, and this read the lane's identity as `h`. It
        failed a lane whose ordering had not changed, which is a test asserting
        a shape the SQL is allowed to outgrow.

        The statement's own FROM is the one at the start of a line with no
        leading parenthesis in front of it; a subquery's sits inside the
        SELECT list and is indented under one.
        """
        found = [m for m in re.finditer(r"(?m)^\s*from\s+(\w+)\s+(\w+)", sql)]
        self.assertTrue(found, "the query has no FROM clause to read")
        return found[0].group(2)

    def test_there_is_more_than_one_pair_to_check(self):
        """Without this the whole class passes on an empty generator."""
        pairs = list(self.pairs())
        self.assertGreater(len(pairs), len(list(v2_lanes.LANES)),
                           "no lane offers a second sort, so nothing here "
                           "exercises cross-table ordering at all")

    def test_at_least_one_pair_actually_sorts_on_another_table(self):
        """The control. If every sort column shares the lane's own alias, this
        class is asserting something that cannot fail."""
        crossing = []
        for lane, sort, sql in self.pairs():
            # SORTS is (expression, row key, cast). It was (expression, cast)
            # until the row key had to be named rather than split out of the
            # expression -- `s.total` is selected as `score`, so the inferred
            # cursor named a column no row carried and Load more died on
            # Found's default sort.
            column = (v2_lanes.SORTS[sort][0] if lane in v2_lanes.POSTING_LANES
                      else "p.posted_at")
            if column.split(".")[0] != self.identity(sql):
                crossing.append((lane, sort))
        self.assertTrue(crossing,
                        "no lane sorts on a table other than its own, so the "
                        "assertion below is vacuous")

    #: The statement's own ORDER BY, which is the last one and the only one at
    #: the top level.
    #:
    #: **Not "the only one".** It was, until the application lanes grew
    #: correlated subqueries for the completed event's time and payload, and
    #: each of those carries `order by h.at desc limit 1` of its own. A count
    #: of three then failed a lane whose ordering was untouched. A test that
    #: assumes a shape the SQL is allowed to outgrow fails the change rather
    #: than the fault.
    @staticmethod
    def top_level_order(sql):
        clauses = [line.strip() for line in sql.splitlines()
                   if line.strip().startswith("order by")]
        assert clauses, "no order by at all"
        return clauses[-1]

    def test_every_ordering_ties_on_the_lanes_own_row(self):
        for lane, sort, sql in self.pairs():
            with self.subTest(lane=lane, sort=sort):
                own = self.identity(sql)
                clause = self.top_level_order(sql)
                self.assertIn("limit %(limit)s", sql.split(clause)[-1],
                              "the clause taken is not the statement's own: "
                              "the top-level ORDER BY is the one the LIMIT "
                              "follows")
                tie = re.findall(r"\b(\w+)\.id\b", clause)
                self.assertEqual(tie, [own],
                                 f"{lane}/{sort} breaks ties on {tie} rather "
                                 f"than its own row {own!r}")

    def test_the_keyset_ties_on_the_same_row_as_the_ordering(self):
        """A cursor predicate and an ORDER BY that disagree about the
        tie-breaker walk two different sequences, which repeats and skips
        rows at every page boundary rather than failing outright."""
        for lane, sort, sql in self.pairs():
            with self.subTest(lane=lane, sort=sort):
                own = self.identity(sql)
                where = sql[sql.index(" where "):
                            sql.rindex(self.top_level_order(sql))]
                cursor = set(re.findall(r"\b(\w+)\.id\s*<\s*%\(after_id\)s",
                                        where))
                self.assertEqual(cursor, {own},
                                 f"{lane}/{sort} walks its cursor on {cursor} "
                                 f"rather than {own!r}")


class TheLanesOrderTheWayTheBoardDoes(LaneCase):
    """What each lane sorts on, which the walk tests deliberately cannot say.

    `test_each_lane_is_highest_first_with_absent_last` asks the query for its
    cursor and then checks that cursor is descending, so it is true of *any*
    ordering by construction -- revert a lane to `updated_at` and it still
    passes. That is the right shape for the keyset, whose correctness is
    relative to the ordering rather than to a particular one, and it means
    nothing pins the choice. These do.

    Each rule is the board's, cited, because "v2 should look like the board" is
    the whole reason these lanes changed.
    """

    def order(self, lane, column):
        """The lane's rows in lane order, with one column of each read back."""
        with self.conn.cursor() as cur:
            cur.execute(v2_lanes.page_sql(lane),
                        {"account": self.account, "limit": 10000,
                         "after_at": None, "after_id": None, **NO_FILTERS})
            names = [d.name for d in cur.description]
            at = names.index(column)
            return [row[at] for row in cur.fetchall()]

    def test_needs_you_leads_with_the_row_closest_to_done(self):
        """`dashboard.py:645` sorts that bucket by `len(pendingFields)`
        ascending: one blocker away is nearly finished, three is an afternoon.
        """
        counts = [len(b or []) for b in self.order("act", "blockers")]
        self.assertEqual(counts, sorted(counts),
                         "Needs you is not fewest-blockers-first")
        self.assertGreater(len(set(counts)), 1,
                           "every row has the same blocker count, so this "
                           "would pass under any ordering at all")

    def test_watching_is_newest_posted_first(self):
        """`dashboard.py:951`, which sorts on the derived posting date and
        leaves undated roles last rather than letting them look fresh."""
        dates = self.order("watch", "posted_at")
        present = [d for d in dates if d is not None]
        self.assertEqual(present, sorted(present, reverse=True),
                         "Watching is not newest-posted first")
        self.assertTrue(present and None in dates,
                        "the fixture has no mix of dated and undated rows, so "
                        "this cannot see the ordering it is about")

    def test_dropped_is_most_recently_dropped_first(self):
        """`dashboard.py:652`, sorting the archive on `droppedAt`."""
        dates = self.order("dropped", "dropped_at")
        present = [d for d in dates if d is not None]
        self.assertEqual(present, sorted(present, reverse=True),
                         "Dropped is not most-recently-dropped first")
        self.assertTrue(present, "no row carries a drop time")

    def test_submitted_is_most_recently_completed_first(self):
        """`dashboard.py:861`, sorting the history events on `at`. Not
        `updated_at`, which differs from the completion on every one of the 104
        real submitted applications."""
        dates = self.order("done", "completed_at")
        self.assertEqual(dates, sorted(dates, reverse=True),
                         "Submitted is not most-recently-completed first")
        self.assertGreater(len(set(dates)), 1,
                           "every completion is at the same instant, so this "
                           "would pass under any ordering at all")
