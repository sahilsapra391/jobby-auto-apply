"""A search on an application lane, from the URL the page produces to the rows.

**#235 made `q` a list for every caller and moved only Found's predicate.**
The five application lanes went on casting it to `::text`, node-pg sent
`["Scale Media"]` as the array literal `{"Scale Media"}`, and the `ilike`
pattern carried the braces and the quotes. It matched nothing and raised
nothing. Measured on production: Watching held 19 rows and `&q=Scale%20Media`
returned 0, and the same on In flight, Needs you, Submitted and Dropped.

Every check that existed passed, because each one either read the SQL as text
or handed the predicate a value the page never sends. So this starts from a
query string and goes through the real `filtersFrom`, `filterValues` and
`argsFor`, the generated SQL, and the `pg` driver the app ships, by running
`tests/lane_query_runner.mjs`. The rule is CLAUDE.md's: "every test of a filter
starts from a query string the page produces, never from a query the test
constructs."

Needs a database (`JOBBY_TEST_DATABASE_URL`) and `apps/web`'s node_modules, so
it skips without them. CI has both: `npm ci` runs before the Python tests.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import migrate  # noqa: E402
import pgtest  # noqa: E402
import seed_shape_fixture  # noqa: E402

RUNNER = REPO / "tests" / "lane_query_runner.mjs"
NODE = shutil.which("node")
PG_DRIVER = REPO / "apps" / "web" / "node_modules" / "pg"

FIXTURE = seed_shape_fixture.EMPLOYER
APP = seed_shape_fixture.APPLICATION

#: The fixture's application in each lane. The shape fixture seeds four of
#: them; Dropped is added here, and Submitted needs the completed event the
#: fixture does not write.
IN_LANE = {
    "watch": f"{APP}-watch",
    "flight": f"{APP}-inflight",
    "act": f"{APP}-review",
    "done": f"{APP}-done",
    "dropped": f"{APP}-dropped",
}

#: One row per lane that does not mention the fixture employer, so a search
#: that narrows nothing cannot pass by returning the whole lane.
OTHER = {
    "watch": ("other-watch", "watch"),
    "flight": ("other-flight", "inflight"),
    "act": ("other-act", "review"),
    "done": ("other-done", "done"),
    "dropped": ("other-dropped", "dropped"),
}


@unittest.skipUnless(NODE and PG_DRIVER.exists(),
                     "needs node and apps/web/node_modules (run npm ci in apps/web)")
class ApplicationLaneSearchTests(pgtest.SchemaCase):
    prefix = "lanesearch"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('s@example.com')")
        seeded = seed_shape_fixture.seed(self.conn, pgtest.URL)
        self.account, posting = seeded["account"], seeded["posting"]
        with self.conn.cursor() as cur:
            cur.execute(
                """insert into applications (account_id, legacy_id, status,
                                             posting_id)
                   values (%s, %s, 'dropped', %s)""",
                (self.account, IN_LANE["dropped"], posting))
            for legacy, status in OTHER.values():
                cur.execute(
                    """insert into applications (account_id, legacy_id, status,
                                                 company, role)
                       values (%s, %s, %s, 'Unrelated Co', 'Analyst')""",
                    (self.account, legacy, status))
            # Submitted is a completed event, not a status.
            cur.execute(
                """insert into application_events (account_id, application_id,
                                                   kind)
                   select account_id, id, 'completed' from applications
                    where legacy_id in (%s, %s)""",
                (IN_LANE["done"], OTHER["done"][0]))
            # The archive's own fields, the way the mirror writes them.
            cur.execute(
                """insert into applications (account_id, legacy_id, status,
                        company, role, dropped_from, dropped_stage,
                        dropped_reason, dropped_at)
                   values (%s, 'dropped-closed', 'dropped', 'Alpha Labs',
                           'Designer', 'Watching', 'not started',
                           'posting closed by the employer, noticed on 2026-10-02',
                           now()),
                          (%s, 'dropped-flight', 'dropped', 'Beta Corp',
                           'Engineer', 'In flight',
                           'Queued (starting the local form run)', null,
                           now())""",
                (self.account, self.account))
            # A restored application keeps its old reason. It must not answer
            # a search for that reason from Watching.
            cur.execute(
                """insert into applications (account_id, legacy_id, status,
                        company, role, dropped_from, dropped_reason)
                   values (%s, 'restored-watch', 'watch', 'Gamma Inc', 'Writer',
                           'Watching', 'posting closed by the employer')""",
                (self.account,))

    def lane(self, query):
        """The count and the page for one URL, exactly as the board asks."""
        if isinstance(query, list):
            query = urlencode(query)
        done = subprocess.run(
            [NODE, str(RUNNER), pgtest._direct(), self.schema,
             str(self.account), query],
            capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            self.fail(f"the lane runner failed for {query!r}:\n{done.stderr}")
        return json.loads(done.stdout)

    def test_every_application_lane_answers_its_own_search(self):
        for lane, legacy in IN_LANE.items():
            with self.subTest(lane=lane):
                whole = self.lane([("lane", lane)])
                # The control. Without the other row in the lane, a search
                # that filtered nothing would pass.
                self.assertIn(legacy, whole["rows"])
                self.assertIn(OTHER[lane][0], whole["rows"])
                got = self.lane([("lane", lane), ("q", FIXTURE)])
                self.assertEqual(
                    got["rows"], [legacy],
                    f"a search for {FIXTURE!r} on {lane} returned "
                    f"{got['rows']}. Zero rows is the #235 shape: q arrives "
                    f"as a list and the predicate cast it to one string.")
                self.assertEqual(got["count"], 1,
                                 "the count disagrees with the page under it")

    def test_a_keyword_is_case_insensitive_and_a_phrase(self):
        self.assertEqual(self.lane([("lane", "watch"),
                                    ("q", FIXTURE.lower())])["rows"],
                         [IN_LANE["watch"]])
        # Both words present, not adjacent: a phrase does not match it.
        self.assertEqual(self.lane([("lane", "watch"),
                                    ("q", "Employer Fixture")])["rows"], [])

    def test_keywords_are_ord_like_found(self):
        got = self.lane([("lane", "watch"), ("q", FIXTURE), ("q", "Unrelated")])
        self.assertEqual(got["rows"],
                         sorted([IN_LANE["watch"], OTHER["watch"][0]]))
        self.assertEqual(got["count"], 2)

    def test_an_empty_box_is_no_search(self):
        whole = self.lane([("lane", "watch")])
        self.assertEqual(self.lane("lane=watch&q=")["rows"], whole["rows"])

    def test_dropped_searches_where_it_came_from_and_why(self):
        """The board's archive haystack: company, role, the lane it was dropped
        from, the stage and the reason. v2 found 0 for "posting closed" and
        for "watch" against an archive where the board found 2 and 49."""
        self.assertEqual(
            self.lane([("lane", "dropped"), ("q", "posting closed")])["rows"],
            ["dropped-closed"])
        self.assertEqual(
            self.lane([("lane", "dropped"), ("q", "watch")])["rows"],
            ["dropped-closed"])
        self.assertEqual(
            self.lane([("lane", "dropped"), ("q", "queued")])["rows"],
            ["dropped-flight"])

    def test_a_reason_does_not_leak_into_the_working_lanes(self):
        self.assertIn("restored-watch", self.lane([("lane", "watch")])["rows"])
        self.assertEqual(
            self.lane([("lane", "watch"), ("q", "posting closed")])["rows"], [])

