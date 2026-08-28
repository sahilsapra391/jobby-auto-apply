"""A failed document provider hands off on retry, and names its failure.

Three Gemini document jobs exited 1 for one application on 2026-08-28, and
the system's whole account of each was "provider exited 1". Two separate
faults made that loop: the failure detail only consulted stderr, and agy
speaks stream-json, so its reason went to stdout and was discarded with the
partial output; and the tick's provider walk only skipped providers whose
health probes failed, so a provider that probes healthy while failing every
document job it is handed was re-picked on every Retry while two accounts
that could do the work sat idle.
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import document_worker  # noqa: E402
import watch  # noqa: E402


def failed_job(provider):
    return {"state": "failed", "provider": provider}


class DocumentCandidateOrderTests(unittest.TestCase):
    ORDER = ("openai", "anthropic", "gemini")

    def test_a_fresh_job_keeps_the_saved_preference_first(self):
        self.assertEqual(
            watch.document_candidate_order("gemini", self.ORDER, None, False),
            ["gemini", "openai", "anthropic"])

    def test_a_retry_of_a_failed_job_tries_every_other_connection_first(self):
        self.assertEqual(
            watch.document_candidate_order("gemini", self.ORDER,
                                           failed_job("gemini"), True),
            ["openai", "anthropic", "gemini"])

    def test_the_failed_provider_still_runs_when_it_is_the_only_one(self):
        self.assertEqual(
            watch.document_candidate_order("gemini", ("gemini",),
                                           failed_job("gemini"), True),
            ["gemini"])

    def test_a_retry_whose_job_is_not_failed_is_not_reordered(self):
        self.assertEqual(
            watch.document_candidate_order(
                "gemini", self.ORDER, {"state": "ready", "provider": "gemini"},
                True),
            ["gemini", "openai", "anthropic"])

    def test_a_failure_on_a_disconnected_provider_changes_nothing(self):
        self.assertEqual(
            watch.document_candidate_order("openai", ("openai", "anthropic"),
                                           failed_job("gemini"), True),
            ["openai", "anthropic"])

    def test_no_saved_preference_walks_the_connection_order(self):
        self.assertEqual(
            watch.document_candidate_order(None, self.ORDER, None, True),
            ["openai", "anthropic", "gemini"])

    def test_only_a_retry_click_demotes(self):
        """The demotion answers the user's Retry; a fresh click keeps the
        preference so a transient failure does not reroute unrelated work."""
        self.assertEqual(
            watch.document_candidate_order("gemini", self.ORDER,
                                           failed_job("gemini"), False),
            ["gemini", "openai", "anthropic"])


class StdoutErrorLineTests(unittest.TestCase):
    def line(self, raw):
        return document_worker._stdout_error_line(raw)

    def test_picks_the_last_line_that_names_an_error(self):
        raw = (b'{"event":"init","model":"gemini-3.7-flash-medium"}\n'
               b'{"event":"step_update","text":"drafting the resume"}\n'
               b'{"event":"error","message":"quota exceeded for model"}\n')
        self.assertIn("quota exceeded", self.line(raw))

    def test_the_latest_error_wins_when_there_are_several(self):
        raw = (b'{"event":"error","message":"first"}\n'
               b'{"event":"error","message":"second"}\n')
        self.assertIn("second", self.line(raw))
        self.assertNotIn("first", self.line(raw))

    def test_partial_document_output_is_never_quoted(self):
        """The stream carries the model's own draft. A failure reason that
        quoted it would put resume text in a log, so a run whose stdout has
        no error line reports nothing rather than something."""
        raw = (b'{"event":"step_update","text":"Sahil led a 12 person team"}\n'
               b'{"event":"step_update","text":"and shipped the platform"}\n')
        self.assertEqual(self.line(raw), "")

    def test_case_does_not_matter(self):
        self.assertIn("Rate Limited",
                      self.line(b'{"event":"ERROR","message":"Rate Limited"}\n'))

    def test_the_line_is_bounded(self):
        raw = b'{"event":"error","message":"' + b"x" * 1000 + b'"}\n'
        self.assertLessEqual(len(self.line(raw)), 300)

    def test_broken_bytes_do_not_crash_the_failure_path(self):
        self.assertEqual(self.line(b"\xff\xfe no reason here"), "")

    def test_the_scrubbed_reason_still_reads(self):
        detail = self.line(b'{"event":"error","message":"quota exceeded"}\n')
        reason = document_worker.failure_reason(f"provider exited 1: {detail}")
        self.assertIn("provider exited 1", reason)
        self.assertIn("quota exceeded", reason)


if __name__ == "__main__":
    unittest.main()
