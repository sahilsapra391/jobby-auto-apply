"""The board carries a slice of the job pool, never the pool.

The store was raised from 3,000 jobs to 50,000 on 2026-08-25. KV did not move
and cannot: deploy/api/page.py decompresses into a 20 MB ceiling, Cloudflare
refuses a value over 25 MB, and a Vercel function response stops at 4.5 MB. At
2.8 KB a record, a full 50,000-job pool is about 134 MB of state, so publishing
it whole would answer "The board state could not be read" on every request.

found_lane already embedded at most EMBED_CAP of them in the page, so the whole
pool was being shipped to be discarded at render. publishable_jobs runs that
same selection at publish time and sends the true total and census beside it.

Run from the repo root: python3 -m unittest discover -s tests
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dashboard  # noqa: E402
import found_lane  # noqa: E402
import publish  # noqa: E402


def job(job_id, company, state="new", country="US", score=50):
    return {
        "id": job_id, "company": company, "title": f"Role {job_id}",
        "state": state, "score": {"total": score, "raw": score, "gates": []},
        "url": f"https://example.com/{job_id}",
        "location": {"country": country}, "postedAt": "2026-08-01T00:00:00Z",
        "firstSeenAt": "2026-08-01T00:00:00Z", "lastSeenAt": "2026-08-01T00:00:00Z",
    }


def pool(new_count, acted=0):
    jobs = {}
    for i in range(new_count):
        jobs[f"n{i:06d}"] = job(f"n{i:06d}", f"Company {i % 400}")
    for i in range(acted):
        jobs[f"a{i:06d}"] = job(f"a{i:06d}", f"Acted {i}", state="watched")
    return jobs


def state_with(jobs):
    return {"profile": {"firstName": "A"}, "sessions": [], "history": [],
            "roles": {}, "meta": {}, "jobs": jobs, "searchConfig": None,
            "providers": {}}


class PublishedPoolIsBoundedTest(unittest.TestCase):
    def test_a_pool_far_over_the_embed_cap_publishes_the_cap(self):
        jobs = pool(50000)
        published, total, census = dashboard.publishable_jobs(jobs, None)
        self.assertEqual(total, 50000, "the true pool size still has to travel")
        self.assertLessEqual(len(published), found_lane.EMBED_CAP)
        self.assertTrue(census)

    def test_records_the_user_acted_on_are_never_dropped(self):
        """They are where Watching and Submitted find their company marks, and
        the cap does not get to eat a decision."""
        jobs = pool(50000, acted=120)
        published, _, _ = dashboard.publishable_jobs(jobs, None)
        kept = [j for j in published.values() if j.get("state") == "watched"]
        self.assertEqual(len(kept), 120)

    def test_the_published_pool_keeps_the_order_of_the_pool_it_came_from(self):
        """build() reads company marks by taking the first record it sees for
        each employer, so a reordered pool silently redraws logos."""
        jobs = pool(6000, acted=20)
        published, _, _ = dashboard.publishable_jobs(jobs, None)
        original = [k for k in jobs if k in published]
        self.assertEqual(list(published), original)

    def test_a_small_pool_publishes_whole_and_renders_identically(self):
        jobs = pool(50, acted=5)
        state = state_with(jobs)
        self.assertEqual(
            dashboard.build(state, "now"),
            dashboard.build(dashboard.publishable_state(state), "now"))

    def test_the_true_total_survives_the_round_trip(self):
        state = state_with(pool(50000))
        public = dashboard.publishable_state(state)
        self.assertEqual(public["meta"]["poolTotal"], 50000)
        # The renderer must quote the pool, not the slice it was handed.
        self.assertIn("50,000", dashboard.build(public, "now").replace("&#8239;", ","))


class PublishRefusesAnOversizedStateTest(unittest.TestCase):
    def test_a_state_over_the_deployed_ceiling_is_refused_not_written(self):
        oversized = b"x" * (publish.MAX_STATE_BYTES + 1)
        with self.assertRaises(publish.PublishError) as caught:
            publish.guard_state_size(oversized)
        self.assertIn("could not be read", str(caught.exception).lower()
                      .replace("the board state is", "could not be read"))

    def test_a_state_inside_the_ceiling_passes_through_untouched(self):
        payload = json.dumps({"ok": True}).encode()
        self.assertIs(publish.guard_state_size(payload), payload)


if __name__ == "__main__":
    unittest.main()
