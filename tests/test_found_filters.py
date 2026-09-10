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
        # **The property, not the spelling.** `is null` alone was the bug:
        # an empty array is not null and `unnest('{}')` yields no rows, so the
        # filter matched nothing instead of being off.
        self.assertIn("array_length(%(q)s::text[], 1), 0) = 0", sql)
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
        """The lane, with the named filters on and every other one off.

        **The off-state comes from `v2_lanes.no_filters()` rather than from a
        list here.** A hand-written one held `q`, `within` and `direct` while
        the query grew to ten parameters, and every test in this file then
        raised "query parameter missing" -- in CI only, because the module
        skips without a database and so was wrong on the machine that edited
        it.
        """
        args = dict(account=self.account, after_at=None, after_id=None,
                    limit=50, **v2_lanes.no_filters())
        args.update(filters)
        with self.conn.cursor() as cur:
            cur.execute(v2_lanes.page_sql("found"), args)
            cols = [c.name for c in cur.description]
            return sorted(dict(zip(cols, r))["legacy_id"] for r in cur.fetchall())

    def test_the_fixture_is_visible_without_filters(self):
        """The control. Every assertion below is satisfied by an empty lane."""
        self.assertEqual(self.found(), ["pm-new", "pm-old", "wh-new", "wh-old"])

    def test_a_keyword_narrows_it(self):
        self.assertEqual(self.found(q=["product manager"]), ["pm-new", "pm-old"])

    def test_a_two_word_keyword_is_a_phrase_and_not_two_terms(self):
        """The reason this whole predicate was rewritten.

        `websearch_to_tsquery` ANDs bare terms, so "product manager" meant
        "product somewhere and manager somewhere" and matched a Full Stack iOS
        posting because both words appear in its description. The board does
        `hay.indexOf(keyword)`: one chip is one literal substring, so a
        two-word chip is a phrase without anybody typing quotes.
        """
        # Both words present, never adjacent: a tsquery matches this and a
        # phrase does not.
        self.assertEqual(self.found(q=["manager product"]), [])

    def test_keywords_are_ord_because_the_board_ors_them(self):
        """The board's decision, with the board's reason: "no posting is a
        product manager AND a business analyst", so requiring every chip
        empties the lane. A second keyword widens."""
        self.assertEqual(self.found(q=["product manager"]), ["pm-new", "pm-old"])
        self.assertEqual(
            sorted(self.found(q=["product manager", "warehouse"])),
            ["pm-new", "pm-old", "wh-new", "wh-old"])

    def test_exclusion_is_the_exclude_box_and_not_a_dash(self):
        """**Capability deliberately given up, named rather than lost.**

        `-word` was `websearch_to_tsquery` syntax and the board has never had
        it: negation there is a separate control, `jb-ex`, which is `exclude`
        here. Matching the board means a dash is now a literal dash, so the
        test that asserted the old syntax becomes the test that asserts where
        negation actually lives.
        """
        self.assertEqual(self.found(q=["operative -warehouse"]), [])
        self.assertEqual(
            self.found(q=["manager"], exclude="warehouse"), ["pm-new", "pm-old"])

    def test_stemming_is_gone_with_the_tsquery_and_that_is_the_trade(self):
        """**The one real loss, written down rather than discovered later.**

        The index stems, so `managers` matched `Manager`. A substring does not,
        and the board never did either -- `hay.indexOf('managers')` is false
        against "Product Manager". Matching the board costs this, and the
        reader's recourse is to type the shorter word.
        """
        self.assertEqual(self.found(q=["managers"]), [])
        self.assertEqual(self.found(q=["manager"]), ["pm-new", "pm-old"])

    def test_quotes_are_literal_now_because_a_chip_is_already_a_phrase(self):
        """`"product manager"` was how you asked a tsquery for a phrase. A chip
        is one, so the quotes are characters and match nothing."""
        self.assertEqual(self.found(q=['"product manager"']), [])
        self.assertEqual(self.found(q=["product manager"]), ["pm-new", "pm-old"])

    def test_the_scope_picks_the_haystack_rather_than_narrowing_a_match(self):
        """**The employer name is the clean discriminator here.**

        Every fixture title is "Product Manager" or "Warehouse Operative", so a
        keyword drawn from a title cannot tell the two scopes apart -- it is in
        both haystacks. `acme` is the employer, which the board puts in the
        wide haystack (`job.co`) and never in the narrow one, so it is in
        exactly one of them and the test can fail in both directions.

        The scope used to be a second predicate narrowing an already-matched
        row to its title, which with a tsquery matching the description meant
        "title only" was really "matches the description AND the raw string is
        in the title".
        """
        self.assertEqual(sorted(self.found(q=["acme"])),
                         ["pm-new", "pm-old", "wh-new", "wh-old"])
        self.assertEqual(self.found(q=["acme"], scope="title"), [])
        # And a title word is in both, so the scope does not simply refuse.
        self.assertEqual(self.found(q=["manager"], scope="title"),
                         ["pm-new", "pm-old"])

    def test_the_date_window_narrows_it(self):
        self.assertEqual(self.found(within="7 days"), ["pm-new", "wh-new"])

    def test_direct_listings_only_is_the_ats_source_class(self):
        self.assertEqual(self.found(direct=True), ["pm-new", "pm-old"])

    def test_the_filters_compose(self):
        self.assertEqual(
            self.found(q=["product manager"], within="7 days", direct=True),
            ["pm-new"])

    def test_an_undated_posting_follows_the_unknown_bucket(self):
        """**Whether a dateless posting survives a window is the reader's
        choice, and the board's default is that it does.**

        This asserted the opposite -- that `posted_at is null` must never slip
        through a date filter -- and the reasoning was good: "posted in the
        last 24 hours" cannot honestly include a posting whose date nobody
        stated. What it missed is that the board has always offered the choice.
        `found_lane.matches` reads

            if (!when) { if (!unknownAllowed('postedAt')) return false; }

        and the box ships ticked, so v2 was on the *narrow* side of a switch it
        did not have. 2,433 postings have no date; clicked on the board
        2026-09-09, unticking the box moves 3300 to 3211.

        Both sides are asserted here, because a test for one side of a switch
        passes just as happily when the switch does nothing.
        """
        with self.conn.cursor() as cur:
            cur.execute("update postings set posted_at = null "
                        "where legacy_id = 'pm-new'")
        # The default, which `no_filters()` supplies as None and the predicate
        # reads through `coalesce(..., true)`.
        self.assertIn("pm-new", self.found(within="7 days"))
        self.assertIn("pm-new", self.found())
        # And the reader who unticks it.
        self.assertNotIn("pm-new", self.found(within="7 days", unk_posted=False))
        # The switch must not widen a window that is not set, nor narrow one it
        # is not about: a dated posting inside the window is unaffected either
        # way, which is what says the clause is scoped to the null case.
        self.assertIn("pm-old", self.found(unk_posted=False))

    def test_the_other_two_unknown_buckets_switch_the_same_way(self):
        """`unk_wp` and `unk_exp` widen the filters beside them, and v2 had
        both stuck: `p.workplace = any(...)` is NULL for a null workplace, and
        the years clause had no toggle at all."""
        with self.conn.cursor() as cur:
            cur.execute("update postings set workplace = null, years_min = null "
                        "where legacy_id = 'pm-new'")
        self.assertIn("pm-new", self.found(wp=["remote"]))
        self.assertNotIn("pm-new", self.found(wp=["remote"], unk_wp=False))
        self.assertIn("pm-new", self.found(exp=3))
        self.assertNotIn("pm-new", self.found(exp=3, unk_exp=False))

    def test_a_countryless_remote_posting_follows_include_remote(self):
        """The board keeps genuinely location-free remote work in a scoped
        search, and only when asked: "Only genuinely location-free remote work
        answers a search for somewhere specific, and only when asked for."
        v2 dropped every country-less posting from a scoped search, so the
        include-remote box had nothing to widen."""
        with self.conn.cursor() as cur:
            cur.execute("update postings set country = null, remote = true "
                        "where legacy_id = 'pm-new'")
            # The fixture leaves `country` null on every row, so the control
            # below needs a posting that actually names one. Without this it
            # asserts nothing: a null-country row is dropped by the scope
            # whichever way the toggle is set, so the assertion would pass for
            # the wrong reason.
            cur.execute("update postings set country = 'IN' "
                        "where legacy_id = 'pm-old'")
        self.assertIn("pm-new", self.found(loc=["IN"]))
        self.assertNotIn("pm-new", self.found(loc=["IN"], inc_remote=False))
        # A posting that names a country is unaffected by the toggle: the
        # clause is about the null case, and a rule that let any remote posting
        # through any country filter is the one the board's comment warns about
        # ("Remote - Canada" answering a search for India).
        self.assertIn("pm-old", self.found(loc=["IN"], inc_remote=False))
