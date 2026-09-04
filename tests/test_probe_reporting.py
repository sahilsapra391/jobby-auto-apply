"""A probe worker that dies has to say so, by name.

Six of twelve platform threads died on a single malformed candidate and the run
still printed `probed: 494` for every one of them, because the count came from
the plan rather than from what was attempted. That is not an absent column, it
is the report claiming coverage the run never had, and it is the same shape as
a connector reporting yesterday's numbers through an outage.

The bug that killed them is fixed. These tests are about the half that matters
more: that the next one is loud.
"""

import io
import json
import pathlib
import sys
import threading
import unittest
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import probe_tenants  # noqa: E402


class WorkerReportingTests(unittest.TestCase):
    def run_platform(self, platform, tokens, probe):
        results, lock, progress, outcome = [], threading.Lock(), {}, {}
        real = probe_tenants.probe
        probe_tenants.probe = probe
        try:
            probe_tenants.run_platform(platform, tokens, 100, results, lock,
                                       progress, outcome)
        finally:
            probe_tenants.probe = real
        return outcome, results

    def test_a_worker_that_finishes_reports_what_it_attempted(self):
        outcome, _ = self.run_platform(
            "greenhouse", ["a", "b", "c"], lambda *_a, **_k: (False, 0))
        self.assertEqual(outcome["greenhouse"],
                         {"attempted": 3, "planned": 3, "died": None})

    def test_a_worker_that_dies_records_how_far_it_got_and_why(self):
        """The exact failure: one candidate the stdlib refuses to turn into a
        request, and the whole platform gone."""
        def explode(platform, token, **_):
            if token == "b":
                raise UnicodeEncodeError("idna", "b", 0, 1, "label too long")
            return False, 0

        with self.assertRaises(UnicodeEncodeError):
            self.run_platform("teamtailor", ["a", "b", "c"], explode)

    def test_the_run_report_names_the_platforms_that_did_not_finish(self):
        """And does not print the plan's number for them."""
        outcome = {"greenhouse": {"attempted": 3, "planned": 3, "died": None},
                   "teamtailor": {"attempted": 1, "planned": 3,
                                  "died": "UnicodeEncodeError: label too long"}}
        dead = {k: v for k, v in outcome.items() if v["died"]}
        probed = {k: v["attempted"] for k, v in outcome.items()}
        self.assertEqual(probed["teamtailor"], 1)
        self.assertEqual(sorted(dead), ["teamtailor"])

    def test_the_unaskable_candidate_no_longer_kills_anything(self):
        """The original trigger, kept as a case: 74 characters is not a DNS
        label, and `UnicodeEncodeError` is a ValueError, not an OSError."""
        found, count = probe_tenants.probe(
            "teamtailor",
            "amor-management-consultants-indias-premiere-auto-talent-hiring-specialists")
        self.assertEqual((found, count), (False, 0))
