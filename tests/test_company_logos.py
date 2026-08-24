import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import company_logos  # noqa: E402


class CompanyLogoTests(unittest.TestCase):
    def test_real_feed_logo_uses_the_same_origin_proxy(self):
        url = company_logos.proxy_url({
            "logoUrl": "https://cdn-images.himalayas.app/company-logo",
        })
        self.assertTrue(url.startswith("/api/company-logo?src="))
        self.assertNotIn("cdn-images.himalayas.app/", url)

    def test_direct_employer_domain_uses_a_private_favicon_proxy(self):
        url = company_logos.proxy_url({}, "https://careers.example.com/jobs/42")
        self.assertEqual(url, "/api/company-logo?domain=careers.example.com")

    def test_ats_and_aggregator_hosts_never_pose_as_the_employer(self):
        for url in (
            "https://boards.greenhouse.io/example/jobs/42",
            "https://example.wd3.myworkdayjobs.com/en-US/jobs/job/42",
            "https://www.linkedin.com/jobs/view/42",
            "https://consultdss.recruitee.com/o/product-manager",
            "https://wfglobal.zohorecruit.com/jobs/Careers/42",
            "https://jobs.jobviewtrack.com/product-manager",
        ):
            with self.subTest(url=url):
                self.assertEqual(company_logos.proxy_url({}, url), "")

    def test_unknown_remote_logo_host_falls_back_without_loading_it(self):
        url = company_logos.proxy_url({
            "logoUrl": "https://hostile.example/pixel.png",
        })
        self.assertEqual(url, "")

    def test_employer_website_is_recovered_from_the_posting_description(self):
        website = company_logos.website_from_description(
            "Innovaccer Analytics",
            '<a href="https://www.linkedin.com/company/innovaccer">Social</a>'
            '<a href="https://innovaccer.com/about">About Innovaccer</a>',
        )
        self.assertEqual(website, "https://innovaccer.com")
        self.assertEqual(
            company_logos.proxy_url({"companyWebsite": website}),
            "/api/company-logo?domain=innovaccer.com",
        )

    def test_monograms_are_stable_and_ignore_legal_suffixes(self):
        self.assertEqual(company_logos.initials("Northstar Labs, Inc."), "NL")
        self.assertEqual(company_logos.initials("IFS"), "I")


if __name__ == "__main__":
    unittest.main()
