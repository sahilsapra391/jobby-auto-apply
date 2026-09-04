"""Tests for the match rubric.

Two properties carry the whole design and both are asserted here rather than
assumed: the parts always sum to the raw total, so the "why this %" panel on a
card is arithmetic rather than decoration; and an unknown attribute takes half
its weight rather than zero, so a posting is never punished for being vague
about something Phase 1 does not extract anyway.

The gate boundaries get the same treatment as the answer-matching suite: what
must NOT fire matters as much as what must. A clearance gate that fires on "no
security clearance is required" caps a perfectly good role at 25 and tells the
user the reason is clearance.

Run from the repo root: python3 -m pytest tests/ -q
"""

import importlib.util
import pathlib
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


match_score = load("match_score")

CONFIG = {
    "keywords": ["product manager", "business analyst"],
    "locations": [{"country": "US"}, {"country": "IN"}],
    "workplace": ["remote", "hybrid", "onsite"],
    "maxExperienceYears": 5,
    "searchScope": "title+description",
}


def job(**fields):
    base = {
        "title": "Product Manager",
        "descriptionExcerpt": "We are hiring a product manager to own the roadmap.",
        "location": {"raw": "New York, NY", "city": "New York", "region": "NY", "country": "US"},
        "workplace": "hybrid",
        "postedAt": (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "firstSeenAt": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extracted": {"yearsMax": None, "degree": None,
                      "employmentType": None, "roleCategory": None},
    }
    base.update(fields)
    return base


def scored(config=None, **fields):
    return match_score.score(job(**fields), config if config is not None else CONFIG, now=NOW)


class ArithmeticTests(unittest.TestCase):
    def test_the_weights_are_a_hundred_and_live_in_one_place(self):
        self.assertEqual(sum(match_score.WEIGHTS.values()), 100)
        self.assertEqual(match_score.WEIGHTS,
                         {"title": 35, "experience": 20, "location": 15,
                          "keywords": 20, "freshness": 10})

    def test_the_parts_always_sum_to_the_raw_total(self):
        # The card shows these as bars beside the number. If they do not add
        # up, the explanation is not an explanation.
        for case in (
            {}, {"title": "Chief Dog Officer"}, {"workplace": None},
            {"location": {"raw": "", "country": None}}, {"postedAt": None},
            {"extracted": {"yearsMax": 9}}, {"descriptionExcerpt": ""},
        ):
            result = scored(**case)
            self.assertEqual(sum(result["parts"].values()), result["raw"], case)

    def test_the_total_is_the_raw_score_when_nothing_gates(self):
        result = scored()
        self.assertEqual(result["total"], result["raw"])
        self.assertEqual(result["gates"], [])

    def test_no_component_can_exceed_its_weight(self):
        for case in ({}, {"title": "Product Manager Business Analyst"},
                     {"extracted": {"yearsMax": 0}}):
            result = scored(**case)
            for name, points in result["parts"].items():
                self.assertLessEqual(points, match_score.WEIGHTS[name], (name, case))
                self.assertGreaterEqual(points, 0)

    def test_scoring_is_deterministic(self):
        self.assertEqual(scored(), scored())

    def test_the_config_version_travels_with_the_score(self):
        result = match_score.score(job(), {**CONFIG, "version": 7}, now=NOW)
        self.assertEqual(result["configVersion"], 7)


class UnknownNeutralityTests(unittest.TestCase):
    def test_an_unstated_experience_requirement_takes_half_its_weight(self):
        # Scoring it zero would rank every posting that states requirements
        # below every posting that does not, which is a fact about the prose.
        result = scored(extracted={"yearsMax": None})
        self.assertEqual(result["parts"]["experience"], 10)
        self.assertIn("experience", result["unknowns"])

    def test_an_unstated_workplace_is_neutral_and_named(self):
        result = scored(workplace=None)
        self.assertIn("workplace", result["unknowns"])
        self.assertLess(result["parts"]["location"], match_score.WEIGHTS["location"])
        self.assertGreater(result["parts"]["location"], 0)

    def test_a_missing_posted_date_falls_back_to_the_sighting_at_half_weight(self):
        # The sighting date is evidence about when the scan ran, not about when
        # the posting went up, so it is worth half.
        dated = scored()
        undated = scored(postedAt=None)
        self.assertIn("postedAt", undated["unknowns"])
        self.assertEqual(undated["parts"]["freshness"], round(dated["parts"]["freshness"] / 2))

    def test_a_job_with_nothing_known_still_scores_in_the_middle(self):
        result = scored(workplace=None, postedAt=None, firstSeenAt=None,
                        location={"raw": "", "country": None},
                        extracted={"yearsMax": None})
        self.assertGreater(result["total"], 25)
        self.assertLess(result["total"], 75)

    def test_a_search_with_no_keywords_scores_the_word_components_neutral(self):
        # This is what a manual ingest before any saved search looks like. A
        # first run returning nothing would read like a broken connector.
        result = match_score.score(job(), {}, now=NOW)
        self.assertEqual(result["parts"]["title"], round(match_score.WEIGHTS["title"] * 0.5))
        self.assertEqual(result["parts"]["keywords"], round(match_score.WEIGHTS["keywords"] * 0.5))
        self.assertIn("title", result["unknowns"])
        self.assertGreaterEqual(result["total"], 40)


class ComponentTests(unittest.TestCase):
    def test_a_matching_title_takes_the_full_weight(self):
        self.assertEqual(scored(title="Product Manager")["parts"]["title"], 35)

    def test_a_title_does_not_have_to_be_worded_exactly(self):
        # Stemming handles the plural, and extra words around a searched title
        # do not reduce the match: "Senior Product Manager, Platform" is one.
        self.assertEqual(scored(title="Product Managers")["parts"]["title"], 35)
        self.assertEqual(scored(title="Senior Product Manager, Platform")["parts"]["title"], 35)

    def test_an_unrelated_title_scores_nothing(self):
        self.assertEqual(scored(title="Warehouse Operative")["parts"]["title"], 0)

    def test_open_to_all_experience_satisfies_the_component(self):
        result = match_score.score(job(extracted={"yearsMax": 12}),
                                   {**CONFIG, "maxExperienceYears": None}, now=NOW)
        self.assertEqual(result["parts"]["experience"], 20)
        self.assertNotIn("experience", result["unknowns"])

    def test_experience_inside_the_ceiling_is_full_marks(self):
        self.assertEqual(scored(extracted={"yearsMax": 5})["parts"]["experience"], 20)

    def test_overshooting_degrades_rather_than_cliff_edges(self):
        one_over = scored(extracted={"yearsMax": 6})["parts"]["experience"]
        three_over = scored(extracted={"yearsMax": 8})["parts"]["experience"]
        far_over = scored(extracted={"yearsMax": 12})["parts"]["experience"]
        self.assertEqual((one_over, three_over, far_over), (10, 5, 0))

    def test_a_country_out_of_scope_costs_the_location_component(self):
        out = scored(location={"raw": "Berlin", "country": "DE"})
        self.assertLess(out["parts"]["location"], scored()["parts"]["location"])

    def test_remote_is_in_scope_wherever_it_is(self):
        # includeRemoteForSelection is on by default; a remote role in a country
        # nobody selected is still a role you can take.
        result = scored(workplace="remote", location={"raw": "Remote", "country": "DE"})
        self.assertEqual(result["parts"]["location"], 15)

    def test_an_unwanted_arrangement_costs_only_its_share(self):
        result = match_score.score(job(workplace="onsite"),
                                   {**CONFIG, "workplace": ["remote"]}, now=NOW)
        self.assertGreater(result["parts"]["location"], 0)
        self.assertLess(result["parts"]["location"], 15)

    def test_title_only_scope_ignores_the_body(self):
        body = "product manager business analyst product manager business analyst"
        wide = match_score.score(job(title="Analyst", descriptionExcerpt=body),
                                 CONFIG, now=NOW)
        narrow = match_score.score(job(title="Analyst", descriptionExcerpt=body),
                                   {**CONFIG, "searchScope": "title"}, now=NOW)
        self.assertGreater(wide["parts"]["keywords"], narrow["parts"]["keywords"])

    def test_freshness_runs_from_full_to_nothing_over_a_month(self):
        today = scored(postedAt=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        old = scored(postedAt=(NOW - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        middle = scored(postedAt=(NOW - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(today["parts"]["freshness"], 10)
        self.assertEqual(old["parts"]["freshness"], 0)
        self.assertTrue(0 < middle["parts"]["freshness"] < 10)

    def test_a_future_posted_date_is_not_worth_more_than_today(self):
        ahead = scored(postedAt=(NOW + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(ahead["parts"]["freshness"], 10)


class GateTests(unittest.TestCase):
    def test_a_gate_caps_rather_than_drops(self):
        # The role stays on the board with the reason attached. "Not shown" and
        # "shown and explained" are very different things to read.
        clean = scored()
        gated = scored(descriptionExcerpt="US citizenship is required for this role.")
        self.assertGreaterEqual(clean["total"], 75)
        self.assertEqual(gated["total"], match_score.GATE_CAP)
        self.assertGreater(gated["raw"], match_score.GATE_CAP)
        self.assertEqual(gated["gates"][0]["gate"], "citizenship")

    def test_the_gate_says_what_it_saw(self):
        gated = scored(descriptionExcerpt="Active security clearance required.")
        self.assertIn("clearance", gated["gates"][0]["label"])
        self.assertTrue(gated["gates"][0]["evidence"])

    def test_the_clearance_phrases_that_should_fire(self):
        for text in ("Must be a US citizen.",
                     "U.S. citizenship required.",
                     "US citizens only, no exceptions.",
                     "Requires an active TS/SCI clearance.",
                     "Must be able to obtain a security clearance.",
                     "This position requires a Public Trust clearance."):
            self.assertTrue(match_score.gates(job(descriptionExcerpt=text)), text)

    def test_the_phrases_that_must_not_fire(self):
        # Every one of these appears in ordinary postings, and gating on them
        # would cap good roles at 25 with a reason that is not true.
        for text in ("No security clearance is required for this role.",
                     "A security clearance is not required.",
                     "This role does not require a security clearance.",
                     "We sponsor visas for exceptional candidates.",
                     "Background check required before start.",
                     "We are a US-based company with a distributed team.",
                     "You will work with our citizen developer community.",
                     "Top secret sauce: we ship on Fridays.",
                     ""):
            self.assertEqual(match_score.gates(job(descriptionExcerpt=text)), [], text)

    def test_liveness_and_duplicate_gates_come_from_ingest(self):
        result = match_score.score(job(), CONFIG, now=NOW,
                                   flags={"duplicateOfApplied": True,
                                          "duplicateReason": "already submitted"})
        self.assertEqual(result["total"], match_score.GATE_CAP)
        self.assertEqual(result["gates"][0]["gate"], "duplicate")
        dead = match_score.score(job(), CONFIG, now=NOW,
                                 flags={"livenessFailed": True, "livenessReason": "404"})
        self.assertEqual(dead["gates"][0]["gate"], "liveness")

    def test_several_gates_are_all_reported(self):
        result = match_score.score(job(descriptionExcerpt="US citizenship required."),
                                   CONFIG, now=NOW, flags={"duplicateOfApplied": True})
        self.assertEqual(sorted(g["gate"] for g in result["gates"]),
                         ["citizenship", "duplicate"])

    def test_a_gate_never_takes_a_low_score_higher(self):
        low = match_score.score(job(title="Warehouse Operative", descriptionExcerpt=
                                    "US citizenship required. Warehouse work."),
                                CONFIG, now=NOW)
        self.assertLessEqual(low["total"], match_score.GATE_CAP)
        self.assertEqual(low["total"], min(low["raw"], match_score.GATE_CAP))


class FreshnessWordingTests(unittest.TestCase):
    """The rubric explanation is read by a person. "1 days ago" is not English.

    Wording only. The number it reads from is the number freshness is scored on,
    and that arithmetic is deliberately untouched: it went through the gate, and
    rounding a day differently would move scores.
    """

    def test_the_awkward_counts_read_as_words(self):
        self.assertEqual(match_score.when_words(0), "today")
        self.assertEqual(match_score.when_words(0.4), "today")
        self.assertEqual(match_score.when_words(1), "yesterday")
        self.assertEqual(match_score.when_words(1.2), "yesterday")

    def test_everything_else_still_counts_days(self):
        self.assertEqual(match_score.when_words(2), "2 days ago")
        self.assertEqual(match_score.when_words(30.6), "31 days ago")

    def test_no_explanation_ever_says_one_days(self):
        # The whole string, not a substring: "11 days ago" contains "1 days
        # ago" and is perfectly good English.
        bad = {"0 days ago", "1 days ago", "-1 days ago"}
        for age in range(0, 120):
            with self.subTest(age=age):
                self.assertNotIn(match_score.when_words(age), bad)
