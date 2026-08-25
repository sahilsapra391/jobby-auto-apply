"""Tests for scripts/posting_body.py, the posting text a promoted job lacks.

A job promoted from the Found board carries no posting body, so every one of
them failed document generation with the fixed code `invalid_request`. This
module is what closes that gap, and these are the four things it has to get
right: read the full body when the ATS offers one, fall back to the stored
excerpt rather than returning nothing, never raise at the caller, and never
reach the network for a job the store does not hold.

Every test here injects its fetcher. Nothing in this file makes a request.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


posting_body = load("posting_body")
base = posting_body.base

FULL_BODY = "<p>Who we are</p><p>" + ("Build the thing. " * 400) + "</p>"
EXCERPT = "Who we are. Build the thi"


class FakeFetcher:
    """Stands in for base.Fetcher. Records what was asked for."""

    def __init__(self, payload=None, fail=False):
        self.payload = payload
        self.fail = fail
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        if self.fail:
            return None, base.Response(404, error="not found")
        return self.payload, base.Response(200)


class GreenhouseUrlTests(unittest.TestCase):
    def test_reads_the_token_and_job_id_from_the_current_board_host(self):
        self.assertEqual(
            posting_body._greenhouse_parts(
                "https://job-boards.greenhouse.io/twilio/jobs/8136763"),
            ("twilio", "8136763"))

    def test_a_query_string_does_not_become_part_of_the_job_id(self):
        # The older boards.greenhouse.io links in the pool arrive carrying
        # ?gh_jid=. Splitting the raw URL on "/" welds that to the job id and
        # the API answers 404, which is a fetch failure that looks like a dead
        # posting. Two of the six parked applications had this URL shape.
        self.assertEqual(
            posting_body._greenhouse_parts(
                "https://boards.greenhouse.io/robinhood/jobs/8131181"
                "?gh_jid=8131181&t=gh_src%3D"),
            ("robinhood", "8131181"))

    def test_a_non_greenhouse_url_has_no_parts(self):
        self.assertIsNone(posting_body._greenhouse_parts(
            "https://jobs.lever.co/acme/1234"))

    def test_a_board_listing_page_is_not_a_job(self):
        self.assertIsNone(posting_body._greenhouse_parts(
            "https://job-boards.greenhouse.io/twilio"))

    def test_a_non_numeric_job_id_is_refused(self):
        self.assertIsNone(posting_body._greenhouse_parts(
            "https://job-boards.greenhouse.io/twilio/jobs/apply"))


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jobby-posting-")
        self.root = pathlib.Path(self.dir)

    def write_jobs(self, jobs):
        (self.root / "jobs.json").write_text(json.dumps(jobs))

    def job(self, **over):
        entry = {
            "id": "abc123",
            "company": "Twilio",
            "canonicalSource": "greenhouse",
            "canonicalUrl": "https://job-boards.greenhouse.io/twilio/jobs/8136763",
            "descriptionExcerpt": EXCERPT,
        }
        entry.update(over)
        return entry

    def test_the_full_body_beats_the_stored_excerpt(self):
        self.write_jobs({"abc123": self.job()})
        fetcher = FakeFetcher(payload={"content": FULL_BODY})
        text = posting_body.resolve("abc123", root=self.root, fetcher=fetcher)
        self.assertGreater(len(text), len(EXCERPT))
        self.assertIn("Build the thing.", text)
        self.assertNotIn("<p>", text)

    def test_a_failed_fetch_falls_back_to_the_excerpt(self):
        # Truncated posting text beats no posting text. The alternative is an
        # application that parks and waits for a human.
        self.write_jobs({"abc123": self.job()})
        text = posting_body.resolve(
            "abc123", root=self.root, fetcher=FakeFetcher(fail=True))
        self.assertEqual(text, EXCERPT)

    def test_an_empty_body_falls_back_to_the_excerpt(self):
        self.write_jobs({"abc123": self.job()})
        text = posting_body.resolve(
            "abc123", root=self.root, fetcher=FakeFetcher(payload={"content": ""}))
        self.assertEqual(text, EXCERPT)

    def test_an_ats_with_no_fetcher_uses_the_excerpt_without_a_request(self):
        self.write_jobs({"abc123": self.job(
            canonicalSource="lever",
            canonicalUrl="https://jobs.lever.co/acme/1234")})
        fetcher = FakeFetcher(payload={"content": FULL_BODY})
        text = posting_body.resolve("abc123", root=self.root, fetcher=fetcher)
        self.assertEqual(text, EXCERPT)
        self.assertEqual(fetcher.urls, [])

    def test_a_job_the_store_does_not_hold_resolves_to_nothing(self):
        self.write_jobs({})
        fetcher = FakeFetcher(payload={"content": FULL_BODY})
        self.assertEqual(
            posting_body.resolve("abc123", root=self.root, fetcher=fetcher), "")
        self.assertEqual(fetcher.urls, [])

    def test_a_missing_store_resolves_to_nothing_rather_than_raising(self):
        self.assertEqual(
            posting_body.resolve("abc123", root=self.root / "gone"), "")

    def test_a_job_with_neither_body_nor_excerpt_resolves_to_nothing(self):
        self.write_jobs({"abc123": self.job(descriptionExcerpt="")})
        self.assertEqual(
            posting_body.resolve(
                "abc123", root=self.root, fetcher=FakeFetcher(fail=True)), "")

    def test_allow_network_false_never_calls_the_fetcher(self):
        self.write_jobs({"abc123": self.job()})
        fetcher = FakeFetcher(payload={"content": FULL_BODY})
        text = posting_body.resolve(
            "abc123", root=self.root, fetcher=fetcher, allow_network=False)
        self.assertEqual(text, EXCERPT)
        self.assertEqual(fetcher.urls, [])


if __name__ == "__main__":
    unittest.main()
