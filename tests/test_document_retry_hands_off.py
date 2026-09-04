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

import os
import pathlib
import shutil
import sys
import tempfile
import time
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

    def test_a_conversation_id_does_not_eat_the_reason(self):
        """36 characters of noise inside a 140-character cap.

        Every recorded Gemini failure on 2026-08-28 spent its whole budget on
        the event shape and the conversation id and was cut before the part
        that would have said anything.
        """
        raw = (b'{"event":"result","result":{'
               b'"conversation_id":"a3d7c777-5cb0-4202-8731-fd37d5ee00ba",'
               b'"status":"ERROR","response":"","error":"quota exceeded"}}\n')
        self.assertNotIn("a3d7c777", self.line(raw))
        reason = document_worker.failure_reason(
            f"provider exited 1: {document_worker._stream_error(raw)}")
        self.assertIn("quota exceeded", reason)


class StreamErrorTests(unittest.TestCase):
    """The earliest non-empty error wins over the terminal result's empty one."""

    def test_an_earlier_reason_beats_the_terminal_empty_one(self):
        raw = (b'{"event":"error","error":"model is overloaded"}\n'
               b'{"event":"result","result":{"status":"ERROR",'
               b'"response":"","error":""}}\n')
        self.assertEqual(document_worker._stream_error(raw),
                         "model is overloaded")

    def test_an_empty_error_is_not_a_reason(self):
        raw = (b'{"event":"result","result":{"status":"ERROR",'
               b'"response":"","error":""}}\n')
        self.assertEqual(document_worker._stream_error(raw), "")

    def test_the_conversation_id_is_recovered_for_the_join(self):
        raw = (b'{"event":"init","init":{"model":"gemini-3.7-flash-medium"}}\n'
               b'{"event":"result","result":{'
               b'"conversation_id":"a3d7c777-5cb0-4202-8731-fd37d5ee00ba",'
               b'"status":"ERROR","response":"","error":""}}\n')
        self.assertEqual(document_worker._stream_conversation_id(raw),
                         "a3d7c777-5cb0-4202-8731-fd37d5ee00ba")


class AgyLogReasonTests(unittest.TestCase):
    """The reason agy never hands back.

    A failed agy run exits 1 with stderr empty and a terminal result whose
    error string is empty, so the sentence naming the cause exists only in the
    CLI's own log. These are the real lines from the 2026-08-28 failures.
    """

    FAULT = ("ERROR: logging before google.Init: E0827 23:45:00.417392       1 "
             "printmode.go:545] Print mode: conversation update stream failed: "
             "subscriber fell behind updates, stalled for 5s")
    CONVERSATION = "a3d7c777-5cb0-4202-8731-fd37d5ee00ba"

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, name, body, age_seconds=60):
        path = self.root / name
        path.write_text(body, encoding="utf-8")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def reason(self, conversation=None):
        return document_worker._agy_log_reason(
            conversation or self.CONVERSATION, log_dir=self.root)

    def test_it_recovers_the_reason_the_stream_withheld(self):
        self.write("cli-1.log",
                   f"Created conversation {self.CONVERSATION}\n{self.FAULT}\n")
        self.assertEqual(
            self.reason(),
            "Print mode: conversation update stream failed: subscriber fell "
            "behind updates, stalled for 5s")

    def test_a_newer_unrelated_run_is_not_attributed_to_this_one(self):
        """Runs overlap. A neighbour's fault reported as this job's is worse
        than reporting none, so the join is by conversation id and never by
        taking the newest file."""
        other = "f8f870fe-5010-44aa-80c6-047d478c566c"
        self.write("cli-old.log",
                   f"Created conversation {self.CONVERSATION}\n{self.FAULT}\n",
                   age_seconds=300)
        self.write("cli-new.log",
                   f"Created conversation {other}\n"
                   + self.FAULT.replace("stalled for 5s", "a different fault"),
                   age_seconds=10)
        self.assertIn("stalled for 5s", self.reason())
        self.assertNotIn("different fault", self.reason())

    def test_the_signed_in_account_never_reaches_a_job_log(self):
        """The same file records the Google account on its OAuth lines. The
        allowlist is what keeps an email address out of every failure log."""
        oauth = ("ERROR: logging before google.Init: E0827 23:44:52.223854     1 "
                 "server_oauth.go:190] applyAuthResult: "
                 "email=sahilpsapra03@gmail.com, authMethod=consumer")
        self.write("cli-1.log",
                   f"Created conversation {self.CONVERSATION}\n{oauth}\n")
        self.assertEqual(self.reason(), "")

    def test_a_stale_log_is_out_of_range(self):
        self.write("cli-1.log",
                   f"Created conversation {self.CONVERSATION}\n{self.FAULT}\n",
                   age_seconds=document_worker.MAX_AGY_LOG_AGE_SECONDS + 60)
        self.assertEqual(self.reason(), "")

    def test_an_unknown_conversation_reports_nothing(self):
        self.write("cli-1.log",
                   f"Created conversation {self.CONVERSATION}\n{self.FAULT}\n")
        self.assertEqual(
            self.reason("11111111-2222-3333-4444-555555555555"), "")

    def test_a_path_is_refused_where_a_conversation_id_belongs(self):
        for value in ("../../etc/passwd", "*", "", "not-a-uuid"):
            self.assertEqual(self.reason(value), "")

    def test_a_missing_log_directory_is_not_a_second_failure(self):
        self.assertEqual(
            document_worker._agy_log_reason(self.CONVERSATION,
                                            log_dir=self.root / "absent"), "")
