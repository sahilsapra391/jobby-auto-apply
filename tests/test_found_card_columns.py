"""The Found card's sightings, board count and direct rank, in SQL and in Python.

`found_lane.compact` builds three things from a posting's `sources` for the
board: `sg`, the "see more on" links; `bd`, how many boards carry it; and
`rank`, whether the best sighting is the employer's own form, which colours the
source chip. v2 computes the same three in the Found query
(`v2_lanes.SIGHTINGS`, `BOARDS`, `DIRECT_RANK`) so a page carries them without
shipping `sources` whole.

**One contract in two languages, so the test compares outputs.** Each case is a
`sources` value stored in a real `postings` row and read back through the
generated expressions, against what `compact` and `direct_rank` say about the
same list. The cases are the shapes that disagree when a port is restated
rather than run: a sighting with no class, a relative or `javascript:` URL, two
sightings on one board, an empty list.

Needs a database (`JOBBY_TEST_DATABASE_URL`); skips without one.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import found_lane  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402
import v2_lanes  # noqa: E402

CASES = {
    "none": [],
    "one-ats": [{"sourceClass": "ats", "source": "greenhouse",
                 "url": "https://boards.example.com/acme/1"}],
    "careers-and-feed": [
        {"sourceClass": "feed", "source": "himalayas",
         "url": "https://feed.example.com/1"},
        {"sourceClass": "careers", "source": "acme",
         "url": " https://acme.example.com/jobs/1 "}],
    "aggregators-only": [
        {"sourceClass": "aggregator", "source": "reed",
         "url": "https://jobs.example.org/1"},
        {"sourceClass": "aggregator", "source": "reed",
         "url": "https://jobs.example.org/2"},
        {"sourceClass": "aggregator", "source": "adzuna", "url": "/relative"}],
    "unclassed": [{"source": "workingnomads", "url": "javascript:alert(1)"},
                  {"url": "http://plain.example.net/x"}],
}


@unittest.skipUnless(pgtest.available(), "needs JOBBY_TEST_DATABASE_URL")
class FoundCardColumnsAgree(pgtest.SchemaCase):
    prefix = "foundcard"

    def setUp(self):
        super().setUp()
        self.assert_isolated()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute(
                """insert into employers (canonical_name, norm_name)
                   values ('Acme', 'acme') returning id""")
            employer = cur.fetchone()[0]
            for legacy, sources in CASES.items():
                cur.execute(
                    """insert into postings (legacy_id, source, board_slug,
                                             external_id, title, employer_id,
                                             last_seen_at, apply_url, sources)
                       values (%s, 'greenhouse', 'acme', %s, 'Analyst', %s,
                               now(), 'https://example.com/x', %s::jsonb)""",
                    (legacy, legacy, employer, json.dumps(sources)))

    def sql(self, legacy):
        with self.conn.cursor() as cur:
            cur.execute(
                f"""select {v2_lanes.SIGHTINGS}, {v2_lanes.BOARDS},
                           {v2_lanes.DIRECT_RANK}
                      from postings p where p.legacy_id = %s""", (legacy,))
            return cur.fetchone()

    def test_every_case_matches_the_board(self):
        for legacy, sources in CASES.items():
            with self.subTest(case=legacy):
                sightings, boards, rank = self.sql(legacy)
                board = found_lane.compact(
                    {"id": legacy, "sources": sources}, found_lane.WhyTable())
                # The board labels each sighting; v2 carries the slug and the
                # card labels it through the generated SOURCE_LABELS, which
                # `card_words_read_like_the_board.test.mjs` holds to the same
                # rule. So the URLs and their order are what must agree here.
                self.assertEqual([s["u"] for s in sightings],
                                 [s["u"] for s in board["sg"]])
                self.assertEqual(
                    [found_lane.SOURCE_LABELS.get(s["s"], s["s"].title())
                     for s in sightings],
                    [s["s"] for s in board["sg"]])
                self.assertEqual(boards, board["bd"])
                self.assertEqual(rank, found_lane.direct_rank(
                    {"sources": sources}))

    def test_the_cases_reach_every_branch(self):
        # Anti-vacuity: the cases have to disagree with each other, or a
        # constant would pass the comparison above.
        ranks = {self.sql(k)[2] for k in CASES}
        boards = {self.sql(k)[1] for k in CASES}
        self.assertEqual(ranks, {0, 1, 2, 3})
        self.assertGreaterEqual(len(boards), 3)
