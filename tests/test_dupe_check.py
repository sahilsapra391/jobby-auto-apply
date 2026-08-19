"""Tests for scripts/dupe_check.py, the one-application-per-job guard.

The guard is the only thing standing between a re-queued role and a second
application landing on a recruiter's desk. The cases worth pinning down are the
ones that are easy to get wrong: the same posting arriving under a different
application id, and a `completed` event that was later retracted.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dupe_check", ROOT / "scripts" / "dupe_check.py")
dupe_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dupe_check)


class FakeStore:
    """Stands in for store.py so these tests never touch the real history."""

    def __init__(self, history, roles):
        self.history, self.roles = history, roles

    def __call__(self, args):
        if args[0] == "history-list":
            return self.history
        if args[0] == "roles-list":
            return self.roles
        if args[0] == "session-load":
            # store.py exits non-zero when no session exists; guard() then falls
            # back to the durable role index, which is the path worth testing.
            return None
        return None


class NormalisationTests(unittest.TestCase):
    def test_company_and_role_match_across_punctuation_and_case(self):
        self.assertEqual(dupe_check.norm("Scale AI"), dupe_check.norm("scale-ai"))
        self.assertEqual(dupe_check.norm("Product Manager, Models"),
                         dupe_check.norm("product manager models"))

    def test_url_match_ignores_tracking_and_scheme(self):
        a = dupe_check.norm_url("https://www.jobs.example.com/p/123?utm_source=li")
        b = dupe_check.norm_url("http://jobs.example.com/p/123/")
        self.assertEqual(a, b)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.history = [
            {"at": "2026-01-01T00:00:00Z", "event": "completed", "applicationId": "acme-pm",
             "company": "Acme", "role": "Product Manager"},
        ]
        self.roles = {
            "acme-pm": {"url": "https://boards.example.com/acme/pm"},
            "acme-pm-dup": {"url": "https://boards.example.com/acme/pm?src=rss",
                            "company": "Acme", "role": "Product Manager"},
            "acme-other": {"url": "https://boards.example.com/acme/eng",
                           "company": "Acme", "role": "Engineering Manager"},
        }
        dupe_check.store = FakeStore(self.history, self.roles)

    def test_same_id_is_a_duplicate(self):
        self.assertTrue(dupe_check.guard("acme-pm")["duplicate"])

    def test_same_posting_under_a_different_id_is_a_duplicate(self):
        result = dupe_check.guard("acme-pm-dup")
        self.assertTrue(result["duplicate"])

    def test_a_different_role_at_the_same_company_is_allowed(self):
        self.assertFalse(dupe_check.guard("acme-other")["duplicate"])

    def test_a_retracted_completion_does_not_block_a_real_application(self):
        # A mark logged in error, then corrected. The role must stay applicable.
        self.history.append({"at": "2026-01-02T00:00:00Z", "event": "abandoned",
                             "applicationId": "acme-pm", "company": "Acme",
                             "role": "Product Manager"})
        self.assertFalse(dupe_check.guard("acme-pm")["duplicate"])

    def test_audit_reports_clean_when_nothing_repeats(self):
        self.assertTrue(dupe_check.audit()["clean"])

    def test_audit_flags_the_same_posting_completed_twice(self):
        self.history.append({"at": "2026-01-03T00:00:00Z", "event": "completed",
                             "applicationId": "acme-pm-dup", "company": "Acme",
                             "role": "Product Manager"})
        report = dupe_check.audit()
        self.assertFalse(report["clean"])
        self.assertTrue(any(d["kind"] == "same posting url" for d in report["duplicates"]))


if __name__ == "__main__":
    unittest.main()
