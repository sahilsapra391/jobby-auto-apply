"""The select beside each application lane's search, from the URL to the rows.

The board's Watching, Needs you and Submitted lanes each carry a select
(`dashboard.py`'s `filter_bar`): posted in the last 7 days, a region or a
discipline; ready to submit or still blocked; and the submission's outcome.
The board filters an embedded payload on `data-` attributes. v2 pages from
Postgres, so each option is a clause in the generated lane SQL
(`v2_lanes.FACET_ARMS`) and the URL carries which one as `facet`.

**Every case starts from a query string the page produces**, and runs it
through `tests/lane_query_runner.mjs`: the real `filtersFrom`, `filterValues`
and `argsFor` compiled out of `db.ts`, the generated SQL, and the `pg` driver
the app ships. CLAUDE.md's rule, from the night a hand-built `Filters` object
hid an empty lane: a filter test that builds its own query is testing a
different page.

**Rows, not counts.** The count comes from a different query, so a predicate
that moved the count and not the page would pass a count check. Each case
compares the row set and then checks the count agrees with it.

Needs a database (`JOBBY_TEST_DATABASE_URL`) and `apps/web`'s node_modules, so
it skips without them, like `test_application_lane_search`.
"""

import json
import re
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
import v2_lanes  # noqa: E402

RUNNER = REPO / "tests" / "lane_query_runner.mjs"
NODE = shutil.which("node")
PG_DRIVER = REPO / "apps" / "web" / "node_modules" / "pg"
APP = seed_shape_fixture.APPLICATION


class TheStaticOptionsAreTheBoards(unittest.TestCase):
    """The labels v2 offers are the ones `dashboard.py` writes, read back."""

    def test_each_static_option_is_in_the_board_source(self):
        source = (REPO / "scripts" / "dashboard.py").read_text(encoding="utf-8")
        for lane, options in v2_lanes.FACET_STATIC.items():
            for value, label in options:
                with self.subTest(lane=lane, value=value):
                    self.assertRegex(
                        source,
                        re.escape(f'("{value}", "{label}")'),
                        f"dashboard.py no longer offers {value!r} as "
                        f"{label!r}; v2's {lane} select would drift from it")

    def test_a_lane_with_an_arm_is_a_lane_the_page_can_offer(self):
        # A facet arm with no option to reach it is a clause nobody can turn
        # on, and an option with no arm is one that empties the lane.
        for lane in v2_lanes.FACET_ARMS:
            with self.subTest(lane=lane):
                self.assertTrue(v2_lanes.FACET_STATIC.get(lane)
                                or v2_lanes.facets_sql(lane),
                                f"{lane} has a facet clause and no options")
        for lane in ("flight", "dropped", "found"):
            self.assertNotIn(lane, v2_lanes.FACET_ARMS)
            self.assertIsNone(v2_lanes.facets_sql(lane))


@unittest.skipUnless(NODE and PG_DRIVER.exists(),
                     "needs node and apps/web/node_modules (run npm ci in apps/web)")
class ApplicationLaneFacetTests(pgtest.SchemaCase):
    prefix = "lanefacet"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('s@example.com')")
        # The fixture gives one row per lane: Watching posted yesterday in GB
        # with a discipline, Needs you with one blocker plus a capability
        # park, and Submitted with an interview after its completion.
        self.account = seed_shape_fixture.seed(self.conn, pgtest.URL)["account"]
        with self.conn.cursor() as cur:
            rows = [
                # Watching: stale and Indian; undated with no region at all.
                ("facet-watch-old", "watch", "IN", None,
                 "now() - interval '30 days'", None),
                ("facet-watch-undated", "watch", None, None, "null", None),
                # Needs you: nothing pending, and two things pending.
                ("facet-act-ready", "review", None, None, "null", None),
                ("facet-act-hold", "active", None, None, "null",
                 '[{"question": "a"}, {"question": "b"}]'),
                # Submitted, plain and rejected.
                ("facet-done-plain", "done", None, None, "null", None),
                ("facet-done-rejected", "done", None, None, "null", None),
            ]
            for legacy, status, region, discipline, posted, blockers in rows:
                cur.execute(
                    f"""insert into applications (account_id, legacy_id, status,
                            company, role, region, discipline, posted_at,
                            blockers)
                        values (%s, %s, %s, 'Initech', 'Analyst', %s, %s,
                                {posted}, %s::jsonb)""",
                    (self.account, legacy, status, region, discipline,
                     blockers))
            for legacy, kinds in (("facet-done-plain", ["completed"]),
                                  ("facet-done-rejected",
                                   ["completed", "rejected"])):
                for i, kind in enumerate(kinds):
                    cur.execute(
                        """insert into application_events (account_id,
                                application_id, kind, at)
                           select account_id, id, %s,
                                  now() - make_interval(days => %s)
                             from applications where legacy_id = %s""",
                        (kind, 5 - i, legacy))

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
        got = json.loads(done.stdout)
        self.assertEqual(got["count"], len(got["rows"]),
                         f"{query}: the count disagrees with the page under it")
        return sorted(got["rows"])

    def test_watching_fresh_region_and_discipline(self):
        everything = self.lane("lane=watch")
        self.assertEqual(everything, sorted([f"{APP}-watch", "facet-watch-old",
                                             "facet-watch-undated"]))
        # Undated is not fresh: the board's `data-fresh` needs a day count.
        self.assertEqual(self.lane("lane=watch&facet=fresh"), [f"{APP}-watch"])
        self.assertEqual(self.lane("lane=watch&facet=region%3AIN"),
                         ["facet-watch-old"])
        self.assertEqual(self.lane("lane=watch&facet=region%3AGB"),
                         [f"{APP}-watch"])
        self.assertEqual(
            self.lane([("lane", "watch"),
                       ("facet", "disc:Shape Fixture Discipline")]),
            [f"{APP}-watch"])

    def test_the_select_s_all_is_every_row(self):
        # `facet=` is what the form submits for All. Passed through as "" it
        # would match no arm and empty the lane: the `[] || null` shape.
        for lane in ("watch", "act", "done"):
            with self.subTest(lane=lane):
                self.assertEqual(self.lane(f"lane={lane}&facet="),
                                 self.lane(f"lane={lane}"))

    def test_needs_you_ready_and_still_blocked(self):
        everything = self.lane("lane=act")
        self.assertIn(f"{APP}-fault", everything)
        self.assertEqual(self.lane("lane=act&facet=ready"), ["facet-act-ready"])
        self.assertEqual(self.lane("lane=act&facet=hold"),
                         sorted([f"{APP}-review", "facet-act-hold"]))
        # A capability park is the board's `data-state="fault"`: neither.
        for facet in ("ready", "hold"):
            self.assertNotIn(f"{APP}-fault", self.lane(f"lane=act&facet={facet}"))

    def test_submitted_by_outcome(self):
        self.assertEqual(self.lane("lane=done"),
                         sorted([f"{APP}-done", "facet-done-plain",
                                 "facet-done-rejected"]))
        self.assertEqual(self.lane("lane=done&facet=completed"),
                         ["facet-done-plain"])
        self.assertEqual(self.lane("lane=done&facet=interview"), [f"{APP}-done"])
        self.assertEqual(self.lane("lane=done&facet=rejected"),
                         ["facet-done-rejected"])

    def test_a_facet_on_a_lane_without_one_changes_nothing(self):
        self.assertEqual(self.lane("lane=flight&facet=hold"),
                         self.lane("lane=flight"))

    def test_the_options_are_read_off_the_lane(self):
        def options(lane):
            with self.conn.cursor() as cur:
                cur.execute(v2_lanes.facets_sql(lane), {"account": self.account})
                return cur.fetchall()
        # Regions first, then disciplines, each sorted: the board's order.
        self.assertEqual(options("watch"), [
            ("region:GB", "GB"), ("region:IN", "IN"),
            ("disc:Shape Fixture Discipline", "Shape Fixture Discipline")])
        # Only the states the lane's rows are in, labelled like `.title()`.
        self.assertEqual(options("done"), [
            ("completed", "Completed"), ("interview", "Interview"),
            ("rejected", "Rejected")])
