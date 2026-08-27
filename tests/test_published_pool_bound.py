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
import state_slim  # noqa: E402
import publish  # noqa: E402


# What a record in the real pool actually weighs, and why the old fixture could
# not see the fault. Measured 2026-08-27 over 4,366 live records: 4,961 bytes
# each whole, 3,050 slimmed. The fixture below was 264 bytes, eighteen times
# light, so fifty thousand of them serialized to less than a megabyte and the
# size guard was never anywhere near being exercised.
REAL_RECORD_BYTES = 4961
SLIMMED_RECORD_BYTES = 3050


def job(job_id, company, state="new", country="US", score=50, realistic=True):
    """A record shaped and weighed like one from the real pool.

    ``realistic=False`` gives the old thin fixture, kept for the tests that are
    about selection rather than size.
    """
    record = {
        "id": job_id, "company": company, "title": f"Role {job_id}",
        "state": state, "score": {"total": score, "raw": score, "gates": []},
        "url": f"https://example.com/{job_id}",
        "canonicalUrl": f"https://boards.greenhouse.io/{company}/jobs/{job_id}",
        "location": {"country": country}, "postedAt": "2026-08-01T00:00:00Z",
        "firstSeenAt": "2026-08-01T00:00:00Z", "lastSeenAt": "2026-08-01T00:00:00Z",
    }
    if not realistic:
        return record
    # The three fields that are 79% of the real slice.
    record["sections"] = {
        "responsibilities": [f"Responsibility {n} " + "detail " * 30 for n in range(4)],
        "requirements": [f"Requirement {n} " + "detail " * 30 for n in range(4)],
    }
    record["descriptionExcerpt"] = f"About {company}. " + "posting body text " * 80
    record["score"] = {
        "total": score, "raw": score,
        "parts": {"title": 20, "seniority": 15, "location": 10, "recency": 5},
        "why": {"title": "the title matches " * 4, "seniority": "the level fits " * 4},
        "unknowns": [], "configVersion": 1,
        "gates": [{"label": "Years", "evidence": "the posting says five " * 8}],
    }
    record["sources"] = [{"url": f"https://board{n}.example/{job_id}",
                          "source": "greenhouse", "sourceClass": "employer",
                          "firstSeenAt": "2026-08-01T00:00:00Z"} for n in range(3)]
    record["extracted"] = {"yearsMin": 5, "yearsMax": 8, "yearsText": "5-8 years",
                           "degree": None, "employmentType": None, "roleCategory": None}
    record["tags"] = ["product", "remote", "senior"]
    record["normTitle"] = f"role {job_id}"
    record["descFingerprint"] = "simhash64:0123456789abcdef"
    return record


def pool(new_count, acted=0, realistic=True):
    jobs = {}
    for i in range(new_count):
        jobs[f"n{i:06d}"] = job(f"n{i:06d}", f"Company {i % 400}", realistic=realistic)
    for i in range(acted):
        jobs[f"a{i:06d}"] = job(f"a{i:06d}", f"Acted {i}", state="watched",
                                realistic=realistic)
    return jobs


def state_with(jobs):
    return {"profile": {"firstName": "A"}, "sessions": [], "history": [],
            "roles": {}, "meta": {}, "jobs": jobs, "searchConfig": None,
            "providers": {}}


class PublishedPoolIsBoundedTest(unittest.TestCase):
    def test_a_pool_far_over_the_embed_cap_publishes_the_cap(self):
        jobs = pool(50000, realistic=False)
        published, total, census = dashboard.publishable_jobs(jobs, None)
        self.assertEqual(total, 50000, "the true pool size still has to travel")
        self.assertLessEqual(len(published), found_lane.EMBED_CAP)
        self.assertTrue(census)

    def test_the_count_bound_does_not_hold_once_the_user_has_acted(self):
        """And that is correct behaviour, which is why count is the wrong bound.

        Records the user acted on ride along whatever their state, so the
        published count is the cap plus however many decisions exist. In
        production on 2026-08-27 that was 3,400 selected plus 966 acted, and
        the count assertion above would have been false all along had any test
        combined a large pool with decisions. Bytes are the thing to bound.
        """
        published, _, _ = dashboard.publishable_jobs(
            pool(50000, acted=1200, realistic=False), None)
        self.assertGreater(len(published), found_lane.EMBED_CAP)
        self.assertEqual(len(published), found_lane.EMBED_CAP + 1200)

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
    """The guard itself, on synthetic bytes. Necessary and, alone, not enough.

    These two passed throughout the 22 hours production could not publish. They
    prove the fence stands; they say nothing about which side of it the real
    slice is on. That is what StateFitsTheCeilingTest below is for.
    """

    def test_a_state_over_the_deployed_ceiling_is_refused_not_written(self):
        oversized = b"x" * (publish.MAX_STATE_BYTES + 1)
        with self.assertRaises(publish.PublishError) as caught:
            publish.guard_state_size(oversized)
        self.assertIn("refusing to publish", str(caught.exception))

    def test_a_state_inside_the_ceiling_passes_through_untouched(self):
        payload = json.dumps({"ok": True}).encode()
        self.assertIs(publish.guard_state_size(payload), payload)


class StateFitsTheCeilingTest(unittest.TestCase):
    """Measure what the guard measures: same slice, same serializer, same constant.

    The old size test bounded a record count against EMBED_CAP while the guard
    weighed bytes, over a fixture 18 times lighter than production. All three
    had to be wrong together for it to stay green while every publish refused,
    and all three were.
    """

    def published_bytes(self, jobs):
        state = dashboard.publishable_state(state_with(jobs))
        return len(publish.serialize_state(state))

    def test_the_fixture_now_weighs_what_the_real_pool_weighs(self):
        """Calibration. If this drifts, every size assertion below means nothing.

        The old fixture was 264 bytes a record against production's 4,961, so
        fifty thousand of them serialized to under a megabyte and the guard was
        never approached. Both the whole and the slimmed weight are pinned,
        because the guard weighs the slimmed one and the fault was in the whole.
        """
        record = job("n000001", "Company 1")
        whole = len(publish.serialize_state(record))
        slimmed = len(publish.serialize_state(state_slim.slim_job(record)))
        self.assertGreater(whole, REAL_RECORD_BYTES * 0.75, f"too light: {whole}")
        self.assertLess(whole, REAL_RECORD_BYTES * 1.25, f"too heavy: {whole}")
        self.assertGreater(slimmed, SLIMMED_RECORD_BYTES * 0.75, f"too light: {slimmed}")
        self.assertLess(slimmed, SLIMMED_RECORD_BYTES * 1.25, f"too heavy: {slimmed}")

    def test_a_realistic_full_pool_publishes_inside_the_ceiling(self):
        # 50,000 records with 1,200 decisions, which is larger than production.
        self.assertLess(self.published_bytes(pool(50000, acted=1200)),
                        publish.MAX_STATE_BYTES)

    def test_the_slice_that_broke_production_would_now_be_caught(self):
        """The regression test proper: this fails if slimming is removed.

        4,366 realistic records is the exact shape that reached 21.03 MB and
        refused every publish for 22 hours. Slimmed it is comfortably under.
        """
        jobs = pool(3400, acted=966)
        self.assertEqual(len(jobs), 4366)
        self.assertLess(self.published_bytes(jobs), publish.MAX_STATE_BYTES)

    def test_it_really_does_fail_when_the_slice_is_over_the_ceiling(self):
        """A bound nothing can violate is not a bound. Prove this one bites.

        Two things had quietly taken the teeth out of it, and both are the same
        mistake: a number here that depends on a number somewhere else.

        The embed cap is pinned at 3,400 rather than read from `found_lane`,
        because this test is about the shape that actually broke production and
        that shape had 3,400 embedded records in it. When the cap moved to
        3,350 on 2026-08-26 to make room for the Profile page, the unslimmed
        slice fell by 230,700 bytes.

        And `acted` is 1,200 rather than 966, re-measured on 2026-08-26. On
        `origin/main` at e9efe5e, before this branch touched anything, the
        original 966 produced 20,140,313 bytes against a 20,971,520 ceiling and
        this test already failed: the section cap that PR #78 added to fix the
        22-hour outage also shrank the unslimmed baseline it is measured
        against, so the guard stopped being able to demonstrate the fault it
        exists for. 1,200 is the figure the realistic-pool test above already
        uses.

        Both failed loudly rather than silently passing, which is the only
        reason either was noticed.
        """
        original = dashboard.state_slim.slim_jobs
        original_cap = found_lane.EMBED_CAP
        dashboard.state_slim.slim_jobs = staticmethod(lambda jobs: jobs)
        found_lane.EMBED_CAP = 3400
        try:
            unslimmed = self.published_bytes(pool(3400, acted=1200))
        finally:
            dashboard.state_slim.slim_jobs = original
            found_lane.EMBED_CAP = original_cap
        self.assertGreater(unslimmed, publish.MAX_STATE_BYTES,
                           "the unslimmed slice must exceed the ceiling, or this "
                           "test cannot detect the fault it exists for")
        with self.assertRaises(publish.PublishError):
            publish.guard_state_size(b"x" * unslimmed)


if __name__ == "__main__":
    unittest.main()
