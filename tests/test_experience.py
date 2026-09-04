"""Tests for scripts/experience.py, the per-role resume lift.

Forms ask for employment history with a free-text box under each entry, and the
text has to come from the resume built for that posting so the form and the
attachment agree. The failure that matters is not a crash: it is quietly
returning the wrong role's bullets, which files five years of history under the
wrong job in a real application.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "experience", ROOT / "scripts" / "experience.py"
)
experience = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experience)

# One org heading covering two roles, an entity-laden bullet, and a compressed
# entry that carries its own employer. Every shape the real resumes use.
RESUME = """
<style>.org { font-weight: bold }</style>
<div class="sec">EXPERIENCE</div>
<div class="org">CAMP Systems International Inc., Charlotte, NC<span class="when">2024 &ndash; 2026</span></div>
<div class="jobtitle">Technical Product Manager &amp; Business Analyst II (2025 &ndash; 2026)</div>
<div class="body lede">Owned the internal AI tooling.</div>
<ul>
  <li>Built the team&rsquo;s test case generator.</li>
  <li>Raised coverage from 62% to 81%.</li>
  <li>Onboarded 75 customers.</li>
</ul>
<div class="jobtitle">Product Manager &amp; Business Analyst (2024 &ndash; 2025)</div>
<div class="body lede">Supported post-merger integration.</div>
<ul>
  <li>Led adoption of LaunchDarkly.</li>
</ul>
<div class="sec">ADDITIONAL EXPERIENCE</div>
<div class="jobtitle">Summer Intern, <span style="color:#282828">Keiretsu Forum, San Francisco, CA</span></div>
<ul>
  <li>Built classification models for 250 startups.</li>
</ul>
<div class="sec">EDUCATION</div>
<div class="edu-title">Bachelor of Science</div>
"""

BARE = """
<div class="sec">EXPERIENCE</div>
<div class="org">Some Company<span class="when">2020</span></div>
<div class="jobtitle">Office Assistant</div>
<div class="sec">EDUCATION</div>
"""


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.roles = experience.parse(RESUME)

    def test_finds_every_role_and_stops_at_education(self):
        self.assertEqual(
            [r["title"] for r in self.roles],
            [
                "Technical Product Manager & Business Analyst II",
                "Product Manager & Business Analyst",
                "Summer Intern",
            ],
        )

    def test_two_roles_under_one_org_do_not_share_bullets(self):
        senior, junior = self.roles[0], self.roles[1]
        self.assertEqual(len(senior["bullets"]), 3)
        self.assertEqual(junior["bullets"], ["Led adoption of LaunchDarkly."])
        self.assertNotIn("Led adoption of LaunchDarkly.", senior["bullets"])

    def test_org_heading_carries_down_to_the_second_role(self):
        self.assertEqual(self.roles[1]["company"], "CAMP Systems International Inc., Charlotte, NC")

    def test_a_compressed_entry_keeps_its_own_employer(self):
        # The regression: with no org heading of its own it inherited whichever
        # company was listed above it.
        intern = self.roles[2]
        self.assertEqual(intern["company"], "Keiretsu Forum, San Francisco, CA")
        self.assertNotIn("CAMP", intern["company"])

    def test_bullets_attach_to_a_role_that_touches_its_list(self):
        # The jobtitle block ends exactly where its <ul> begins, so an
        # off-by-one here silently reports the role as having no content.
        self.assertEqual(
            self.roles[2]["bullets"], ["Built classification models for 250 startups."]
        )

    def test_dates_come_from_the_title_when_it_carries_them(self):
        self.assertEqual(self.roles[0]["dates"], "2025 – 2026")

    def test_entities_are_resolved(self):
        text = " ".join(self.roles[0]["bullets"]) + self.roles[0]["title"]
        for entity in ("&rsquo;", "&amp;", "&ndash;"):
            self.assertNotIn(entity, text)
        self.assertIn("team’s", self.roles[0]["bullets"][0])
        self.assertIn("&", self.roles[0]["title"])


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.roles = experience.parse(RESUME)

    def test_exact_title_wins_over_being_a_substring_of_another(self):
        # "Product Manager & Business Analyst" is one role's full title and also
        # sits inside "Technical Product Manager & Business Analyst II".
        hits = experience.pick(self.roles, "Product Manager & Business Analyst")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["dates"], "2024 – 2025")

    def test_exact_match_ignores_punctuation_and_case(self):
        hits = experience.pick(self.roles, "product manager and business analyst")
        self.assertEqual(len(hits), 1)

    def test_a_genuinely_ambiguous_fragment_returns_both(self):
        self.assertEqual(len(experience.pick(self.roles, "CAMP")), 2)

    def test_unknown_role_returns_nothing(self):
        self.assertEqual(experience.pick(self.roles, "Chief Astronaut"), [])


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.role = experience.parse(RESUME)[0]

    def test_text_form_leads_with_the_summary_then_bullets(self):
        out = experience.render(self.role)
        lines = out.splitlines()
        self.assertEqual(lines[0], "Owned the internal AI tooling.")
        self.assertTrue(all(l.startswith("- ") for l in lines[1:]))

    def test_prose_form_has_no_bullet_markers(self):
        out = experience.render(self.role, "prose")
        self.assertNotIn("\n", out)
        self.assertNotIn("- ", out)

    def test_cap_drops_whole_bullets_rather_than_halving_a_sentence(self):
        out = experience.render(self.role, "text", max_chars=90)
        self.assertLessEqual(len(out), 90)
        for line in out.splitlines()[1:]:
            self.assertTrue(line.rstrip().endswith("."), line)

    def test_cap_above_the_content_changes_nothing(self):
        self.assertEqual(
            experience.render(self.role, "text", max_chars=10000),
            experience.render(self.role),
        )

    def test_a_role_with_no_description_renders_empty(self):
        # The caller turns this into an explicit instruction rather than
        # writing a blank box.
        bare = experience.parse(BARE)[0]
        self.assertEqual(experience.render(bare), "")


class PlanTests(unittest.TestCase):
    """Every position, not just the newest.

    Forms that take employment history almost always let you add another entry,
    and runs were filling the first and moving on. That leaves a candidate who
    looks like he has held one job, next to an attached resume listing six.
    """

    HISTORY = [
        {"title": "Technical Product Manager & Business Analyst II",
         "company": "CAMP Systems International Inc.", "city": "Charlotte, NC",
         "start": "2025-08", "end": "2026-06-06"},
        {"title": "Product Manager & Business Analyst",
         "company": "CAMP Systems International Inc.", "city": "Charlotte, NC",
         "start": "2024-05", "end": "2025-08"},
    ]

    def plan_with(self, history):
        original = experience.work_history
        experience.work_history = lambda root=None: history
        try:
            return experience.plan(RESUME)
        finally:
            experience.work_history = original

    def plan(self):
        return self.plan_with(self.HISTORY)

    def test_every_role_in_the_resume_becomes_a_position(self):
        # Three in the fixture, including the one with no profile entry.
        self.assertEqual(len(self.plan()), 3)

    def test_positions_come_back_newest_first(self):
        starts = [(p["startDate"] or {}).get("text") or "" for p in self.plan()]
        self.assertEqual(starts, sorted(starts, reverse=True))
        self.assertEqual([p["order"] for p in self.plan()], [1, 2, 3])

    def test_dates_are_month_level_from_the_profile_not_years_from_the_resume(self):
        senior = [p for p in self.plan()
                  if p["title"].startswith("Technical Product Manager")][0]
        self.assertEqual(senior["startDate"],
                         {"year": 2025, "month": 8, "text": "2025-08"})
        self.assertEqual(senior["endDate"],
                         {"year": 2026, "month": 6, "text": "2026-06"})
        self.assertTrue(senior["datesFromProfile"])

    def test_a_job_with_no_end_date_is_marked_current(self):
        history = [dict(self.HISTORY[0], end=None), self.HISTORY[1]]
        current = [p for p in self.plan_with(history)
                   if p["title"].startswith("Technical Product Manager")][0]
        self.assertTrue(current["isCurrent"])
        self.assertIsNone(current["endDate"])

    def test_a_role_with_no_profile_entry_is_still_offered(self):
        # The compressed "Additional Experience" entry. Dropping it would be the
        # bug this whole command exists to fix, one entry smaller.
        intern = [p for p in self.plan() if p["title"] == "Summer Intern"]
        self.assertEqual(len(intern), 1)
        self.assertFalse(intern[0]["datesFromProfile"])

    def test_each_position_carries_its_own_description(self):
        descriptions = [p["description"] for p in self.plan() if p["description"]]
        self.assertGreater(len(descriptions), 1)
        self.assertEqual(len(set(descriptions)), len(descriptions),
                         "two positions must not share a description")

    def test_the_location_comes_from_the_profile(self):
        for position in self.plan():
            if position["datesFromProfile"]:
                self.assertEqual(position["location"], "Charlotte, NC")
