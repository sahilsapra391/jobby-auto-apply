"""One posting body, fetched when the application does not carry one.

The pool stores a 1,500-character excerpt, and promotion from Found copies only
what the job record holds, so an application that arrived by pressing the eye
reached the document worker with nothing to tailor against. Every build failed
with `invalid_request`, a code that named nothing and appeared six times in a
row before anyone could tell what it meant.
"""

import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "sources"))

import base  # noqa: E402
import job_description  # noqa: E402


BODY = "About the role. " * 60


class FakeFetcher:
    """Answers by URL. Anything unlisted is a 404, which is the real default."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def _answer(self, url):
        self.asked.append(url)
        value = self.answers.get(url)
        if value is None:
            return base.Response(404)
        if isinstance(value, base.Response):
            return value
        return base.Response(200, json.dumps(value).encode()
                             if not isinstance(value, bytes) else value)

    def get(self, url, conditional=True, headers=None):
        return self._answer(url)

    def get_json(self, url, conditional=True, headers=None):
        response = self._answer(url)
        if not response.ok:
            return None, response
        try:
            return response.json(), response
        except json.JSONDecodeError:
            return None, response


class RouteTests(unittest.TestCase):
    """Each board is asked through its own API, because the page is not readable.

    Workday is the clearest case: its posting page is an empty single-page app
    shell, so fetching the URL a person clicks returns navigation and no job.
    """

    CASES = {
        "https://job-boards.greenhouse.io/twilio/jobs/8136763":
            "https://boards-api.greenhouse.io/v1/boards/twilio/jobs/8136763",
        "https://jobs.lever.co/dnb/f48f3c65-f620-4b24-a8fc-0af53ffb4afe":
            "https://api.lever.co/v0/postings/dnb/f48f3c65-f620-4b24-a8fc-0af53ffb4afe",
        "https://jobs.smartrecruiters.com/IFS1/744000145277942":
            "https://api.smartrecruiters.com/v1/companies/IFS1/postings/744000145277942",
        "https://criteo.wd3.myworkdayjobs.com/Criteo_Career_Site/job/Paris/Tax-Apprentice-_r20698-1":
            "https://criteo.wd3.myworkdayjobs.com/wday/cxs/criteo/Criteo_Career_Site"
            "/job/Paris/Tax-Apprentice-_r20698-1",
    }

    def test_oracle_is_read_through_its_rest_resource(self):
        """The page route reports a live Oracle posting as delisted.

        Oracle Recruiting Cloud answers a direct fetch of the posting with a
        redirect to its own index, which `_from_page` correctly reads as "no
        longer listed". 1,466 of the board's postings are on it, and every one
        of them was unreadable until the REST resource was wired up.
        """
        posting = ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience"
                   "/en/sites/CX_1/job/341846")
        api = ('https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/'
               'recruitingCEJobRequisitionDetails?onlyData=true&expand=all'
               '&finder=ById;Id="341846",siteNumber=CX_1')
        filler = "<p>" + ("Own the roadmap and the outcomes it drives. " * 30) + "</p>"
        fetcher = FakeFetcher({api: {"items": [{
            "ExternalDescriptionStr":
                "<p>Job Responsibilities</p><p>Own the roadmap.</p>" + filler,
            "ExternalQualificationsStr":
                "<p>Required qualifications</p><p>5 years.</p>",
        }]}})
        text = job_description.fetch(posting, fetcher=fetcher)
        self.assertEqual(fetcher.asked, [api])
        self.assertIn("Own the roadmap.", text)
        # Both fields carry part of the body, and either can hold the
        # requirements, so neither may be dropped.
        self.assertIn("5 years.", text)

    def test_each_board_is_read_through_its_own_api(self):
        for posting, api in self.CASES.items():
            with self.subTest(posting=posting):
                fetcher = FakeFetcher({api: self._payload(api)})
                text = job_description.fetch(posting, fetcher=fetcher)
                self.assertIn("About the role", text)
                self.assertEqual(fetcher.asked, [api])

    def test_both_greenhouse_url_shapes_reach_the_same_job(self):
        # The older boards.greenhouse.io links in the pool arrive carrying
        # ?gh_jid=. Splitting the raw URL on "/" welds that to the job id and
        # the API answers 404, which is a fetch failure that looks like a dead
        # posting. Some real parked applications had that shape.
        api = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/1000001"
        for posting in (
            "https://job-boards.greenhouse.io/acme/jobs/1000001",
            "https://job-boards.greenhouse.io/acme/jobs/1000001/",
            "https://boards.greenhouse.io/acme/jobs/1000001?gh_jid=1000001&t=x",
        ):
            with self.subTest(posting=posting):
                fetcher = FakeFetcher({api: {"content": f"<p>{BODY}</p>"}})
                self.assertIn("About the role",
                              job_description.fetch(posting, fetcher=fetcher))
                self.assertEqual(fetcher.asked, [api])

    def test_a_board_index_is_not_a_job_and_goes_straight_to_the_page(self):
        listing = "https://job-boards.greenhouse.io/twilio"
        fetcher = FakeFetcher({listing: base.Response(
            200, f"<html><body><p>{BODY}</p></body></html>".encode())})
        job_description.fetch(listing, fetcher=fetcher)
        # No board API call: there is no job id to ask about.
        self.assertEqual(fetcher.asked, [listing])

    def test_an_ashby_board_answer_is_narrowed_to_the_requested_posting(self):
        posting = "https://jobs.ashbyhq.com/langchain/7948d642-ed18-45cf-8d04-b0789039b422"
        api = ("https://api.ashbyhq.com/posting-api/job-board/langchain"
               "?includeCompensation=true&jobPostingId=7948d642-ed18-45cf-8d04-b0789039b422")
        fetcher = FakeFetcher({api: {"jobs": [
            {"id": "another-posting", "descriptionHtml": "<p>WRONG ROLE " * 60 + "</p>"},
            {"id": "7948d642-ed18-45cf-8d04-b0789039b422",
             "descriptionHtml": f"<p>{BODY}</p>"},
        ]}})
        text = job_description.fetch(posting, fetcher=fetcher)
        self.assertIn("About the role", text)
        self.assertNotIn("WRONG ROLE", text)

    @staticmethod
    def _payload(api):
        if "greenhouse" in api:
            return {"content": f"<p>{BODY}</p>"}
        if "lever" in api:
            return {"descriptionPlain": BODY, "lists": [], "additionalPlain": ""}
        if "smartrecruiters" in api:
            return {"jobAd": {"sections": {"jobDescription": {"text": BODY}}}}
        return {"jobPostingInfo": {"jobDescription": f"<p>{BODY}</p>"}}


class FallbackTests(unittest.TestCase):
    POSTING = "https://job-boards.greenhouse.io/acme/jobs/12345"
    API = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/12345"

    def test_a_board_api_that_404s_is_not_the_end_of_the_attempt(self):
        # A closed posting 404s on the API while its page still renders, and a
        # tenant can move. Failing before the page is tried loses applications
        # that were perfectly answerable.
        fetcher = FakeFetcher({self.POSTING: base.Response(
            200, f"<html><body><p>{BODY}</p></body></html>".encode())})
        text = job_description.fetch(self.POSTING, fetcher=fetcher)
        self.assertIn("About the role", text)
        self.assertEqual(fetcher.asked, [self.API, self.POSTING])

    def test_a_withdrawn_posting_says_so_rather_than_naming_a_status_code(self):
        # Boards answer a withdrawn posting by redirecting to their own index.
        # "HTTP 302" sends the user looking for a network fault that is not there.
        fetcher = FakeFetcher({self.POSTING: base.Response(302)})
        with self.assertRaises(job_description.Unfetchable) as caught:
            job_description.fetch(self.POSTING, fetcher=fetcher)
        self.assertEqual(str(caught.exception),
                         "the posting is no longer listed on its job board")

    def test_a_stub_body_is_refused_with_its_measured_length(self):
        fetcher = FakeFetcher({self.API: {"content": "<p>Apply now.</p>"},
                               self.POSTING: base.Response(200, b"<html></html>")})
        with self.assertRaises(job_description.Unfetchable) as caught:
            job_description.fetch(self.POSTING, fetcher=fetcher)
        self.assertIn("too short to tailor against", str(caught.exception))

    def test_only_an_http_posting_address_is_accepted(self):
        for url in ("", None, "file:///etc/passwd", "javascript:alert(1)", "acme.test/j/1"):
            with self.subTest(url=url):
                with self.assertRaises(job_description.Unfetchable):
                    job_description.fetch(url, fetcher=FakeFetcher({}))


class BriefTests(unittest.TestCase):
    """The brief is where the posting is resolved: every path needing one goes through it."""

    def setUp(self):
        import tempfile  # noqa: PLC0415

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = pathlib.Path(self.temp.name) / "Acme"

    def test_a_promoted_application_gets_its_posting_and_keeps_it(self):
        import brief  # noqa: PLC0415

        with mock.patch.object(brief.posting_body, "resolve_detail",
                               return_value=(BODY, "posting", "")) as resolve:
            first = self._build(brief, "acme-pm")
            second = self._build(brief, "acme-pm")
        # Resolved once. The second brief reads the file the first one wrote, so
        # a retry costs no request and cannot lose the posting to a board that
        # has since withdrawn it.
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(first["jobDescriptionSource"], "posting")
        self.assertEqual(second["jobDescriptionSource"], "cached")

    def test_an_excerpt_is_recorded_rather_than_passing_as_the_posting(self):
        import brief  # noqa: PLC0415

        with mock.patch.object(
            brief.posting_body, "resolve_detail",
            return_value=("truncated", "excerpt", "the posting body came back empty"),
        ):
            summary = self._build(brief, "thin-pm")
        self.assertEqual(summary["jobDescriptionSource"], "excerpt")
        self.assertIsNotNone(summary["jdPath"])

    def test_an_unresolvable_posting_leaves_the_reason_on_the_summary(self):
        import brief  # noqa: PLC0415

        with mock.patch.object(
            brief.posting_body, "resolve_detail",
            return_value=("", "", "the posting is no longer listed on its job board"),
        ):
            summary = self._build(brief, "gone-pm")
        self.assertIsNone(summary["jdPath"])
        self.assertEqual(summary["jobDescriptionSource"], "")
        self.assertEqual(summary["jobDescriptionError"],
                         "the posting is no longer listed on its job board")

    def _build(self, brief, aid):
        entry = {"applicationId": aid, "company": "Acme", "role": "PM",
                 "url": "https://job-boards.greenhouse.io/acme/jobs/1", "region": "US"}

        def fake_run_json(argv, default=None):
            return entry if "session-load" in argv else {}

        with mock.patch.object(brief, "run_json", fake_run_json), \
             mock.patch.object(brief, "company_folder", return_value=self.folder), \
             mock.patch.object(brief, "pick_base", return_value={"path": "", "identity": {}}), \
             mock.patch.object(brief, "find_documents",
                               return_value={"resume": None, "coverLetter": None}), \
             mock.patch.object(brief, "story_facts", return_value=""), \
             mock.patch.object(brief, "toolchain", return_value={}), \
             mock.patch.object(brief.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout="{}", stderr="")), \
             mock.patch.object(brief.document_contract, "output_names", return_value={}):
            return brief.build(aid)
