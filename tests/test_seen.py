"""Tests for scripts/seen.py and the duplicate guard on session-save.

A sourcing sweep finds the same job on the company's board, on an aggregator
that copied it, and on LinkedIn, and finds it all again next week. Every one of
those used to become another watch entry, and two watch entries for one job is
how a job gets applied to twice under the user's name.

What matters here is both halves: that a posting already on record is refused
however it arrives, and that two genuinely different roles at one company are
still two roles. A filter that is too eager silently stops the sourcing working.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("seen", ROOT / "scripts" / "seen.py")
seen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seen)


class NormalisationTests(unittest.TestCase):
    def test_the_same_link_survives_tracking_and_host_noise(self):
        canonical = "boards.greenhouse.io/acme/jobs/123"
        for variant in (
            "https://boards.greenhouse.io/acme/jobs/123",
            "http://www.boards.greenhouse.io/acme/jobs/123/",
            "https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_medium=x",
            "https://boards.greenhouse.io/acme/jobs/123#apply",
        ):
            self.assertEqual(seen.norm_url(variant), canonical, variant)

    def test_one_title_written_three_ways_is_one_title(self):
        base = seen.norm_role("Senior Product Manager - AI")
        self.assertEqual(base, seen.norm_role("Senior Product Manager, AI"))
        self.assertEqual(base, seen.norm_role("Senior Product Manager (AI)"))
        self.assertEqual(base, seen.norm_role("  senior   product manager   ai "))

    def test_seniority_is_not_normalised_away(self):
        # Both are commonly open at once and they are different jobs.
        self.assertNotEqual(seen.norm_role("Product Manager"),
                            seen.norm_role("Senior Product Manager"))

    def test_the_ats_job_id_is_recovered_from_the_link(self):
        self.assertEqual(seen.ats_key("https://boards.greenhouse.io/acme/jobs/4567"), "4567")
        self.assertEqual(
            seen.ats_key("https://acme.wd3.myworkdayjobs.com/Acme/job/Sr-PM_R2119"), "r2119")
        self.assertEqual(seen.ats_key("https://jobs.lever.co/acme/"), "")


class StoreFixture:
    """Setup and helpers only.

    Deliberately NOT a TestCase. Subclassing a TestCase to reuse its setUp
    re-runs every one of its test methods in the subclass: two subclasses here
    were quietly running the same eleven tests three times over, which inflates
    the suite count without adding a line of coverage.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-seen-")
        self.root = pathlib.Path(self.dir)
        (self.root / "sessions").mkdir(parents=True)

    def watch(self, aid, company, role, url, status="watch"):
        (self.root / "sessions" / f"{aid}.json").write_text(json.dumps({
            "applicationId": aid, "company": company, "role": role,
            "url": url, "status": status}))

    def submitted(self, aid, company, role, url):
        with open(self.root / "applications.jsonl", "a") as fh:
            fh.write(json.dumps({"event": "completed", "applicationId": aid,
                                 "company": company, "role": role, "url": url,
                                 "at": "2026-08-01T00:00:00Z", "eventId": aid}) + "\n")

    def check(self, **candidate):
        return seen.check(candidate, seen.known_records(str(self.root)))

    def save(self, aid, payload, extra=()):
        path = self.root / "candidate.json"
        path.write_text(json.dumps(payload))
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "store.py"),
             "--root", str(self.root), "session-save", "--id", aid,
             "--input", str(path)] + list(extra),
            capture_output=True, text=True)

class RetentionTests(StoreFixture, unittest.TestCase):
    """Dedupe must survive the role index being pruned.

    roles.json is one of three sources seen.py reads, and the retention work on
    the discovery side prunes records by age. Anything already applied to has to
    stay un-sourceable even if its index entry is gone, or a sweep re-adds a job
    that was already sent under the user's name.

    It holds because applications.jsonl carries the company, role and URL on a
    completed event, and history is append-only. This pins that, so a later
    retention change cannot quietly remove the last thing holding it up.
    """

    def test_an_applied_role_stays_known_after_its_index_entry_is_pruned(self):
        self.submitted("acme-pm", "Acme", "Product Manager",
                       "https://boards.greenhouse.io/acme/jobs/123")
        (self.root / "roles.json").write_text("{}")
        by_pair = self.check(company="Acme", role="Product Manager",
                             url="https://www.linkedin.com/jobs/view/9")
        self.assertTrue(by_pair["known"])
        self.assertIn("applied", by_pair["where"])

    def test_the_url_still_matches_after_pruning_when_the_wording_differs(self):
        # A second board words the title differently, so company+role misses and
        # the URL is what is left.
        self.submitted("acme-pm", "Acme", "Product Manager",
                       "https://boards.greenhouse.io/acme/jobs/123")
        (self.root / "roles.json").write_text("{}")
        found = self.check(company="Acme Corp", role="PM, Growth",
                           url="https://boards.greenhouse.io/acme/jobs/123?src=x")
        self.assertTrue(found["known"])
        self.assertIn("URL", found["reason"])

    def test_a_role_only_ever_seen_becomes_sourceable_again_once_pruned(self):
        # Deliberate, and the opposite of the two above. Something merely seen,
        # never applied to and with no session, is a fresh candidate again. If
        # it is too old the date gate refuses it; that is not this gate's job.
        (self.root / "roles.json").write_text(json.dumps({"ghost-pm": {
            "applicationId": "ghost-pm", "company": "Ghost", "role": "Product Manager",
            "url": "https://x/1"}}))
        self.assertTrue(self.check(company="Ghost", role="Product Manager")["known"])
        (self.root / "roles.json").write_text("{}")
        self.assertFalse(self.check(company="Ghost", role="Product Manager")["known"])


class QueryKeyedUrlTests(StoreFixture, unittest.TestCase):
    """A careers page shared by every posting must not count as one posting.

    stripe.com/jobs/search?gh_jid=<id> strips to stripe.com/jobs/search, which
    258 different Stripe postings share (measured against the real pool on
    2026-08-28, where 4,202 of 14,569 jobs carry their identity in the query).
    One recorded Stripe role therefore declared every other Stripe job
    "already known", and the Apply that hit it wedged the watcher for 88
    consecutive ticks. Identity for these lives in the ATS key.
    """

    def test_a_second_posting_behind_the_same_careers_page_is_new(self):
        self.watch("stripe-hw", "Stripe", "Hardware Program Manager",
                   "https://stripe.com/jobs/search?gh_jid=8160754")
        verdict = self.check(company="Stripe", role="Terminal Logistics Lead",
                             url="https://stripe.com/jobs/search?gh_jid=9999999")
        self.assertFalse(verdict["known"], verdict)

    def test_the_same_query_keyed_posting_is_still_known(self):
        self.watch("stripe-hw", "Stripe", "Hardware Program Manager",
                   "https://stripe.com/jobs/search?gh_jid=8160754")
        verdict = self.check(company="Stripe", role="HW PM (Terminal)",
                             url="https://stripe.com/jobs/search?gh_jid=8160754")
        self.assertTrue(verdict["known"], verdict)
        self.assertIn("ATS", verdict["reason"])

    def test_a_stored_bare_careers_url_from_before_the_fix_does_not_match(self):
        # Records written before 2026-08-28 can hold the stripped page in the
        # urls set; a keyed candidate must not be matched against it.
        self.watch("elastic-old", "Elastic", "Search PM",
                   "https://jobs.elastic.co/jobs")
        verdict = self.check(company="Elastic", role="Observability PM",
                             url="https://jobs.elastic.co/jobs?gh_jid=1234567")
        self.assertFalse(verdict["known"], verdict)

    def test_a_path_borne_id_still_matches_by_url(self):
        # The strip loses nothing here, so URL matching keeps carrying the
        # case where both company and role are worded differently.
        self.watch("acme-pm", "Acme", "Product Manager",
                   "https://boards.greenhouse.io/acme/jobs/123")
        verdict = self.check(company="Acme Corp", role="PM, Growth",
                             url="https://boards.greenhouse.io/acme/jobs/123?src=x")
        self.assertTrue(verdict["known"], verdict)


class StoreBackedTests(StoreFixture, unittest.TestCase):
    def test_the_same_job_from_another_source_is_known(self):
        # No shared URL at all: the LinkedIn copy and the company's own listing.
        self.watch("acme-pm", "Acme", "Product Manager",
                   "https://boards.greenhouse.io/acme/jobs/123")
        v = self.check(company="Acme", role="Product Manager",
                       url="https://www.linkedin.com/jobs/view/999/?trk=z")
        self.assertTrue(v["known"])
        self.assertIn("another source", v["reason"])

    def test_an_aggregator_copy_of_the_same_link_is_known(self):
        self.watch("acme-pm", "Acme", "Product Manager",
                   "https://boards.greenhouse.io/acme/jobs/123")
        v = self.check(company="Acme", role="Product Manager (Remote)",
                       url="https://boards.greenhouse.io/acme/jobs/123?gh_src=x")
        self.assertTrue(v["known"])

    def test_an_already_submitted_role_is_known(self):
        self.submitted("acme-pm", "Acme", "Product Manager", "https://x/1")
        self.assertTrue(self.check(company="Acme", role="Product Manager")["known"])

    def test_a_role_the_user_dropped_stays_dropped(self):
        # Re-adding something the user cancelled puts it back in front of them.
        self.watch("acme-pm", "Acme", "Product Manager", "https://x/1", status="dropped")
        v = self.check(company="Acme", role="Product Manager", url="https://other/2")
        self.assertTrue(v["known"])
        self.assertIn("dropped", v["where"])

    def test_a_different_role_at_the_same_company_is_new(self):
        self.watch("acme-pm", "Acme", "Product Manager", "https://x/1")
        self.assertFalse(self.check(company="Acme", role="Data Analyst")["known"])

    def test_the_same_role_at_a_different_company_is_new(self):
        self.watch("acme-pm", "Acme", "Product Manager", "https://x/1")
        self.assertFalse(self.check(company="Zephyr", role="Product Manager")["known"])

    def test_an_unreadable_history_refuses_to_answer(self):
        # Answering "not known" off a truncated file would re-add everything.
        self.submitted("acme-pm", "Acme", "Product Manager", "https://x/1")
        with open(self.root / "applications.jsonl", "a") as fh:
            fh.write("{truncated\n")
        with self.assertRaises(SystemExit):
            seen.known_records(str(self.root))

    def test_the_store_refuses_a_new_watch_entry_for_a_known_posting(self):
        self.watch("acme-pm", "Acme", "Product Manager",
                   "https://boards.greenhouse.io/acme/jobs/123")
        r = self.save("acme-pm-linkedin", {
            "company": "Acme", "role": "Product Manager",
            "url": "https://www.linkedin.com/jobs/view/999", "status": "watch"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already in the system", r.stdout + r.stderr)
        self.assertFalse((self.root / "sessions" / "acme-pm-linkedin.json").exists())

    def test_the_store_still_accepts_a_genuinely_new_posting(self):
        self.watch("acme-pm", "Acme", "Product Manager", "https://x/1")
        r = self.save("zephyr-pm", {"company": "Zephyr", "role": "Product Manager",
                                    "url": "https://y/2", "status": "watch"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.root / "sessions" / "zephyr-pm.json").exists())

    def test_updating_an_existing_session_is_not_blocked(self):
        # phase, park and drop all rewrite a session that already exists.
        self.watch("acme-pm", "Acme", "Product Manager", "https://x/1")
        r = self.save("acme-pm", {"applicationId": "acme-pm", "company": "Acme",
                                  "role": "Product Manager", "url": "https://x/1",
                                  "status": "review"})
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = json.loads((self.root / "sessions" / "acme-pm.json").read_text())
        self.assertEqual(saved["status"], "review")

    def test_a_deliberate_restore_can_override(self):
        self.watch("acme-pm", "Acme", "Product Manager", "https://x/1", status="dropped")
        r = self.save("acme-pm-restored",
                      {"company": "Acme", "role": "Product Manager",
                       "url": "https://x/1", "status": "watch"},
                      extra=["--allow-known"])
        self.assertEqual(r.returncode, 0, r.stderr)


class FilterTests(StoreFixture, unittest.TestCase):
    def filter(self, candidates):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "seen.py"), "--filter",
             "--root", str(self.root), "--input", "-"],
            input=json.dumps(candidates), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_one_sweep_finding_a_job_on_three_boards_keeps_it_once(self):
        out = self.filter([
            {"company": "Zephyr", "role": "Product Manager",
             "url": "https://boards.greenhouse.io/zephyr/jobs/777"},
            {"company": "Zephyr", "role": "Product Manager",
             "url": "https://www.linkedin.com/jobs/view/1/?trk=a"},
            {"company": "Zephyr", "role": "Product Manager",
             "url": "https://boards.greenhouse.io/zephyr/jobs/777?utm_source=x"},
        ])
        self.assertEqual(out["counts"], {"in": 3, "new": 1, "alreadyKnown": 2})

    def test_it_keeps_the_genuinely_new_and_drops_the_known(self):
        self.watch("acme-pm", "Acme", "Product Manager", "https://x/1")
        out = self.filter([
            {"company": "Acme", "role": "Product Manager", "url": "https://other/2"},
            {"company": "Zephyr", "role": "Product Manager", "url": "https://y/3"},
            {"company": "Acme", "role": "Senior Product Manager", "url": "https://z/4"},
        ])
        self.assertEqual(out["counts"]["new"], 2)
        kept = {c["company"] + "/" + c["role"] for c in out["new"]}
        self.assertEqual(kept, {"Zephyr/Product Manager", "Acme/Senior Product Manager"})

    def test_an_empty_sweep_is_not_an_error(self):
        self.assertEqual(self.filter([])["counts"],
                         {"in": 0, "new": 0, "alreadyKnown": 0})
