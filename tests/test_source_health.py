"""A problems list a reader can act on, and an alert two sweeps deep.

**The board said "27 problems" and had one.** `ScanBanner` derived the list
itself with `c.status === "error" || c.why`, and every channel carries a `why`:
"353 boards", "1 of 1 countries x 1 terms". Measured 2026-09-07 over the real
`lastIngest`: 27 channels listed, 1 actually errored, 26 present for having a
note. That is the third signal in a week that existed and said the wrong thing,
after the error list that was 80% 304s and the registry fields nothing wrote.

So a problem has to pass one test: a reader can do something about it. Each
entry names what it is and what it would take.
"""

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


import source_health as sh  # noqa: E402
import sweep_runs  # noqa: E402


#: The real channel notes from the 2026-09-07 sweep, which are what the old
#: derivation turned into problems. Pinned so the check is against the corpus
#: rather than an opinion of it.
REAL_CHANNELS = {
    "adzuna": {"status": "ok", "found": 50, "why": "1 of 1 countries x 1 terms"},
    "ashby": {"status": "ok", "found": 2241,
              "why": "194 boards, 2 token(s) 404 and need re-resolving"},
    "breezy": {"status": "ok", "found": 0, "why": "17 boards"},
    "careerjet": {"status": "ok", "found": 50, "why": "1 of 1 locales x 1 terms"},
    "gem": {"status": "ok", "found": 4, "why": "1 boards"},
    "greenhouse": {"status": "ok", "found": 14254,
                   "why": "353 boards, 6 token(s) 404 and need re-resolving"},
    "jooble": {"status": "error", "found": 0, "why": "jooble any '': HTTP 400"},
}


class ProblemsTests(unittest.TestCase):
    def test_a_channel_note_is_not_a_problem(self):
        # The whole finding. 26 of the old list's 27 were here.
        found = sh.problems(REAL_CHANNELS, {}, [])
        self.assertEqual([p["source"] for p in found], ["jooble"])

    def test_the_one_real_failure_is_reported_with_its_reason(self):
        found = sh.problems(REAL_CHANNELS, {}, [])
        self.assertIn("HTTP 400", found[0]["what"])
        self.assertIn("scripts/sources/jooble.py", found[0]["takes"])

    def test_a_missing_key_names_what_to_set(self):
        channels = {"reed": {"status": "error",
                             "why": "no key: set JOBBY_REED_KEY"}}
        found = sh.problems(channels, {}, [])
        self.assertIn("JOBBY_REED_KEY", found[0]["what"])
        self.assertIn("set the variable", found[0]["takes"])

    def test_every_entry_says_what_it_would_take(self):
        # A line a reader cannot act on is a log entry wearing a problem's
        # clothes, which is what the 27 were.
        found = sh.problems(
            REAL_CHANNELS,
            {"lever": {"found": 900, "lowRuns": 3, "lastRatio": 0.21}},
            [{"source": "greenhouse", "board_slug": "acme",
              "disabled_at": "now", "last_failure": "http 404"}])
        self.assertEqual(len(found), 3)
        for p in found:
            with self.subTest(kind=p["kind"]):
                self.assertTrue(p["what"] and len(p["what"]) > 20)
                self.assertTrue(p["takes"] and len(p["takes"]) > 20)

    def test_the_worst_kind_comes_first(self):
        found = sh.problems(
            REAL_CHANNELS,
            {"lever": {"found": 900, "lowRuns": 2, "lastRatio": 0.21}},
            [{"source": "gh", "board_slug": "acme", "disabled_at": "now"}])
        self.assertEqual([p["kind"] for p in found],
                         ["channel_failed", "source_under_anchor",
                          "board_disabled"])

    def test_a_disabled_board_is_named(self):
        found = sh.disabled_boards([
            {"source": "greenhouse", "board_slug": "acme-corp",
             "disabled_at": "now", "last_failure": "http 404"},
            {"source": "greenhouse", "board_slug": "live-one"},
        ])
        self.assertEqual(len(found), 1)
        self.assertIn("acme-corp", found[0]["what"])
        self.assertIn("404", found[0]["what"])

    def test_a_healthy_sweep_has_no_problems(self):
        healthy = {k: v for k, v in REAL_CHANNELS.items() if k != "jooble"}
        self.assertEqual(sh.problems(healthy, {}, []), [])


class DetectionTests(unittest.TestCase):
    def test_one_low_sweep_is_not_an_alert(self):
        # An ordinary source. greenhouse and ashby are deliberately not
        # ordinary and have their own class below.
        entry = sh.observe({"found": 5909}, 2000, 5909, source="lever")
        self.assertEqual(entry["lowRuns"], 1)
        self.assertEqual(sh.under_anchor({"lever": {**entry, "found": 5909}}), [])

    def test_two_low_sweeps_running_are(self):
        entry = sh.observe({"found": 5909}, 2000, 5909, source="lever")
        entry = sh.observe(entry, 2100, 5909, source="lever")
        self.assertEqual(entry["lowRuns"], 2)
        found = sh.under_anchor({"lever": {**entry, "found": 5909}})
        self.assertEqual(len(found), 1)
        self.assertIn("lever", found[0]["what"])
        self.assertIn("36%", found[0]["what"])

    def test_a_recovery_clears_the_counter(self):
        entry = sh.observe({"found": 5909}, 2000, 5909, source="lever")
        entry = sh.observe(entry, 5800, 5909, source="lever")
        self.assertEqual(entry["lowRuns"], 0)

    def test_the_2026_08_30_greenhouse_sweep_is_a_low_sweep(self):
        # 3,209 against 5,909 is 54%, above the 50% detection floor and below
        # the 70% coverage floor. It is refused by the gate and is NOT an
        # alert on its own, which is the intended split: the cheap protective
        # action fires readily, the one that costs attention does not.
        entry = sh.observe({"found": 5909}, 3209, 5909, source="lever")
        self.assertEqual(entry["lowRuns"], 0)
        self.assertFalse(sweep_runs.gate("greenhouse", 3209, {"found": 5909})[0])

    def test_the_floors_are_different_numbers_for_different_questions(self):
        self.assertLess(sh.DETECTION_FLOOR, sweep_runs.YIELD_FLOOR)

    def test_a_source_with_no_anchor_is_not_under_it(self):
        entry = sh.observe({}, 5, None)
        self.assertEqual(entry["lowRuns"], 0)
        self.assertNotIn("lastRatio", entry)

    def test_an_unusable_count_is_not_counted_as_low(self):
        for bad in (None, -1, "3209"):
            with self.subTest(found=bad):
                self.assertEqual(sh.observe({"found": 900}, bad, 900)["lowRuns"], 0)


class StrictSourceTests(unittest.TestCase):
    """greenhouse and ashby, which 48 of 52 submitted applications came from.

    Two sweeps is a reasonable guarantee for a source contributing a hundred
    postings and too slow for the two carrying the pipeline: eight hours of a
    dead greenhouse looks exactly like a quiet day.
    """

    def test_one_sweep_at_54_percent_alerts_for_greenhouse(self):
        # The 2026-08-30 ratio. Refused by the coverage gate and, for an
        # ordinary source, not an alert until it happens twice.
        entry = sh.observe({"found": 5909}, 3209, 5909, source="greenhouse")
        self.assertEqual(entry["lowRuns"], 1)
        found = sh.under_anchor({"greenhouse": {**entry, "found": 5909}})
        self.assertEqual(len(found), 1)
        self.assertIn("54%", found[0]["what"])

    def test_the_same_ratio_does_not_alert_for_an_ordinary_source(self):
        entry = sh.observe({"found": 5909}, 3209, 5909, source="lever")
        self.assertEqual(sh.under_anchor({"lever": {**entry, "found": 5909}}), [])

    def test_ashby_is_strict_too(self):
        entry = sh.observe({"found": 2241}, 1300, 2241, source="ashby")
        self.assertEqual(len(sh.under_anchor({"ashby": {**entry, "found": 2241}})), 1)

    def test_the_strict_floor_is_the_coverage_gate_s_own(self):
        # Deliberate coupling: for these two the sweep whose absences stop
        # being believed is the sweep the user is told about. Refusing to close
        # on a source and not mentioning it is the quiet half of the fault.
        self.assertEqual(sh.floor_for("greenhouse"), sweep_runs.YIELD_FLOOR)
        self.assertEqual(sh.floor_for("ashby"), sweep_runs.YIELD_FLOOR)

    def test_every_strict_source_says_why_it_is_strict(self):
        for name, spec in sh.STRICT_SOURCES.items():
            with self.subTest(source=name):
                self.assertGreater(len(spec.get("why", "")), 20)

    def test_every_strict_source_has_a_canary(self):
        # A stricter threshold catches a source going quiet. It cannot catch
        # one board of three hundred breaking, which is what the canary is for,
        # so a source held to the higher standard gets both.
        self.assertEqual(set(sh.STRICT_SOURCES), set(sh.CANARIES))


class CanaryTests(unittest.TestCase):
    """One board per strict source, checked on its own every sweep."""

    def posting(self, slug, external="1"):
        return {"boardKey": {"slug": slug, "externalId": external}}

    def test_it_counts_only_the_canary_board(self):
        postings = ([self.posting("spacex", str(i)) for i in range(40)]
                    + [self.posting("stripe", str(i)) for i in range(9)])
        self.assertEqual(sh.canary_yield("greenhouse", postings), 40)

    def test_a_source_that_emits_no_key_is_unreadable_not_zero(self):
        # "Could not look" is never "is not there". A source that stopped
        # emitting keys has regressed the contract, and reporting 0 would
        # raise a canary alarm about a board that is fine.
        self.assertIsNone(sh.canary_yield("greenhouse", [{"title": "x"}]))
        problem = sh.canary_problem("greenhouse", None, 500)
        self.assertEqual(problem["kind"], "canary_unreadable")

    def test_a_silent_canary_is_a_problem(self):
        problem = sh.canary_problem("greenhouse", 0, 583)
        self.assertEqual(problem["kind"], "canary_silent")
        self.assertIn("spacex", problem["what"])
        self.assertIn("parse broke", problem["takes"])

    def test_an_unchanged_canary_is_not_a_problem(self):
        # It answered 304 and returned nothing legitimately. Without this the
        # 304 fault repeats one layer down, on the check built to catch it.
        self.assertIsNone(sh.canary_problem("greenhouse", 0, 583,
                                            unchanged_slugs=["spacex"]))
        self.assertIsNone(sh.canary_problem("greenhouse", 0, 583,
                                            unchanged_slugs=["a", "spacex", "b"]))

    def test_another_board_being_unchanged_does_not_excuse_the_canary(self):
        problem = sh.canary_problem("greenhouse", 0, 583,
                                    unchanged_slugs=["stripe", "elastic"])
        self.assertEqual(problem["kind"], "canary_silent")

    def test_movement_inside_the_tolerance_is_normal(self):
        # Measured arrivals run about 1.1% of the board per four-hour sweep,
        # so ordinary churn sits well inside 10%.
        for count in (583, 600, 560, 640, 530):
            with self.subTest(count=count):
                self.assertIsNone(sh.canary_problem("greenhouse", count, 583))

    def test_movement_outside_it_is_reported_with_both_numbers(self):
        problem = sh.canary_problem("greenhouse", 300, 583)
        self.assertEqual(problem["kind"], "canary_moved")
        self.assertIn("300", problem["what"])
        self.assertIn("583", problem["what"])

    def test_the_first_sweep_has_nothing_to_compare_against(self):
        self.assertIsNone(sh.canary_problem("greenhouse", 583, None))
        self.assertIsNone(sh.canary_problem("greenhouse", 583, 0))

    def test_a_source_with_no_canary_is_not_checked(self):
        self.assertIsNone(sh.canary_yield("lever", []))
        self.assertIsNone(sh.canary_problem("lever", 0, 100))

    def test_a_canary_problem_reaches_the_problems_list(self):
        ledger = {"greenhouse": {"found": 14254, "canaryProblem": {
            "kind": "canary_silent", "source": "greenhouse", "board": "spacex",
            "what": "greenhouse canary board spacex was fetched and returned "
                    "no postings",
            "takes": "open the board's API response by hand"}}}
        found = sh.problems({}, ledger, [])
        self.assertEqual([p["kind"] for p in found], ["canary_silent"])

    def test_a_canary_problem_is_louder_than_a_source_under_anchor(self):
        ledger = {"greenhouse": {"found": 14254, "lowRuns": 5,
                                 "lastRatio": 0.2,
                                 "canaryProblem": {"kind": "canary_silent",
                                                   "source": "greenhouse",
                                                   "what": "x" * 30,
                                                   "takes": "y" * 30}}}
        self.assertEqual([p["kind"] for p in sh.problems({}, ledger, [])],
                         ["canary_silent", "source_under_anchor"])


class WarningTests(unittest.TestCase):
    """The alert, on the ping that already exists."""

    def test_it_is_silent_when_sourcing_is_healthy(self):
        healthy = {k: v for k, v in REAL_CHANNELS.items() if k != "jooble"}
        self.assertEqual(sh.warning({"byChannel": healthy}, {}, []), "")

    def test_it_names_the_worst_and_counts_the_rest(self):
        line = sh.warning(
            {"byChannel": REAL_CHANNELS},
            {"lever": {"found": 900, "lowRuns": 2, "lastRatio": 0.21}}, [])
        self.assertTrue(line.startswith("SOURCING: "))
        self.assertIn("jooble", line)
        self.assertIn("and 1 more", line)

    def test_it_is_one_line(self):
        # It joins five other reasons on one ping. An alarm that tries to be a
        # report is one nobody finishes reading.
        line = sh.warning({"byChannel": REAL_CHANNELS}, {}, [])
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 300)

    def test_it_is_wired_into_the_dead_man_ping(self):
        # A warning nothing calls is the `activity.json` counter again.
        import watch
        self.assertIn(watch.sourcing_warning, watch.PING_REASONS)

    def test_the_ping_reason_reads_the_key_the_sweep_writes(self):
        # Two names for one store key is how an alarm ends up reading a key
        # nothing writes.
        import watch
        self.assertEqual(watch.source_health_key(), sweep_runs.STORE_KEY)


class ConnectorReportsUnchangedTests(unittest.TestCase):
    """The connectors name the boards that answered 304, not just count them.

    **The canary check depends on this and nothing covered it.** Found by
    mutation: removing `unchanged_slugs=` from greenhouse's `base.ok` call left
    every test green, and the consequence is a canary board that answers 304
    being reported as silent -- a false alarm on the alarm built to catch a
    real one, which is the 304 fault repeating inside its own fix.
    """

    class Unchanged:
        unchanged = True
        status = 304

    class Fetcher:
        requests = 0
        def get_json(self, url, **kw):
            return None, ConnectorReportsUnchangedTests.Unchanged()

    def registry(self, ats, tokens):
        return {t: {"name": t.title(), "ats": ats, "token": t} for t in tokens}

    def test_greenhouse_names_them(self):
        from sources import greenhouse
        out = greenhouse.fetch(self.Fetcher(),
                               companies=self.registry("greenhouse",
                                                       ["spacex", "stripe"]))
        self.assertEqual(out["unchanged"], 2)
        self.assertEqual(out["unchangedSlugs"], ["spacex", "stripe"])
        self.assertEqual(out["errors"], [])

    def test_ashby_names_them(self):
        from sources import ashby
        out = ashby.fetch(self.Fetcher(),
                          companies=self.registry("ashby",
                                                  ["snowflake", "vanta"]))
        self.assertEqual(out["unchanged"], 2)
        self.assertEqual(out["unchangedSlugs"], ["snowflake", "vanta"])

    def test_the_canary_is_excused_by_what_the_connector_reported(self):
        # End to end across the seam the mutation exposed: the connector's
        # answer is what the canary check reads, so a 304 canary must not fire.
        from sources import greenhouse
        out = greenhouse.fetch(self.Fetcher(),
                               companies=self.registry("greenhouse",
                                                       ["spacex", "stripe"]))
        seen = sh.canary_yield("greenhouse", out["postings"])
        self.assertIsNone(
            sh.canary_problem("greenhouse", seen, 583,
                              unchanged_slugs=out["unchangedSlugs"]))

    def test_and_fires_when_the_canary_was_actually_asked(self):
        # The control. If it were excused unconditionally the check would be
        # inert rather than correct.
        from sources import greenhouse
        out = greenhouse.fetch(self.Fetcher(),
                               companies=self.registry("greenhouse",
                                                       ["stripe", "elastic"]))
        problem = sh.canary_problem("greenhouse", 0, 583,
                                    unchanged_slugs=out["unchangedSlugs"])
        self.assertEqual(problem["kind"], "canary_silent")


class CanaryFieldTests(unittest.TestCase):
    """The one failure mode no count can see.

    Every other way these two break moves a number, and every one of those is
    caught inside a sweep:

        url scheme change     every board 404s -> channel_failed
        auth added            every board 401s -> channel_failed
        response shape change `jobs` empties   -> anchor + canary_silent
        new pagination cap    count drops      -> anchor at 70%
        registry emptied      count drops to 0 -> anchor

    A field change moves nothing. `absolute_url` renamed, `location.name`
    becoming `location.text`: the response parses, the count is identical, and
    the postings enter the pool with no link. The count is right and the thing
    it summarises is not.
    """

    def good(self, n=100, **over):
        return [dict({"boardKey": {"slug": "spacex", "externalId": str(i)},
                      "canonicalUrl": f"https://boards.greenhouse.io/spacex/jobs/{i}",
                      "title": "Engineer"}, **over) for i in range(n)]

    def test_a_healthy_board_reports_nothing(self):
        shares = sh.canary_fields("greenhouse", self.good())
        self.assertEqual(shares["canonicalUrl"], 1.0)
        self.assertIsNone(sh.canary_field_problem("greenhouse", shares))

    def test_a_renamed_url_field_is_caught_with_the_count_unchanged(self):
        broken = self.good(canonicalUrl="")
        self.assertEqual(sh.canary_yield("greenhouse", broken), 100)
        self.assertIsNone(sh.canary_problem("greenhouse", 100, 100))
        problem = sh.canary_field_problem(
            "greenhouse", sh.canary_fields("greenhouse", broken))
        self.assertEqual(problem["kind"], "canary_field_missing")
        self.assertEqual(problem["field"], "canonicalUrl")
        self.assertIn("right number of postings", problem["what"])

    def test_a_lost_record_key_is_caught(self):
        # The contract regressing on one source, which would also take that
        # source's canary with it.
        broken = [{k: v for k, v in p.items() if k != "boardKey"}
                  for p in self.good()]
        # Nothing to measure once the key is gone, which is the *other* alarm.
        self.assertIsNone(sh.canary_yield("greenhouse", broken))
        self.assertEqual(sh.canary_problem("greenhouse", None, 100)["kind"],
                         "canary_unreadable")

    def test_a_few_blanks_are_normal(self):
        # A real board has the occasional posting with a missing field, and a
        # check at 100% is one somebody turns off.
        mixed = self.good(96) + [dict(p, title="") for p in self.good(4)]
        self.assertIsNone(sh.canary_field_problem(
            "greenhouse", sh.canary_fields("greenhouse", mixed)))

    def test_it_only_looks_at_the_canary_board(self):
        others = [{"boardKey": {"slug": "stripe", "externalId": str(i)},
                   "canonicalUrl": "", "title": ""} for i in range(500)]
        shares = sh.canary_fields("greenhouse", self.good() + others)
        self.assertEqual(shares["canonicalUrl"], 1.0)

    def test_no_canary_postings_is_not_a_field_problem(self):
        # "Could not look" belongs to canary_problem, which says canary_silent.
        self.assertEqual(sh.canary_fields("greenhouse", []), {})
        self.assertIsNone(sh.canary_field_problem("greenhouse", {}))

    def test_every_threshold_leaves_room_for_a_real_board(self):
        for field, floor in sh.CANARY_FIELDS.items():
            with self.subTest(field=field):
                self.assertGreater(floor, 0.5)
                self.assertLess(floor, 1.0,
                                "a check at 100% is a check somebody turns off")
