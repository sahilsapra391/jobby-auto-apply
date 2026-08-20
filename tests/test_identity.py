"""Tests for identity resolution: one job description, one record.

The headline case is the fixture trio — the same requisition as Greenhouse,
Adzuna and LinkedIn each carry it, with three URLs, three company spellings,
three titles and three truncations of the body. It has to come out as one job
with three sightings, in either order of arrival.

The boundaries matter more than the matches here. A matcher that is slightly
too eager merges two real openings into one and the second is simply never
shown to anyone, which nothing errors on and nobody notices.

Run from the repo root: python3 -m pytest tests/ -q
"""

import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


identity = load("identity")
jobs_store = load("jobs_store")

SYNDICATED = json.loads((FIXTURES / "syndicated_jd.json").read_text())

COMPANIES = {
    "northwind analytics": {
        "name": "Northwind Analytics",
        "aliases": ["northwind analytics", "northwindanalytics", "northwind"],
        "ats": "greenhouse", "token": "northwind", "region": "US",
    }
}


def record(sighting):
    """A normalised job record from one board's version of a posting.

    This is the shape the connectors produce in step 4; identity works on it
    rather than on any board's raw payload.
    """
    job = jobs_store.blank_job("pending", sighting["company"], sighting["title"])
    job.update({
        "canonicalUrl": identity.canonical_url(sighting["url"]),
        "canonicalSource": sighting["source"],
        "sources": [{"source": sighting["source"], "sourceClass": sighting["sourceClass"],
                     "tier": sighting["tier"], "url": identity.canonical_url(sighting["url"]),
                     "firstSeenAt": "2026-08-19T20:00:00Z"}],
        "location": sighting["location"],
        "workplace": sighting["workplace"],
        "postedAt": sighting["postedAt"],
        "descriptionExcerpt": sighting["body"][:1500],
        "descFingerprint": sighting["descFingerprint"],
    })
    return job


def ingest(sightings, companies=COMPANIES):
    """Run sightings through resolve → upsert, exactly as ingest.py will."""
    jobs = {}
    index = identity.Index(jobs, companies)
    hows = []
    for sighting in sightings:
        incoming = record(sighting)
        job_id, how = identity.resolve(index, incoming)
        hows.append(how)
        jobs, _ = jobs_store.upsert(jobs, incoming, match_id=job_id)
        index.add(job_id, jobs[job_id])
    return jobs, hows


class CanonicalUrlTests(unittest.TestCase):
    def test_tracking_parameters_are_stripped(self):
        self.assertEqual(
            identity.canonical_url("https://boards.greenhouse.io/x/jobs/1?utm_source=t&gclid=z"),
            "https://boards.greenhouse.io/x/jobs/1")

    def test_a_parameter_that_identifies_the_job_is_kept(self):
        # gh_jid on a company careers page IS the job. Stripping it would
        # collapse every posting at that company into one record.
        self.assertIn("gh_jid=123", identity.canonical_url(
            "https://acme.com/careers?gh_jid=123&utm_campaign=x"))

    def test_fragments_and_trailing_slashes_go(self):
        self.assertEqual(identity.canonical_url("https://acme.com/jobs/1/#apply"),
                         "https://acme.com/jobs/1")

    def test_parameter_order_does_not_change_the_url(self):
        a = identity.canonical_url("https://acme.com/j?b=2&a=1")
        b = identity.canonical_url("https://acme.com/j?a=1&b=2")
        self.assertEqual(a, b)

    def test_only_http_urls_survive(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x/y", "", None):
            self.assertEqual(identity.canonical_url(bad), "")

    def test_the_stored_url_keeps_its_host_exactly(self):
        # This value goes into an href. A `www.` stripped off a host that needs
        # it is a dead link on the board.
        self.assertEqual(identity.canonical_url("https://www.klaviyo.com/careers/jobs/1"),
                         "https://www.klaviyo.com/careers/jobs/1")

    def test_the_comparison_key_is_allowed_to_be_lossy(self):
        self.assertEqual(identity.url_key("https://www.Acme.com/Jobs/1/"),
                         identity.url_key("http://acme.com/jobs/1"))


class OriginLinkTests(unittest.TestCase):
    def test_the_employer_link_is_pulled_out_of_an_aggregator_wrapper(self):
        found = identity.origin_link(SYNDICATED["sightings"][1]["url"])
        self.assertEqual(found, "https://job-boards.greenhouse.io/northwind/jobs/4471002")

    def test_a_same_host_parameter_is_not_an_origin_link(self):
        self.assertEqual(identity.origin_link(
            "https://acme.com/a?next=https%3A%2F%2Facme.com%2Fb"), "")

    def test_a_url_with_no_wrapper_yields_nothing(self):
        self.assertEqual(identity.origin_link(
            "https://job-boards.greenhouse.io/northwind/jobs/4471002"), "")


class RedirectTests(unittest.TestCase):
    def chain(self, hops):
        def fetch(url):
            return hops.get(url, (200, ""))
        return fetch

    def test_a_wrapped_link_resolves_to_where_it_lands(self):
        fetch = self.chain({
            "https://bit.ly/abc": (301, "https://track.example/x"),
            "https://track.example/x": (302, "https://jobs.lever.co/acme/1"),
        })
        self.assertEqual(identity.resolve_redirects("https://bit.ly/abc", fetch=fetch),
                         "https://jobs.lever.co/acme/1")

    def test_hops_are_capped(self):
        # An uncapped chain is an ingest that hangs, which reports nothing at
        # all rather than reporting less.
        def fetch(url):
            return 302, url + "/x"
        out = identity.resolve_redirects("https://a.example/1", fetch=fetch, max_hops=3)
        self.assertEqual(out.count("/x"), 3)

    def test_a_redirect_loop_terminates(self):
        fetch = self.chain({"https://a.example/1": (302, "https://b.example/2"),
                            "https://b.example/2": (302, "https://a.example/1")})
        self.assertEqual(identity.resolve_redirects("https://a.example/1", fetch=fetch),
                         "https://b.example/2")

    def test_with_no_fetcher_nothing_is_followed(self):
        # This is what keeps the matching path free of network access by
        # default, and the whole test suite offline.
        self.assertEqual(identity.resolve_redirects("https://bit.ly/abc"),
                         "https://bit.ly/abc")


class FingerprintTests(unittest.TestCase):
    def test_the_hash_is_stable_across_processes(self):
        # Python's built-in hash() is randomised per process, so a fingerprint
        # built on it would change on every restart: nothing would ever merge
        # and everything would quietly duplicate.
        text = " ".join(f"word{i}" for i in range(40))
        self.assertEqual(identity.fingerprint(text), identity.fingerprint(text))
        self.assertEqual(identity.fingerprint(text), "simhash64:f17f818e37d7b2c0")

    def test_syndicated_copies_of_one_body_are_within_the_threshold(self):
        prints = [s["descFingerprint"] for s in SYNDICATED["sightings"]]
        for other in prints[1:]:
            self.assertTrue(identity.similar(prints[0], other))

    def test_a_different_opening_at_the_same_company_is_not(self):
        self.assertFalse(identity.similar(SYNDICATED["sightings"][0]["descFingerprint"],
                                          SYNDICATED["sibling"]["descFingerprint"]))

    def test_a_body_too_thin_to_fingerprint_says_so(self):
        # A 20-word aggregator excerpt is mostly boilerplate, and any two of
        # them look alike. None is the honest answer.
        self.assertIsNone(identity.fingerprint("Product Manager. New York. Apply now."))
        self.assertIsNone(identity.fingerprint(""))

    def test_an_absent_fingerprint_never_counts_as_a_match(self):
        self.assertFalse(identity.similar(None, None))
        self.assertFalse(identity.similar("simhash64:0000000000000000", None))
        self.assertFalse(identity.similar("not-a-fingerprint", "simhash64:0"))

    def test_the_threshold_is_what_the_plan_specifies(self):
        self.assertEqual(identity.SIMHASH_MAX_DISTANCE, 6)
        self.assertEqual(identity.SIMHASH_BITS, 64)

    def test_hamming_counts_differing_bits(self):
        self.assertEqual(identity.hamming(0b1011, 0b1000), 2)
        self.assertEqual(identity.hamming(7, 7), 0)


class CompanyAndLocationTests(unittest.TestCase):
    def test_a_legal_suffix_does_not_make_a_different_company(self):
        alias_map = identity.company_alias_map(COMPANIES)
        self.assertEqual(identity.norm_company("Northwind Analytics, Inc.", alias_map),
                         identity.norm_company("Northwind Analytics", alias_map))

    def test_a_company_not_in_the_registry_still_matches_itself(self):
        self.assertEqual(identity.norm_company("Someco Technologies Ltd"),
                         identity.norm_company("Someco"))

    def test_the_location_bucket_is_country_not_city(self):
        # Boards do not agree on city strings. "New York, NY" and "New York,
        # United States" are the same place and must bucket together.
        a = {"location": {"raw": "New York, NY", "country": "US"}}
        b = {"location": {"raw": "New York, United States", "country": "US"}}
        self.assertEqual(identity.location_bucket(a), identity.location_bucket(b))

    def test_remote_is_its_own_bucket(self):
        self.assertEqual(identity.location_bucket({"workplace": "remote",
                                                   "location": {"country": "US"}}), "remote")
        self.assertEqual(identity.location_bucket({"location": {"raw": "Remote - Anywhere"}}),
                         "remote")

    def test_an_unknown_country_is_unknown_not_a_guess(self):
        self.assertEqual(identity.location_bucket({"location": {"raw": "Head office"}}),
                         "unknown")


class JobIdTests(unittest.TestCase):
    def test_an_ats_posting_is_identified_by_its_url(self):
        job = record(SYNDICATED["sightings"][0])
        self.assertTrue(identity.dedupe_key(job).startswith("url:"))

    def test_an_aggregator_only_posting_falls_back_to_content(self):
        job = record(SYNDICATED["sightings"][2])
        self.assertTrue(identity.dedupe_key(job).startswith("job:"))

    def test_the_id_is_short_stable_and_hex(self):
        job = record(SYNDICATED["sightings"][0])
        first = identity.job_id(job)
        self.assertEqual(len(first), 16)
        self.assertEqual(first, identity.job_id(record(SYNDICATED["sightings"][0])))
        int(first, 16)


class SyndicationTests(unittest.TestCase):
    """The hard requirement: one JD posted in three places is one record."""

    def test_three_boards_one_job_three_sightings(self):
        jobs, hows = ingest(SYNDICATED["sightings"])
        self.assertEqual(len(jobs), 1, f"merged via {hows}")
        job = next(iter(jobs.values()))
        self.assertEqual(len(job["sources"]), 3)
        self.assertEqual(sorted(s["source"] for s in job["sources"]),
                         ["adzuna", "greenhouse", "linkedin"])
        self.assertEqual(job["seenCount"], 3)

    def test_the_ats_copy_is_the_one_the_board_links_to(self):
        jobs, _ = ingest(SYNDICATED["sightings"])
        job = next(iter(jobs.values()))
        self.assertEqual(job["canonicalSource"], "greenhouse")
        self.assertEqual(job["canonicalUrl"],
                         "https://job-boards.greenhouse.io/northwind/jobs/4471002")

    def test_it_merges_whichever_board_is_seen_first(self):
        # Ingest order is whatever the connectors return that hour. A matcher
        # that only works ATS-first would duplicate on most real runs.
        for order in ([1, 2, 0], [2, 1, 0], [1, 0, 2], [2, 0, 1], [0, 2, 1]):
            sightings = [SYNDICATED["sightings"][i] for i in order]
            jobs, hows = ingest(sightings)
            self.assertEqual(len(jobs), 1, f"order {order} produced {len(jobs)} via {hows}")
            job = next(iter(jobs.values()))
            self.assertEqual(job["canonicalSource"], "greenhouse", f"order {order}")

    def test_the_earliest_posted_date_survives_the_merge(self):
        # Adzuna listed it two days after Greenhouse. The posting is not two
        # days younger because a second board noticed it late.
        jobs, _ = ingest(SYNDICATED["sightings"])
        self.assertEqual(next(iter(jobs.values()))["postedAt"], "2026-08-13T00:00:00Z")

    def test_the_full_body_beats_the_truncated_ones(self):
        jobs, _ = ingest([SYNDICATED["sightings"][2], SYNDICATED["sightings"][0]])
        job = next(iter(jobs.values()))
        self.assertIn("performance reviews by committee", job["descriptionExcerpt"])

    def test_the_reason_for_each_merge_is_reportable(self):
        # Dedupe health is only checkable if the mechanism is named.
        _, hows = ingest(SYNDICATED["sightings"])
        self.assertIsNone(hows[0])
        self.assertTrue(all(h in ("canonical-url", "redirect", "company-title-location",
                                  "fingerprint") for h in hows[1:]), hows)


class NonMatchTests(unittest.TestCase):
    def test_two_real_openings_at_one_company_stay_two(self):
        # Same company, same title, same country, different requisition. Rule 3
        # alone would merge these and one would never be seen again; the
        # fingerprints are 35 bits apart and that is the evidence to stop it.
        jobs, _ = ingest([SYNDICATED["sightings"][0], SYNDICATED["sibling"]])
        self.assertEqual(len(jobs), 2)

    def test_the_veto_only_applies_when_both_bodies_are_known(self):
        # Without a fingerprint there is nothing to veto with, and rule 3 is
        # the best evidence available.
        thin = dict(SYNDICATED["sibling"], descFingerprint=None,
                    title="Product Manager, Internal Reporting",
                    url="https://www.linkedin.com/jobs/view/9999",
                    source="linkedin", sourceClass="aggregator", tier="2")
        jobs, hows = ingest([SYNDICATED["sightings"][0], thin])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(hows[1], "company-title-location")

    def test_the_same_title_at_a_different_company_is_a_different_job(self):
        other = dict(SYNDICATED["sightings"][0], company="Southwind Analytics",
                     url="https://job-boards.greenhouse.io/southwind/jobs/1")
        jobs, _ = ingest([SYNDICATED["sightings"][0], other])
        self.assertEqual(len(jobs), 2)

    def test_the_same_job_in_two_countries_is_two_records(self):
        # Identical body, same company, same title, different country. Rule 4
        # on its own merges these; they are two applications with two different
        # work-authorisation answers, so the known countries separate them.
        london = dict(SYNDICATED["sightings"][0],
                      url="https://job-boards.greenhouse.io/northwind/jobs/4471003",
                      location={"raw": "London, UK", "city": "London",
                                "region": None, "country": "GB"})
        jobs, _ = ingest([SYNDICATED["sightings"][0], london])
        self.assertEqual(len(jobs), 2)

    def test_a_redirect_match_needs_a_fetcher_to_happen(self):
        jobs = {}
        index = identity.Index(jobs, COMPANIES)
        first = record(SYNDICATED["sightings"][0])
        jobs, _ = jobs_store.upsert(jobs, first, match_id="known")
        index.add("known", jobs["known"])

        wrapped = dict(SYNDICATED["sightings"][2], url="https://bit.ly/nw-pm")
        incoming = record(wrapped)
        incoming["descFingerprint"] = None
        incoming["title"] = "Reporting PM"
        incoming["normTitle"] = jobs_store.norm_title("Reporting PM")
        self.assertEqual(identity.match(index, incoming), (None, None))

        def fetch(url):
            if "bit.ly" in url:
                return 301, "https://job-boards.greenhouse.io/northwind/jobs/4471002"
            return 200, ""
        self.assertEqual(identity.match(index, incoming, fetch=fetch), ("known", "redirect"))


if __name__ == "__main__":
    unittest.main()
