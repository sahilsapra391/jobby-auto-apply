"""A keyword is a phrase, keywords are OR'd, and the scope picks the haystack.

All three are the original Python board's rules, carried into v2's SQL:

    one chip, one substring
    any of them, not all
    the scope picks title alone, or title + company + summary + tags

v2 ran `websearch_to_tsquery('english', q)` over `search_tsv`, which ANDs bare
terms and stems them. "product manager" therefore meant "product somewhere and
manager somewhere" and returned a Full Stack iOS role because both words appear
in its description. That is wrong rather than different, which is how it was
reported.

The OR is the board's decision with the board's reason, and it is the half most
likely to be "fixed" back into an AND by somebody who thinks two keywords should
narrow: "Three job titles in the box means 'show me all three kinds of job';
requiring every word meant a second title emptied the board, because no posting
is a product manager AND a business analyst."
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import v2_lanes  # noqa: E402

def sql_only(block):
    """The SQL, with its comments removed.

    **The first version of this test failed on the comment explaining the
    removal.** `websearch_to_tsquery` appears in the note saying why it is
    gone, and a scanner that cannot tell a use from a mention reads its own
    documentation as the thing documented. Fifth instance in this repository,
    written by someone who had just read the fourth.
    """
    return "\n".join(line.split("--", 1)[0] for line in block.splitlines())


FILTERS = sql_only(v2_lanes.FILTERS)


class TheKeywordPredicateMatchesTheBoardTests(unittest.TestCase):
    def test_a_keyword_is_a_substring_not_a_tsquery(self):
        self.assertNotIn(
            "websearch_to_tsquery", FILTERS,
            "the keyword is a full-text query again. It ANDs bare terms and "
            "stems them, so a two-word chip stops being a phrase.")
        self.assertIn("ilike '%%' || kw || '%%'", FILTERS,
                      "the keyword is no longer matched as a substring")

    def test_keywords_are_ord_not_anded(self):
        """`exists (select 1 from unnest(...))` is the OR. An `all` or a
        `not exists` over the negation would be the AND."""
        # **`not exists` contains `exists`, and the first version of this
        # matched both.** Turning the OR into "none of them" left the test
        # green, which is a mutation that reached the code and a check that
        # could not see it. The shape has to be pinned with what precedes it.
        self.assertRegex(
            FILTERS,
            r"array_length\(%\(q\)s::text\[\], 1\), 0\) = 0\s+or exists\s*\(\s*"
            r"select 1 from unnest\(%\(q\)s::text\[\]\)",
            "keywords are no longer a plain `or exists` over the list. "
            "`not exists` there means none of them; an `and` over the "
            "negation means all of them. The board means any of them.")
        self.assertNotIn("or not exists", FILTERS,
                         "the keyword predicate is negated, so a row matching "
                         "a keyword is now excluded by it")
        self.assertNotIn("= all (", FILTERS.lower())

    def test_an_empty_keyword_list_is_the_filter_being_off(self):
        """**`is null` alone read `{}` as "match nothing".**

        An empty array is not null, and `unnest('{}')` yields no rows, so the
        `exists` was false for every posting. One caller passed `[]` -- the
        keyword box submits alongside the chips, so an empty box put `q=` in
        the URL -- and the loosest filter the form can produce read
        "0 of 22,735". The caller is fixed; this is the half that stops a
        fourth one repeating it.
        """
        self.assertIn("array_length(%(q)s::text[], 1), 0) = 0", FILTERS,
                      "an empty keyword array narrows the lane to nothing "
                      "instead of leaving the filter off")

    def test_the_haystack_is_the_boards_two(self):
        hay = v2_lanes.HAYSTACK
        self.assertIn("p.title", hay)
        for field, why in (("canonical_name", "job.co"), ("p.summary", "job.sum"),
                           ("p.tags", "job.tg")):
            self.assertIn(field, hay,
                          f"the wide haystack drops {field}; the board's is "
                          f"ti + co + sum + tg and this one loses {why}")
        self.assertIn("= 'title'", hay,
                      "the narrow haystack is no longer selected by the scope")

    def test_exclude_reads_the_same_haystack(self):
        """The board has one haystack and uses it for both.

        Excluding against a different text from the one searched is how a term
        can be both matched and not excluded.
        """
        self.assertEqual(
            FILTERS.count(v2_lanes.HAYSTACK), 2,
            "the keyword and the exclude predicates no longer share a haystack")

    def test_the_scope_is_not_a_second_predicate(self):
        """It used to narrow an already-matched row to its title, which is a
        different thing from searching a smaller haystack once: with the
        tsquery matching the description, "title only" meant "matches the
        description AND the raw string is in the title"."""
        self.assertNotIn("or p.title ilike '%%' || %(q)s::text", FILTERS)


class TheKeywordBoxBehavesLikeTheBoardsTests(unittest.TestCase):
    """The two small ones, both found by clicking the deployed board.

    Neither is visible from the SQL: they are what the form does around it.
    """

    def test_the_box_does_not_restore_what_it_submitted(self):
        surface = (REPO / "apps" / "web" / "app" / "_ui" / "FoundSurface.tsx").read_text()
        box = surface[surface.index('type="search" name="q"'):]
        box = box[:box.index("/>")]
        self.assertIn('autoComplete="off"', box,
                      "the browser restores the last submitted value, so the "
                      "box comes back holding the keyword that is now a chip "
                      "beside it, which reads as 'it did not take'")

    def test_keywords_are_deduped_like_the_boards(self):
        db = (REPO / "apps" / "web" / "app" / "_ui" / "db.ts").read_text()
        block = db[db.index("function filterValues"):]
        block = block[:block.index("\n}")]
        self.assertIn("new Set(", block,
                      "keywords are not deduped. The box submits alongside the "
                      "chips, so Enter on a keyword that is already a chip adds "
                      "it twice.")
