"""Tests for the discovery stores: jobs, tombstones, the saved search, the
company registry and the source catalog.

The boundaries matter as much as the happy paths here, and for the same reason
they do in the answer store: a rule that is slightly too loose does something
wrong silently. A prune that eats a watched role, a config that accepts four
sweeps a day, a seeder that invents an ATS token off a LinkedIn URL — none of
those raise anything. They just quietly make the system wrong.

Run from the repo root: python3 -m pytest tests/ -q
"""

import copy
import inspect
import re
import importlib.util
import json
import pathlib
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jobs_store = load("jobs_store")
search_config = load("search_config")
seed_companies = load("seed_companies")
sources_registry = load("sources_registry")


def sighting(source, url, source_class, seen="2026-08-19T20:00:00Z"):
    return {"source": source, "sourceClass": source_class, "tier": "1a",
            "url": url, "firstSeenAt": seen}


def job(job_id="j1", **fields):
    base = jobs_store.blank_job(job_id, fields.pop("company", "Acme"),
                                fields.pop("title", "Product Manager"))
    base.update(fields)
    return base


class TempStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class JobSchemaTests(unittest.TestCase):
    def test_every_field_in_the_schema_exists_from_the_start(self):
        # Absent and unknown are different, and the board draws an explicit
        # `unknown` chip. A missing key cannot be told apart from a renderer
        # that forgot the field.
        record = jobs_store.blank_job("abc", "Acme", "Product Manager")
        for field in ("id", "company", "title", "normTitle", "canonicalUrl",
                      "canonicalSource", "sources", "location", "workplace",
                      "postedAt", "firstSeenAt", "lastSeenAt", "seenCount", "lang",
                      "descriptionExcerpt", "descFingerprint", "tags", "salary",
                      "extracted", "industry", "score", "configVersion", "state"):
            self.assertIn(field, record)
        self.assertEqual(record["state"], "new")
        self.assertEqual(record["normTitle"], "product manager")

    def test_norm_title_collapses_punctuation_and_case(self):
        self.assertEqual(jobs_store.norm_title("Manager, Internal Product Analytics"),
                         "manager internal product analytics")
        self.assertEqual(jobs_store.norm_title("Sr. Product Manager (AI/ML)"),
                         "sr product manager ai ml")


class UpsertAndMergeTests(unittest.TestCase):
    def test_a_new_id_creates_and_a_known_id_merges(self):
        jobs = {}
        jobs, action = jobs_store.upsert(jobs, job("j1"))
        self.assertEqual(action, "created")
        jobs, action = jobs_store.upsert(jobs, job("j1"))
        self.assertEqual(action, "merged")
        self.assertEqual(len(jobs), 1)

    def test_the_same_sighting_twice_does_not_grow_the_board_count(self):
        # "seen on 3 boards" has to mean three boards. Re-fetching Greenhouse
        # every hour must not inflate it to twenty-four by tomorrow.
        one = job("j1", sources=[sighting("greenhouse", "https://gh/x", "ats")])
        jobs, _ = jobs_store.upsert({}, one)
        for _ in range(5):
            jobs, _ = jobs_store.upsert(jobs, job("j1", sources=[
                sighting("greenhouse", "https://gh/x", "ats")]))
        self.assertEqual(len(jobs["j1"]["sources"]), 1)
        self.assertEqual(jobs["j1"]["seenCount"], 6)

    def test_a_channel_with_per_request_urls_does_not_grow_a_sighting_an_hour(self):
        # Careerjet answers with a fresh tracking redirect every time it is
        # asked — verified by running one query twice a second apart and
        # getting three different URLs for three unchanged jobs. Keyed on the
        # URL, that appends a sighting every hour, and by tomorrow the job
        # claims to have been seen on twenty-four boards.
        unstable = [dict(sighting("careerjet", f"https://jobviewtrack.com/v2/tok{i}",
                                  "aggregator", seen=f"2026-08-{19 + i:02d}T00:00:00Z"),
                         urlStable=False) for i in range(4)]
        jobs = {}
        for entry in unstable:
            jobs, _ = jobs_store.upsert(jobs, job("j1", sources=[entry]))
        held = jobs["j1"]["sources"]
        self.assertEqual(len(held), 1, "one channel, one sighting")
        self.assertEqual(held[0]["url"], "https://jobviewtrack.com/v2/tok3",
                         "the newest URL is the one that still resolves")
        self.assertEqual(held[0]["firstSeenAt"], "2026-08-19T00:00:00Z",
                         "but it was first seen when it was first seen")

    def test_an_unstable_channel_does_not_swallow_a_stable_one(self):
        jobs, _ = jobs_store.upsert({}, job("j1", sources=[
            dict(sighting("careerjet", "https://jobviewtrack.com/v2/a", "aggregator"),
                 urlStable=False)]))
        jobs, _ = jobs_store.upsert(jobs, job("j1", sources=[
            sighting("greenhouse", "https://gh/x", "ats")]))
        self.assertEqual(sorted(s["source"] for s in jobs["j1"]["sources"]),
                         ["careerjet", "greenhouse"])

    def test_three_boards_one_job_keeps_three_sightings(self):
        jobs, _ = jobs_store.upsert({}, job("j1", sources=[
            sighting("adzuna", "https://adzuna/x", "aggregator")]))
        jobs, _ = jobs_store.upsert(jobs, job("j1", sources=[
            sighting("greenhouse", "https://gh/x", "ats")]))
        jobs, _ = jobs_store.upsert(jobs, job("j1", sources=[
            sighting("linkedin", "https://li/x", "aggregator")]))
        self.assertEqual(len(jobs["j1"]["sources"]), 3)

    def test_the_ats_sighting_becomes_canonical_over_an_aggregator(self):
        # Applying through the aggregator's copy means a redirect chain and a
        # posting that may already be stale.
        jobs, _ = jobs_store.upsert({}, job("j1", sources=[
            sighting("adzuna", "https://adzuna/x", "aggregator")]))
        jobs, _ = jobs_store.upsert(jobs, job("j1", sources=[
            sighting("greenhouse", "https://gh/x", "ats")]))
        self.assertEqual(jobs["j1"]["canonicalSource"], "greenhouse")
        self.assertEqual(jobs["j1"]["canonicalUrl"], "https://gh/x")

    def test_an_aggregator_never_overwrites_the_ats_title(self):
        ats = job("j1", company="Datadog", title="Manager, Internal Product Analytics",
                  sources=[sighting("greenhouse", "https://gh/x", "ats")])
        agg = job("j1", company="Datadog Inc", title="Manager Internal Product Analytics - NY",
                  sources=[sighting("adzuna", "https://adzuna/x", "aggregator")])
        merged = jobs_store.merge_records(ats, agg)
        self.assertEqual(merged["title"], "Manager, Internal Product Analytics")
        self.assertEqual(merged["company"], "Datadog")

    def test_the_ats_record_does_overwrite_an_aggregator_one(self):
        agg = job("j1", company="Datadog Inc", title="Manager Internal Product Analytics - NY",
                  sources=[sighting("adzuna", "https://adzuna/x", "aggregator")])
        ats = job("j1", company="Datadog", title="Manager, Internal Product Analytics",
                  sources=[sighting("greenhouse", "https://gh/x", "ats")])
        merged = jobs_store.merge_records(agg, ats)
        self.assertEqual(merged["company"], "Datadog")
        self.assertEqual(merged["normTitle"], "manager internal product analytics")

    def test_the_fuller_description_survives_a_truncated_one(self):
        full = job("j1", descriptionExcerpt="x" * 1500, descFingerprint="simhash64:aaa")
        stub = job("j1", descriptionExcerpt="x" * 180, descFingerprint="simhash64:bbb")
        self.assertEqual(len(jobs_store.merge_records(full, stub)["descriptionExcerpt"]), 1500)
        self.assertEqual(jobs_store.merge_records(full, stub)["descFingerprint"], "simhash64:aaa")
        self.assertEqual(len(jobs_store.merge_records(stub, full)["descriptionExcerpt"]), 1500)

    def test_the_earliest_posted_date_wins(self):
        # A board that lists a syndicated copy the day it picked it up would
        # otherwise make a three-week-old posting look like it appeared today.
        old = job("j1", postedAt="2026-07-28T00:00:00Z")
        new = job("j1", postedAt="2026-08-19T00:00:00Z")
        self.assertEqual(jobs_store.merge_records(old, new)["postedAt"], "2026-07-28T00:00:00Z")
        self.assertEqual(jobs_store.merge_records(new, old)["postedAt"], "2026-07-28T00:00:00Z")

    def test_last_seen_moves_forward_and_first_seen_does_not(self):
        a = job("j1", firstSeenAt="2026-08-01T00:00:00Z", lastSeenAt="2026-08-01T00:00:00Z")
        b = job("j1", firstSeenAt="2026-08-19T00:00:00Z", lastSeenAt="2026-08-19T00:00:00Z")
        merged = jobs_store.merge_records(a, b)
        self.assertEqual(merged["firstSeenAt"], "2026-08-01T00:00:00Z")
        self.assertEqual(merged["lastSeenAt"], "2026-08-19T00:00:00Z")

    def test_a_null_posted_date_does_not_erase_a_known_one(self):
        known = job("j1", postedAt="2026-08-13T00:00:00Z")
        unknown = job("j1", postedAt=None)
        self.assertEqual(jobs_store.merge_records(known, unknown)["postedAt"],
                         "2026-08-13T00:00:00Z")

    def test_merging_never_moves_the_state_the_user_set(self):
        watched = job("j1", state="watched")
        merged = jobs_store.merge_records(watched, job("j1", state="new"))
        self.assertEqual(merged["state"], "watched")

    def test_the_store_refuses_to_merge_what_the_matcher_declined(self):
        # The defensive half of the fix. identity.resolve() makes this
        # unreachable; if a future caller bypasses it, this refuses loudly
        # rather than folding two requisitions into one where nobody sees it.
        jobs, _ = jobs_store.upsert({}, job("j1"), matched=False)
        with self.assertRaises(jobs_store.JobsStoreError) as ctx:
            jobs_store.upsert(jobs, job("j1"), match_id="j1", matched=False)
        self.assertIn("declined to match", str(ctx.exception))

    def test_a_matched_job_still_merges(self):
        jobs, _ = jobs_store.upsert({}, job("j1"), matched=False)
        jobs, action = jobs_store.upsert(jobs, job("j1"), match_id="j1", matched=True)
        self.assertEqual(action, "merged")

    def test_an_unstated_verdict_keeps_the_old_behaviour(self):
        # Callers that do their own matching are not forced to change.
        jobs, _ = jobs_store.upsert({}, job("j1"))
        jobs, action = jobs_store.upsert(jobs, job("j1"))
        self.assertEqual(action, "merged")

    def test_a_job_without_an_id_is_refused(self):
        with self.assertRaises(jobs_store.JobsStoreError):
            jobs_store.upsert({}, {"company": "Acme"})


class StateTransitionTests(unittest.TestCase):
    def test_the_ordinary_moves_are_allowed(self):
        for start, end in (("new", "watched"), ("new", "passed"), ("new", "applied"),
                           ("watched", "applied"), ("watched", "passed"),
                           ("expired", "new")):
            self.assertEqual(jobs_store.transition(job(state=start), end)["state"], end)

    def test_passing_is_final(self):
        # The whole promise of the tombstone. If ingest could move a passed job
        # back to new, "never re-offered" would be untrue by the next hour.
        for target in ("new", "watched", "applied", "expired"):
            with self.assertRaises(jobs_store.JobsStoreError):
                jobs_store.transition(job(state="passed"), target)

    def test_applying_is_final(self):
        for target in ("new", "watched", "passed"):
            with self.assertRaises(jobs_store.JobsStoreError):
                jobs_store.transition(job(state="applied"), target)

    def test_a_watched_job_cannot_be_demoted_to_new(self):
        with self.assertRaises(jobs_store.JobsStoreError):
            jobs_store.transition(job(state="watched"), "new")

    def test_an_unknown_state_is_refused(self):
        with self.assertRaises(jobs_store.JobsStoreError):
            jobs_store.transition(job(), "interested")

    def test_moving_to_the_state_it_is_already_in_is_a_no_op(self):
        self.assertEqual(jobs_store.transition(job(state="watched"), "watched")["state"],
                         "watched")


class TombstoneTests(TempStore):
    def test_passing_writes_a_tombstone_and_the_state(self):
        jobs_store.save_jobs(self.root, {"j1": job("j1", company="Acme")})
        result = jobs_store.mark_passed(self.root, "j1", reason="wrong seniority")
        self.assertEqual(result["passed"], "j1")
        self.assertEqual(jobs_store.load_jobs(self.root)["j1"]["state"], "passed")
        tomb = jobs_store.load_passed(self.root)["j1"]
        self.assertEqual(tomb["company"], "Acme")
        self.assertEqual(tomb["reason"], "wrong seniority")
        self.assertTrue(tomb["passedAt"])

    def test_passing_twice_is_idempotent(self):
        jobs_store.save_jobs(self.root, {"j1": job("j1")})
        jobs_store.mark_passed(self.root, "j1")
        first = jobs_store.load_passed(self.root)["j1"]["passedAt"]
        jobs_store.mark_passed(self.root, "j1")
        self.assertEqual(jobs_store.load_passed(self.root)["j1"]["passedAt"], first)

    def test_the_tombstone_outlives_the_job_record(self):
        # Pruning must not be able to resurrect something the user said no to.
        jobs_store.save_jobs(self.root, {"j1": job("j1")})
        jobs_store.mark_passed(self.root, "j1")
        jobs_store.save_jobs(self.root, {})
        self.assertIn("j1", jobs_store.load_passed(self.root))
        self.assertEqual(jobs_store.mark_passed(self.root, "j1")["alreadyPassed"], True)

    def test_passing_an_unknown_job_says_so_rather_than_inventing_one(self):
        with self.assertRaises(jobs_store.JobsStoreError):
            jobs_store.mark_passed(self.root, "never-existed")

    def test_the_eye_is_idempotent(self):
        # The board re-sends remembered clicks on every load, so a second 👁 on
        # the same job must not create a second session.
        jobs_store.save_jobs(self.root, {"j1": job("j1")})
        self.assertNotIn("alreadyWatched", jobs_store.mark_watched(self.root, "j1"))
        self.assertTrue(jobs_store.mark_watched(self.root, "j1")["alreadyWatched"])


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 19, tzinfo=timezone.utc)

    def stamp(self, days_ago):
        return (self.now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_stale_unacted_on_jobs_are_dropped(self):
        jobs = {"old": job("old", lastSeenAt=self.stamp(45)),
                "fresh": job("fresh", lastSeenAt=self.stamp(2))}
        jobs, dropped = jobs_store.prune(jobs, at=self.now)
        self.assertEqual(sorted(jobs), ["fresh"])
        self.assertEqual(dropped[0]["id"], "old")

    def test_a_decision_is_never_pruned_by_age(self):
        # Watched, applied and passed are the user acting. A retention rule that
        # eats those is deleting their work, not tidying up.
        jobs = {state: job(state, state=state, lastSeenAt=self.stamp(400))
                for state in ("watched", "applied", "passed")}
        jobs, dropped = jobs_store.prune(jobs, at=self.now)
        self.assertEqual(len(jobs), 3)
        self.assertEqual(dropped, [])

    def test_the_cap_drops_the_lowest_scoring_first(self):
        jobs = {f"j{i}": job(f"j{i}", score={"total": i}, lastSeenAt=self.stamp(1))
                for i in range(10)}
        jobs, dropped = jobs_store.prune(jobs, cap=4, at=self.now)
        self.assertEqual(len(jobs), 4)
        self.assertEqual(sorted(jobs), ["j6", "j7", "j8", "j9"])
        self.assertEqual(len(dropped), 6)

    def test_the_cap_never_evicts_a_decision(self):
        jobs = {"kept": job("kept", state="watched", score={"total": 1},
                            lastSeenAt=self.stamp(1))}
        jobs.update({f"j{i}": job(f"j{i}", score={"total": 90}, lastSeenAt=self.stamp(1))
                     for i in range(5)})
        jobs, _ = jobs_store.prune(jobs, cap=3, at=self.now)
        self.assertIn("kept", jobs)

    def test_the_cap_keeps_direct_listings_over_aggregator_copies(self):
        # Measured, not theorised. A full sweep brought in 1,482 postings from
        # company ATS boards and 5,300 aggregator copies; sorting on score
        # alone kept 3,000 of which only 214 were direct. Aggregator snippets
        # are short and keyword-dense, so they out-score real job descriptions
        # and evict the records that link to the employer's own form.
        jobs = {}
        for i in range(20):
            jobs[f"agg{i}"] = job(f"agg{i}", score={"total": 95},
                                  lastSeenAt=self.stamp(1),
                                  sources=[{"source": "careerjet",
                                            "sourceClass": "aggregator",
                                            "url": f"https://c/{i}"}])
        for i in range(5):
            jobs[f"ats{i}"] = job(f"ats{i}", score={"total": 60},
                                  lastSeenAt=self.stamp(1),
                                  sources=[{"source": "greenhouse", "sourceClass": "ats",
                                            "url": f"https://gh/{i}"}])
        jobs, _ = jobs_store.prune(jobs, cap=5, at=self.now)
        self.assertEqual(sorted(jobs), ["ats0", "ats1", "ats2", "ats3", "ats4"],
                         "every direct listing survives before any aggregator copy")

    def test_within_direct_listings_the_better_match_still_wins(self):
        jobs = {f"j{i}": job(f"j{i}", score={"total": i * 10}, lastSeenAt=self.stamp(1),
                             sources=[{"source": "greenhouse", "sourceClass": "ats",
                                       "url": f"https://gh/{i}"}])
                for i in range(6)}
        jobs, _ = jobs_store.prune(jobs, cap=2, at=self.now)
        self.assertEqual(sorted(jobs), ["j4", "j5"])

    def test_a_careers_page_is_kept_like_an_ats_listing(self):
        jobs = {"agg": job("agg", score={"total": 99}, lastSeenAt=self.stamp(1),
                           sources=[{"source": "adzuna", "sourceClass": "aggregator",
                                     "url": "https://a/1"}]),
                "careers": job("careers", score={"total": 41}, lastSeenAt=self.stamp(1),
                               sources=[{"source": "acme", "sourceClass": "careers",
                                         "url": "https://acme.com/1"}])}
        jobs, _ = jobs_store.prune(jobs, cap=1, at=self.now)
        self.assertEqual(sorted(jobs), ["careers"])

    def test_an_unscored_job_loses_to_a_scored_one(self):
        jobs = {"scored": job("scored", score={"total": 10}, lastSeenAt=self.stamp(1)),
                "unscored": job("unscored", lastSeenAt=self.stamp(1))}
        jobs, _ = jobs_store.prune(jobs, cap=1, at=self.now)
        self.assertEqual(sorted(jobs), ["scored"])

    def test_no_cap_means_no_cap(self):
        # The user's call, and a real one: the pool is a single JSON file
        # rewritten whole on every ingest, so it is the store design rather
        # than the number that eventually bites. Measured: 50,000 jobs is a
        # 150 MB file, 1.6s to save, 0.4s to load, 539 MB peak.
        jobs = {f"j{i}": job(f"j{i}", score={"total": i}, lastSeenAt=self.stamp(1))
                for i in range(50)}
        kept, dropped = jobs_store.prune(jobs, cap=None, at=self.now)
        self.assertEqual(len(kept), 50)
        self.assertEqual(dropped, [])

    def test_the_staleness_window_can_be_widened(self):
        jobs = {"old": job("old", lastSeenAt=self.stamp(80)),
                "ancient": job("ancient", lastSeenAt=self.stamp(200))}
        kept, _ = jobs_store.prune(jobs, cap=None, max_age_days=90, at=self.now)
        self.assertEqual(sorted(kept), ["old"], "80 days survives a 90-day window")

    def test_the_agreed_volume_knobs_are_what_ships(self):
        self.assertEqual(jobs_store.STORED_CAP, 3000)
        self.assertEqual(jobs_store.NEW_MAX_AGE_DAYS, 30)


class ReadFailureTests(TempStore):
    def test_a_corrupt_pool_raises_rather_than_reading_as_empty(self):
        # §13 item 8. `json.loads(stdout or "[]")` turned every failure into a
        # convincing empty result; an empty Found lane over a real one is the
        # same bug wearing different clothes.
        (self.root / "jobs.json").write_text("{not json at all")
        with self.assertRaises(jobs_store.JobsStoreError):
            jobs_store.load_jobs(self.root)

    def test_a_pool_of_the_wrong_shape_raises(self):
        (self.root / "jobs.json").write_text("[]")
        with self.assertRaises(jobs_store.JobsStoreError):
            jobs_store.load_jobs(self.root)

    def test_an_absent_pool_is_genuinely_empty(self):
        self.assertEqual(jobs_store.load_jobs(self.root), {})

    def test_the_pool_is_written_owner_only(self):
        # The store is mode 700 and its contents name companies and roles.
        jobs_store.save_jobs(self.root, {"j1": job("j1")})
        mode = stat.S_IMODE((self.root / "jobs.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_every_new_writer_uses_the_shared_atomic_writer(self):
        # §13 item 7: a shared `<name>.tmp` let one writer truncate another's
        # half-written file, and the replace then moved the half-written file
        # into place. Every writer added here goes through the one mkstemp-based
        # writer rather than growing its own.
        seen = []
        original = jobs_store.store.write_private

        def spy(path, payload):
            seen.append(pathlib.Path(path).name)
            return original(path, payload)

        jobs_store.store.write_private = spy
        search_config.store.write_private = spy
        try:
            jobs_store.save_jobs(self.root, {})
            jobs_store.save_passed(self.root, {})
            jobs_store.save_companies(self.root, {})
            search_config.save(self.root, {})
        finally:
            jobs_store.store.write_private = original
            search_config.store.write_private = original
        self.assertEqual(seen, ["jobs.json", "passed.json", "companies.json",
                                "search-config.json"])
        self.assertIn("tempfile.mkstemp", (ROOT / "scripts" / "store.py").read_text())


class SearchConfigTests(TempStore):
    def test_the_defaults_carry_nobody_s_search(self):
        # This file is public. A default config naming one person's titles and
        # countries would ship their job search in the repository.
        self.assertEqual(search_config.DEFAULTS["keywords"], [])
        self.assertEqual(search_config.DEFAULTS["locations"], [])
        self.assertEqual(search_config.DEFAULTS["roleCategories"], [])
        self.assertIsNone(search_config.DEFAULTS["maxExperienceYears"])

    def test_the_agreed_volume_knob_is_the_default(self):
        self.assertEqual(search_config.DEFAULTS["minScoreToKeep"], 40)

    def test_no_saved_config_reads_as_none_not_as_defaults(self):
        # The hourly scheduler arms only once a search exists. Returning the
        # defaults here would arm it against nobody's search.
        self.assertIsNone(search_config.load(self.root))

    def test_saving_bumps_the_version_every_time(self):
        first = search_config.save(self.root, {"keywords": ["product manager"]})
        second = search_config.save(self.root, {"keywords": ["product manager"]})
        self.assertEqual(second["version"], first["version"] + 1)

    def test_a_saved_config_is_written_owner_only(self):
        search_config.save(self.root, {})
        self.assertEqual(stat.S_IMODE(search_config.config_path(self.root).stat().st_mode),
                         0o600)

    def test_fewer_than_six_sweeps_a_day_is_refused(self):
        # Sweeps rotate through six channel packs. Five a day leaves one pack
        # unswept for a whole day while the board still claims coverage.
        for bad in (0, 1, 5, "6", 6.5, True):
            with self.assertRaises(search_config.ConfigError):
                search_config.validate({"tier2SweepsPerDay": bad})
        self.assertEqual(search_config.validate({"tier2SweepsPerDay": 6})["tier2SweepsPerDay"], 6)
        self.assertEqual(search_config.validate({"tier2SweepsPerDay": 12})["tier2SweepsPerDay"], 12)

    def test_a_too_frequent_tier_one_interval_is_refused(self):
        with self.assertRaises(search_config.ConfigError):
            search_config.validate({"tier1IntervalMinutes": 1})

    def test_region_packs_expand_and_deduplicate(self):
        config = search_config.validate({"locations": [
            {"regionPack": "EU"}, {"country": "DE"}, {"country": "us"}]})
        countries = search_config.expand_locations(config)
        self.assertIn("DE", countries)
        self.assertIn("US", countries)
        self.assertEqual(len(countries), len(set(countries)))
        self.assertEqual(countries, sorted(countries))

    def test_the_packs_cover_the_regions_the_plan_names(self):
        self.assertEqual(set(search_config.REGION_PACKS["MIDDLE_EAST"]),
                         {"AE", "SA", "QA", "KW", "BH", "OM", "JO", "EG"})
        self.assertEqual(set(search_config.REGION_PACKS["SEA"]),
                         {"SG", "MY", "PH", "ID", "TH", "VN"})
        self.assertIn("GB", search_config.REGION_PACKS["EU"])

    def test_an_unknown_region_pack_is_refused_rather_than_ignored(self):
        # Ignoring it would silently narrow every scan to nothing and look
        # exactly like the connectors having gone quiet.
        with self.assertRaises(search_config.ConfigError):
            search_config.validate({"locations": [{"regionPack": "ANTARCTICA"}]})

    def test_a_country_must_be_a_two_letter_code(self):
        for bad in ("United States", "USA", "u", ""):
            with self.assertRaises(search_config.ConfigError):
                search_config.validate({"locations": [{"country": bad}]})
        self.assertEqual(search_config.validate(
            {"locations": [{"country": "in"}]})["locations"], [{"country": "IN"}])

    def test_an_empty_location_list_means_everywhere(self):
        self.assertEqual(search_config.expand_locations(search_config.validate({})), [])

    def test_the_date_window_vocabulary_is_closed(self):
        with self.assertRaises(search_config.ConfigError):
            search_config.validate({"postedWithin": "3d"})
        self.assertEqual(search_config.posted_within_days({"postedWithin": "24h"}), 1)
        # `all` used to be None, meaning no limit. It is the ceiling now.
        self.assertEqual(search_config.posted_within_days({"postedWithin": "all"}),
                         search_config.MAX_AGE_DAYS)

    def test_the_longer_windows_exist(self):
        # A dated posting older than two months is not worth bringing in; an
        # undated one still is, which is what includeUnknowns.postedAt governs.
        # 90d was the old ceiling and is no longer offered; a config that still
        # carries it is read as 60d rather than rejected.
        self.assertEqual(search_config.posted_within_days({"postedWithin": "60d"}), 60)
        self.assertEqual(
            search_config.validate(dict(search_config.DEFAULTS,
                                        postedWithin="90d"))["postedWithin"], "60d")
        self.assertEqual(search_config.posted_within_days({"postedWithin": "60d"}), 60)

    def test_the_stored_cap_can_be_turned_off_but_not_set_absurdly_low(self):
        self.assertIsNone(search_config.validate({"storedCap": None})["storedCap"])
        self.assertEqual(search_config.validate({"storedCap": 100000})["storedCap"], 100000)
        for bad in (5, 0, -1, "lots", 3.5):
            with self.assertRaises(search_config.ConfigError):
                search_config.validate({"storedCap": bad})

    def test_workplace_values_are_checked(self):
        with self.assertRaises(search_config.ConfigError):
            search_config.validate({"workplace": ["anywhere"]})
        self.assertEqual(search_config.validate({"workplace": []})["workplace"],
                         list(search_config.WORKPLACES))

    def test_open_to_all_experience_is_null_not_a_large_number(self):
        self.assertIsNone(search_config.validate({"maxExperienceYears": None})["maxExperienceYears"])
        with self.assertRaises(search_config.ConfigError):
            search_config.validate({"maxExperienceYears": -1})
        self.assertEqual(search_config.validate({"maxExperienceYears": 5.0})["maxExperienceYears"], 5)

    def test_a_score_floor_outside_zero_to_a_hundred_is_refused(self):
        for bad in (-1, 101, "40", None):
            with self.assertRaises(search_config.ConfigError):
                search_config.validate({"minScoreToKeep": bad})

    def test_companies_mode_and_degree_are_closed_vocabularies(self):
        with self.assertRaises(search_config.ConfigError):
            search_config.validate({"companiesMode": "onlythese"})
        with self.assertRaises(search_config.ConfigError):
            search_config.validate({"degreeMax": "phd"})
        self.assertEqual(search_config.validate({"degreeMax": "masters"})["degreeMax"], "masters")

    def test_include_unknowns_defaults_to_showing_them(self):
        # Hiding unknowns by default would silently drop most of a fresh scan,
        # since Phase 1 extracts none of these attributes yet.
        unknowns = search_config.validate({})["includeUnknowns"]
        self.assertEqual(unknowns, {k: True for k in search_config.UNKNOWN_KEYS})

    def test_a_field_the_board_added_later_survives_a_round_trip(self):
        saved = search_config.save(self.root, {"futureFilter": ["x"]})
        self.assertEqual(saved["futureFilter"], ["x"])
        self.assertEqual(search_config.load(self.root)["futureFilter"], ["x"])

    def test_a_corrupt_config_raises_rather_than_reading_as_unsaved(self):
        search_config.config_path(self.root).write_text("{oops")
        with self.assertRaises(search_config.ConfigError):
            search_config.load(self.root)


class CompanyRegistryTests(unittest.TestCase):
    def test_board_tokens_are_read_off_the_url(self):
        cases = [
            ("https://job-boards.greenhouse.io/anthropic/jobs/5247640008", "greenhouse", "anthropic"),
            ("https://boards.greenhouse.io/datadog", "greenhouse", "datadog"),
            ("https://boards.eu.greenhouse.io/someco/jobs/1", "greenhouse", "someco"),
            ("https://jobs.lever.co/airslate/5e6dec30", "lever", "airslate"),
            ("https://jobs.ashbyhq.com/applied/84cd980d/application", "ashby", "applied"),
            ("https://jobs.smartrecruiters.com/IFS1/744000140278967", "smartrecruiters", "IFS1"),
            ("https://apply.workable.com/innovaccer-analytics/", "workable", "innovaccer-analytics"),
            ("https://consultdss.recruitee.com/o/ai-product-owner", "recruitee", "consultdss"),
            ("https://wfglobal.zohorecruit.com/jobs/Careers/574752000028891183", "zoho", "wfglobal"),
            ("https://geico.wd1.myworkdayjobs.com/External", "workday", "geico"),
            ("https://career.teamtailor.com/jobs/123-product", "teamtailor", "career"),
            ("https://workwithus.pinpointhq.com/en/postings/abc", "pinpoint", "workwithus"),
            ("https://focuskpi.applytojob.com/apply/jobs/details/abc", "jazzhr", "focuskpi"),
            ("https://jobs.gem.com/lumalabs-ai/abc", "gem", "lumalabs-ai"),
            ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
             "oracle_orc", "CX_1"),
        ]
        for url, ats, token in cases:
            self.assertEqual(seed_companies.ats_from_url(url), (ats, token), url)

    def test_an_aggregator_url_never_becomes_a_board_token(self):
        # This is the one that matters. "linkedin.com/jobs/view/..." yielding
        # a token would point every future fetch at a board that is not theirs.
        for url in ("https://www.linkedin.com/jobs/view/agent-pm-at-actively-4407859175",
                    "https://in.linkedin.com/jobs/view/x",
                    "https://www.google.com/about/careers/applications/jobs/results/1",
                    "https://www.instahyre.com/job-402799-senior-product-manager",
                    "https://www.indeed.com/viewjob?jk=abc"):
            self.assertEqual(seed_companies.ats_from_url(url), (None, None), url)

    def test_a_workable_job_url_never_becomes_the_j_tenant(self):
        self.assertEqual(
            seed_companies.ats_from_url("https://apply.workable.com/j/A9A35765A4"),
            (None, None))

    def test_verified_pack_repairs_a_bad_learned_workable_token(self):
        learned = seed_companies.blank_company("Kahoot", "NO", "postings")
        learned.update({"ats": "workable", "token": "j"})
        registry, _summary = seed_companies.seed(
            {"kahoot": learned}, seed_companies.from_pack())
        self.assertEqual((registry["kahoot"]["ats"], registry["kahoot"]["token"]),
                         ("workable", "kahoot"))

    def test_unresolvable_workable_j_token_is_removed_visibly(self):
        learned = seed_companies.blank_company("Unknown Tenant", "US", "postings")
        learned.update({"ats": "workable", "token": "j"})
        registry, summary = seed_companies.seed({"unknown tenant": learned}, [])
        self.assertEqual((registry["unknown tenant"]["ats"],
                          registry["unknown tenant"]["token"]), ("unknown", None))
        self.assertIn("invalid tenant", registry["unknown tenant"]["lastStatus"])
        self.assertEqual(summary["invalidTokensDropped"], 1)

    def test_learned_evidence_cannot_replace_a_verified_tenant(self):
        verified = seed_companies.blank_company("Kahoot", "NO", "pack")
        verified.update({"ats": "workable", "token": "kahoot"})
        learned = seed_companies.blank_company("Kahoot", "NO", "postings")
        learned.update({"ats": "workable", "token": "j"})
        merged = seed_companies.merge_company(verified, learned)
        self.assertEqual((merged["ats"], merged["token"]), ("workable", "kahoot"))

    def test_verified_pack_replaces_a_stale_learned_careers_url(self):
        learned = seed_companies.blank_company("Acronis", "CH", "roles")
        learned.update({
            "ats": "workday", "token": "acronis",
            "careersUrl": "https://acronis.wd3.myworkdayjobs.com/Acronis",
        })
        registry, _summary = seed_companies.seed(
            {"acronis": learned}, seed_companies.from_pack())
        self.assertEqual(
            registry["acronis"]["careersUrl"],
            "https://acronis.wd502.myworkdayjobs.com/acronis_careers")

    def test_a_normalized_direct_sighting_can_grow_the_registry(self):
        rows = seed_companies.from_postings([{
            "company": "Acme", "canonicalUrl": "https://jobs.lever.co/acme/123",
            "sources": [], "location": {"country": "US"},
        }])
        self.assertEqual((rows[0]["ats"], rows[0]["token"]), ("lever", "acme"))

    def test_an_aggregator_sighting_never_invents_a_tenant(self):
        rows = seed_companies.from_postings([{
            "company": "Acme", "canonicalUrl": "https://www.linkedin.com/jobs/view/1",
            "sources": [], "location": {"country": "US"},
        }])
        self.assertEqual(rows, [])

    def test_a_company_careers_page_yields_a_url_but_no_token(self):
        self.assertEqual(seed_companies.ats_from_url("https://careers.withwaymo.com/jobs"),
                         (None, None))
        self.assertEqual(seed_companies.careers_url("https://careers.withwaymo.com/jobs"),
                         "https://careers.withwaymo.com")
        self.assertEqual(seed_companies.careers_url("https://www.linkedin.com/jobs/view/x"), "")

    def test_the_registry_key_strips_legal_furniture(self):
        self.assertEqual(seed_companies.company_key("Datadog, Inc."), "datadog")
        self.assertEqual(seed_companies.company_key("Acme Technologies Pvt Ltd"), "acme")
        self.assertEqual(seed_companies.company_key("Anthropic"), "anthropic")
        self.assertEqual(seed_companies.company_key("Trade Republic"), "trade republic")

    def test_aliases_carry_the_spellings_an_aggregator_uses(self):
        aliases = seed_companies.aliases_for("Trade Republic, Inc.", "traderepublic")
        for expected in ("trade republic", "traderepublic", "trade republic inc"):
            self.assertIn(expected, aliases)

    def test_a_later_sighting_never_downgrades_a_known_board(self):
        known = seed_companies.blank_company("Acme")
        known.update({"ats": "greenhouse", "token": "acme"})
        vague = seed_companies.blank_company("Acme")
        vague["careersUrl"] = "https://acme.com"
        merged = seed_companies.merge_company(known, vague)
        self.assertEqual(merged["ats"], "greenhouse")
        self.assertEqual(merged["token"], "acme")
        self.assertEqual(merged["careersUrl"], "https://acme.com")

    def test_seeding_from_roles_derives_boards_and_leaves_the_rest_unknown(self):
        roles = {
            "a": {"company": "Anthropic", "region": "US",
                  "url": "https://job-boards.greenhouse.io/anthropic/jobs/1"},
            "b": {"company": "Waymo", "region": "US",
                  "url": "https://careers.withwaymo.com/jobs"},
            "c": {"company": "Actively", "region": "US",
                  "url": "https://www.linkedin.com/jobs/view/x"},
        }
        registry, summary = seed_companies.seed({}, seed_companies.from_roles(roles))
        self.assertEqual(registry["anthropic"]["ats"], "greenhouse")
        self.assertEqual(registry["anthropic"]["token"], "anthropic")
        self.assertEqual(registry["waymo"]["ats"], "unknown")
        self.assertEqual(registry["waymo"]["careersUrl"], "https://careers.withwaymo.com")
        self.assertEqual(registry["actively"]["ats"], "unknown")
        self.assertEqual(registry["actively"]["careersUrl"], "")
        self.assertEqual(summary["awaitingAtsResolution"], 2)

    def test_host_dependent_boards_keep_their_exact_public_origin(self):
        roles = {
            "w": {"company": "Example W", "region": "US",
                  "url": "https://example.wd1.myworkdayjobs.com/en-US/External/job/1"},
            "o": {"company": "Example O", "region": "US",
                  "url": "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/job/1"},
        }
        records = {r["name"]: r for r in seed_companies.from_roles(roles)}
        self.assertEqual(records["Example W"]["careersUrl"],
                         "https://example.wd1.myworkdayjobs.com/External")
        self.assertEqual(records["Example O"]["careersUrl"],
                         "https://example.fa.us2.oraclecloud.com")

    def test_verified_host_dependent_seeds_keep_their_public_origins(self):
        records = {r["name"]: r for r in seed_companies.from_pack()}
        self.assertEqual(records["GEICO"]["careersUrl"],
                         "https://geico.wd1.myworkdayjobs.com/External")
        self.assertEqual(records["Oracle"]["careersUrl"],
                         "https://eeho.fa.us2.oraclecloud.com")

    def test_the_new_direct_connectors_each_have_a_fetchable_seed(self):
        records = seed_companies.from_pack()
        for ats in ("workday", "pinpoint", "teamtailor", "jazzhr",
                    "oracle_orc", "gem"):
            candidates = [r for r in records if r["ats"] == ats and r["token"]]
            self.assertTrue(candidates, ats)
            if ats in ("workday", "oracle_orc"):
                self.assertTrue(any(r["careersUrl"] for r in candidates), ats)

    def test_verified_seed_repairs_a_token_only_workday_record(self):
        existing = seed_companies.blank_company("GEICO", "US", "postings")
        existing.update({"ats": "workday", "token": "geico", "careersUrl": ""})
        registry, _summary = seed_companies.seed(
            {"geico": existing}, seed_companies.from_pack())
        self.assertEqual(registry["geico"]["careersUrl"],
                         "https://geico.wd1.myworkdayjobs.com/External")

    def test_seeding_is_idempotent(self):
        pack = seed_companies.from_pack()
        first, _ = seed_companies.seed({}, pack)
        second, summary = seed_companies.seed(first, pack)
        self.assertEqual(len(first), len(second))
        self.assertEqual(summary["added"], 0)

    def test_the_curated_pack_is_verified_and_unambiguous(self):
        seen = set()
        for name, ats, token, region in seed_companies.SEED_PACK:
            self.assertIn(ats, seed_companies.TIER1_PLATFORMS, name)
            self.assertTrue(token, name)
            self.assertNotIn((ats, token), seen, f"{name} duplicates a board")
            seen.add((ats, token))
            # "unknown" is a legal value and an honest one: the pack is built
            # from live verification, not from a country lookup, and inventing
            # a headquarters for an entry with no evidence would be worse.
            self.assertTrue(region == "unknown" or (len(region) == 2 and region.isupper()),
                            f"{name}: {region!r}")

    def test_most_of_the_pack_has_a_known_region(self):
        regions = [r for _, _, _, r in seed_companies.SEED_PACK]
        unknown = sum(1 for r in regions if r == "unknown")
        self.assertLess(unknown / len(regions), 0.25,
                        "region is informational, but mostly-unknown would make it useless")

    def test_the_pack_reaches_every_region_the_plan_names(self):
        # US, India, Canada, Australia, Europe, Middle East, Japan, Korea, SEA.
        regions = {region for _, _, _, region in seed_companies.SEED_PACK}
        for country in ("US", "IN", "CA", "AU", "JP", "KR", "AE"):
            self.assertIn(country, regions)
        self.assertTrue(regions & set(search_config.REGION_PACKS["EU"]))
        self.assertTrue(regions & set(search_config.REGION_PACKS["SEA"]))

    def test_free_text_regions_normalise_without_guessing(self):
        self.assertEqual(seed_companies.normalise_region("Canada"), "CA")
        self.assertEqual(seed_companies.normalise_region("UK"), "GB")
        self.assertEqual(seed_companies.normalise_region("EMEA (region-restric"), "unknown")
        self.assertEqual(seed_companies.normalise_region(""), "unknown")


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.doc = sources_registry.load()

    def test_the_catalog_loads_and_every_id_is_unique(self):
        ids = [s["id"] for s in self.doc["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 50)

    def test_every_channel_holds_one_of_the_six_statuses(self):
        for entry in self.doc["sources"]:
            self.assertIn(entry["status"], sources_registry.STATUSES, entry["id"])

    def test_an_exclusion_without_a_reason_is_refused(self):
        # An unexplained exclusion is how coverage gets quietly lost.
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"sources": [{"id": "x", "status": "excluded", "statusReason": ""}]}, fh)
            path = fh.name
        try:
            with self.assertRaises(sources_registry.RegistryError):
                sources_registry.load(path)
        finally:
            pathlib.Path(path).unlink()

    def test_every_blocked_and_excluded_channel_names_its_reason(self):
        for entry in self.doc["sources"]:
            if entry["status"] in ("blocked", "excluded"):
                self.assertTrue(entry.get("statusReason"), entry["id"])

    def test_the_channels_the_plan_puts_in_phase_one_are_all_catalogued(self):
        catalogued = sources_registry.by_id(self.doc)
        for sid in ("greenhouse", "lever", "ashby", "remoteok", "weworkremotely",
                    "remotive", "himalayas", "adzuna", "jooble", "careerjet",
                    "reed", "usajobs", "themuse"):
            self.assertIn(sid, catalogued)
            self.assertEqual(catalogued[sid]["phase"], 1, sid)

    def test_the_auth_walled_japanese_boards_are_documented_not_missing(self):
        catalogued = sources_registry.by_id(self.doc)
        for sid in ("bizreach", "rikunabi", "doda"):
            self.assertEqual(catalogued[sid]["status"], "blocked")
            self.assertIn("auth", catalogued[sid]["statusReason"])

    def test_the_markdown_matches_the_catalog(self):
        # Two files maintained by hand drift; one generated from the other does
        # not. This is the test that keeps that true.
        self.assertTrue(sources_registry.check(self.doc),
                        "run: python3 scripts/sources_registry.py render")

    def test_a_pipe_in_a_note_cannot_break_the_table(self):
        self.assertEqual(sources_registry._cell("a|b"), "a\\|b")


class PostedCeilingPruneTests(unittest.TestCase):
    """Retention has to judge on the same field the gate judged on.

    The gate refuses a posting older than the ceiling on `postedAt`. Retention
    measured staleness on `lastSeenAt` — how long since *we* looked — so a
    February posting first seen yesterday was refused at the door and kept
    forever once it was already inside. 1,253 of them were sitting in the live
    store, 12% of everything dated.
    """

    NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)

    def job(self, job_id, **fields):
        base = {"id": job_id, "state": "new", "company": "Northwind",
                "title": "Product Manager",
                "firstSeenAt": "2026-08-19T00:00:00Z",
                "lastSeenAt": "2026-08-19T00:00:00Z"}
        base.update(fields)
        return base

    def test_a_posting_older_than_the_ceiling_is_dropped_even_if_just_seen(self):
        jobs = {"old": self.job("old", postedAt="2026-02-01T00:00:00Z")}
        kept, dropped = jobs_store.prune(jobs, cap=None, at=self.NOW,
                                         posted_ceiling_days=60)
        self.assertEqual(kept, {})
        self.assertIn("posted over 60 days ago", dropped[0]["why"])

    def test_a_recent_posting_survives(self):
        jobs = {"new": self.job("new", postedAt="2026-08-01T00:00:00Z")}
        kept, _ = jobs_store.prune(jobs, cap=None, at=self.NOW, posted_ceiling_days=60)
        self.assertEqual(list(kept), ["new"])

    def test_an_undated_posting_survives(self):
        # Most ATS boards publish no date. Dropping these would drop the direct
        # listings the lane exists to surface.
        jobs = {"undated": self.job("undated")}
        kept, _ = jobs_store.prune(jobs, cap=None, at=self.NOW, posted_ceiling_days=60)
        self.assertEqual(list(kept), ["undated"])

    def test_a_decision_the_user_made_is_never_dropped(self):
        # Watched, applied and passed are decisions. The ceiling does not get to
        # eat them just because the posting aged.
        jobs = {state: self.job(state, state=state, postedAt="2026-01-01T00:00:00Z")
                for state in ("watched", "applied", "passed")}
        kept, dropped = jobs_store.prune(jobs, cap=None, at=self.NOW,
                                         posted_ceiling_days=60)
        self.assertEqual(sorted(kept), ["applied", "passed", "watched"])
        self.assertEqual(dropped, [])

    def test_without_a_ceiling_nothing_changes(self):
        jobs = {"old": self.job("old", postedAt="2020-01-01T00:00:00Z")}
        kept, _ = jobs_store.prune(jobs, cap=None, at=self.NOW)
        self.assertEqual(list(kept), ["old"])

    def test_the_ingest_passes_the_ceiling_through(self):
        # The gate and retention must judge on the same number. Reading it off
        # search_config in one place and hardcoding it in the other is how they
        # drift apart again.
        source = (ROOT / "scripts" / "ingest.py").read_text()
        self.assertIn("posted_ceiling_days=search_config.MAX_AGE_DAYS", source)


class LocationBackfillTests(unittest.TestCase):
    """A parser fix has to reach records that are already stored.

    Conditional requests mean an unchanged board is never re-fetched, so its
    jobs are never re-parsed. When the parser learned to read "Chicago", 938
    live records kept the empty country they were saved with and went on
    answering a search for every country.
    """

    def test_a_blank_country_is_filled_from_the_stored_string(self):
        jobs = {"a": {"id": "a", "location": {"raw": "Chicago", "country": None}}}
        jobs, filled = jobs_store.refresh_locations(jobs)
        self.assertEqual(filled, 1)
        self.assertEqual(jobs["a"]["location"]["country"], "US")

    def test_a_country_already_on_the_record_is_never_second_guessed(self):
        # It was read from this same string once. Rewriting it would let a
        # parser regression quietly reshape the store.
        jobs = {"a": {"id": "a", "location": {"raw": "Chicago", "country": "CA"}}}
        jobs, filled = jobs_store.refresh_locations(jobs)
        self.assertEqual(filled, 0)
        self.assertEqual(jobs["a"]["location"]["country"], "CA")

    def test_a_string_the_parser_still_cannot_read_is_left_alone(self):
        jobs = {"a": {"id": "a", "location": {"raw": "London", "country": None}},
                "b": {"id": "b", "location": {"raw": "", "country": None}},
                "c": {"id": "c"}}
        jobs, filled = jobs_store.refresh_locations(jobs)
        self.assertEqual(filled, 0)
        self.assertIsNone(jobs["a"]["location"]["country"])

    def test_a_record_with_a_country_comes_out_byte_identical(self):
        # This runs unattended over every record, six times a day. The guard
        # has to be that nothing with an answer is touched, not that the new
        # answer usually agrees.
        jobs = {
            "agrees": {"id": "agrees", "company": "A", "state": "new",
                       "location": {"raw": "Chicago", "country": "US",
                                    "city": "Chicago", "region": "IL"}},
            "disagrees": {"id": "disagrees", "company": "B", "state": "new",
                          "location": {"raw": "Chicago", "country": "CA",
                                       "city": "Chicago", "region": None}},
            "nonsense": {"id": "nonsense", "company": "C", "state": "applied",
                         "location": {"raw": "Berlin", "country": "ZZ"}},
        }
        before = copy.deepcopy(jobs)
        jobs, filled = jobs_store.refresh_locations(jobs)
        self.assertEqual(filled, 0)
        self.assertEqual(jobs, before)

    def test_filling_a_blank_touches_nothing_but_the_country(self):
        jobs = {"a": {"id": "a", "company": "A", "state": "new", "score": {"total": 80},
                      "sources": [{"source": "greenhouse"}],
                      "location": {"raw": "Chicago", "country": None,
                                   "city": "Chicago", "region": "IL"}}}
        before = copy.deepcopy(jobs)
        jobs, filled = jobs_store.refresh_locations(jobs)
        self.assertEqual(filled, 1)
        self.assertEqual(jobs["a"]["location"]["country"], "US")
        # everything else, including the rest of the location object
        before["a"]["location"]["country"] = "US"
        self.assertEqual(jobs, before)

    def test_a_record_with_no_location_gains_no_keys(self):
        jobs = {"a": {"id": "a", "state": "new"}}
        before = copy.deepcopy(jobs)
        jobs, filled = jobs_store.refresh_locations(jobs)
        self.assertEqual(filled, 0)
        self.assertEqual(jobs, before)

    def test_the_only_key_it_ever_assigns_is_country(self):
        # Structural, because the loop is short and the claim is absolute.
        source = inspect.getsource(jobs_store.refresh_locations)
        assignments = re.findall(r'location\[([^\]]+)\]\s*=', source)
        self.assertEqual(assignments, ['"country"'])

    def test_every_state_is_left_alone_the_same_way(self):
        # A watched or applied record is the user's decision. This function has
        # no business knowing about state, so it must treat them identically.
        jobs = {state: {"id": state, "state": state,
                        "location": {"raw": "Chicago", "country": None}}
                for state in ("new", "watched", "applied", "passed")}
        jobs, filled = jobs_store.refresh_locations(jobs)
        self.assertEqual(filled, 4)
        for state in jobs:
            with self.subTest(state=state):
                self.assertEqual(jobs[state]["location"]["country"], "US")
                self.assertEqual(jobs[state]["state"], state)

    def test_the_ingest_runs_it_before_retention(self):
        # A record that just gained a country should be counted under it.
        source = (ROOT / "scripts" / "ingest.py").read_text()
        self.assertIn("refresh_locations", source)
        self.assertLess(source.index("refresh_locations"), source.index("jobs_store.prune"))


if __name__ == "__main__":
    unittest.main()
