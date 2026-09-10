"""A keyword is a phrase, keywords are OR'd, and the scope picks the haystack.

All three are the board's, copied rather than invented (`found_lane.matches`,
`found_lane.haystack`):

    hay.indexOf(keywords[i].toLowerCase()) !== -1   one chip, one substring
    for (...) { if (hit) break; }                   any of them, not all
    scope === 'title' ? job.ti : ti + co + sum + tg

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

BOARD = (REPO / "scripts" / "found_lane.py").read_text()


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
            r"%\(q\)s::text\[\] is null\s+or exists\s*\(\s*"
            r"select 1 from unnest\(%\(q\)s::text\[\]\)",
            "keywords are no longer a plain `or exists` over the list. "
            "`not exists` there means none of them; an `and` over the "
            "negation means all of them. The board means any of them.")
        self.assertNotIn("or not exists", FILTERS,
                         "the keyword predicate is negated, so a row matching "
                         "a keyword is now excluded by it")
        self.assertNotIn("= all (", FILTERS.lower())

    def test_the_board_still_ors_and_still_substrings(self):
        """**The other half of a two-language contract.**

        This asserts against `found_lane.py` rather than restating what it
        does, so the day the board changes its mind the copy is told.
        """
        self.assertIn("hay.indexOf(keywords[i].toLowerCase()) !== -1", BOARD,
                      "the board no longer matches a keyword as a substring; "
                      "v2 copies it and now disagrees")
        self.assertRegex(
            BOARD, r"if \(hay\.indexOf\(keywords\[i\]\.toLowerCase\(\)\) !== -1\)"
                   r" \{ hit = true; break; \}",
            "the board no longer breaks on the first hit, so it may have "
            "stopped OR'ing")

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
