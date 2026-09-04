"""A job the scan found has to appear in what the board serves.

This is the promise the outage broke, and none of the tests covering the stages
noticed. The scan was healthy and tested. The publisher was healthy and tested.
The renderer was healthy and tested. The *seam* between them failed: publish
refused a state over its ceiling, the board kept serving the previous evening,
and every stage still passed on its own.

So this one runs the whole chain -- ingest a posting, build the published state,
render from that state -- and asserts the posting is in what comes out. It is
deliberately end to end, because every cheaper version of it was already green
while the board sat frozen for 23 hours.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "sources"))

import dashboard  # noqa: E402
import ingest  # noqa: E402
import jobs_store  # noqa: E402
import publish  # noqa: E402
import search_config  # noqa: E402
from sources import base  # noqa: E402

NOW = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)


def posting(title, url, posted):
    return base.posting(
        "greenhouse", "ats", "1a", "Northwind Analytics", title, url,
        location={"raw": "New York, NY", "city": "New York", "country": "US"},
        workplace="hybrid", posted_at=posted.strftime("%Y-%m-%dT%H:%M:%SZ"),
        description="Own the reporting roadmap. Five years of product "
                    "management experience required.",
        seen_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))


class NewJobsReachTheBoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "init"], capture_output=True)
        search_config.save(self.root, {"keywords": ["product"],
                                       "searchScope": "title+description"})
        self.original = dict(ingest.sources.ALL)

    def tearDown(self):
        ingest.sources.ALL.clear()
        ingest.sources.ALL.update(self.original)
        self.tmp.cleanup()

    def sweep(self, postings):
        def channel(fetcher, **_):
            return base.ok("greenhouse", postings, requests=1, complete=True)
        ingest.sources.ALL.clear()
        ingest.sources.ALL["greenhouse"] = channel
        return ingest.run(self.root, now=NOW, settings={},
                          fetcher=base.Fetcher(root=self.root, opener=None))

    def served_state(self):
        """Exactly what publish would hand the deployed renderer."""
        state = dashboard.load_state(self.root)
        return dashboard.publishable_state(state)

    def test_a_posting_the_scan_found_is_in_what_the_board_serves(self):
        fresh = posting("Product Manager, Reporting",
                        "https://job-boards.greenhouse.io/northwind/jobs/1",
                        NOW - timedelta(hours=2))
        report, _ = self.sweep([fresh])
        self.assertEqual(report["kept"], 1)

        served = self.served_state()
        urls = [(j.get("canonicalUrl") or "") for j in (served.get("jobs") or {}).values()]
        self.assertTrue(any("jobs/1" in u for u in urls),
                        "the scan kept it and the board does not serve it")

    def test_the_newest_posting_survives_all_the_way_through(self):
        """The freshness invariant. A board serving only older records is the
        exact shape of the outage: real, complete, and yesterday's."""
        older = posting("Product Manager, Billing",
                        "https://job-boards.greenhouse.io/northwind/jobs/2",
                        NOW - timedelta(days=9))
        newest = posting("Product Manager, Platform Signals",
                         "https://job-boards.greenhouse.io/northwind/jobs/3",
                         NOW - timedelta(minutes=30))
        self.sweep([older, newest])

        served = self.served_state()
        stamps = [j.get("postedAt") for j in (served.get("jobs") or {}).values()
                  if j.get("postedAt")]
        self.assertTrue(stamps, "the served state carries no dated jobs at all")
        self.assertEqual(max(stamps), newest["postedAt"],
                         "the newest posting the scan found is not the newest "
                         "one the board serves")

    def test_the_served_state_fits_what_the_reader_will_accept(self):
        """The other half of the same seam: a state the reader rejects is a
        board that keeps serving whatever it had."""
        self.sweep([posting("Product Manager, Reporting",
                            "https://job-boards.greenhouse.io/northwind/jobs/1",
                            NOW - timedelta(hours=1))])
        payload = publish.serialize_state(self.served_state())
        self.assertLessEqual(len(payload), publish.MAX_STATE_BYTES)
