"""What the Found filter bar actually filters on.

Sorting was half the problem: the score puts a program manager above a
warehouse night shift, and the warehouse night shift is still in the lane. The
filters are the other half.

**Parameters rather than query variants.** Three optional filters and two
sorts is sixteen generated queries per lane, every one a place for the
predicate to be spelled differently. A NULL parameter means the filter is off.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import migrate  # noqa: E402
import pgtest  # noqa: E402
import v2_lanes  # noqa: E402


class FilterPredicateShapeTests(unittest.TestCase):
    """No database needed, and these are the ones that catch a silent filter."""

    def test_the_windows_come_from_the_boards_own_list(self):
        import found_lane
        self.assertEqual(sorted(v2_lanes.WINDOWS),
                         sorted(v for v, _ in found_lane.DATE_WINDOWS),
                         "the two boards offer different date windows")

    def test_the_sorts_come_from_the_boards_own_list(self):
        import found_lane
        self.assertEqual(sorted(v2_lanes.SORT_LABELS),
                         sorted(v for v, _ in found_lane.SORT_OPTIONS))

    def test_every_lane_takes_a_keyword_and_only_found_takes_the_rest(self):
        """Each lane offers the search its own header offers.

        **This asserted that an application lane took no keyword at all**, on
        the grounds that it has no `search_tsv` and no `sources` so accepting
        one would return everything. The first half of that is still true and
        the conclusion no longer follows: the lanes now search with `ilike`
        over the four fields their rows actually draw, which is the right tool
        for tens of rows and the wrong one for 22,000 with a description each.
        `within` and `direct` remain Found's alone -- they read `posted_at` and
        `sources`, which an application row has only through a posting it may
        not have.
        """
        for lane in v2_lanes.LANES:
            with self.subTest(lane=lane):
                sql = v2_lanes.page_sql(lane)
                self.assertIn("%(q)s", sql, "every lane offers a search")
                for name in ("%(within)s", "%(direct)s"):
                    if lane in v2_lanes.POSTING_LANES:
                        self.assertIn(name, sql)
                    else:
                        self.assertNotIn(name, sql)

    def test_an_application_lane_searches_what_its_row_draws(self):
        """A reader typing "toast" wants the row that says Toast."""
        sql = v2_lanes.page_sql("watch")
        self.assertIn("ilike", sql)
        self.assertNotIn("search_tsv", sql,
                         "stemming a company name is how Toast stops matching "
                         "itself")
        for column in ("canonical_name", "a.company", "p.title", "a.role",
                       "location_raw", "core_skills"):
            self.assertIn(column, sql)

    def test_the_application_count_carries_the_search_too(self):
        """Otherwise the tab badge disagrees with the lane under it, which is
        the same fault the Found count already had."""
        for lane in v2_lanes.LANES:
            if lane in v2_lanes.POSTING_LANES:
                continue
            with self.subTest(lane=lane):
                self.assertIn("%(q)s", v2_lanes.count_sql(lane))

    def test_the_count_carries_the_same_filters_as_the_page(self):
        """Otherwise the tab badge says 18,104 over a lane showing nine."""
        page = v2_lanes.page_sql("found")
        count = v2_lanes.count_sql("found")
        for name in ("%(q)s", "%(within)s", "%(direct)s"):
            self.assertIn(name, page)
            self.assertIn(name, count)

    def test_a_null_parameter_means_the_filter_is_off(self):
        sql = v2_lanes.page_sql("found")
        self.assertIn("%(q)s::text is null", sql)
        self.assertIn("%(within)s::text is null", sql)
        self.assertIn("not coalesce(%(direct)s::boolean, false)", sql)

    def test_every_query_numbers_its_parameters_from_one_without_gaps(self):
        """**The bug this cost.** A shared name-to-position map gave the Found
        count `$1, $5, $6, $7`, and Postgres cannot infer a type for a
        parameter the statement never references: the whole page 500s with
        "could not determine data type of parameter $2" whatever is passed.
        """
        emitted = v2_lanes.as_typescript()
        for match in re.finditer(r'"(?:sql|page|count)":\s*"((?:[^"\\]|\\.)*)"',
                                 emitted):
            sql = match.group(1)
            nums = sorted({int(n) for n in re.findall(r"\$(\d+)", sql)})
            if not nums:
                continue
            self.assertEqual(nums, list(range(1, len(nums) + 1)),
                             f"parameters are not contiguous: {nums}")

    def test_each_query_declares_the_parameters_it_takes(self):
        emitted = v2_lanes.as_typescript()
        self.assertIn('"params"', emitted)
        self.assertIn('"countParams"', emitted)


class FilterBehaviourTests(pgtest.SchemaCase):
    prefix = "foundfilter"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('f@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]
            cur.execute("insert into employers (canonical_name, norm_name) "
                        "values ('Acme','acme') returning id")
            employer = cur.fetchone()[0]
            rows = [
                ("pm-new", "Product Manager", 2, '[{"sourceClass":"ats"}]'),
                ("pm-old", "Product Manager", 40, '[{"sourceClass":"ats"}]'),
                ("wh-new", "Warehouse Operative", 2, '[{"sourceClass":"feed"}]'),
                ("wh-old", "Warehouse Operative", 40, '[{"sourceClass":"feed"}]'),
            ]
            for legacy, title, days, sources in rows:
                cur.execute(
                    """insert into postings (employer_id, legacy_id, source,
                            board_slug, external_id, title, apply_url,
                            posted_at, first_seen_at, last_seen_at, sources)
                       values (%s, %s, 'greenhouse', 'acme', %s, %s,
                               %s, now() - (%s || ' days')::interval,
                               now(), now(), %s::jsonb)""",
                    (employer, legacy, legacy,
                     title, f"https://boards.greenhouse.io/acme/jobs/{legacy}",
                     days, sources))

    def found(self, **filters):
        args = dict(account=self.account, after_at=None, after_id=None,
                    limit=50, q=None, within=None, direct=None)
        args.update(filters)
        with self.conn.cursor() as cur:
            cur.execute(v2_lanes.page_sql("found"), args)
            cols = [c.name for c in cur.description]
            return sorted(dict(zip(cols, r))["legacy_id"] for r in cur.fetchall())

    def test_the_fixture_is_visible_without_filters(self):
        """The control. Every assertion below is satisfied by an empty lane."""
        self.assertEqual(self.found(), ["pm-new", "pm-old", "wh-new", "wh-old"])

    def test_a_keyword_narrows_it(self):
        self.assertEqual(self.found(q="product manager"), ["pm-new", "pm-old"])

    def test_a_negated_keyword_excludes(self):
        """`websearch_to_tsquery`, so `-word` means what the reader typed."""
        self.assertEqual(self.found(q="operative -warehouse"), [])
        self.assertEqual(self.found(q="manager -warehouse"), ["pm-new", "pm-old"])

    def test_a_quoted_phrase_is_a_phrase(self):
        self.assertEqual(self.found(q='"product manager"'), ["pm-new", "pm-old"])

    def test_the_date_window_narrows_it(self):
        self.assertEqual(self.found(within="7 days"), ["pm-new", "wh-new"])

    def test_direct_listings_only_is_the_ats_source_class(self):
        self.assertEqual(self.found(direct=True), ["pm-new", "pm-old"])

    def test_the_filters_compose(self):
        self.assertEqual(
            self.found(q="product manager", within="7 days", direct=True),
            ["pm-new"])

    def test_an_undated_posting_is_excluded_by_a_window_rather_than_kept(self):
        """`posted_at is null` must not slip through a date filter. 2,143
        postings have no date, and "posted in the last 24 hours" cannot honestly
        include a posting whose date nobody stated."""
        with self.conn.cursor() as cur:
            cur.execute("update postings set posted_at = null "
                        "where legacy_id = 'pm-new'")
        self.assertNotIn("pm-new", self.found(within="7 days"))
        self.assertIn("pm-new", self.found())
