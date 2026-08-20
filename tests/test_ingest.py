"""Tests for the ingest pipeline.

Most of these are about the config gate and about `meta.lastIngest`, because
those are the two places a wrong answer is invisible. A gate that is slightly
too tight silently narrows every scan and looks exactly like the connectors
having gone quiet; a `lastIngest` that is only written on success leaves
yesterday's healthy numbers on the board through an outage.

Nothing here touches the network or the real store: every run gets a temporary
store directory and a fake connector.

Run from the repo root: python3 -m pytest tests/ -q
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import identity  # noqa: E402
import ingest  # noqa: E402
import jobs_store  # noqa: E402
import search_config  # noqa: E402
from sources import base  # noqa: E402

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)
SYNDICATED = json.loads((FIXTURES / "syndicated_jd.json").read_text())


def when(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def posting(**fields):
    """A normalised posting, as a connector produces one."""
    record = base.posting(
        fields.pop("source", "greenhouse"), fields.pop("sourceClass", "ats"), "1a",
        fields.pop("company", "Northwind Analytics"),
        fields.pop("title", "Product Manager, Internal Reporting"),
        fields.pop("url", "https://job-boards.greenhouse.io/northwind/jobs/4471002"),
        location=fields.pop("location", {"raw": "New York, NY", "city": "New York",
                                         "region": "NY", "country": "US"}),
        workplace=fields.pop("workplace", "hybrid"),
        posted_at=fields.pop("posted_at", when(2)),
        description=fields.pop("description", SYNDICATED["sightings"][0]["body"]),
        seen_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    record.update(fields)
    return record


def channel(postings, name="greenhouse", status="ok", reason=""):
    def connector(fetcher, **_):
        if status == "ok":
            return base.ok(name, postings, requests=1, note=reason)
        if status == "skipped":
            return base.skipped(name, reason, requests=0)
        return base.failed(name, reason, requests=1, errors=[reason])
    return connector


class IngestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "init"], capture_output=True)
        self.original = dict(ingest.sources.ALL)

    def tearDown(self):
        ingest.sources.ALL.clear()
        ingest.sources.ALL.update(self.original)
        self.tmp.cleanup()

    def run_ingest(self, connectors, config=None, fetcher=None, **kwargs):
        if config is not None:
            search_config.save(self.root, config)
        ingest.sources.ALL.clear()
        ingest.sources.ALL.update(connectors)
        return ingest.run(self.root, now=NOW, settings={},
                          fetcher=fetcher or base.Fetcher(root=self.root, opener=None),
                          **kwargs)

    def meta(self):
        path = self.root / "meta.json"
        return json.loads(path.read_text()).get("lastIngest") if path.exists() else None


class PipelineTests(IngestCase):
    def test_a_posting_travels_all_the_way_into_the_pool(self):
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(report["found"], 1)
        self.assertEqual(report["kept"], 1)
        self.assertEqual(len(jobs), 1)
        job = next(iter(jobs.values()))
        self.assertEqual(job["state"], "new")
        self.assertTrue(job["score"]["total"])
        self.assertEqual(job["score"]["parts"].keys(), job["score"]["weights"].keys())

    def test_the_same_job_from_three_channels_is_one_record(self):
        trio = {}
        for sighting in SYNDICATED["sightings"]:
            name = sighting["source"]
            trio[name] = channel([posting(
                source=name, sourceClass=sighting["sourceClass"],
                company=sighting["company"], title=sighting["title"],
                url=sighting["url"], workplace=sighting["workplace"],
                location=sighting["location"],
                posted_at=sighting["postedAt"] or when(2),
                description=sighting["body"])], name=name)
        report, jobs = self.run_ingest(trio)
        self.assertEqual(report["found"], 3)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(report["deduped"], 2)
        self.assertEqual(len(next(iter(jobs.values()))["sources"]), 3)

    def test_a_dry_run_writes_nothing(self):
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])}, dry_run=True)
        self.assertEqual(report["kept"], 1)
        self.assertFalse((self.root / "jobs.json").exists())
        self.assertIsNone(self.meta())

    def test_the_limit_is_passed_to_the_connectors(self):
        seen = {}

        def connector(fetcher, limit=None, **_):
            seen["limit"] = limit
            return base.ok("greenhouse", [posting()], requests=1)
        self.run_ingest({"greenhouse": connector}, limit=7)
        self.assertEqual(seen["limit"], 7)

    def test_only_the_named_channel_runs(self):
        ran = []

        def make(name):
            def connector(fetcher, **_):
                ran.append(name)
                return base.ok(name, [], requests=1)
            return connector
        self.run_ingest({"greenhouse": make("greenhouse"), "lever": make("lever")},
                        channels=["lever"])
        self.assertEqual(ran, ["lever"])

    def test_an_unknown_channel_is_refused_by_name(self):
        with self.assertRaises(ingest.IngestError) as ctx:
            self.run_ingest({"greenhouse": channel([])}, channels=["linkedout"])
        self.assertIn("linkedout", str(ctx.exception))


class AccountingTests(IngestCase):
    def test_every_channel_appears_in_the_report(self):
        report, _ = self.run_ingest({
            "greenhouse": channel([posting()]),
            "adzuna": channel([], "adzuna", "skipped", "no key (JOBBY_ADZUNA_ID is not set)"),
            "remotive": channel([], "remotive", "error", "HTTP 500"),
        })
        self.assertEqual(sorted(report["byChannel"]), ["adzuna", "greenhouse", "remotive"])
        self.assertEqual(report["byChannel"]["adzuna"]["status"], "skipped")
        self.assertEqual(report["byChannel"]["remotive"]["status"], "error")

    def test_a_skipped_channel_is_listed_with_its_reason(self):
        # A channel that is quietly absent looks exactly like one that found
        # nothing, which is the failure the whole envelope exists to prevent.
        report, _ = self.run_ingest({
            "adzuna": channel([], "adzuna", "skipped", "no key (JOBBY_ADZUNA_ID is not set)")})
        self.assertEqual(report["channelsSkipped"][0]["channel"], "adzuna")
        self.assertIn("JOBBY_ADZUNA_ID", report["channelsSkipped"][0]["why"])

    def test_a_scan_that_found_nothing_differs_from_one_that_did_not_run(self):
        quiet, _ = self.run_ingest({"greenhouse": channel([])})
        broken, _ = self.run_ingest({"greenhouse": channel([], status="error",
                                                           reason="HTTP 503")})
        self.assertEqual(quiet["byChannel"]["greenhouse"]["status"], "ok")
        self.assertEqual(quiet["errors"], [])
        self.assertEqual(broken["byChannel"]["greenhouse"]["status"], "error")
        self.assertTrue(broken["errors"])

    def test_last_ingest_is_written_to_the_store(self):
        self.run_ingest({"greenhouse": channel([posting()])})
        stored = self.meta()
        self.assertEqual(stored["found"], 1)
        self.assertIn("greenhouse", stored["byChannel"])
        self.assertTrue(stored["at"])

    def test_last_ingest_is_written_when_the_run_fails(self):
        # The run that died is the one you most need on the board. Writing only
        # on success leaves yesterday's healthy numbers up through an outage.
        (self.root / "jobs.json").write_text("{not json")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ingest.py"),
             "--tier", "1", "--store", str(self.root)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        stored = self.meta()
        self.assertTrue(stored["failed"])
        self.assertTrue(stored["errors"])

    def test_a_connector_that_raises_does_not_take_the_others_with_it(self):
        def explodes(fetcher, **_):
            raise RuntimeError("bad shape")
        report, jobs = self.run_ingest({"boom": explodes,
                                        "greenhouse": channel([posting()])})
        self.assertEqual(report["byChannel"]["boom"]["status"], "error")
        self.assertIn("bad shape", report["byChannel"]["boom"]["why"])
        self.assertEqual(report["kept"], 1)

    def test_the_reasons_jobs_were_gated_out_are_counted(self):
        report, _ = self.run_ingest(
            {"greenhouse": channel([posting(title="Warehouse Operative",
                                            description="Lifting boxes."),
                                    posting()])},
            config={"keywords": ["product manager"]})
        self.assertEqual(report["gatedOut"], 1)
        self.assertTrue(any("search terms" in why for why in report["gatedOutBy"]))


class RequestAccountingTests(IngestCase):
    def test_the_run_counts_every_request_including_body_fetches(self):
        # Bodies are fetched outside the connectors, so counting only what the
        # connectors report understates our own traffic on somebody's server.
        counting = base.Fetcher(root=self.root, opener=None)
        counting.requests = 4              # this run is not the first thing it did

        def connector(fetcher, **_):
            fetcher.requests += 3          # as a connector's own calls would
            fetcher.requests += 2          # plus two body fetches it never reports
            return base.ok("greenhouse", [posting()], requests=3)

        report, _ = self.run_ingest({"greenhouse": connector}, fetcher=counting)
        self.assertEqual(report["requests"], 5)


class ConfigGateTests(IngestCase):
    def gate(self, config, **fields):
        return ingest.gate(posting(**fields), search_config.validate(config), now=NOW)

    def test_a_posting_outside_the_date_window_is_dropped(self):
        self.assertTrue(self.gate({"postedWithin": "24h"}, posted_at=when(9)))
        self.assertFalse(self.gate({"postedWithin": "24h"}, posted_at=when(0)))
        # `all` used to mean no limit. It now means the two-month ceiling, so a
        # 400-day-old posting is dropped under every window on offer.
        self.assertTrue(self.gate({"postedWithin": "all"}, posted_at=when(400)))
        self.assertFalse(self.gate({"postedWithin": "all"}, posted_at=when(10)))

    def test_an_undated_posting_follows_the_include_unknowns_switch(self):
        self.assertFalse(self.gate({"postedWithin": "7d"}, posted_at=None))
        self.assertTrue(self.gate({"postedWithin": "7d",
                                   "includeUnknowns": {"postedAt": False}}, posted_at=None))

    def test_a_country_outside_the_selection_is_dropped(self):
        config = {"locations": [{"country": "US"}]}
        self.assertFalse(self.gate(config))
        self.assertTrue(self.gate(config, location={"raw": "Berlin", "country": "DE"}))

    def test_a_region_pack_admits_its_members(self):
        config = {"locations": [{"regionPack": "EU"}]}
        self.assertFalse(self.gate(config, location={"raw": "Berlin", "country": "DE"}))
        self.assertFalse(self.gate(config, location={"raw": "London", "country": "GB"}))
        self.assertTrue(self.gate(config, location={"raw": "Tokyo", "country": "JP"}))

    def test_remote_survives_a_country_selection_when_asked_to(self):
        config = {"locations": [{"country": "US"}], "includeRemoteForSelection": True}
        self.assertFalse(self.gate(config, workplace="remote",
                                   location={"raw": "Remote", "country": "DE"}))
        strict = dict(config, includeRemoteForSelection=False)
        self.assertTrue(self.gate(strict, workplace="remote",
                                  location={"raw": "Remote", "country": "DE"}))

    def test_an_unknown_country_is_not_dropped(self):
        # Phase 1 cannot always tell a country from a location string, and
        # dropping everything it cannot parse would quietly halve every scan.
        self.assertFalse(self.gate({"locations": [{"country": "US"}]},
                                   location={"raw": "Head office", "country": None}))

    def test_an_unwanted_arrangement_is_dropped(self):
        self.assertTrue(self.gate({"workplace": ["remote"]}, workplace="onsite"))
        self.assertFalse(self.gate({"workplace": ["remote"]}, workplace="remote"))

    def test_an_excluded_word_drops_the_posting(self):
        config = {"excludeKeywords": ["staffing agency"]}
        self.assertTrue(self.gate(config, description="We are a staffing agency."))
        self.assertFalse(self.gate(config))

    def test_an_exclusion_searches_the_body_whatever_the_scope_says(self):
        # Deliberately reversed. This used to follow searchScope, which was
        # invisible while the scope defaulted to title-and-description and
        # became a silent widening of what gets stored the moment the default
        # moved to titles.
        #
        # An exclusion is a veto and a veto should look everywhere. "staffing
        # agency" almost never appears in a job title and almost always appears
        # in the body, so a title-scoped exclusion list stops rejecting exactly
        # what it exists to reject.
        body = {"description": "This is a staffing agency posting."}
        for scope in ("title", "title+description"):
            with self.subTest(scope=scope):
                self.assertTrue(self.gate({"excludeKeywords": ["staffing agency"],
                                           "searchScope": scope}, **body))

    def test_an_include_keyword_still_respects_the_scope(self):
        # The other half: narrowing the scope narrows what *matches*, it just
        # does not narrow what gets rejected.
        body = {"title": "Warehouse Operative",
                "description": "You will work with our product manager daily."}
        self.assertTrue(self.gate({"keywords": ["product manager"],
                                   "searchScope": "title"}, **body))
        self.assertFalse(self.gate({"keywords": ["product manager"],
                                    "searchScope": "title+description"}, **body))

    def test_a_posting_matching_no_search_term_is_dropped(self):
        config = {"keywords": ["product manager"], "searchScope": "title"}
        self.assertTrue(self.gate(config, title="Warehouse Operative"))
        self.assertFalse(self.gate(config, title="Senior Product Manager"))

    def test_no_keywords_means_no_keyword_gate(self):
        self.assertFalse(self.gate({}, title="Warehouse Operative"))

    def test_companies_only_and_hide_both_bite(self):
        only = {"companiesMode": "only", "companies": ["Northwind Analytics"]}
        self.assertFalse(self.gate(only))
        self.assertTrue(self.gate(only, company="Southwind Analytics"))
        hide = {"companiesMode": "hide", "companies": ["Northwind Analytics"]}
        self.assertTrue(self.gate(hide))
        self.assertFalse(self.gate(hide, company="Southwind Analytics"))

    def test_the_companies_filter_sees_through_a_legal_suffix(self):
        only = {"companiesMode": "only", "companies": ["Northwind Analytics"]}
        self.assertFalse(self.gate(only, company="Northwind Analytics, Inc."))

    def test_the_score_floor_is_applied_after_scoring(self):
        # A partial title match survives the keyword gate and then scores 83,
        # so the floor is the only thing that can reject it.
        weak = posting(title="Product Operations Associate")
        report, jobs = self.run_ingest({"greenhouse": channel([weak])},
                                       config={"keywords": ["product manager"],
                                               "minScoreToKeep": 90})
        self.assertEqual(report["kept"], 0)
        self.assertEqual(len(jobs), 0)
        self.assertTrue(any("below 90" in why for why in report["gatedOutBy"]))


class AlreadyDecidedTests(IngestCase):
    def add_session(self, aid, company, role, status, url=None):
        payload = {"applicationId": aid, "company": company, "role": role,
                   "status": status, "url": url or ""}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(payload, fh)
            path = fh.name
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "session-save", "--id", aid,
                        "--input", path], capture_output=True)
        pathlib.Path(path).unlink()

    def add_completed(self, aid, company, role, url):
        payload = {"event": "completed", "applicationId": aid, "company": company,
                   "role": role, "url": url}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(payload, fh)
            path = fh.name
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "history-append", "--input", path],
                       capture_output=True)
        pathlib.Path(path).unlink()

    def test_a_submitted_role_comes_back_gated_and_hidden(self):
        self.add_completed("nw-pm", "Northwind Analytics",
                           "Product Manager, Internal Reporting",
                           "https://job-boards.greenhouse.io/northwind/jobs/4471002")
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        job = next(iter(jobs.values()))
        self.assertEqual(job["state"], "applied")
        self.assertEqual(job["score"]["gates"][0]["gate"], "duplicate")
        self.assertEqual(job["score"]["total"], 25)

    def test_a_role_already_in_watching_is_marked_watched_not_applied(self):
        # "Already on the board" and "already applied to" are different facts,
        # and claiming an application that never happened is the worse one.
        self.add_session("nw-pm", "Northwind Analytics",
                         "Product Manager, Internal Reporting", "watch")
        _, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(next(iter(jobs.values()))["state"], "watched")

    def test_a_dropped_role_is_never_offered_again(self):
        self.add_session("nw-pm", "Northwind Analytics",
                         "Product Manager, Internal Reporting", "dropped")
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(len(jobs), 0)
        self.assertTrue(any("dropped by the user" in why for why in report["gatedOutBy"]))

    def test_a_role_in_flight_is_treated_as_applied(self):
        self.add_session("nw-pm", "Northwind Analytics",
                         "Product Manager, Internal Reporting", "inflight")
        _, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(next(iter(jobs.values()))["state"], "applied")

    def test_an_unrelated_role_at_the_same_company_is_untouched(self):
        self.add_completed("nw-ba", "Northwind Analytics", "Business Analyst",
                           "https://job-boards.greenhouse.io/northwind/jobs/999")
        _, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(next(iter(jobs.values()))["state"], "new")


class TombstoneTests(IngestCase):
    def test_a_passed_job_is_never_offered_again(self):
        _, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        job_id = next(iter(jobs))
        jobs_store.mark_passed(self.root, job_id, "not for me")
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(report["kept"], 0)
        self.assertTrue(any("passed on before" in why for why in report["gatedOutBy"]))

    def test_the_tombstone_outlives_a_prune(self):
        # Without this, "never re-offered" only lasts until the next prune.
        _, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        job_id = next(iter(jobs))
        jobs_store.mark_passed(self.root, job_id, "no")
        jobs_store.save_jobs(self.root, {})
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(len(jobs), 0)

    def test_a_passed_job_is_recognised_through_a_different_url(self):
        _, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        jobs_store.mark_passed(self.root, next(iter(jobs)), "no")
        again = posting(source="adzuna", sourceClass="aggregator",
                        url="https://www.adzuna.com/land/ad/4471002")
        report, jobs = self.run_ingest({"adzuna": channel([again], name="adzuna")})
        # The record itself stays, tombstoned. What must not happen is a second
        # record arriving under the aggregator's own URL.
        self.assertEqual(report["kept"], 0)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(next(iter(jobs.values()))["state"], "passed")
        self.assertTrue(any("passed on before" in why for why in report["gatedOutBy"]))


class UnmatchedCollisionTests(IngestCase):
    """The Major review finding, end to end through the real pipeline.

    Aggregator-class postings carry no ATS URL, so their id falls back to
    company + title + country — which is exactly the tuple the matcher already
    looked at and declined. Every Tier-1b channel and every remote feed takes
    this path, so it is not a corner.
    """

    def aggregator(self, url, body, **fields):
        return posting(source="adzuna", sourceClass="aggregator", url=url,
                       description=body, **fields)

    def test_two_requisitions_that_the_matcher_declined_stay_two(self):
        first = self.aggregator("https://www.adzuna.com/land/ad/111",
                                SYNDICATED["sightings"][0]["body"])
        second = self.aggregator("https://www.adzuna.com/land/ad/222",
                                 SYNDICATED["sibling"]["body"])
        report, jobs = self.run_ingest({"adzuna": channel([first, second], name="adzuna")},
                                       config={"postedWithin": "all"})
        self.assertEqual(report["kept"], 2)
        self.assertEqual(report["deduped"], 0)
        self.assertEqual(len(jobs), 2)
        for job in jobs.values():
            self.assertEqual(len(job["sources"]), 1)

    def test_the_syndicated_trio_with_no_ats_url_still_collapses(self):
        postings = [self.aggregator(f"https://agg{i}.example/ad/{i}", s["body"],
                                    title=s["title"], company=s["company"])
                    for i, s in enumerate(SYNDICATED["sightings"])]
        report, jobs = self.run_ingest({"adzuna": channel(postings, name="adzuna")},
                                       config={"postedWithin": "all"})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(report["deduped"], 2)

    def test_thin_bodies_still_merge_and_the_matcher_owns_that_decision(self):
        # With no fingerprint on either side the veto cannot fire, so rule 3
        # matches directly. Conservative, deliberate, and unchanged — the point
        # is that the *matcher* decides it rather than the id colliding.
        thin_a = self.aggregator("https://a.example/1", "Product Manager. Apply now.")
        thin_b = self.aggregator("https://a.example/2", "Product Manager. Apply today.")
        report, jobs = self.run_ingest({"adzuna": channel([thin_a, thin_b], name="adzuna")},
                                       config={"postedWithin": "all"})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(list(report["dedupedBy"]), ["company-title-location"])

    def test_no_merge_is_ever_recorded_without_a_stated_reason(self):
        # The invariant the fix buys. `dedupedBy: {"id": N}` used to mean "the
        # ids collided and nobody agreed these were the same job", and it was
        # indistinguishable in the report from a real merge.
        postings = [self.aggregator(f"https://a.example/{i}", body)
                    for i, body in enumerate([SYNDICATED["sightings"][0]["body"],
                                              SYNDICATED["sibling"]["body"],
                                              SYNDICATED["sightings"][1]["body"],
                                              "Product Manager. Apply now."])]
        report, _ = self.run_ingest({"adzuna": channel(postings, name="adzuna")},
                                    config={"postedWithin": "all"})
        for reason in (report.get("dedupedBy") or {}):
            self.assertIn(reason, ("canonical-url", "redirect",
                                   "company-title-location", "fingerprint"), reason)

    def test_a_store_refusal_is_reported_rather_than_losing_the_run(self):
        # identity.resolve() makes the guard unreachable, so this forces it and
        # checks the run survives and says what happened.
        original = ingest.identity.resolve
        ingest.identity.resolve = lambda index, incoming, fetch=None: ("fixed-id", None)
        try:
            report, jobs = self.run_ingest(
                {"adzuna": channel([self.aggregator("https://a.example/1",
                                                    SYNDICATED["sightings"][0]["body"]),
                                    self.aggregator("https://a.example/2",
                                                    SYNDICATED["sibling"]["body"])],
                                   name="adzuna")},
                config={"postedWithin": "all"})
        finally:
            ingest.identity.resolve = original
        self.assertEqual(len(jobs), 1)
        self.assertTrue(any("declined to match" in e["why"] for e in report["errors"]))


class PruneAndPersistTests(IngestCase):
    def test_stale_jobs_are_pruned_on_the_way_out(self):
        old = posting(company="Longsince Corp", title="Office Manager",
                      url="https://job-boards.greenhouse.io/longsince/jobs/1",
                      description="An office manager role nobody applied for.",
                      posted_at=when(200))
        old["lastSeenAt"] = when(200)
        old["firstSeenAt"] = when(200)
        jobs_store.save_jobs(self.root, {"stale": {**old, "id": "stale", "state": "new"}})
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])},
                                       config={"postedWithin": "all"})
        self.assertEqual(report["pruned"], 1)
        self.assertNotIn("stale", jobs)

    def test_the_pool_survives_between_runs(self):
        self.run_ingest({"greenhouse": channel([posting()])})
        second = posting(title="Business Analyst",
                         url="https://job-boards.greenhouse.io/northwind/jobs/5",
                         description=SYNDICATED["sibling"]["body"])
        _, jobs = self.run_ingest({"greenhouse": channel([second])})
        self.assertEqual(len(jobs), 2)

    def test_a_second_sighting_updates_rather_than_duplicates(self):
        self.run_ingest({"greenhouse": channel([posting()])})
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(report["deduped"], 1)
        self.assertEqual(next(iter(jobs.values()))["seenCount"], 2)


class DefaultConfigTests(IngestCase):
    def test_running_before_any_search_is_saved_says_so(self):
        report, jobs = self.run_ingest({"greenhouse": channel([posting()])})
        self.assertTrue(report["usingDefaults"])
        self.assertEqual(report["configVersion"], 0)
        self.assertEqual(report["kept"], 1,
                         "a first manual run returning nothing reads as a broken connector")

    def test_a_saved_search_stamps_its_version_on_every_job(self):
        _, jobs = self.run_ingest({"greenhouse": channel([posting()])},
                                  config={"keywords": ["product manager"]})
        job = next(iter(jobs.values()))
        self.assertEqual(job["configVersion"], 1)
        self.assertFalse(self.meta()["usingDefaults"])


class TwoMonthCeilingTests(unittest.TestCase):
    """Nothing older than two months is ingested, kept, or shown.

    The window used to be entirely the user's call and `all` resolved to None,
    which every caller downstream read as "no limit". The ceiling binds whatever
    the config says.
    """

    def test_all_resolves_to_the_ceiling_rather_than_no_limit(self):
        days = search_config.posted_within_days({"postedWithin": "all"})
        self.assertEqual(days, search_config.MAX_AGE_DAYS)
        self.assertIsNotNone(days, "None reads as 'keep everything' downstream")

    def test_no_offered_window_reaches_past_two_months(self):
        for name in search_config.POSTED_WITHIN:
            with self.subTest(window=name):
                self.assertLessEqual(
                    search_config.posted_within_days({"postedWithin": name}),
                    search_config.MAX_AGE_DAYS)

    def test_ninety_days_is_no_longer_on_offer(self):
        self.assertNotIn("90d", search_config.POSTED_WITHIN)

    def test_a_config_saved_before_the_ceiling_still_loads(self):
        # There is a 90d config on disk in the live store. Raising on it would
        # break the board rather than narrowing it.
        out = search_config.validate(dict(search_config.DEFAULTS,
                                          postedWithin="90d"))
        self.assertEqual(out["postedWithin"], "60d")

    def test_a_posting_past_the_ceiling_is_gated_out_even_on_all(self):
        config = dict(search_config.DEFAULTS, postedWithin="all")
        stale = {"title": "Product Manager", "company": "Northwind",
                 "postedAt": "2026-05-01", "location": {"country": "US"}}
        self.assertIn("posted more than", ingest.gate(stale, config, now=NOW) or "")
        fresh = dict(stale, postedAt="2026-08-01")
        self.assertFalse(ingest.gate(fresh, config, now=NOW))

    def test_an_undated_posting_is_still_kept(self):
        # Most ATS boards publish no date at all. Dropping those would drop the
        # direct listings this lane exists to surface.
        config = dict(search_config.DEFAULTS, postedWithin="all")
        undated = {"title": "Product Manager", "company": "Northwind",
                   "location": {"country": "US"}}
        self.assertFalse(ingest.gate(undated, config, now=NOW))

    def test_retention_never_keeps_past_the_ceiling(self):
        for name in search_config.POSTED_WITHIN:
            with self.subTest(window=name):
                window = search_config.posted_within_days({"postedWithin": name})
                kept = max(window, jobs_store.NEW_MAX_AGE_DAYS)
                self.assertLessEqual(kept, search_config.MAX_AGE_DAYS)


class AddToFoundTests(IngestCase):
    """A scan fills Found. Only the eye moves a job to Watching.

    The agent sweep wrote `status: "watch"` sessions directly, so everything it
    found landed on the user's shelf without them ever seeing it in Found. 44 of
    49 live watch entries arrived that way. Sahil's rule is that a scan can only
    offer; choosing is the eye's job.
    """

    def posting(self, **fields):
        base = {"title": "Product Manager, Payments", "company": "Northwind",
                "canonicalUrl": "https://boards.greenhouse.io/northwind/jobs/99",
                "canonicalSource": "greenhouse",
                "descriptionExcerpt": "Own the payments roadmap.",
                "location": {"raw": "Chicago"}, "postedAt": "2026-08-19T00:00:00Z",
                "sources": [{"source": "greenhouse", "sourceClass": "ats",
                             "url": "https://boards.greenhouse.io/northwind/jobs/99"}]}
        base.update(fields)
        return base

    def test_an_added_posting_lands_in_found_as_new(self):
        report = ingest.add_postings(self.root, [self.posting()], now=NOW)
        self.assertEqual(report["kept"], 1)
        jobs = jobs_store.load_jobs(self.root)
        self.assertEqual([j["state"] for j in jobs.values()], ["new"])

    def test_it_creates_no_session_at_all(self):
        ingest.add_postings(self.root, [self.posting()], now=NOW)
        sessions = list((self.root / "sessions").glob("*.json"))
        self.assertEqual(sessions, [], "a scan must not put anything in Watching")

    def test_adding_the_same_posting_twice_merges(self):
        ingest.add_postings(self.root, [self.posting()], now=NOW)
        again = ingest.add_postings(self.root, [self.posting()], now=NOW)
        self.assertEqual(again["kept"], 0)
        self.assertEqual(again["deduped"], 1)
        self.assertEqual(len(jobs_store.load_jobs(self.root)), 1)

    def test_it_is_scored_by_the_same_rubric(self):
        ingest.add_postings(self.root, [self.posting()], now=NOW)
        job = list(jobs_store.load_jobs(self.root).values())[0]
        self.assertIn("score", job)
        self.assertIn("total", job["score"])
        self.assertIn("parts", job["score"])

    def test_something_the_user_passed_on_stays_passed(self):
        ingest.add_postings(self.root, [self.posting()], now=NOW)
        job_id = list(jobs_store.load_jobs(self.root))[0]
        jobs_store.mark_passed(self.root, job_id, "not for me")
        report = ingest.add_postings(self.root, [self.posting()], now=NOW)
        self.assertEqual(report["kept"], 0)
        self.assertIn("dropped by the user before", json.dumps(report))

    def test_the_gate_still_applies(self):
        stale = self.posting(postedAt="2026-01-01T00:00:00Z")
        report = ingest.add_postings(self.root, [stale], now=NOW)
        self.assertEqual(report["kept"], 0)
        self.assertEqual(report["gatedOut"], 1)


class ScanPromptTests(unittest.TestCase):
    """The prompt is the only thing standing between the sweep and Watching."""

    def prompt(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "run_scan", ROOT / "scripts" / "run_scan.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PROMPT

    def test_it_sends_findings_to_found(self):
        self.assertIn("--add", self.prompt())

    def test_it_never_asks_for_a_watch_entry(self):
        prompt = self.prompt()
        self.assertNotIn("session-save", prompt)
        self.assertNotIn('status "watch"', prompt)

    def test_it_says_so_in_words_as_well(self):
        # The instruction that matters is the one a reader acts on.
        self.assertIn("do not add", self.prompt().lower())


if __name__ == "__main__":
    unittest.main()
