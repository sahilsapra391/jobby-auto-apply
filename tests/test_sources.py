"""Tests for the Tier-1 connectors.

The property every one of them has to hold is that **failure is never quiet**.
An empty list is a legitimate answer — a board with nothing open returns one
every hour — so "returned nothing" and "could not be asked" must be different
values. That is §13 item 1 in a new place: a crashed check and an empty queue
both looked like an empty queue, and it ran for hours.

Fixtures for the ten channels reachable without a key are recorded from real
responses on 2026-08-19. The five that need a key carry a `__fixture_note`
saying they were written from the published contract instead, because no key
is installed on this machine and pretending otherwise at review would be worse
than saying so.

No test here touches the network: every one runs against a fake opener.

Run from the repo root: python3 -m pytest tests/ -q
"""

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import identity  # noqa: E402
import sources_registry  # noqa: E402
import sources  # noqa: E402
from sources import adzuna, ashby, base, boards, careerjet, feeds, greenhouse  # noqa: E402
from sources import enterprise_boards, jooble, lever, reed, themuse, usajobs, workday  # noqa: E402


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.headers = headers or {}

    def read(self, limit=None):
        return self._body[:limit] if limit else self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Serves canned responses by URL substring and records what was asked."""

    def __init__(self, routes, default=(404, "not found")):
        self.routes = routes
        self.default = default
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append(request)
        for needle, reply in self.routes.items():
            if needle in request.full_url:
                if callable(reply):
                    reply = reply(request, len([c for c in self.calls
                                                if needle in c.full_url]))
                status, body = reply[0], reply[1]
                headers = reply[2] if len(reply) > 2 else {}
                return FakeResponse(status, body, headers)
        return FakeResponse(*self.default)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def tick(self):
        return self.now


def fetcher(routes, root=None, default=(404, "not found")):
    clock = Clock()
    opener = FakeOpener(routes, default)
    f = base.Fetcher(root=root, opener=opener, sleep=clock.sleep, clock=clock.tick)
    f.fake_clock = clock
    f.fake_opener = opener
    return f


COMPANIES = {
    "vercel": {"name": "Vercel", "ats": "greenhouse", "token": "vercel", "region": "US"},
    "deputy": {"name": "Deputy", "ats": "lever", "token": "deputy", "region": "AU"},
    "zapier": {"name": "Zapier", "ats": "ashby", "token": "zapier", "region": "US"},
}
NO_SETTINGS = {}


class EnvelopeTests(unittest.TestCase):
    """Every connector answers in the same shape, whatever happened."""

    def test_every_registered_connector_is_callable(self):
        self.assertEqual(len(sources.ALL), 26)
        for name, fn in sources.ALL.items():
            self.assertTrue(callable(fn), name)

    def test_every_connected_catalog_entry_has_runtime_code(self):
        connected = {entry["id"] for entry in sources_registry.load()["sources"]
                     if entry["status"] == "connected"}
        self.assertEqual(connected, set(sources.ALL))

    def test_the_three_envelopes_carry_the_same_keys(self):
        shapes = [base.ok("x", []), base.skipped("x", "why"), base.failed("x", "why")]
        for shape in shapes:
            self.assertEqual(sorted(shape),
                             ["errors", "postings", "reason", "requests", "source", "status"])

    def test_a_skip_is_never_an_empty_success(self):
        # This is the whole rule. If a skipped channel returned status "ok" with
        # no postings, a missing key would be indistinguishable from a quiet day.
        self.assertEqual(base.skipped("adzuna", "no key")["status"], "skipped")
        self.assertNotEqual(base.skipped("adzuna", "no key")["status"], "ok")


class GreenhouseTests(unittest.TestCase):
    def setUp(self):
        self.board = fixture("greenhouse_board.json")

    def test_a_board_normalises_into_job_records(self):
        f = fetcher({"boards-api.greenhouse.io": (200, self.board)})
        result = greenhouse.fetch(f, companies=COMPANIES)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["postings"]), 2)
        job = result["postings"][0]
        self.assertEqual(job["company"], "Vercel")
        self.assertTrue(job["title"])
        self.assertTrue(job["canonicalUrl"].startswith("https://"))
        self.assertEqual(job["sources"][0]["source"], "greenhouse")
        self.assertEqual(job["sources"][0]["sourceClass"], "ats")
        self.assertTrue(job["postedAt"])

    def test_the_posted_date_is_when_it_was_published_not_last_edited(self):
        # updated_at moves whenever anyone fixes a typo. Using it would make
        # every edited posting look brand new on a board sorted newest first.
        raw = json.loads(self.board)["jobs"][0]
        job = greenhouse.parse_job(raw, "Vercel", "vercel")
        self.assertEqual(job["postedAt"], base.iso_stamp(raw["first_published"]))

    def test_only_registry_companies_on_this_platform_are_asked(self):
        f = fetcher({"boards-api.greenhouse.io": (200, self.board)})
        greenhouse.fetch(f, companies=COMPANIES)
        self.assertEqual(len(f.fake_opener.calls), 1)
        self.assertIn("/vercel/", f.fake_opener.calls[0].full_url)

    def test_an_empty_registry_is_a_skip_not_a_success(self):
        f = fetcher({})
        result = greenhouse.fetch(f, companies={})
        self.assertEqual(result["status"], "skipped")
        self.assertIn("registry", result["reason"])

    def test_a_response_that_hits_the_byte_cap_says_so(self):
        # Found live: OpenAI's Ashby board is 12.1 MB and the cap was 12 MB, so
        # it arrived cut mid-document and surfaced as a JSON parse error on an
        # HTTP 200. One real company was silently missing from every sweep, and
        # the error line named neither the cause nor the fix.
        original = base.MAX_RESPONSE_BYTES
        base.MAX_RESPONSE_BYTES = 64
        try:
            f = fetcher({"example.test": (200, "x" * 200)})
            response = f.get("https://example.test/a")
            self.assertIn("truncated", response.error)
            self.assertIn("cap", response.error)
        finally:
            base.MAX_RESPONSE_BYTES = original

    def test_the_cap_clears_the_largest_board_we_have_seen(self):
        # 12.1 MB, and boards only grow.
        self.assertGreaterEqual(base.MAX_RESPONSE_BYTES, 24_000_000)

    def test_a_200_with_an_unparseable_body_does_not_read_as_success(self):
        # Seen live: one Ashby board answers 200 with something that is not its
        # usual JSON, and the error line read "HTTP 200" — a success code
        # sitting in the error list, which is the sort of thing a person scans
        # past.
        f = fetcher({"api.ashbyhq.com": (200, "<html>maintenance</html>")})
        result = ashby.fetch(f, companies={"zapier": COMPANIES["zapier"]})
        self.assertEqual(result["status"], "error")
        self.assertIn("not JSON", result["errors"][0])

    def test_a_dead_token_is_reported_rather_than_swallowed(self):
        # A 404 means the token is wrong, which is a registry problem that will
        # never fix itself if nobody is told.
        f = fetcher({"boards-api.greenhouse.io": (404, '{"error":"Job not found"}')})
        result = greenhouse.fetch(f, companies={"vercel": COMPANIES["vercel"]})
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["errors"])
        self.assertIn("404", result["errors"][0])

    def test_one_dead_board_does_not_lose_the_others(self):
        def route(request, n):
            return (404, "nope") if "/deputy/" in request.full_url else (200, self.board)
        f = fetcher({"boards-api.greenhouse.io": route})
        companies = {**COMPANIES, "deputy": {"name": "Deputy", "ats": "greenhouse",
                                             "token": "deputy"}}
        result = greenhouse.fetch(f, companies=companies)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["postings"])
        self.assertTrue(result["errors"], "the dead board still has to be reported")

    def test_an_unchanged_board_costs_no_postings_and_no_error(self):
        f = fetcher({"boards-api.greenhouse.io": (304, "")})
        result = greenhouse.fetch(f, companies={"vercel": COMPANIES["vercel"]})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["postings"], [])
        self.assertEqual(result["errors"], [])

    def test_a_body_is_fetched_separately(self):
        # The list is 59 KB for 83 jobs; the same list with content=true is
        # 845 KB. Bodies are only worth fetching for jobs that survive the gate.
        f = fetcher({"boards-api.greenhouse.io": (200, fixture("greenhouse_job.json"))})
        body, error = greenhouse.fetch_body(f, None, "vercel", "6136160004")
        self.assertEqual(error, "")
        self.assertGreater(len(body), 500)

    def test_a_failed_body_fetch_says_so_rather_than_returning_empty(self):
        f = fetcher({"boards-api.greenhouse.io": (500, "boom")})
        body, error = greenhouse.fetch_body(f, None, "vercel", "1")
        self.assertEqual(body, "")
        self.assertIn("500", error)


class LeverTests(unittest.TestCase):
    def test_a_board_normalises_with_its_own_workplace_and_country(self):
        # Lever states both, so neither has to be guessed from a location string.
        f = fetcher({"api.lever.co": (200, fixture("lever_board.json"))})
        result = lever.fetch(f, companies=COMPANIES)
        self.assertEqual(result["status"], "ok")
        job = result["postings"][0]
        self.assertIn(job["workplace"], ("remote", "hybrid", "onsite", None))
        self.assertTrue(job["descriptionExcerpt"])
        self.assertTrue(job["descFingerprint"])

    def test_lever_workplace_words_map_onto_the_schema(self):
        for raw, expected in (("remote", "remote"), ("hybrid", "hybrid"),
                              ("on-site", "onsite"), ("unspecified", None)):
            self.assertEqual(lever.WORKPLACE.get(raw), expected)

    def test_a_response_of_the_wrong_shape_is_an_error_not_a_crash(self):
        f = fetcher({"api.lever.co": (200, '{"ok": false}')})
        result = lever.fetch(f, companies={"deputy": COMPANIES["deputy"]})
        self.assertTrue(result["errors"])


class AshbyTests(unittest.TestCase):
    def test_a_board_normalises(self):
        f = fetcher({"api.ashbyhq.com": (200, fixture("ashby_board.json"))})
        result = ashby.fetch(f, companies=COMPANIES)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["postings"])
        self.assertEqual(result["postings"][0]["sources"][0]["source"], "ashby")

    def test_an_unlisted_posting_is_not_collected(self):
        # isListed false is the company choosing not to show it publicly.
        data = json.loads(fixture("ashby_board.json"))
        for job in data["jobs"]:
            job["isListed"] = False
        f = fetcher({"api.ashbyhq.com": (200, json.dumps(data))})
        result = ashby.fetch(f, companies={"zapier": COMPANIES["zapier"]})
        self.assertEqual(result["postings"], [])
        self.assertEqual(result["status"], "ok")

    def test_a_plain_text_404_does_not_reach_the_json_parser(self):
        f = fetcher({"api.ashbyhq.com": (404, "Not Found")})
        result = ashby.fetch(f, companies={"zapier": COMPANIES["zapier"]})
        self.assertEqual(result["status"], "error")


class FeedTests(unittest.TestCase):
    def test_remoteok_skips_its_own_legal_notice(self):
        # The feed's first element is a legal notice, not a job. Without this it
        # enters the pool as a posting with no title and no company.
        f = fetcher({"remoteok.com": (200, fixture("remoteok.json"))})
        result = feeds.remoteok(f)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["postings"])
        for job in result["postings"]:
            self.assertTrue(job["title"])
            self.assertNotIn("legal", (job["title"] or "").lower())

    def test_every_feed_posting_is_marked_remote(self):
        cases = [(feeds.remoteok, "remoteok.com", "remoteok.json"),
                 (feeds.remotive, "remotive.com", "remotive.json"),
                 (feeds.himalayas, "himalayas.app", "himalayas.json"),
                 (feeds.working_nomads, "workingnomads.com", "workingnomads.json")]
        for fn, host, name in cases:
            f = fetcher({host: (200, fixture(name))})
            result = fn(f)
            self.assertEqual(result["status"], "ok", name)
            self.assertTrue(result["postings"], name)
            for job in result["postings"]:
                self.assertEqual(job["workplace"], "remote", name)

    def test_the_rss_feed_splits_company_from_role(self):
        # We Work Remotely writes "Company: Role" and that title is the only
        # place the company name appears at all.
        f = fetcher({"weworkremotely.com": (200, fixture("weworkremotely.rss"))})
        result = feeds.weworkremotely(f)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["postings"])
        self.assertTrue(result["postings"][0]["company"])
        self.assertNotIn(":", result["postings"][0]["title"][:1])

    def test_malformed_rss_is_an_error_not_a_crash(self):
        f = fetcher({"weworkremotely.com": (200, "<rss><channel>truncated")})
        result = feeds.weworkremotely(f)
        self.assertEqual(result["status"], "error")
        self.assertIn("RSS", result["reason"])

    def test_a_feed_outage_is_reported(self):
        f = fetcher({"remotive.com": (500, "upstream error")})
        result = feeds.remotive(f)
        self.assertEqual(result["status"], "error")
        self.assertIn("500", result["reason"])

    def test_the_limit_is_honoured(self):
        f = fetcher({"remoteok.com": (200, fixture("remoteok.json"))})
        self.assertEqual(len(feeds.remoteok(f, limit=1)["postings"]), 1)


class KeyedConnectorTests(unittest.TestCase):
    """A missing key is a normal state, and it is never silent."""

    def test_every_keyed_connector_reports_a_visible_skip(self):
        for name, fn, host in (
            ("adzuna", adzuna.fetch, "api.adzuna.com"),
            ("jooble", jooble.fetch, "jooble.org"),
            ("careerjet", careerjet.fetch, "public.api.careerjet.net"),
            ("reed", reed.fetch, "reed.co.uk"),
            ("usajobs", usajobs.fetch, "data.usajobs.gov"),
        ):
            f = fetcher({host: (200, "{}")})
            result = fn(f, config={}, settings=NO_SETTINGS, countries=["US", "GB"])
            self.assertEqual(result["status"], "skipped", name)
            self.assertIn("key", result["reason"], name)
            self.assertEqual(len(f.fake_opener.calls), 0,
                             f"{name} must not call out without a key")

    def test_a_skip_names_the_variable_to_set(self):
        f = fetcher({})
        result = adzuna.fetch(f, config={}, settings=NO_SETTINGS, countries=["US"])
        self.assertIn("JOBBY_ADZUNA_ID", result["reason"])

    def test_adzuna_normalises(self):
        f = fetcher({"api.adzuna.com": (200, fixture("adzuna_search.json"))})
        result = adzuna.fetch(f, config={"keywords": ["product manager"]},
                              settings={"JOBBY_ADZUNA_ID": "id", "JOBBY_ADZUNA_KEY": "key"},
                              countries=["US"])
        self.assertEqual(result["status"], "ok")
        job = result["postings"][0]
        self.assertTrue(job["company"])
        self.assertEqual(job["location"]["country"], "US")
        self.assertEqual(job["sources"][0]["sourceClass"], "aggregator")
        self.assertTrue(job["postedAt"])

    def test_adzuna_does_not_expose_the_employer_link(self):
        # Recorded from a live response, and it contradicts what plan §3.2
        # assumes about aggregators "usually" exposing an origin link. Adzuna's
        # redirect_url is adzuna.com/details/<id> or /land/ad/<id>?se=…&v=… with
        # nothing embedded, so identity rule 1 can never fire for an Adzuna
        # sighting: its copies merge by company-title-country or by fingerprint
        # or not at all. Recorded in references/sources-registry.md.
        for entry in json.loads(fixture("adzuna_search.json"))["results"]:
            self.assertEqual(identity.origin_link(entry["redirect_url"]), "",
                             "if this ever starts passing, rule 1 got cheaper")

    def test_adzuna_says_which_selected_countries_it_cannot_answer_for(self):
        f = fetcher({"api.adzuna.com": (200, fixture("adzuna_search.json"))})
        result = adzuna.fetch(f, config={"keywords": ["pm"]},
                              settings={"JOBBY_ADZUNA_ID": "id", "JOBBY_ADZUNA_KEY": "key"},
                              countries=["US", "JP", "KR"])
        self.assertIn("jp", result["reason"])
        self.assertIn("kr", result["reason"])

    def test_adzuna_skips_entirely_when_no_selected_country_is_covered(self):
        f = fetcher({})
        result = adzuna.fetch(f, config={}, settings={"JOBBY_ADZUNA_ID": "i",
                                                      "JOBBY_ADZUNA_KEY": "k"},
                              countries=["JP", "KR"])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(len(f.fake_opener.calls), 0)

    def test_jooble_posts_and_normalises(self):
        f = fetcher({"jooble.org": (200, fixture("jooble_search.json"))})
        result = jooble.fetch(f, config={"keywords": ["product manager"]},
                              settings={"JOBBY_JOOBLE_KEY": "k"}, countries=["AE"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["postings"]), 2)
        self.assertEqual(f.fake_opener.calls[0].get_method(), "POST")

    def test_the_jooble_key_never_appears_in_an_error_line(self):
        # The key is in the URL path, so an error that echoed the URL would put
        # a live credential in a log and on the board.
        f = fetcher({"jooble.org": (500, "boom")})
        result = jooble.fetch(f, config={"keywords": ["pm"]},
                              settings={"JOBBY_JOOBLE_KEY": "SECRETKEY123"}, countries=["AE"])
        blob = json.dumps(result)
        self.assertNotIn("SECRETKEY123", blob)

    def test_careerjet_maps_countries_to_locales(self):
        f = fetcher({"careerjet.net": (200, fixture("careerjet_search.json"))})
        result = careerjet.fetch(f, config={"keywords": ["product owner"]},
                                 settings={"JOBBY_CAREERJET_KEY": "k"}, countries=["IN"])
        self.assertEqual(result["status"], "ok")
        self.assertIn("en_IN", f.fake_opener.calls[0].full_url)
        self.assertEqual(result["postings"][0]["location"]["country"], "IN")

    def test_careerjet_reports_its_own_error_envelope(self):
        f = fetcher({"careerjet.net": (200, '{"type":"ERROR","error":"bad affid"}')})
        result = careerjet.fetch(f, config={}, settings={"JOBBY_CAREERJET_KEY": "k"},
                                 countries=["US"])
        self.assertTrue(result["errors"])

    def test_reed_is_not_asked_for_a_search_that_excludes_the_uk(self):
        f = fetcher({"reed.co.uk": (200, fixture("reed_search.json"))})
        result = reed.fetch(f, config={}, settings={"JOBBY_REED_KEY": "k"},
                            countries=["US", "IN"])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(len(f.fake_opener.calls), 0)

    def test_reed_reads_a_uk_date(self):
        # dd/mm/yyyy, confirmed against a live response: "04/08/2026" is the
        # fourth of August, not the eighth of April.
        f = fetcher({"reed.co.uk": (200, fixture("reed_search.json"))})
        result = reed.fetch(f, config={}, settings={"JOBBY_REED_KEY": "k"}, countries=["GB"])
        raw = json.loads(fixture("reed_search.json"))["results"][0]["date"]
        day, month, year = raw.split("/")
        self.assertEqual(result["postings"][0]["postedAt"],
                         f"{year}-{month}-{day}T00:00:00Z")

    def test_usajobs_needs_both_the_key_and_the_email(self):
        f = fetcher({})
        result = usajobs.fetch(f, config={}, settings={"JOBBY_USAJOBS_KEY": "k"},
                               countries=["US"])
        self.assertEqual(result["status"], "skipped")
        self.assertIn("JOBBY_USAJOBS_EMAIL", result["reason"])

    def test_usajobs_normalises_its_nested_shape(self):
        # Everything worth having is under MatchedObjectDescriptor, and the
        # description has to be assembled from QualificationSummary plus the
        # duty list rather than read from one field.
        f = fetcher({"data.usajobs.gov": (200, fixture("usajobs_search.json"))})
        result = usajobs.fetch(f, config={}, settings={"JOBBY_USAJOBS_KEY": "k",
                                                       "JOBBY_USAJOBS_EMAIL": "a@b.example"},
                               countries=["US"])
        self.assertEqual(result["status"], "ok")
        job = result["postings"][0]
        self.assertTrue(job["title"])
        self.assertTrue(job["company"])
        self.assertEqual(job["location"]["country"], "US")
        self.assertTrue(job["descriptionExcerpt"])
        self.assertTrue(job["canonicalUrl"].startswith("https://"))

    def test_themuse_runs_without_a_key_and_says_so(self):
        # Appendix A lists The Muse as free-key. The public endpoint answers
        # with no key; a key only raises the rate limit.
        f = fetcher({"themuse.com": (200, fixture("themuse.json"))})
        result = themuse.fetch(f, config={}, settings=NO_SETTINGS)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["postings"])
        self.assertIn("no key needed", result["reason"])


class FetcherTests(unittest.TestCase):
    def test_requests_to_one_host_are_spaced_out(self):
        f = fetcher({"example.test": (200, "{}")})
        f.get("https://example.test/a")
        f.get("https://example.test/b")
        self.assertTrue(f.fake_clock.slept)
        self.assertGreaterEqual(f.fake_clock.slept[0], base.MIN_INTERVAL_PER_HOST)

    def test_different_hosts_do_not_wait_on_each_other(self):
        f = fetcher({"a.test": (200, "{}"), "b.test": (200, "{}")})
        f.get("https://a.test/1")
        f.get("https://b.test/1")
        self.assertEqual(f.fake_clock.slept, [])

    def test_a_rate_limit_is_retried_with_growing_backoff(self):
        f = fetcher({"example.test": (429, "slow down")})
        response = f.get("https://example.test/a")
        self.assertEqual(response.status, 429)
        self.assertEqual(len(f.fake_opener.calls), base.MAX_ATTEMPTS)
        waits = [s for s in f.fake_clock.slept if s >= base.BACKOFF_SECONDS]
        self.assertEqual(waits, [base.BACKOFF_SECONDS, base.BACKOFF_SECONDS * 2])

    def test_a_404_is_not_retried(self):
        # A wrong token does not become right on the third attempt; retrying it
        # just triples the traffic.
        f = fetcher({"example.test": (404, "nope")})
        f.get("https://example.test/a")
        self.assertEqual(len(f.fake_opener.calls), 1)

    def test_the_user_agent_names_the_project(self):
        f = fetcher({"example.test": (200, "{}")})
        f.get("https://example.test/a")
        agent = f.fake_opener.calls[0].get_header("User-agent")
        self.assertIn("Jobby", agent)
        self.assertIn("github.com", agent)

    def test_a_validator_is_stored_and_sent_back(self):
        f = fetcher({"example.test": (200, "{}", {"ETag": 'W/"abc"'})})
        f.get("https://example.test/a")
        f.get("https://example.test/a")
        self.assertEqual(f.fake_opener.calls[1].get_header("If-none-match"), 'W/"abc"')

    def test_a_host_that_offers_no_validator_is_not_sent_a_null_header(self):
        # Sending If-None-Match: None raises inside http.client with a message
        # that names neither the header nor the URL. Greenhouse, Ashby and The
        # Muse all returned no validator when this was checked.
        f = fetcher({"example.test": (200, "{}")})
        f.get("https://example.test/a")
        f.get("https://example.test/a")
        self.assertIsNone(f.fake_opener.calls[1].get_header("If-none-match"))

    def test_a_network_error_becomes_a_reported_status_not_an_exception(self):
        class Broken:
            calls = []

            def open(self, request, timeout=None):
                self.calls.append(request)
                raise OSError("connection reset")

        clock = Clock()
        f = base.Fetcher(opener=Broken(), sleep=clock.sleep, clock=clock.tick)
        response = f.get("https://example.test/a")
        self.assertEqual(response.status, 0)
        self.assertIn("connection reset", response.error)

    def test_a_non_json_body_is_reported_rather_than_raised(self):
        f = fetcher({"example.test": (200, "<html>maintenance</html>")})
        data, response = f.get_json("https://example.test/a")
        self.assertIsNone(data)
        self.assertIn("not JSON", response.error)

    def test_the_validator_cache_survives_between_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            f = fetcher({"example.test": (200, "{}", {"ETag": '"v1"'})}, root=root)
            f.get("https://example.test/a")
            f.save_validators()
            later = fetcher({"example.test": (304, "")}, root=root)
            later.get("https://example.test/a")
            self.assertEqual(later.fake_opener.calls[0].get_header("If-none-match"), '"v1"')

    def test_a_corrupt_validator_cache_costs_speed_not_correctness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "source-cache.json").write_text("{not json")
            f = fetcher({"example.test": (200, "{}")}, root=root)
            self.assertEqual(f.validators, {})


class NewDirectBoardTests(unittest.TestCase):
    CASES = (
        ("pinpoint", enterprise_boards.pinpoint, "pinpoint.json",
         {"acme": {"name": "Acme", "ats": "pinpoint", "token": "workwithus"}},
         "pinpointhq.com"),
        ("teamtailor", enterprise_boards.teamtailor, "teamtailor.json",
         {"acme": {"name": "Acme", "ats": "teamtailor", "token": "example"}},
         "teamtailor.com"),
        ("jazzhr", enterprise_boards.jazzhr, "jazzhr.html",
         {"acme": {"name": "Acme", "ats": "jazzhr", "token": "example"}},
         "applytojob.com"),
        ("oracle_orc", enterprise_boards.oracle, "oracle_orc.json",
         {"acme": {"name": "Acme", "ats": "oracle_orc", "token": "CX_1",
                   "careersUrl": "https://example.fa.us2.oraclecloud.com"}},
         "oraclecloud.com"),
        ("gem", enterprise_boards.gem, "gem.json",
         {"acme": {"name": "Acme", "ats": "gem", "token": "example"}},
         "api.gem.com"),
    )

    def test_each_live_shape_normalises_to_the_shared_contract(self):
        for source, connector, fixture_name, companies, host in self.CASES:
            with self.subTest(source=source):
                f = fetcher({host: (200, fixture(fixture_name))})
                result = connector(f, companies=companies)
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(len(result["postings"]), 1)
                job = result["postings"][0]
                self.assertEqual(job["sources"][0]["source"], source)
                self.assertTrue(job["title"])
                self.assertTrue(job["canonicalUrl"].startswith("https://"))
                self.assertIn(job["workplace"], (None, "remote", "hybrid", "onsite"))

    def test_each_missing_registry_is_visible_as_skipped(self):
        for source, connector, _fixture_name, _companies, _host in self.CASES:
            with self.subTest(source=source):
                result = connector(fetcher({}), companies={})
                self.assertEqual(result["status"], "skipped")
                self.assertIn("registry", result["reason"])

    def test_each_failed_board_is_visible_as_error(self):
        for source, connector, _fixture_name, companies, host in self.CASES:
            with self.subTest(source=source):
                result = connector(fetcher({host: (503, "maintenance")}),
                                   companies=companies)
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["errors"])

    def test_workday_post_uses_verified_host_tenant_and_site(self):
        body = json.dumps({"total": 1, "jobPostings": [{
            "title": "Product Manager", "externalPath": "/job/PM_R1",
            "locationsText": "New York, NY", "timeType": "Full time"}]})
        companies = {"acme": {"name": "Acme", "ats": "workday", "token": "acme",
                                "careersUrl": "https://acme.wd1.myworkdayjobs.com/External"}}
        f = fetcher({"/wday/cxs/acme/External/jobs": (200, body)})
        result = workday.fetch(f, companies=companies)
        self.assertEqual(result["status"], "ok")
        request = f.fake_opener.calls[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("/wday/cxs/acme/External/jobs", request.full_url)

    def test_workday_failure_is_not_an_empty_success(self):
        companies = {"acme": {"name": "Acme", "ats": "workday", "token": "acme",
                                "careersUrl": "https://acme.wd1.myworkdayjobs.com/External"}}
        result = workday.fetch(fetcher({"myworkdayjobs.com": (503, "down")}),
                               companies=companies)
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["errors"])

    def test_workday_fetches_past_the_old_hundred_job_cutoff(self):
        companies = {"acme": {"name": "Acme", "ats": "workday", "token": "acme",
                                "careersUrl": "https://acme.wd1.myworkdayjobs.com/External"}}
        def page(_request, call):
            count = workday.PAGE_SIZE if call <= 6 else 1
            rows = [{"title": f"Role {call}-{i}", "externalPath": f"/job/{call}-{i}"}
                    for i in range(count)]
            return 200, json.dumps({"total": workday.PAGE_SIZE * 6 + 1,
                                    "jobPostings": rows})
        f = fetcher({"myworkdayjobs.com": page})
        result = workday.fetch(f, companies=companies)
        self.assertEqual(len(result["postings"]), workday.PAGE_SIZE * 6 + 1)
        self.assertEqual(result["requests"], 7)

    def test_oracle_paginates_until_the_reported_total(self):
        companies = {"acme": {"name": "Acme", "ats": "oracle_orc", "token": "CX_1",
                                "careersUrl": "https://example.fa.oraclecloud.com"}}
        def page(request, _call):
            offset = enterprise_boards.ORACLE_PAGE_SIZE if "offset=200" in request.full_url else 0
            count = 1 if offset else enterprise_boards.ORACLE_PAGE_SIZE
            rows = [{"Id": str(offset + i), "Title": f"Role {offset + i}"}
                    for i in range(count)]
            return 200, json.dumps({"items": [{
                "TotalJobsCount": enterprise_boards.ORACLE_PAGE_SIZE + 1,
                "requisitionList": rows}]})
        f = fetcher({"oraclecloud.com": page})
        result = enterprise_boards.oracle(f, companies=companies)
        self.assertEqual(len(result["postings"]), enterprise_boards.ORACLE_PAGE_SIZE + 1)
        self.assertEqual(result["requests"], 2)

    def test_transient_host_failure_opens_a_per_sweep_circuit(self):
        f = fetcher({"example.test": (503, "down")})
        first = f.get("https://example.test/one")
        calls = len(f.fake_opener.calls)
        second = f.get("https://example.test/two")
        self.assertEqual(first.status, 503)
        self.assertEqual(second.status, 0)
        self.assertIn("circuit open", second.error)
        self.assertEqual(len(f.fake_opener.calls), calls)

    def test_a_404_does_not_poison_other_tenants_on_the_same_host(self):
        f = fetcher({"/bad": (404, "missing"), "/good": (200, "{}")})
        self.assertEqual(f.get("https://example.test/bad").status, 404)
        self.assertEqual(f.get("https://example.test/good").status, 200)


class NormalisationTests(unittest.TestCase):
    def test_html_becomes_readable_text(self):
        html = "<div><p>Own the <b>roadmap</b>.</p><script>evil()</script><li>SQL</li></div>"
        text = base.strip_html(html)
        self.assertIn("Own the roadmap", text)
        self.assertNotIn("evil()", text)
        self.assertNotIn("<", text)

    def test_a_double_escaped_description_still_comes_out_as_text(self):
        # Greenhouse serves `content` HTML-escaped, so the payload holds
        # "&lt;h2&gt;About the Role&lt;/h2&gt;". Unescaping after stripping
        # turned those into tags a moment too late to remove them, and every
        # Greenhouse description carried its own markup as visible text — onto
        # the card, into the simhash, and into experience extraction.
        raw = ("&lt;h2&gt;&lt;strong&gt;About the Role&lt;/strong&gt;&lt;/h2&gt; "
               "&lt;p&gt;At Chime, we believe&lt;/p&gt;")
        text = base.strip_html(raw)
        self.assertIn("About the Role", text)
        self.assertIn("At Chime", text)
        self.assertNotIn("<", text)
        self.assertNotIn("&lt;", text)

    def test_an_escaped_script_is_removed_rather_than_shown(self):
        self.assertNotIn("evil", base.strip_html("&lt;script&gt;evil()&lt;/script&gt;Real"))

    def test_an_entity_that_is_not_a_tag_survives(self):
        # Two unescaping passes must not eat legitimate text.
        self.assertEqual(base.strip_html("R&amp;D and 5+ years"), "R&D and 5+ years")

    def test_entities_are_unescaped(self):
        self.assertEqual(base.strip_html("R&amp;D &mdash; product"), "R&D — product")

    def test_a_country_is_only_set_when_the_string_says_so(self):
        # Guessing a country from a city is how a London, Ontario role gets
        # filtered as a UK one.
        self.assertEqual(base.parse_location("London")["country"], None)
        self.assertEqual(base.parse_location("London, United Kingdom")["country"], "GB")
        self.assertEqual(base.parse_location("London, ON")["country"], "CA")
        self.assertEqual(base.parse_location("New York, NY")["country"], "US")
        self.assertEqual(base.parse_location("Bengaluru, India")["country"], "IN")
        self.assertEqual(base.parse_location("Dubai, United Arab Emirates")["country"], "AE")

    def test_an_empty_location_is_empty_not_a_guess(self):
        self.assertEqual(base.parse_location("")["country"], None)
        self.assertEqual(base.parse_location(None)["raw"], "")

    def test_workplace_detection_prefers_the_more_specific_word(self):
        self.assertEqual(base.detect_workplace("Hybrid - Remote friendly"), "hybrid")
        self.assertEqual(base.detect_workplace("Remote (US)"), "remote")
        self.assertEqual(base.detect_workplace("On-site in Berlin"), "onsite")
        self.assertIsNone(base.detect_workplace("Berlin"))
        self.assertIsNone(base.detect_workplace(""))

    def test_dates_arrive_in_every_shape_and_leave_in_one(self):
        self.assertEqual(base.iso_stamp("2026-08-13T09:12:41Z"), "2026-08-13T09:12:41Z")
        self.assertEqual(base.iso_stamp("2026-08-13"), "2026-08-13T00:00:00Z")
        self.assertEqual(base.iso_stamp(1755000000), "2025-08-12T12:00:00Z")
        self.assertEqual(base.iso_stamp(1755000000000), "2025-08-12T12:00:00Z")

    def test_an_unreadable_date_is_none_rather_than_today(self):
        # A posting dated today because nobody could parse its date sorts
        # straight to the top of a board ordered newest first.
        for bad in ("not a date", "", None, 0, "yesterday"):
            self.assertIsNone(base.iso_stamp(bad), bad)

    def test_a_posting_carries_a_fingerprint_when_the_body_is_thick_enough(self):
        long_body = " ".join(f"responsibility {i} owning the roadmap" for i in range(20))
        record = base.posting("x", "ats", "1a", "Acme", "PM", "https://a.test/1",
                              description=long_body)
        self.assertTrue(record["descFingerprint"])
        thin = base.posting("x", "ats", "1a", "Acme", "PM", "https://a.test/2",
                            description="Apply now.")
        self.assertIsNone(thin["descFingerprint"])

    def test_a_posting_url_is_canonicalised_on_the_way_in(self):
        record = base.posting("x", "ats", "1a", "Acme", "PM",
                              "https://a.test/1?utm_source=x#apply")
        self.assertEqual(record["canonicalUrl"], "https://a.test/1")

    def test_the_excerpt_is_capped(self):
        record = base.posting("x", "ats", "1a", "Acme", "PM", "https://a.test/1",
                              description="word " * 5000)
        self.assertLessEqual(len(record["descriptionExcerpt"]), base.EXCERPT_CHARS)


class QueryBudgetTests(unittest.TestCase):
    """A keyed channel may not ask fifty countries every hour.

    Measured, not assumed: the plan's nine regions expand to about fifty
    countries, and Adzuna, Jooble and Careerjet querying every country against
    every search term came to roughly four hundred requests and blew a
    ten-minute timeout on the first supervised dry run. At twenty-four runs a
    day that exhausts the free quotas these channels are on.
    """

    def test_a_short_list_is_asked_in_full(self):
        items, start = base.query_slice(["US", "GB", "IN"], cursor=0)
        self.assertEqual(items, ["US", "GB", "IN"])
        self.assertEqual(start, 0)

    def test_a_long_list_is_capped(self):
        items, _ = base.query_slice([f"C{i}" for i in range(50)], cursor=0)
        self.assertEqual(len(items), base.MAX_QUERIES_PER_RUN)

    def test_the_window_rotates_and_covers_everything_across_a_day(self):
        # Coverage over a day rather than over an hour is the trade. If the
        # rotation did not actually reach everything, the coverage claim would
        # be false and nothing would say so.
        countries = [f"C{i}" for i in range(50)]
        seen = set()
        for hour in range(24):
            asked, _ = base.query_slice(countries, cursor=hour)
            seen.update(asked)
        self.assertEqual(seen, set(countries))

    def test_the_same_hour_asks_the_same_thing(self):
        # A retry inside one hour must not be a fresh burst of traffic.
        countries = [f"C{i}" for i in range(50)]
        self.assertEqual(base.query_slice(countries, cursor=7),
                         base.query_slice(countries, cursor=7))

    def test_the_window_wraps_rather_than_running_short(self):
        countries = [f"C{i}" for i in range(10)]
        asked, start = base.query_slice(countries, cursor=1, cap=8)
        self.assertEqual(len(asked), 8)
        self.assertEqual(start, 8)
        self.assertEqual(asked[0], "C8")
        self.assertEqual(asked[2], "C0", "it wraps back to the beginning")

    def test_an_empty_list_is_not_a_crash(self):
        self.assertEqual(base.query_slice([], cursor=3), ([], 0))

    def test_a_capped_channel_says_how_much_it_asked(self):
        # A channel that asked six of fifty countries must say so, or the
        # coverage accounting is a lie.
        f = fetcher({"api.adzuna.com": (200, fixture("adzuna_search.json"))})
        result = adzuna.fetch(f, config={"keywords": ["pm"]},
                              settings={"JOBBY_ADZUNA_ID": "i", "JOBBY_ADZUNA_KEY": "k"},
                              countries=["US", "GB", "IN", "CA", "AU", "DE", "FR", "NL",
                                         "PL", "ES", "IT", "SE"],
                              cursor=0)
        # Eleven of the twelve are countries Adzuna operates in; Sweden is not,
        # and the same line says so rather than dropping it quietly.
        self.assertIn(f"{base.MAX_QUERIES_PER_RUN} of 11 countries", result["reason"])
        self.assertIn("rotating window", result["reason"])
        self.assertIn("not covered here: se", result["reason"])


class CareerjetTransportTests(unittest.TestCase):
    """Two things the live run contradicted, both recorded in the registry."""

    def test_the_endpoint_is_plain_http(self):
        # Verified 2026-08-20: the public endpoint refuses a TLS connection
        # outright (connection refused on 443) and answers on port 80.
        self.assertTrue(careerjet.SEARCH_URL.startswith("http://"))
        self.assertNotIn("https://", careerjet.SEARCH_URL)

    def test_the_caller_declares_itself_with_a_referer(self):
        # "Undeclared referrer. Please add a Referer header so we know who is
        # calling this API and from which page." — the 403 body, verbatim.
        f = fetcher({"careerjet.net": (200, fixture("careerjet_search.json"))})
        careerjet.fetch(f, config={"keywords": ["pm"]},
                        settings={"JOBBY_CAREERJET_KEY": "k"}, countries=["US"])
        referer = f.fake_opener.calls[0].get_header("Referer")
        self.assertTrue(referer)
        self.assertIn("github.com", referer)

    def test_the_referer_names_the_project_rather_than_a_browser(self):
        # The honest-identification rule applies to the Referer for the same
        # reason it applies to the User-Agent.
        self.assertIn("jobby", careerjet.REFERER.lower())


class FixtureProvenanceTests(unittest.TestCase):
    def test_every_fixture_is_now_a_recorded_response(self):
        # These five were written from each published contract while no key was
        # installed, and carried a __fixture_note saying so. The keys are in
        # now and all twenty connectors have run live, so the fixtures are
        # re-recorded and the note is gone. A test asserting they are
        # unverified would itself have become the untrue claim.
        for name in ("adzuna_search", "jooble_search", "careerjet_search",
                     "reed_search", "usajobs_search"):
            data = json.loads(fixture(f"{name}.json"))
            self.assertNotIn("__fixture_note", data, name)

    def test_no_fixture_carries_an_api_key(self):
        # These were recorded from authenticated requests, and Adzuna echoes
        # the app id back inside every redirect_url it returns.
        import toolpath
        settings = toolpath.settings()
        secrets = [v for k, v in settings.items()
                   if k.startswith("JOBBY_") and v and len(v) > 6]
        for path in sorted(FIXTURES.iterdir()):
            text = path.read_text(encoding="utf-8", errors="replace")
            for secret in secrets:
                self.assertNotIn(secret, text, f"{path.name} carries a live key")

    def test_no_fixture_carries_personal_data(self):
        # §13 item 11: Playwright wrote 33 snapshots into the repo carrying a
        # real name, phone number and home address.
        banned = ("sahil", "sapra", "216-848", "(216)", "charlotte, nc",
                  "navi mumbai", "spechawk.ai")
        for path in sorted(FIXTURES.iterdir()):
            if path.name == "practice-form.html":
                continue                       # pre-existing, not ours
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for needle in banned:
                self.assertNotIn(needle, text, f"{path.name} contains {needle!r}")


class BoardPlatformTests(unittest.TestCase):
    """The six company-board platforms added after Phase 1's first review.

    Every endpoint here was confirmed against a live tenant before the
    connector was written, and none of the shapes is what a reasonable person
    would guess. Fixtures are recorded from those tenants.

    These are registry-driven, which is the point worth remembering: there is
    no endpoint that returns every SmartRecruiters job, only
    /v1/companies/{token}/postings. A connector adds as many companies as the
    registry knows tenants for.
    """

    COMPANIES = {
        "ifs": {"name": "IFS", "ats": "smartrecruiters", "token": "IFS1"},
        "innovaccer": {"name": "Innovaccer", "ats": "workable",
                       "token": "innovaccer-analytics"},
        "dss": {"name": "Consult DSS", "ats": "recruitee", "token": "consultdss"},
        "personio": {"name": "Personio", "ats": "personio", "token": "personio"},
        "breezy": {"name": "Breezy", "ats": "breezy", "token": "breezy"},
        "rippling": {"name": "Rippling", "ats": "rippling", "token": "rippling"},
    }

    def run_one(self, name, host, fixture_name, accept_xml=False):
        body = fixture(fixture_name)
        f = fetcher({host: (200, body)})
        return boards.CONNECTORS[name](f, companies=self.COMPANIES), f

    def test_each_platform_normalises_its_own_shape(self):
        cases = [("smartrecruiters", "api.smartrecruiters.com", "smartrecruiters_board.json"),
                 ("workable", "apply.workable.com", "workable_board.json"),
                 ("recruitee", "recruitee.com", "recruitee_board.json"),
                 ("personio", "personio.de", "personio_board.xml"),
                 ("breezy", "breezy.hr", "breezy_board.json"),
                 ("rippling", "api.rippling.com", "rippling_board.json")]
        for name, host, fx in cases:
            result, _ = self.run_one(name, host, fx)
            self.assertEqual(result["status"], "ok", f"{name}: {result.get('reason')}")
            self.assertTrue(result["postings"], name)
            for job in result["postings"]:
                self.assertTrue(job["title"], name)
                self.assertTrue(job["company"], name)
                self.assertEqual(job["sources"][0]["source"], name)
                self.assertEqual(job["sources"][0]["sourceClass"], "ats",
                                 f"{name} is a direct listing, not an aggregator copy")

    def test_every_one_of_them_counts_as_a_direct_listing(self):
        # This is why they were added: they rank above aggregator copies in
        # both retention and the lane's ordering.
        for name in boards.CONNECTORS:
            self.assertIn(name, sources.REGISTRY_CONNECTORS)

    def test_an_empty_registry_is_a_visible_skip_not_a_success(self):
        for name, fn in boards.CONNECTORS.items():
            f = fetcher({})
            result = fn(f, companies={})
            self.assertEqual(result["status"], "skipped", name)
            self.assertIn("registry", result["reason"], name)
            self.assertEqual(len(f.fake_opener.calls), 0, name)

    def test_a_dead_token_is_reported(self):
        f = fetcher({"apply.workable.com": (404, "not found")})
        result = boards.workable(f, companies={"innovaccer": self.COMPANIES["innovaccer"]})
        self.assertEqual(result["status"], "error")
        self.assertIn("404", result["errors"][0])

    def test_smartrecruiters_carries_no_description_and_does_not_pretend_to(self):
        # The postings list genuinely has no body. Saying so beats inventing one.
        result, _ = self.run_one("smartrecruiters", "api.smartrecruiters.com",
                                 "smartrecruiters_board.json")
        job = result["postings"][0]
        self.assertEqual(job["descriptionExcerpt"], "")
        self.assertIsNone(job["descFingerprint"])
        self.assertTrue(job["location"]["country"], "but the location is real")

    def test_workable_gives_a_body_and_the_experience_comes_out_of_it(self):
        result, _ = self.run_one("workable", "apply.workable.com", "workable_board.json")
        job = result["postings"][0]
        self.assertGreater(len(job["descriptionExcerpt"]), 500)
        self.assertTrue(job["descFingerprint"])
        self.assertTrue(job["extracted"]["yearsText"])

    def test_recruitee_reads_its_own_workplace_flags(self):
        result, _ = self.run_one("recruitee", "recruitee.com", "recruitee_board.json")
        places = {j["workplace"] for j in result["postings"]}
        self.assertTrue(places & {"remote", "hybrid", "onsite"})

    def test_personio_is_xml_rather_than_json(self):
        result, f = self.run_one("personio", "personio.de", "personio_board.xml")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(f.fake_opener.calls[0].get_header("Accept"), "application/xml")

    def test_malformed_xml_is_an_error_not_a_crash(self):
        f = fetcher({"personio.de": (200, "<workzag-jobs><position>truncated")})
        result = boards.personio(f, companies={"personio": self.COMPANIES["personio"]})
        self.assertTrue(result["errors"])

    def test_rippling_records_are_thin_and_honest_about_it(self):
        # Its payload has five keys and carries no description or date at all.
        result, _ = self.run_one("rippling", "api.rippling.com", "rippling_board.json")
        job = result["postings"][0]
        self.assertIsNone(job["postedAt"])
        self.assertIsNone(job["descFingerprint"])
        self.assertTrue(job["title"])

    def test_rippling_lists_one_requisition_once_per_location(self):
        # Same uuid and same URL across several locations, so canonical-URL
        # matching folds them rather than showing the same job five times.
        result, _ = self.run_one("rippling", "api.rippling.com", "rippling_board.json")
        urls = [j["canonicalUrl"] for j in result["postings"]]
        self.assertLess(len(set(urls)), len(urls) + 1)


class TrailingCountryCodeTests(unittest.TestCase):
    def test_a_trailing_two_letter_code_is_a_country(self):
        # Rippling writes "Remote (New Jersey, US)" and that was being thrown
        # away, leaving the country unknown on every one of its postings.
        self.assertEqual(base.parse_location("Remote (New Jersey, US)")["country"], "US")
        self.assertEqual(base.parse_location("Bengaluru, IN")["country"], "IN")

    def test_a_two_letter_word_mid_sentence_is_not_a_country(self):
        self.assertIsNone(base.parse_location("Please contact us")["country"])

    def test_a_us_state_still_wins_where_it_applies(self):
        self.assertEqual(base.parse_location("New York, NY")["country"], "US")


class LocationCountryTests(unittest.TestCase):
    """A posting with no country answers a search for every country.

    Selecting India showed Chicago, San Francisco and "Remote - US", because
    none of those produced a country and the client can only filter on what it
    is given. The parser was right to refuse to guess from an ambiguous city —
    London, Ontario is the example in its own docstring — but Chicago is not a
    guess, and "Remote - US" is not even a city.
    """

    def country(self, raw):
        return base.parse_location(raw)["country"]

    def test_a_stated_code_is_read_wherever_it_sits(self):
        for raw in ("Remote - US", "US Remote", "Remote - US: Select locations",
                    "Remote (GB)", "Remote - IN"):
            with self.subTest(raw=raw):
                self.assertIn(self.country(raw), ("US", "GB", "IN"))

    def test_lowercase_words_are_never_read_as_codes(self):
        # "in" is a preposition long before it is India.
        self.assertIsNone(self.country("Engineer in a growing team"))
        self.assertIsNone(self.country("based in the office"))

    def test_an_unambiguous_city_gives_its_country(self):
        for city, code in (("Chicago", "US"), ("San Francisco", "US"),
                           ("New York", "US"), ("Bengaluru", "IN"),
                           ("Mumbai", "IN"), ("Berlin", "DE"), ("Dubai", "AE"),
                           ("Singapore", "SG"), ("Toronto", "CA")):
            with self.subTest(city=city):
                self.assertEqual(self.country(city), code)

    def test_a_city_with_a_famous_twin_is_still_refused(self):
        # The whole reason the parser was conservative. Keep it conservative
        # exactly here.
        for city in ("London", "Cambridge", "Birmingham", "Manchester",
                     "Richmond", "Windsor", "Hamilton", "Perth", "Newcastle",
                     "Waterloo", "Victoria", "Wellington"):
            with self.subTest(city=city):
                self.assertIsNone(self.country(city), city)

    def test_an_explicit_country_still_wins_over_the_city(self):
        # "London, Ontario" must never become GB. It reads as CA now, off the
        # province name, which is the right answer and the reason the city
        # table refuses London on its own.
        self.assertEqual(self.country("Toronto, ON"), "CA")
        self.assertEqual(self.country("Bengaluru, India"), "IN")
        self.assertEqual(self.country("London, Ontario"), "CA")

    def test_a_spelled_out_state_or_province_names_its_country(self):
        for raw, code in (("California - Remote", "US"), ("Texas", "US"),
                          ("Remote - Washington, D.C.", "US"),
                          ("New Jersey", "US"), ("Ontario, Canada", "CA")):
            with self.subTest(raw=raw):
                self.assertEqual(self.country(raw), code)

    def test_georgia_is_refused_because_it_is_also_a_country(self):
        # The one state name that cannot be read as a state. Paired with a city
        # the answer comes from the city instead, which is what should happen.
        self.assertIsNone(self.country("Georgia"))
        self.assertIsNone(self.country("Tbilisi, Georgia"))
        self.assertEqual(self.country("Atlanta, Georgia"), "US")

    def test_a_region_nobody_can_place_stays_unknown(self):
        for raw in ("Remote-AMER", "NAMER", "Remote - International", "EMEA"):
            with self.subTest(raw=raw):
                self.assertIsNone(self.country(raw), raw)

    def test_a_string_with_nothing_in_it_stays_unknown(self):
        self.assertIsNone(self.country("Remote"))
        self.assertIsNone(self.country("Somewhere Odd"))
        self.assertIsNone(self.country(""))

    def test_no_city_in_the_table_collides_with_another_country(self):
        # The table's whole claim is one name, one country. A duplicate key
        # would silently take the last one written.
        names = list(base.UNAMBIGUOUS_CITIES)
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
