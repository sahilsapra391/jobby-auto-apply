"""An aggregator's host is in the logo rules the moment the connector exists.

**The largest single source in this project drew a job board's own favicon as
the employer's mark on every card it supplied, and nothing said so.**
`reed.co.uk` was absent from `company_logos.INFRA_HOST_PARTS` while linkedin,
indeed, adzuna, jooble and careerjet were all there, so `logo_plan` returned
`PLAN_POSTING_HOST` and the client derived the mark from the posting's host.
2,577 of 23,633 postings, measured 2026-09-06.

**It was invisible because it was consistent.** Every reed card had the same
wrong mark, so nothing looked out of place; the defect only surfaced when 47 of
those postings were repointed onto their real employers and still showed a job
board's logo.

So the rule is not "remember to add the host". Every module under
`scripts/sources/` that declares `SOURCE_CLASS = "aggregator"` declares the
hosts its postings live on, and this fails when one of them is not covered.
Adding a connector without adding its host is now a red test rather than a
discovery months later.

**Two more were found by writing it**: `usajobs.gov` (343 postings) and
`themuse.com` (32). Neither was noticed by anyone; both are aggregators by
their own declaration.
"""

import importlib
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import company_logos  # noqa: E402

SOURCES = REPO / "scripts" / "sources"


def connectors():
    """Every source module, imported. Not a list written here."""
    out = {}
    for path in sorted(SOURCES.glob("*.py")):
        if path.stem in ("__init__", "base"):
            continue
        try:
            module = importlib.import_module(f"sources.{path.stem}")
        except Exception:                                    # noqa: BLE001
            # A connector that will not import is a different failure and
            # `test_sources` owns it. Skipping it here would hide it, so it is
            # recorded as a name this check could not reach.
            out[path.stem] = None
            continue
        out[path.stem] = module
    return out


class EveryAggregatorHostIsInTheLogoRules(unittest.TestCase):
    def setUp(self):
        self.modules = connectors()
        self.aggregators = {
            name: m for name, m in self.modules.items()
            if m is not None and getattr(m, "SOURCE_CLASS", "") == "aggregator"}

    def test_the_scan_reaches_the_connectors(self):
        """Every check below passes on an empty set."""
        unreadable = sorted(n for n, m in self.modules.items() if m is None)
        self.assertEqual(unreadable, [],
                         "these source modules would not import, so this check "
                         "could not look at them")
        self.assertGreaterEqual(
            len(self.aggregators), 5,
            f"only {len(self.aggregators)} aggregators found among "
            f"{len(self.modules)} source modules; the scan is probably not "
            f"reading SOURCE_CLASS")

    def test_each_aggregator_declares_where_its_postings_live(self):
        for name, module in sorted(self.aggregators.items()):
            with self.subTest(source=name):
                hosts = getattr(module, "POSTING_HOSTS", None)
                self.assertIsNotNone(
                    hosts,
                    f"{name} is an aggregator and does not declare "
                    f"POSTING_HOSTS. Without it nothing can check that the "
                    f"board will not draw this job board's own mark on every "
                    f"employer card it supplies.")
                self.assertTrue(hosts, f"{name} declares no hosts")

    def test_each_declared_host_is_treated_as_infrastructure(self):
        """The check the reed defect needed and did not have."""
        stranded = []
        for name, module in sorted(self.aggregators.items()):
            for host in getattr(module, "POSTING_HOSTS", ()) or ():
                probe = host if not host.endswith(".") else host + "com"
                if not company_logos._is_infrastructure_host(probe.lstrip(".")):
                    stranded.append(f"{name}:{host}")
        self.assertEqual(
            stranded, [],
            "these aggregator hosts are not in company_logos."
            "INFRA_HOST_PARTS, so `logo_plan` returns PLAN_POSTING_HOST and "
            "the board draws the job board's own favicon as the employer's "
            "mark on every card from them: %s" % stranded)

    def test_an_ats_host_is_not_required_to_be_here(self):
        """The rule is about aggregators, and the distinction is deliberate.

        An ATS host is already covered because a posting on one is not the
        employer's own site either, but the modules are not required to
        declare it: `boards` and `enterprise_boards` serve hundreds of
        tenants and no fixed host list would be true of them.
        """
        ats = {n for n, m in self.modules.items()
               if m is not None and getattr(m, "SOURCE_CLASS", "") == "ats"}
        self.assertTrue(ats, "no ATS connectors found; the scan is wrong")
        for name in ats:
            self.assertNotIn(name, self.aggregators)


class TheEmployerHostsAreStillEmployers(unittest.TestCase):
    """The narrowing must not swallow employers who host their own board.

    Measured over the pool: stripe.com is 220 postings, careers.hellofresh.com
    244, jobs.sap.com 234. Each is the employer's own site and each must keep
    `PLAN_POSTING_HOST`, which is what gives them a real mark.
    """

    def test_a_company_hosting_its_own_board_keeps_its_mark(self):
        for host in ("stripe.com", "careers.hellofresh.com", "jobs.sap.com",
                     "www.coupang.jobs", "databricks.com"):
            with self.subTest(host=host):
                self.assertFalse(company_logos._is_infrastructure_host(host))
