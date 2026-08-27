"""The published state carries what the board draws, and nothing else.

The guard this serves refused every publish for about 22 hours on 2026-08-27,
because the jobs map reached 20.66 MB against a 20 MB ceiling. The board kept
serving the previous evening's snapshot the whole time, which is the failure
this project keeps meeting in new clothes: a system reporting health while doing
nothing.

The load-bearing property is not the size. It is that slimming changes no pixel
and no API answer, so the test that matters renders both ways and compares.
"""

import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import state_slim  # noqa: E402
import found_lane  # noqa: E402
import dashboard  # noqa: E402


def a_record():
    """One discovery record with every field the real store puts on one."""
    return {
        "id": "abc123", "company": "Acme", "title": "Product Manager",
        "canonicalUrl": "https://boards.greenhouse.io/acme/jobs/1",
        "canonicalSource": "greenhouse", "workplace": "remote",
        "postedAt": "2026-08-01T00:00:00Z", "salary": "$1 - $2",
        "tags": ["product"], "state": "new", "logoUrl": "https://x/y.png",
        "companyWebsite": "https://acme.com",
        "descriptionExcerpt": "word " * 600,
        "location": {"country": "US", "city": "Boston", "raw": "Boston, MA",
                     "region": "MA", "lat": 42.0, "lon": -71.0},
        "extracted": {"yearsMin": 5, "yearsMax": 8, "yearsText": "5-8 years",
                      "degree": None, "employmentType": None, "roleCategory": None},
        "score": {"total": 71, "parts": {"a": 1}, "why": {"a": "because"},
                  "unknowns": [], "configVersion": 1,
                  "gates": [{"label": "Years", "evidence": "x" * 500}]},
        "sources": [{"url": "https://a/b", "source": "greenhouse",
                     "sourceClass": "employer", "firstSeenAt": "2026-08-01T00:00:00Z",
                     "raw": {"lots": "of it"}}],
        "sections": {"responsibilities": ["r" * 900] * 30,
                     "requirements": ["q" * 900] * 30,
                     "benefits": ["never rendered"]},
        # None of the following reaches a pixel.
        "normTitle": "product manager", "descFingerprint": "simhash64:deadbeef",
        "firstSeenAt": "2026-08-01T00:00:00Z", "lastSeenAt": "2026-08-27T00:00:00Z",
        "seenCount": 129, "configVersion": 1, "industry": None, "lang": "en-GB",
        "closedAt": None, "stateRepairedWhy": "x", "stateRepairedAt": "y",
    }


class SlimmedRecordTest(unittest.TestCase):
    def setUp(self):
        self.slim = state_slim.slim_job(a_record())

    def test_the_fields_the_card_draws_survive(self):
        for field in ("id", "company", "title", "canonicalUrl", "canonicalSource",
                      "workplace", "postedAt", "salary", "tags", "state",
                      "logoUrl", "companyWebsite"):
            self.assertIn(field, self.slim, field)

    def test_nothing_the_renderer_reads_is_dropped(self):
        # The docstring on publishable_state promises filter and scoring behave the
        # same locally and deployed. This is that promise, field by field.
        self.assertEqual(self.slim["location"]["city"], "Boston")
        self.assertEqual(self.slim["location"]["raw"], "Boston, MA")
        self.assertEqual(self.slim["location"]["country"], "US")
        self.assertEqual(self.slim["extracted"]["yearsMin"], 5)
        self.assertEqual(self.slim["extracted"]["yearsText"], "5-8 years")
        self.assertEqual(self.slim["score"]["total"], 71)
        self.assertEqual(self.slim["score"]["parts"], {"a": 1})
        self.assertEqual(self.slim["score"]["why"], {"a": "because"})
        self.assertEqual(self.slim["sources"][0]["sourceClass"], "employer")

    def test_the_excerpt_is_cut_to_what_renders(self):
        self.assertLessEqual(len(self.slim["descriptionExcerpt"]),
                             found_lane.EMBED_EXCERPT_CHARS)
        self.assertGreater(len(self.slim["descriptionExcerpt"]),
                           found_lane.EMBED_EXCERPT_CHARS - 20)

    def test_the_excerpt_transform_is_a_fixed_point(self):
        # The embed re-runs this over whatever the state holds. If running it
        # twice differs from running it once, a page rendered from a slimmed
        # state differs from one rendered from a whole state by the trailing
        # space, which is exactly the byte this caught on a fixture.
        once = found_lane.embed_excerpt("word " * 600)
        self.assertEqual(found_lane.embed_excerpt(once), once)
        self.assertFalse(once.endswith(" "))
        for text in ("", None, "   ", "a  b\n\tc", "x" * 5000):
            value = found_lane.embed_excerpt(text)
            self.assertEqual(found_lane.embed_excerpt(value), value, repr(text))

    def test_gate_evidence_goes_and_the_label_stays(self):
        self.assertEqual(self.slim["score"]["gates"], [{"label": "Years"}])
        self.assertNotIn("evidence", json.dumps(self.slim["score"]))

    def test_sections_are_cut_to_what_the_card_draws(self):
        for key in ("responsibilities", "requirements"):
            lines = self.slim["sections"][key]
            self.assertEqual(len(lines), found_lane.SECTION_POINTS)
            self.assertTrue(all(len(line) == 400 for line in lines))
        self.assertNotIn("benefits", self.slim["sections"])

    def test_the_javascript_and_python_agree_on_how_many_points_a_card_draws(self):
        """SCRIPT is a raw string, so the number is written twice. Pin them.

        If the card starts drawing six and the state keeps sending five, the
        sixth point silently never appears; if the state sends twelve again,
        seven a side are fetched and discarded. Neither shows up as a failure
        anywhere else.
        """
        source = (ROOT / "scripts" / "found_lane.py").read_text(encoding="utf-8")
        self.assertIn(f"var SECTION_POINTS = {found_lane.SECTION_POINTS};", source)

    def test_the_fields_nothing_draws_are_gone(self):
        for field in ("normTitle", "descFingerprint", "firstSeenAt", "lastSeenAt",
                      "seenCount", "configVersion", "industry", "lang",
                      "stateRepairedWhy", "stateRepairedAt"):
            self.assertNotIn(field, self.slim, field)
        self.assertNotIn("region", self.slim["location"])
        self.assertNotIn("raw", self.slim["sources"][0])
        self.assertNotIn("degree", self.slim["extracted"])

    def test_it_is_much_smaller(self):
        size = lambda o: len(json.dumps(o, separators=(",", ":")).encode())
        self.assertLess(size(self.slim), size(a_record()) / 3)

    def test_order_is_preserved_because_the_first_record_supplies_the_logo(self):
        # build() takes the first record it sees for each employer, so
        # reordering moves company marks. It once moved Datadog's.
        jobs = {f"id{n}": dict(a_record(), id=f"id{n}") for n in range(20)}
        self.assertEqual(list(state_slim.slim_jobs(jobs)), list(jobs))

    def test_a_record_that_is_not_a_dict_is_returned_untouched(self):
        self.assertEqual(state_slim.slim_job("nonsense"), "nonsense")
        self.assertEqual(state_slim.slim_jobs(None), None)

    def test_empty_values_do_not_survive_as_empty_containers(self):
        bare = state_slim.slim_job({"id": "x", "location": {}, "sources": [],
                                    "sections": {}, "score": {}})
        self.assertEqual(bare, {"id": "x"})


class RenderIsUnchangedTest(unittest.TestCase):
    """The whole point: slimming must move no pixel and no API answer."""

    def _state(self, slim):
        store = {
            "jobs": {f"j{n}": dict(a_record(), id=f"j{n}", company=f"Co{n}")
                     for n in range(25)},
            "sessions": [], "history": [], "roles": {}, "meta": {},
            "profile": {"firstName": "Sahil"}, "searchConfig": None, "providers": {},
        }
        original = dashboard.state_slim
        if not slim:
            dashboard.state_slim = type("Off", (), {
                "slim_jobs": staticmethod(lambda jobs: jobs)})()
        try:
            return dashboard.publishable_state(copy.deepcopy(store))
        finally:
            dashboard.state_slim = original

    def test_the_page_is_byte_identical(self):
        full, slim = self._state(False), self._state(True)
        page_full = dashboard.build(copy.deepcopy(full), "2026-08-27T00:00:00Z")
        page_slim = dashboard.build(copy.deepcopy(slim), "2026-08-27T00:00:00Z")
        self.assertEqual(hashlib.sha256(page_full.encode()).hexdigest(),
                         hashlib.sha256(page_slim.encode()).hexdigest())

    def test_the_posting_route_answers_the_same(self):
        full, slim = self._state(False), self._state(True)

        def answer(state, job_id):
            sections = ((state["jobs"].get(job_id) or {}).get("sections")) or {}
            trim = lambda ls: [str(x or "")[:400] for x in (ls or [])][:12]
            return {"responsibilities": trim(sections.get("responsibilities")),
                    "requirements": trim(sections.get("requirements"))}

        for job_id in full["jobs"]:
            self.assertEqual(answer(full, job_id), answer(slim, job_id), job_id)

    def test_slimming_actually_shrank_it(self):
        size = lambda o: len(json.dumps(o, separators=(",", ":")).encode())
        self.assertLess(size(self._state(True)), size(self._state(False)) / 2)


@unittest.skipUnless((pathlib.Path(os.environ.get("JOB_APPLICATION_DIR",
                                                  "~/.job-application")).expanduser()
                      / "jobs.json").exists(),
                     "no real job store on this machine")
class RealStoreTest(unittest.TestCase):
    """Against the actual pool, which is the thing that broke."""

    def test_the_real_state_fits_under_the_publish_ceiling(self):
        spec = importlib.util.spec_from_file_location("publish", ROOT / "scripts" / "publish.py")
        publish = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publish)
        root = pathlib.Path(os.environ.get("JOB_APPLICATION_DIR",
                                           "~/.job-application")).expanduser()
        state = dashboard.publishable_state(dashboard.load_state(root))
        payload = json.dumps(state, separators=(",", ":")).encode()
        self.assertLess(len(payload), publish.MAX_STATE_BYTES,
                        f"the real state is {len(payload)/1024/1024:.2f} MB against a "
                        f"{publish.MAX_STATE_BYTES/1024/1024:.0f} MB ceiling")


if __name__ == "__main__":
    unittest.main()
