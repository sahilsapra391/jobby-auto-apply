"""The Found card's sightings, board count and direct rank, in SQL.

v2 computes three things from a posting's `sources` in the Found query
(`v2_lanes.SIGHTINGS`, `BOARDS`, `DIRECT_RANK`): the "see more on" links, how
many boards carry it, and whether the best sighting is the employer's own form,
which colours the source chip.

These were ported from the original Python board, and this test compared the
two over the cases below. That board is gone, so the expectations are its
outputs for those cases, recorded on the day it was removed (2026-10-20). The
cases are the shapes that disagree when a port is restated rather than run: a
sighting with no class, a relative or `javascript:` URL, two sightings on one
board, an empty list.

Needs a database (`JOBBY_TEST_DATABASE_URL`); skips without one.
"""

import json
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

#: What the original board drew for each case: the links in order, the label
#: on each, how many boards, and the direct rank.
EXPECTED = {
    "none": {"urls": [], "labels": [], "boards": 0, "rank": 3},
    "one-ats": {"urls": ["https://boards.example.com/acme/1"],
                "labels": ["Greenhouse"], "boards": 1, "rank": 0},
    "careers-and-feed": {"urls": ["https://feed.example.com/1",
                                  "https://acme.example.com/jobs/1"],
                         "labels": ["Himalayas", "Acme"], "boards": 2, "rank": 1},
    "aggregators-only": {"urls": ["https://jobs.example.org/1",
                                  "https://jobs.example.org/2"],
                         "labels": ["Reed", "Reed"], "boards": 2, "rank": 2},
    "unclassed": {"urls": ["http://plain.example.net/x"], "labels": [""],
                  "boards": 1, "rank": 3},
}


def source_labels():
    """`SOURCE_LABELS` out of `apps/web/app/rubric.ts`, which the card uses."""
    source = (REPO / "apps" / "web" / "app" / "rubric.ts").read_text(encoding="utf-8")
    found = re.search(r"export const SOURCE_LABELS[^=]*=\s*(\{.*?\});", source)
    return json.loads(found.group(1))


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
        labels = source_labels()
        for legacy in CASES:
            with self.subTest(case=legacy):
                sightings, boards, rank = self.sql(legacy)
                want = EXPECTED[legacy]
                # v2 carries the slug and the card labels it through
                # SOURCE_LABELS, falling back to title case, which
                # `card_words_read_like_the_board.test.mjs` holds to the rule.
                self.assertEqual([s["u"] for s in sightings], want["urls"])
                self.assertEqual(
                    [labels.get(s["s"], s["s"].title()) for s in sightings],
                    want["labels"])
                self.assertEqual(boards, want["boards"])
                self.assertEqual(rank, want["rank"])

    def test_the_cases_reach_every_branch(self):
        # Anti-vacuity: the cases have to disagree with each other, or a
        # constant would pass the comparison above.
        ranks = {self.sql(k)[2] for k in CASES}
        boards = {self.sql(k)[1] for k in CASES}
        self.assertEqual(ranks, {0, 1, 2, 3})
        self.assertGreaterEqual(len(boards), 3)
