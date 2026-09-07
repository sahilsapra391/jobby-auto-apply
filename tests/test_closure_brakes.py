"""Five brakes on the closure sweep, and the sweep of 2026-08-30 as the case.

**The event is not hypothetical and the numbers are its own.** One sweep closed
1,860 records, tripped the per-board brake zero times, and nobody noticed for
four days:

    2026-08-30T01:02Z     found before   found after   status
    greenhouse                   5,909         3,209   ok
    smartrecruiters                996             0   ok
    oracle_orc                   9,357         9,356   ok

Two independent mechanisms would each have caught it, which is a reason to
build both rather than to pick one. `sweep_runs` catches a channel that
under-delivered and reported success. The pool ceiling catches a correlated
failure across channels that each look individually fine.

The corpus is this device's own 72 recorded sweeps, closed as a share of the
pool before that sweep's own prune:

    median 0.269%   p75 0.433%   p90 0.639%   p95 0.701%
    next   1.052%   (2026-08-25)
    max    9.006%   (2026-08-30)

Converted to the closeable denominator at the pool's measured composition
(14,243 of 24,132, 59.0%, on 2026-09-07), the event is 15.26% and the loudest
ordinary sweep is 1.78%. The threshold sits in the canyon between them, which
is what makes 72 sweeps and one outlier a usable sample.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import jobs_store  # noqa: E402
import sweep_runs  # noqa: E402


#: Every sweep this device recorded that carries a closure count, as
#: (closed, pool before that sweep's own prune). Read out of
#: `~/.job-application/logs/ingest-runs.log` on 2026-09-07 and pinned here so
#: the threshold is checked against the corpus rather than against an opinion
#: about it. The top of the distribution is what decides the value; the tail
#: below 0.7% is summarised by its count so this stays readable.
LOUDEST_SWEEPS = [
    ("2026-08-30", 1860, 20654),   # the incident
    ("2026-08-25", 126, 11974),
    ("2026-09-03", 235, 24790),
    ("2026-09-02", 171, 23967),
    ("2026-09-04", 175, 24960),
    ("2026-08-28", 107, 15505),
    ("2026-09-02", 148, 22858),
    ("2026-09-02", 152, 23774),
]
#: The measured share of the pool that is eligible to close at all, used to
#: convert a pool share into the denominator brake 5 actually uses.
CLOSEABLE_RATIO = 0.590


def job(job_id, company, source="greenhouse", state="new", klass="ats"):
    return {"id": job_id, "company": company, "title": "PM", "state": state,
            "canonicalSource": source, "canonicalUrl": f"https://x/{job_id}",
            "sources": [{"source": source, "sourceClass": klass, "tier": "1",
                         "url": f"https://x/{job_id}"}]}


def pool(n, company="acme", **kw):
    return {f"j{i}": job(f"j{i}", company, **kw) for i in range(n)}


class ThresholdTests(unittest.TestCase):
    """The value against the corpus it was drawn from."""

    def share(self, closed, before):
        return closed / (before * CLOSEABLE_RATIO)

    def test_it_refuses_the_sweep_that_actually_happened(self):
        label, closed, before = LOUDEST_SWEEPS[0]
        self.assertEqual(label, "2026-08-30")
        self.assertGreater(self.share(closed, before),
                           jobs_store.POOL_MAX_CLOSED_SHARE,
                           "the brake would not have refused 2026-08-30")

    def test_it_refuses_nothing_else_in_the_corpus(self):
        allowed = [(label, closed, before)
                   for label, closed, before in LOUDEST_SWEEPS[1:]
                   if self.share(closed, before) > jobs_store.POOL_MAX_CLOSED_SHARE]
        self.assertEqual(allowed, [],
                         f"these ordinary sweeps would have been refused: {allowed}")

    def test_the_threshold_sits_in_the_canyon_rather_than_beside_an_edge(self):
        # A value that only just clears the loudest ordinary sweep is a value
        # that fires on the next slightly-louder one. A value that only just
        # catches the incident misses the next slightly-smaller incident.
        loudest = max(self.share(c, b) for _, c, b in LOUDEST_SWEEPS[1:])
        incident = self.share(*LOUDEST_SWEEPS[0][1:])
        self.assertGreater(jobs_store.POOL_MAX_CLOSED_SHARE, loudest * 2)
        self.assertLess(jobs_store.POOL_MAX_CLOSED_SHARE, incident / 2)


class PoolBrakeTests(unittest.TestCase):
    """Brake 5 itself."""

    def sweep(self, jobs, seen):
        return jobs_store.close_missing(dict(jobs), seen)

    def test_an_ordinary_closure_still_closes(self):
        jobs = pool(400)
        # One board, 400 held, 8 missing: 2% of the pool and 2% of one board.
        seen = {("greenhouse", "acme"): {f"j{i}" for i in range(8, 400)}}
        _, report = self.sweep(jobs, seen)
        self.assertEqual(len(report["closed"]), 8)
        self.assertEqual(report["refused"], [])

    def test_a_correlated_failure_refuses_the_whole_sweep(self):
        # Spread across forty boards so no single one crosses MAX_CLOSED_SHARE,
        # which is exactly the shape of 2026-08-30.
        jobs, seen = {}, {}
        for board in range(40):
            company = f"co{board}"
            ids = [f"b{board}-{i}" for i in range(20)]
            for job_id in ids:
                jobs[job_id] = job(job_id, company)
            # 6 of 20 missing: 30%, under the 70% per-board ceiling.
            seen[("greenhouse", company)] = set(ids[6:])
        _, report = self.sweep(jobs, seen)
        self.assertEqual(report["closed"], [], "brake 5 did not refuse")
        pool_refusals = [r for r in report["refused"] if r.get("poolLevel")]
        self.assertEqual(len(pool_refusals), 1)
        self.assertEqual(pool_refusals[0]["wouldClose"], 240)
        self.assertEqual(pool_refusals[0]["held"], 800)
        self.assertIn("closeable population", pool_refusals[0]["why"])

    def test_no_board_tripped_the_per_board_brake_in_that_case(self):
        # The point of brake 5 is that brake 4 is silent here. If brake 4 fired
        # too, this test would be proving nothing about the new one.
        jobs, seen = {}, {}
        for board in range(40):
            company = f"co{board}"
            ids = [f"b{board}-{i}" for i in range(20)]
            for job_id in ids:
                jobs[job_id] = job(job_id, company)
            seen[("greenhouse", company)] = set(ids[6:])
        _, report = self.sweep(jobs, seen)
        per_board = [r for r in report["refused"] if not r.get("poolLevel")]
        self.assertEqual(per_board, [])

    def test_it_refuses_all_of_them_rather_than_the_excess(self):
        # Closing selectively under a pool-level anomaly means choosing which
        # boards to trust on no information.
        jobs, seen = {}, {}
        for board in range(40):
            company = f"co{board}"
            ids = [f"b{board}-{i}" for i in range(20)]
            for job_id in ids:
                jobs[job_id] = job(job_id, company)
            seen[("greenhouse", company)] = set(ids[6:])
        after, report = self.sweep(jobs, seen)
        self.assertEqual(len(report["closed"]), 0)
        self.assertEqual({j["state"] for j in after.values()}, {"new"})

    def test_a_small_pool_is_not_measured_by_ratio(self):
        # Thirty closeable records legitimately lose two in a day.
        jobs = pool(30)
        seen = {("greenhouse", "acme"): {f"j{i}" for i in range(4, 30)}}
        _, report = self.sweep(jobs, seen)
        self.assertEqual(len(report["closed"]), 4)
        self.assertEqual(report["refused"], [])

    def test_the_denominator_is_the_closeable_population_not_the_pool(self):
        # 400 closeable plus 4,000 tombstones. Against the pool the same
        # closure is 0.5% and against the closeable population it is 25%.
        jobs = pool(400)
        for i in range(4000):
            jobs[f"t{i}"] = job(f"t{i}", "acme", state="expired")
        seen = {("greenhouse", "acme"): {f"j{i}" for i in range(100, 400)}}
        _, report = self.sweep(jobs, seen)
        self.assertEqual(report["closeable"], 400)
        self.assertEqual(report["closed"], [])
        self.assertTrue(any(r.get("poolLevel") for r in report["refused"]))

    def test_aggregator_records_are_in_neither_the_numerator_nor_the_denominator(self):
        jobs = pool(400)
        for i in range(4000):
            jobs[f"a{i}"] = job(f"a{i}", "acme", source="adzuna",
                                klass="aggregator")
        seen = {("greenhouse", "acme"): {f"j{i}" for i in range(8, 400)}}
        _, report = self.sweep(jobs, seen)
        self.assertEqual(report["closeable"], 400)
        self.assertEqual(len(report["closed"]), 8)


class SurvivingBrakeTests(unittest.TestCase):
    """The four that were already here. Brake 5 must not have eaten any."""

    def test_brake_1_a_board_the_sweep_never_returned_is_untouched(self):
        # The structural one, with no code of its own, and therefore the one
        # that disappears without anybody deleting anything.
        jobs = pool(400)
        _, report = jobs_store.close_missing(dict(jobs), {})
        self.assertEqual(report["closed"], [])
        self.assertEqual(report["refused"], [])

    def test_brake_1_holds_when_another_board_did_return(self):
        # A sweep that reached one board must not close a second board's rows.
        jobs = {**pool(10, company="seen-co"),
                **{f"u{i}": job(f"u{i}", "unseen-co") for i in range(10)}}
        seen = {("greenhouse", "seen-co"): {f"j{i}" for i in range(2, 10)}}
        _, report = jobs_store.close_missing(dict(jobs), seen)
        self.assertEqual({c["id"] for c in report["closed"]}, {"j0", "j1"})

    def test_brake_2_applied_and_passed_are_never_closed(self):
        jobs = {}
        for i, state in enumerate(("applied", "passed", "new")):
            jobs[f"j{i}"] = job(f"j{i}", "acme", state=state)
        seen = {("greenhouse", "acme"): set()}
        after, report = jobs_store.close_missing(dict(jobs), seen)
        self.assertEqual([c["id"] for c in report["closed"]], ["j2"])
        self.assertEqual(after["j0"]["state"], "applied")
        self.assertEqual(after["j1"]["state"], "passed")

    def test_brake_3_an_aggregator_sighting_is_not_evidence(self):
        jobs = {"a": job("a", "acme", source="adzuna", klass="aggregator")}
        seen = {("adzuna", "acme"): set()}
        _, report = jobs_store.close_missing(dict(jobs), seen)
        self.assertEqual(report["closed"], [])

    def test_brake_4_one_board_losing_most_of_itself_is_a_bad_fetch(self):
        jobs = pool(20)
        seen = {("greenhouse", "acme"): {"j0", "j1"}}
        _, report = jobs_store.close_missing(dict(jobs), seen)
        self.assertEqual(report["closed"], [])
        self.assertEqual(len(report["refused"]), 1)
        self.assertIn("one board", report["refused"][0]["why"])

    def test_brake_4_still_names_the_board_under_brake_5(self):
        # Both can fire in one sweep and the per-board detail is what a person
        # acts on. Losing it to the pool-level refusal would make the louder
        # alarm less useful than the quieter one.
        jobs, seen = {}, {}
        ids = [f"tiny{i}" for i in range(20)]
        for job_id in ids:
            jobs[job_id] = job(job_id, "tiny-co")
        seen[("greenhouse", "tiny-co")] = {ids[0]}          # 95%, brake 4
        for board in range(40):
            company = f"co{board}"
            more = [f"b{board}-{i}" for i in range(20)]
            for job_id in more:
                jobs[job_id] = job(job_id, company)
            seen[("greenhouse", company)] = set(more[6:])   # 30% each, brake 5
        _, report = jobs_store.close_missing(dict(jobs), seen)
        self.assertEqual(report["closed"], [])
        kinds = {bool(r.get("poolLevel")) for r in report["refused"]}
        self.assertEqual(kinds, {True, False})


class SweepRunsTests(unittest.TestCase):
    """The gate, against the numbers it exists for."""

    def test_the_greenhouse_run_of_2026_08_30_is_refused(self):
        believed, why = sweep_runs.gate("greenhouse", 3209, {"found": 5909})
        self.assertFalse(believed)
        self.assertIn("3209", why)
        self.assertIn("5909", why)

    def test_the_empty_smartrecruiters_run_is_refused_too(self):
        # Already safe through brake 1, and refused here as well, because
        # "returned nothing" and "was not asked" are different facts and only
        # one of them is visible in `seen`.
        believed, _ = sweep_runs.gate("smartrecruiters", 0, {"found": 996})
        self.assertFalse(believed)

    def test_the_run_that_held_steady_is_believed(self):
        believed, why = sweep_runs.gate("oracle_orc", 9356, {"found": 9357})
        self.assertTrue(believed)
        self.assertEqual(why, "")

    def test_ordinary_movement_is_believed(self):
        # The real 2026-09-07 sweep against its predecessor, for every channel
        # that carried enough postings for the ratchet to apply.
        for name, found, last in (("greenhouse", 14254, 14000),
                                  ("ashby", 2241, 2100),
                                  ("adzuna", 50, 50)):
            with self.subTest(channel=name):
                self.assertTrue(sweep_runs.gate(name, found, {"found": last})[0])

    def test_a_small_channel_is_not_ratcheted(self):
        self.assertTrue(sweep_runs.gate("gem", 4, {"found": 11})[0])
        self.assertTrue(sweep_runs.gate("breezy", 0, {"found": 30})[0])

    def test_a_channel_with_no_anchor_is_believed(self):
        # No comparison is not a failed comparison. Brake 5 still covers the
        # sweep as a whole, and brake 1 still covers a board nobody fetched.
        self.assertTrue(sweep_runs.gate("new-connector", 5000, None)[0])
        self.assertTrue(sweep_runs.gate("new-connector", 5000, {})[0])

    def test_an_unusable_count_is_refused_rather_than_believed(self):
        for bad in (None, -1, "3209", 3209.0):
            with self.subTest(found=bad):
                self.assertFalse(sweep_runs.gate("x", bad, {"found": 5909})[0])

    def test_only_a_believed_run_moves_the_anchor(self):
        ledger = sweep_runs.remember({}, "greenhouse", 5909, "t0")
        self.assertEqual(ledger["greenhouse"]["found"], 5909)
        ledger, again = sweep_runs.note_refusal(ledger, "greenhouse", 3209, "t1")
        self.assertFalse(again)
        self.assertEqual(ledger["greenhouse"]["found"], 5909,
                         "a refused run moved the anchor, so the ratchet "
                         "would follow a truncation down")

    def test_two_refusals_running_re_anchor(self):
        # One fetch disagreeing with the anchor is a bad fetch; two independent
        # fetches agreeing is a channel that really shrank. Without this a
        # genuinely halved channel is refused for ever.
        ledger = sweep_runs.remember({}, "greenhouse", 5909, "t0")
        ledger, again = sweep_runs.note_refusal(ledger, "greenhouse", 3209, "t1")
        self.assertFalse(again)
        ledger, again = sweep_runs.note_refusal(ledger, "greenhouse", 3100, "t2")
        self.assertTrue(again)
        self.assertEqual(ledger["greenhouse"]["found"], 3100)
        self.assertTrue(sweep_runs.gate("greenhouse", 3100,
                                        ledger["greenhouse"])[0])

    def test_a_believed_run_clears_the_refusal_count(self):
        ledger = sweep_runs.remember({}, "greenhouse", 5909, "t0")
        ledger, _ = sweep_runs.note_refusal(ledger, "greenhouse", 3209, "t1")
        ledger = sweep_runs.remember(ledger, "greenhouse", 5800, "t2")
        self.assertEqual(ledger["greenhouse"]["refusals"], 0)
        ledger, again = sweep_runs.note_refusal(ledger, "greenhouse", 3209, "t3")
        self.assertFalse(again, "the count survived a believed run")

    def test_a_channel_it_has_never_seen_is_not_invented(self):
        ledger, again = sweep_runs.note_refusal({}, "unknown", 10, "t")
        self.assertFalse(again)
        self.assertEqual(ledger, {})


class IngestGateTests(unittest.TestCase):
    """The real sweep, because a gate nothing calls is the shape this repo has
    paid for repeatedly. Driven through `ingest.run` rather than through
    `sweep_runs.gate`, so the wiring is observed and not inferred.
    """

    def setUp(self):
        import json
        import subprocess
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run([sys.executable, str(REPO / "scripts" / "store.py"),
                        "--root", str(self.root), "init"], capture_output=True)
        import ingest
        self.ingest = ingest
        self.original = dict(ingest.sources.ALL)
        self._json = json

    def tearDown(self):
        self.ingest.sources.ALL.clear()
        self.ingest.sources.ALL.update(self.original)
        self.tmp.cleanup()

    def board(self, n, first=0):
        """`n` postings from one real employer board."""
        import sources.base as base
        return [base.posting("greenhouse", "ats", "1", "Acme", f"PM {i}",
                             f"https://boards.greenhouse.io/acme/jobs/{i}",
                             description="Product manager, remote.")
                for i in range(first, first + n)]

    def channel(self, postings):
        import sources.base as base

        def connector(fetcher, **_):
            return base.ok("greenhouse", postings, requests=1, complete=True)
        return connector

    def sweep(self, postings):
        import sources.base as base
        self.ingest.sources.ALL.clear()
        self.ingest.sources.ALL.update({"greenhouse": self.channel(postings)})
        return self.ingest.run(
            self.root, settings={},
            fetcher=base.Fetcher(root=self.root, opener=None))

    def ledger(self):
        path = self.root / "meta.json"
        if not path.exists():
            return {}
        return self._json.loads(path.read_text()).get(sweep_runs.STORE_KEY) or {}

    def test_a_first_sweep_records_what_the_channel_returned(self):
        report, jobs = self.sweep(self.board(120))
        self.assertEqual(report["found"], 120)
        self.assertEqual(self.ledger()["greenhouse"]["found"], 120)
        self.assertNotIn("closureGated", report)

    def test_a_truncated_second_sweep_closes_nothing_and_says_why(self):
        self.sweep(self.board(120))
        # 54% of the previous yield: 2026-08-30's Greenhouse ratio exactly.
        report, jobs = self.sweep(self.board(65))
        self.assertEqual(report["closed"], 0)
        gated = report.get("closureGated") or []
        self.assertEqual([g["channel"] for g in gated], ["greenhouse"])
        self.assertIn("below the 70% floor", gated[0]["why"])
        self.assertEqual(len([j for j in jobs.values()
                              if j.get("state") == "expired"]), 0)

    def test_the_same_sweep_without_the_gate_would_have_closed_them(self):
        # The control. Without it the test above passes for any reason at all,
        # including the sweep simply not reaching the closure pass.
        self.sweep(self.board(120))
        report, jobs = self.sweep(self.board(100))    # 83%, believed
        self.assertNotIn("closureGated", report)
        self.assertEqual(report["closed"], 20)

    def test_a_refused_sweep_does_not_move_the_anchor(self):
        self.sweep(self.board(120))
        self.sweep(self.board(65))
        self.assertEqual(self.ledger()["greenhouse"]["found"], 120)
        self.assertEqual(self.ledger()["greenhouse"]["refusals"], 1)

    def test_a_channel_that_really_shrank_re_anchors_on_the_second_sweep(self):
        self.sweep(self.board(120))
        self.sweep(self.board(65))
        report, _ = self.sweep(self.board(65))
        self.assertEqual(report.get("closureGateReanchored"), ["greenhouse"])
        self.assertEqual(self.ledger()["greenhouse"]["found"], 65)
        # And is believed from then on, rather than refused for ever.
        report, _ = self.sweep(self.board(65))
        self.assertNotIn("closureGated", report)

    def test_a_dry_run_writes_no_anchor(self):
        self.sweep(self.board(120))
        before = dict(self.ledger())
        self.ingest.sources.ALL.clear()
        import sources.base as base
        self.ingest.sources.ALL.update({"greenhouse": self.channel(self.board(65))})
        self.ingest.run(self.root, settings={}, dry_run=True,
                        fetcher=base.Fetcher(root=self.root, opener=None))
        self.assertEqual(self.ledger(), before)

    def test_a_sweep_whose_postings_do_not_commit_leaves_the_anchor_alone(self):
        # **The property the ordering exists for**, rather than an assertion
        # about which line comes first. A yield recorded before the commit is a
        # lie the moment the write fails, and the next sweep would then ratchet
        # against a run that did not happen -- believing a truncation because
        # a failed run said it was fine.
        #
        # `test_a_dry_run_writes_no_anchor` above does not cover this: it
        # passes with the ledger update moved before the commit, because the
        # persist call is separately guarded. Found by mutating the guard and
        # watching that test stay green.
        from unittest import mock
        import sources.base as base
        self.sweep(self.board(120))
        before = dict(self.ledger())
        self.ingest.sources.ALL.clear()
        self.ingest.sources.ALL.update({"greenhouse": self.channel(self.board(300))})
        with mock.patch.object(self.ingest.jobs_store, "save_jobs",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.ingest.run(self.root, settings={},
                                fetcher=base.Fetcher(root=self.root, opener=None))
        self.assertEqual(self.ledger(), before,
                         "the anchor moved for a sweep whose postings never "
                         "reached the store")
