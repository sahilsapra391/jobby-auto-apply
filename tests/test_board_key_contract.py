"""Every connector either emits the §3 record key or says why it cannot.

**`board_key.py` was written as the fallback and became the only path.** Its
own docstring says a connector fetching `boards.greenhouse.io/<slug>` knows
`(source, board_slug, external_id)` by construction and that the module exists
for "the two callers that only have a URL". Every posting went in with a URL,
so the parser guessed for all of them: measured over the live pool on
2026-08-29 it recovers a real tenant for 70.3% of records, and a large part of
the remainder is not a parse failure at all but an aggregator that knows its own
posting id and was never asked.

**The map below is the point of this file, not the reed case.** One connector
emitting is one connector; sixteen not emitting, with nobody counting, is how
the fallback stays the only path for another month. Same shape as the reed
favicon, which was invisible because every reed card was identically wrong:
the question that has an answer is "for each connector, does it emit", asked of
the registry rather than of the connectors somebody remembered.

So a connector is in exactly one of three states, and the third is declared
rather than inferred, because nothing about a connector distinguishes "not done
yet" from "cannot":

    emits        it hands `base.posting` a slug and an external id
    cannot       no stable id exists in what it fetches, with the reason
    not yet      real work, not yet done, with what it would take

A connector missing from the map fails this file. That is the whole guard: the
next connector added has to decide.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import board_key  # noqa: E402
import sources  # noqa: E402

#: Connectors that emit the key today, and the fixture proving it.
EMITS = {
    "reed": ("reed_search.json", lambda d: d["results"][0]),
    "greenhouse": ("greenhouse_board.json", lambda d: d["jobs"][0],
                   {"company_name": "Vercel", "token": "vercel"}),
}

#: Connectors that cannot, with the reason. "Cannot" means the fetch itself
#: carries no id that is stable between sweeps, not that it is inconvenient.
CANNOT = {}

#: Connectors where this is real work nobody has done. The value says what it
#: would take, so the next person picking one up starts from that rather than
#: from the registry.
NOT_YET = {
    "adzuna": "the feed carries an `id` per posting, which is adzuna's own\n               stable identity for it; emit (adzuna, id)",
    "ashby": "the board response carries the job's own id beside the slug the "
             "connector already fetched by",
    "breezy": "the board json carries an id per position, and the slug is\n              the tenant the connector already fetched by",
    "careerjet": "aggregator; the search result carries a job id",
    "gem": "the board response carries the posting id and the company\n           slug the connector fetched by, so both halves are in hand",
    "himalayas": "the feed carries a per-posting id",
    "jazzhr": "scraped html; the posting id is in the link and would have to "
              "come from the same parse that builds the url",
    "jooble": "aggregator; the search result carries an id",
    "lever": "the board response carries the posting uuid beside the tenant",
    "oracle_orc": "the response carries a requisition id",
    "personio": "the xml carries an id per position",
    "pinpoint": "the board json carries the posting id",
    "recruitee": "the board json carries the offer id",
    "remoteok": "the feed carries a numeric id per posting, which is the\n                id its own permalinks are built from",
    "remotive": "the api carries a job id per listing, stable across\n                sweeps and used in its own urls",
    "rippling": "the board json carries the posting id",
    "smartrecruiters": "the board json carries the posting id beside the "
                       "company slug the connector fetched by",
    "successfactors": "the odata response carries a requisition id",
    "teamtailor": "the board json carries the job id",
    "themuse": "the api response carries a job id per listing, alongside\n               the company block the connector already reads",
    "usajobs": "the search result carries a control number, which is the "
               "government's own stable id for the announcement",
    "weworkremotely": "rss; the guid is the posting id",
    "workable": "the board json carries the shortcode, which is the id the "
                "short link would have carried",
    "workday": "the cxs response carries an external path per job",
    "workingnomads": "the feed carries an id per posting; same shape as the\n                     other remote aggregators here",
}


class RegistryTests(unittest.TestCase):
    def test_every_connector_is_in_exactly_one_state(self):
        known = set(sources.ALL)
        placed = set(EMITS) | set(CANNOT) | set(NOT_YET)
        missing = sorted(known - placed)
        self.assertEqual(missing, [],
                         f"these connectors are in no state, so nobody has "
                         f"decided whether they can emit a record key: "
                         f"{', '.join(missing)}")
        stale = sorted(placed - known)
        self.assertEqual(stale, [],
                         f"these are declared and are not connectors: "
                         f"{', '.join(stale)}")

    def test_no_connector_is_in_two_states(self):
        pairs = [("EMITS", EMITS), ("CANNOT", CANNOT), ("NOT_YET", NOT_YET)]
        for i, (name, left) in enumerate(pairs):
            for other, right in pairs[i + 1:]:
                both = sorted(set(left) & set(right))
                self.assertEqual(both, [],
                                 f"{', '.join(both)} is both {name} and {other}")

    def test_every_gap_says_what_it_would_take(self):
        # A reason worth the name, on the rule this repository already applies
        # to a parity exemption and to an observational status. "the daemon
        # owns it" is the sentence that stranded Apply for five days.
        for name, why in {**CANNOT, **NOT_YET}.items():
            with self.subTest(connector=name):
                self.assertGreater(len(why), 30, f"{name} has no real reason")

    def test_the_registry_is_not_empty(self):
        # The anti-vacuity floor, calibrated against the real subject rather
        # than against zero: every check above passes on an empty registry.
        self.assertGreaterEqual(len(sources.ALL), 20)


class EmissionTests(unittest.TestCase):
    """A claim to emit is checked against the connector's own fixture."""

    def fixture(self, name):
        return json.loads((REPO / "tests" / "fixtures" / name).read_text())

    def test_each_claimed_emitter_actually_emits(self):
        self.assertTrue(EMITS, "no connector claims to emit, so this is vacuous")
        for name, spec in sorted(EMITS.items()):
            fixture_name, pick = spec[0], spec[1]
            extra = spec[2] if len(spec) > 2 else {}
            with self.subTest(connector=name):
                module = __import__(f"sources.{name}", fromlist=[name])
                self.assertIs(getattr(module, "EMITS_BOARD_KEY", False), True,
                              f"{name} is declared here and does not declare "
                              f"EMITS_BOARD_KEY on the module")
                record = module.parse_job(pick(self.fixture(fixture_name)),
                                          **extra)
                stored = board_key.stored(record)
                self.assertIsNotNone(
                    stored, f"{name} claims to emit and its fixture produced "
                            f"no usable key")

    def test_the_emitted_key_is_the_one_the_source_uses(self):
        # Not just "a key came out". Reed's own `jobId` is the identity reed
        # uses, and the fallback used this device's pool id, which is local to
        # this machine and means nothing to anyone else.
        raw = self.fixture("reed_search.json")["results"][0]
        from sources import reed
        record = reed.parse_job(raw)
        self.assertEqual(board_key.stored(record),
                         ("reed", str(raw["jobId"])))

    def test_greenhouse_emits_the_token_it_fetched_by_not_a_parse_of_the_url(self):
        # The distinction is the whole contract. The URL happens to agree here;
        # for the 3,259 pool records on an employer's own domain it carries the
        # job id and no slug at all, and `board_key` refuses to guess one.
        from sources import greenhouse
        raw = self.fixture("greenhouse_board.json")["jobs"][0]
        record = greenhouse.parse_job(raw, company_name="Vercel",
                                      token="vercel-eng")
        self.assertEqual(board_key.stored(record), ("vercel-eng", str(raw["id"])))
        # And the URL would have said something else, so this is not passing by
        # accident.
        self.assertEqual(board_key.parse("greenhouse", raw["absolute_url"]),
                         ("vercel", str(raw["id"])))

    def test_greenhouse_uses_id_and_never_internal_job_id(self):
        # `internal_job_id` is a real field on every entry and the per-job
        # endpoint 404s on it, which the module docstring already records.
        from sources import greenhouse
        raw = self.fixture("greenhouse_board.json")["jobs"][0]
        self.assertNotEqual(raw["id"], raw["internal_job_id"])
        record = greenhouse.parse_job(raw, company_name="Vercel", token="v")
        self.assertEqual(board_key.stored(record)[1], str(raw["id"]))

    def test_a_greenhouse_job_with_no_id_falls_back_rather_than_half_keying(self):
        from sources import greenhouse
        raw = dict(self.fixture("greenhouse_board.json")["jobs"][0])
        raw.pop("id")
        record = greenhouse.parse_job(raw, company_name="Vercel", token="v")
        self.assertIsNone(board_key.stored(record))

    def test_a_record_with_no_key_still_resolves_the_old_way(self):
        # Every record written before this contract existed, which is all of
        # them. `for_record` must be the same value `derive` gave.
        record = {"canonicalSource": "greenhouse", "id": "pool-1",
                  "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/4242"}
        self.assertEqual(board_key.for_record(record),
                         board_key.derive("greenhouse", record["canonicalUrl"],
                                          "pool-1"))
        self.assertEqual(board_key.for_record(record), ("acme", "4242"))

    def test_a_stored_key_beats_a_parsable_url(self):
        record = {"canonicalSource": "greenhouse", "id": "pool-1",
                  "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/4242",
                  board_key.RECORD_FIELD: {"slug": "acme-inc",
                                           "externalId": "9001"}}
        self.assertEqual(board_key.for_record(record), ("acme-inc", "9001"))

    def test_half_a_key_is_refused_rather_than_repaired(self):
        # A key with one real half and one invented one is wrong in the way
        # that looks right, and a wrong key costs the identity of the row.
        url = "https://boards.greenhouse.io/acme/jobs/4242"
        for bad in ({"slug": "acme-inc"}, {"externalId": "9001"},
                    {"slug": "", "externalId": "9001"},
                    {"slug": "acme-inc", "externalId": "  "},
                    {"slug": 7, "externalId": "9001"},
                    "acme-inc/9001", ["acme-inc", "9001"], None):
            with self.subTest(stored=bad):
                record = {"canonicalSource": "greenhouse", "id": "pool-1",
                          "canonicalUrl": url, board_key.RECORD_FIELD: bad}
                self.assertEqual(board_key.for_record(record), ("acme", "4242"))

    def test_a_slug_that_is_never_a_slug_is_refused_from_a_record_too(self):
        # The parse side already refuses these; a connector emitting one would
        # otherwise walk straight past that guard.
        never = sorted(board_key._NEVER_A_SLUG)[0]
        record = {"canonicalSource": "greenhouse", "id": "pool-1",
                  "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/4242",
                  board_key.RECORD_FIELD: {"slug": never, "externalId": "9001"}}
        self.assertEqual(board_key.for_record(record), ("acme", "4242"))
