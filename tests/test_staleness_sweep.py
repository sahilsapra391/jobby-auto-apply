"""A job the employer took down has to leave the board.

The failure these cover is the one the user hit: a Greenhouse posting that
still answers HTTP 200, still renders a card at 95% match, and 404s in spirit
when you click Posting. Greenhouse redirects dead requisitions to the board
index, so the status code says nothing. Absence from the board listing is the
signal, and the sweep already fetches those listings.

The interesting half is the brakes. Closing a job is destructive to a lane the
user curates by hand, so a fetch that failed, a run that was truncated, or a
board that answered oddly must all close nothing at all.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jobs_store


NOW = datetime(2026, 8, 25, 17, 0, tzinfo=timezone.utc)


def job(job_id, state="new", source="greenhouse", company="Vercel"):
    record = jobs_store.blank_job(job_id, company, "Program Manager")
    record["state"] = state
    record["canonicalSource"] = source
    # An employer board by default: the only class absence is evidence on.
    record["sources"] = [{"source": source, "sourceClass": "ats",
                          "url": f"https://example.test/{job_id}"}]
    return record


def key(source="greenhouse", company="Vercel"):
    return (source, company.strip().casefold())


class CloseMissingTests(unittest.TestCase):
    def test_a_job_its_board_no_longer_lists_is_closed(self):
        jobs = {"a": job("a"), "b": job("b")}
        jobs, report = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        self.assertEqual(jobs["b"]["state"], "expired")
        self.assertEqual(jobs["a"]["state"], "new")
        self.assertEqual([r["id"] for r in report["closed"]], ["b"])

    def test_the_closure_is_stamped_so_it_can_be_aged_out(self):
        jobs, _ = jobs_store.close_missing({"a": job("a"), "b": job("b")},
                                           {key(): {"a"}}, at=NOW)
        self.assertEqual(jobs["b"]["closedAt"], "2026-08-25T17:00:00Z")

    def test_a_watched_job_is_closed_and_the_report_says_it_was_watched(self):
        """Losing a watched role is a different event from losing a found one.

        The board should be able to tell the user which happened, so the report
        carries the state the job was in, not just a count.
        """
        jobs = {"a": job("a"), "w": job("w", state="watched")}
        jobs, report = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        self.assertEqual(jobs["w"]["state"], "expired")
        self.assertEqual(report["closed"][0]["wasState"], "watched")

    def test_applied_and_passed_are_never_touched(self):
        """The user acted on these. A dead posting does not undo the action."""
        jobs = {"a": job("a"), "ap": job("ap", state="applied"),
                "ps": job("ps", state="passed")}
        jobs, report = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        self.assertEqual(jobs["ap"]["state"], "applied")
        self.assertEqual(jobs["ps"]["state"], "passed")
        self.assertEqual(report["closed"], [])

    def test_a_board_that_was_not_fetched_closes_nothing(self):
        """The whole failure mode, in one test.

        A connector that 500s, times out, or is simply not in this run
        contributes no key. Absent must mean "no information", never "nothing
        is live there" -- otherwise one bad afternoon empties the board.
        """
        jobs = {"a": job("a"), "b": job("b", source="lever")}
        jobs, report = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        self.assertEqual(jobs["b"]["state"], "new")
        self.assertEqual(report["closed"], [])

    def test_boards_are_keyed_per_company_not_per_platform(self):
        """Greenhouse listing Vercel's board says nothing about Stripe's."""
        jobs = {"v": job("v", company="Vercel"), "s": job("s", company="Stripe")}
        jobs, _ = jobs_store.close_missing(jobs, {key(company="Vercel"): {"v"}}, at=NOW)
        self.assertEqual(jobs["s"]["state"], "new")

    def test_a_board_may_not_close_most_of_what_we_hold_for_it(self):
        """A half-empty response looks exactly like a mass layoff.

        We cannot tell them apart from here, so the majority case refuses and
        reports rather than acting. A board that really did empty gets caught
        by the next sweep once it is consistently small.
        """
        jobs = {str(i): job(str(i)) for i in range(10)}
        jobs, report = jobs_store.close_missing(jobs, {key(): {"0", "1"}}, at=NOW)
        self.assertEqual(report["closed"], [])
        self.assertEqual(len(report["refused"]), 1)
        self.assertTrue(all(j["state"] == "new" for j in jobs.values()))

    def test_the_share_brake_does_not_fire_on_a_small_board(self):
        """Two of three gone is 67% but it is also just two jobs.

        The brake exists to catch a broken fetch of a big board. Applying it to
        a three-job board would make small employers permanently unclosable.
        """
        jobs = {"a": job("a"), "b": job("b"), "c": job("c")}
        jobs, report = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        self.assertEqual(len(report["closed"]), 2)

    def test_closing_is_idempotent(self):
        jobs = {"a": job("a"), "b": job("b")}
        jobs, _ = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        jobs, second = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        self.assertEqual(second["closed"], [])


class ReopenTests(unittest.TestCase):
    def test_a_relisted_job_returns_to_found(self):
        """Without this a one-sweep flicker hides a live job until it is reaped."""
        closed = job("b", state="expired")
        closed["closedAt"] = "2026-08-25T17:00:00Z"
        back, reopened = jobs_store.reopen(closed)
        self.assertTrue(reopened)
        self.assertEqual(back["state"], "new")
        self.assertNotIn("closedAt", back)

    def test_reopen_leaves_every_other_state_alone(self):
        for state in ("new", "watched", "applied", "passed"):
            with self.subTest(state=state):
                back, reopened = jobs_store.reopen(job("x", state=state))
                self.assertFalse(reopened)
                self.assertEqual(back["state"], state)


class TombstoneRetentionTests(unittest.TestCase):
    def test_a_closed_job_is_kept_briefly_then_dropped(self):
        fresh = job("f", state="expired")
        fresh["closedAt"] = jobs_store.now_stamp()
        old = job("o", state="expired")
        old["closedAt"] = (NOW - timedelta(days=jobs_store.CLOSED_TOMBSTONE_DAYS + 1)
                           ).strftime("%Y-%m-%dT%H:%M:%SZ")
        jobs, dropped = jobs_store.prune({"f": fresh, "o": old}, cap=None, at=NOW)
        self.assertIn("f", jobs)
        self.assertNotIn("o", jobs)
        self.assertEqual([d["id"] for d in dropped], ["o"])

    def test_tombstones_do_not_evict_live_jobs_through_the_cap(self):
        """The cap measures the whole pool but may only evict `new` records.

        Left in, closed jobs would silently push live postings off the board.
        """
        jobs = {}
        for i in range(8):
            record = job(f"dead{i}", state="expired")
            record["closedAt"] = (NOW - timedelta(days=30)
                                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
            jobs[f"dead{i}"] = record
        for i in range(4):
            jobs[f"live{i}"] = job(f"live{i}")
        jobs, _ = jobs_store.prune(jobs, cap=5, at=NOW)
        self.assertEqual(sorted(jobs), ["live0", "live1", "live2", "live3"])



class SourceClassTests(unittest.TestCase):
    """Absence only counts on a listing that was ever complete."""

    def _job(self, job_id, source_class):
        record = job(job_id)
        record["sources"] = [{"source": "greenhouse", "sourceClass": source_class,
                              "url": f"https://example.test/{job_id}"}]
        return record

    def test_a_job_missing_from_an_employer_board_is_closed(self):
        jobs = {"a": self._job("a", "ats"), "b": self._job("b", "ats")}
        jobs, report = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
        self.assertEqual(jobs["b"]["state"], "expired")

    def test_a_job_missing_from_an_aggregator_page_is_not(self):
        """A search page dropping a result is not an employer taking it down."""
        for source_class in ("aggregator", "feed", "other"):
            with self.subTest(source_class=source_class):
                jobs = {"a": self._job("a", source_class),
                        "b": self._job("b", source_class)}
                jobs, report = jobs_store.close_missing(jobs, {key(): {"a"}}, at=NOW)
                self.assertEqual(jobs["b"]["state"], "new")
                self.assertEqual(report["closed"], [])


if __name__ == "__main__":
    unittest.main()
