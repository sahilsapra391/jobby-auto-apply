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
        entry = sh.observe({"found": 5909}, 2000, 5909)
        self.assertEqual(entry["lowRuns"], 1)
        self.assertEqual(sh.under_anchor({"greenhouse": {**entry, "found": 5909}}), [])

    def test_two_low_sweeps_running_are(self):
        entry = sh.observe({"found": 5909}, 2000, 5909)
        entry = sh.observe(entry, 2100, 5909)
        self.assertEqual(entry["lowRuns"], 2)
        found = sh.under_anchor({"greenhouse": {**entry, "found": 5909}})
        self.assertEqual(len(found), 1)
        self.assertIn("greenhouse", found[0]["what"])
        self.assertIn("36%", found[0]["what"])

    def test_a_recovery_clears_the_counter(self):
        entry = sh.observe({"found": 5909}, 2000, 5909)
        entry = sh.observe(entry, 5800, 5909)
        self.assertEqual(entry["lowRuns"], 0)

    def test_the_2026_08_30_greenhouse_sweep_is_a_low_sweep(self):
        # 3,209 against 5,909 is 54%, above the 50% detection floor and below
        # the 70% coverage floor. It is refused by the gate and is NOT an
        # alert on its own, which is the intended split: the cheap protective
        # action fires readily, the one that costs attention does not.
        entry = sh.observe({"found": 5909}, 3209, 5909)
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
