"""The employer extraction rule, branch by branch, and the queue it feeds.

**Every refusal branch is exercised against a case taken from the corpus**,
because a branch with no instances is a branch nobody has verified, and two of
these had zero instances on the Appcast slice they were written for. They fire
elsewhere in the pool: `intermediary` 11 times, `disagreement` 24,
`enumeration` 26, all measured 2026-09-06 over 23,636 records.

The two narrowings at the end are the ones constraint 3 bought. Running the
rule against employers whose *domain* is wrong found it confidently proposing a
different *name* for two of them, which is exactly the shape that had to be
caught before the rule touched anything.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import employer_extract as ee   # noqa: E402
import employer_resolve as er   # noqa: E402


def record(text, legacy="x"):
    return {"id": legacy, "descriptionExcerpt": text, "company": "Appcast Enterprise"}


class TheFramesExtract(unittest.TestCase):
    def test_at_name_comma(self):
        p = ee.propose(record("Introduction At Artex, we reimagine what risk "
                              "management can be and we do it with bold "
                              "thinking."))
        self.assertFalse(p.refused)
        self.assertEqual(p.name, "Artex")
        self.assertEqual(p.confidence, "high")
        self.assertIn("at", p.frames)

    def test_welcome_to(self):
        p = ee.propose(record("Introduction Welcome to Gallagher - a global "
                              "community of people who bring bold ideas."))
        self.assertEqual(p.name, "Gallagher")

    def test_is_looking_for(self):
        p = ee.propose(record("Description Cleveland House is looking to "
                              "recruit a Chef to join the team."))
        self.assertEqual(p.name, "Cleveland House")

    def test_the_evidence_travels_with_the_proposal(self):
        """A proposal a reader cannot check is one they will confirm anyway."""
        p = ee.propose(record("Introduction At Artex, we reimagine risk."))
        self.assertIn("Artex", p.phrase)
        self.assertIsInstance(p.offset, int)
        self.assertEqual(sorted(p.as_dict()),
                         ["confidence", "frames", "id", "name", "offset", "phrase"])

    def test_a_frame_late_in_the_text_is_medium_not_high(self):
        p = ee.propose(record("x" * 400 + " Join Halfords and help us keep "
                              "the nation moving."))
        self.assertEqual(p.confidence, "medium")


class TheRefusalsAreTheInterestingOutput(unittest.TestCase):
    def test_no_frame_carries_the_opening_so_the_list_is_readable(self):
        p = ee.propose(record("Retail Advisor Salary: 13.66 per hour Working "
                              "hours: 20 Location: Inverness"))
        self.assertTrue(p.refused)
        self.assertEqual(p.reason, "no_frame")
        self.assertIn("Retail Advisor Salary", p.detail)

    def test_an_agency_is_refused_rather_than_filed_under(self):
        """The michaelpage check. Filing a posting under the recruiter swaps
        one wrong employer for another while looking like progress."""
        p = ee.propose(record("Join Michael Page and take the next step. "
                              "Michael Page is recruiting for a leading firm."))
        self.assertTrue(p.refused)
        self.assertEqual(p.reason, "intermediary")
        self.assertIn("Michael Page", p.candidates)

    def test_the_feed_publisher_is_an_intermediary_too(self):
        p = ee.propose(record("At Appcast Enterprise, we deliver recruitment "
                              "advertising at scale."))
        self.assertEqual(p.reason, "intermediary")

    def test_two_employers_refuse_rather_than_pick_one(self):
        p = ee.propose(record("At Choreograph, we build data products. "
                              "Join WPP Media to shape the future."))
        self.assertTrue(p.refused)
        self.assertEqual(p.reason, "disagreement")
        self.assertEqual(p.candidates, ["Choreograph", "WPP Media"])

    def test_one_company_named_twice_is_not_a_disagreement(self):
        """"Gallagher" and "Gallagher Bassett" in one description is a company
        naming itself twice, and refusing that pair would throw away the
        clearest cases in the corpus."""
        p = ee.propose(record("Welcome to Gallagher. Join Gallagher Bassett "
                              "and help us serve clients."))
        self.assertFalse(p.refused)
        self.assertEqual(p.name, "Gallagher Bassett")

    def test_a_job_title_ending_in_the_company_is_the_same_company(self):
        """Measured: pairs like ("Capital One", "Cyber Product Owner Capital
        One") were 8 of the pool's 30 disagreements under a prefix-only test."""
        self.assertTrue(ee._same_company("Capital One",
                                         "Cyber Product Owner Capital One"))
        self.assertTrue(ee._same_company("AVA", "AVA Technologies"))
        self.assertFalse(ee._same_company("One", "Prospa"))

    def test_a_phrase_is_not_a_name(self):
        for text in ("Join Us and grow.", "About Us we are a team.",
                     "About The Role We're looking for cleaners."):
            with self.subTest(text=text):
                self.assertTrue(ee.propose(record(text)).refused)

    def test_a_description_that_is_empty_says_so(self):
        self.assertEqual(ee.propose({"id": "x"}).reason, "no_description")


class TheTwoNarrowingsConstraintThreeBought(unittest.TestCase):
    def test_a_list_of_brands_is_not_a_self_introduction(self):
        """Real, and it was **high** confidence: "At Doordash, Deliveroo and
        Wolt, we're building" made the rule propose Doordash for a Deliveroo
        posting."""
        p = ee.propose(record("About the team At Doordash, Deliveroo and Wolt, "
                              "we're building the future of delivery."))
        self.assertTrue(p.refused)
        self.assertEqual(p.reason, "enumeration")
        self.assertIn("Doordash", p.candidates)

    def test_a_frame_may_not_cross_a_sentence_boundary(self):
        """Real: "in Bengaluru. We are seeking an Engineering Manager" put a
        city into the hiring frame for a Toast posting."""
        p = ee.propose(record("Join the payments team in Bengaluru. We are "
                              "seeking an Engineering Manager."))
        self.assertTrue(p.refused)

    def test_an_interior_period_is_still_a_name(self):
        """The narrowing above must not refuse Warner Bros. Discovery."""
        p = ee.propose(record("Join Warner Bros. Discovery and tell stories."))
        self.assertFalse(p.refused)
        self.assertIn("Warner Bros", p.name)

    def test_a_banner_above_a_name_is_dropped_not_kept(self):
        p = ee.propose(record("Clearance Level: DV UNLEASH YOUR POTENTIAL "
                              "Leidos is seeking a Test Automation Engineer."))
        self.assertFalse(p.refused)
        self.assertEqual(p.name, "Leidos")

    def test_an_ampersand_joins_a_name_rather_than_ending_it(self):
        p = ee.propose(record("So who are we? We are Pipe & Climate Centre, "
                              "part of the Wolseley Group."))
        self.assertEqual(p.name, "Pipe & Climate Centre")


class TheQueueIsWorkingStateAndSaysSo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

    def test_an_unreadable_queue_is_not_an_empty_one(self):
        """Applying against a queue nobody can read would be acting on a set
        nobody can see, which is the failure this whole flow exists to avoid."""
        path = er.queue_path(self.root.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            er.read_queue(self.root.name)
        self.assertIn("unreadable", str(caught.exception))

    def test_a_missing_queue_reads_empty_because_nothing_was_proposed(self):
        self.assertEqual(er.read_queue(self.root.name),
                         {"proposals": [], "refusals": [], "confirmed": []})

    def test_the_decided_by_value_records_that_a_person_confirmed(self):
        """A later rule that decides alone must be distinguishable from this
        one after the fact, and it will be: it will write a different value
        into the same column."""
        self.assertIn("confirmed", er.DECIDED_BY)
        self.assertIn("extracted", er.DECIDED_BY)


class TheResolverCorrectsTheDeviceToo(unittest.TestCase):
    """Moving the mirror alone leaves the system of record wrong.

    **Measured, not anticipated.** The first real `apply` moved 47 postings in
    Postgres and left the pool saying "Appcast Enterprise", and
    `verify_parity` reported exactly 47 `employer_edge` divergences, one per
    repointing and nothing else. The two stores genuinely disagreed; the guard
    was right to be red.
    """

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        import jobs_store
        self.jobs_store = jobs_store
        jobs_store.save_jobs(self.root.name, {
            "a": {"id": "a", "company": "Appcast Enterprise", "title": "T"},
            "b": {"id": "b", "company": "Appcast Enterprise", "title": "U"},
        }, note="test")

    def test_the_pool_learns_the_confirmed_name(self):
        out = er.correct_the_pool([{"id": "a", "name": "Halfords"}],
                                  self.root.name)
        self.assertEqual(out["corrected"], 1)
        jobs = self.jobs_store.load_jobs(self.root.name)
        self.assertEqual(jobs["a"]["company"], "Halfords")
        self.assertEqual(jobs["b"]["company"], "Appcast Enterprise",
                         "a posting nobody confirmed was rewritten")

    def test_a_posting_that_left_the_pool_is_named_not_skipped(self):
        """The edge moved in Postgres and cannot move here, and that
        difference is the only thing that would explain a divergence left
        over afterwards."""
        out = er.correct_the_pool([{"id": "gone", "name": "Y"}],
                                  self.root.name)
        self.assertEqual(out["corrected"], 0)
        self.assertEqual(out["missing"], ["gone"])

    def test_it_is_idempotent_so_a_failed_half_can_be_finished(self):
        """A second `apply` reports every edge "already there", so a pool
        correction driven by this run's moves could never finish one that
        failed the first time."""
        confirmed = [{"id": "a", "name": "Halfords"}]
        first = er.correct_the_pool(confirmed, self.root.name)
        second = er.correct_the_pool(confirmed, self.root.name)
        self.assertEqual((first["corrected"], first["already"]), (1, 0))
        self.assertEqual((second["corrected"], second["already"]), (0, 1))


class ReedIsAJobBoardAndTheLogoRulesNowSaySo(unittest.TestCase):
    """A job board's favicon drawn as the employer's mark, 2,577 times.

    **Found by asking whether the repointed cards would look right.** The 47
    postings got their real employers and every one still showed reed's logo,
    because `reed.co.uk` was absent from `INFRA_HOST_PARTS` while linkedin,
    indeed, adzuna, jooble and careerjet were all in it. With the host treated
    as infrastructure the plan falls through to the company name, which is what
    gives Halfords its own mark.

    Measured over the pool: 2,577 reed postings, all on `PLAN_POSTING_HOST`
    before, 2,540 on `PLAN_NAME_CORE` after and 37 on a monogram because the
    name is too short to be a candidate.
    """

    def setUp(self):
        import company_logos
        self.logos = company_logos

    def entry(self, company, url):
        return {"company": company, "companyWebsite": "", "logoUrl": "",
                "url": url}

    def test_reed_is_infrastructure(self):
        self.assertTrue(self.logos._is_infrastructure_host("www.reed.co.uk"))
        self.assertTrue(self.logos._is_infrastructure_host("reed.co.uk"))

    def test_a_reed_posting_derives_from_the_company_not_the_board(self):
        url = "https://www.reed.co.uk/jobs/mobile-tyre-technician/57290392"
        plan = self.logos.logo_plan(self.entry("Halfords", url), url)
        self.assertEqual(plan, self.logos.PLAN_NAME_CORE)
        self.assertNotEqual(plan, self.logos.PLAN_POSTING_HOST,
                            "the board's own favicon would be drawn as the "
                            "employer's mark")

    def test_a_real_employer_host_is_still_the_employer(self):
        """The narrowing must not swallow employers who host their own board.
        `stripe.com` and `careers.hellofresh.com` are 220 and 244 postings."""
        url = "https://stripe.com/jobs/listing/engineer/1"
        self.assertEqual(self.logos.logo_plan(self.entry("Stripe", url), url),
                         self.logos.PLAN_POSTING_HOST)
