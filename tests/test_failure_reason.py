"""Name the refusal, without letting anything escape that should not.

A fixed code alone cost three diagnoses in one day. "coverLetter employer or
role does not match the job" named the right gate and still took hours, because
it says neither which field nor what the two values were. "generated artifacts
failed validation" is worse. Both were found by re-implementing the gate in a
throwaway process against the live store.

The sentence cannot travel the way #29's does. That one is raised client-side
in build_bundle and never crosses the transport. The worker's is raised on the
far side of the KV bridge, and KV takes ciphertext and fixed status metadata
and nothing else. So the code crosses and the sentence stays here, in the
owner-only per-job log the worker already writes.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JOB = "6a0af422-e8d1-4627-ad87-5d803a8d9fb9"
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


worker = load("document_worker")
job = load("document_job")


class ReasonScrubTests(unittest.TestCase):
    def test_a_plain_sentence_survives_intact(self):
        message = "coverLetter employer or role does not match the job"
        self.assertEqual(message, worker.failure_reason(message))

    def test_it_is_bounded(self):
        self.assertLessEqual(
            len(worker.failure_reason("x" * 5000)), worker.MAX_FAILURE_REASON)

    def test_it_folds_to_one_line(self):
        self.assertEqual("a b", worker.failure_reason("a\n\n\tb"))

    def test_anything_that_is_not_a_plain_sentence_is_cut(self):
        # Belt and braces against a future message that starts interpolating a
        # path, a posting, or a provider body.
        self.assertNotIn("/Users", worker.failure_reason("read /Users/x/secret.pdf"))
        self.assertNotIn("<", worker.failure_reason("<script>alert(1)</script>"))

    def test_a_non_string_is_nothing(self):
        for value in (None, 7, {"a": 1}, ["x"]):
            self.assertEqual("", worker.failure_reason(value))


class WorkerErrorReasonTests(unittest.TestCase):
    def test_a_worker_error_offers_its_own_message(self):
        exc = worker.ResultInvalid("coverLetter has an invalid shape: role is too short")
        self.assertEqual(
            "coverLetter has an invalid shape: role is too short", exc.reason())

    def test_unsupported_terms_gives_the_count_and_never_the_terms(self):
        """The class exists to keep those terms inside the worker."""
        terms = ["custody", "advisor workflow", "ai-native", "next-generation custody"]
        exc = worker.UnsupportedTerms(terms)
        reason = exc.reason()
        self.assertIn("4", reason)
        for term in terms:
            self.assertNotIn(term, reason)

    def test_every_failure_class_can_produce_a_reason(self):
        for cls in (worker.WorkerError, worker.InvalidRequest,
                    worker.GenerationFailed, worker.ResultInvalid):
            with self.subTest(cls=cls.__name__):
                self.assertIsInstance(cls("something went wrong").reason(), str)


class JobLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)

    def test_the_reason_is_written_beside_the_code(self):
        log = worker.JobLog(self.dir, JOB)
        log.record("failure published", "result_invalid",
                   "coverLetter employer or role does not match the job")
        line = (self.dir / f"{JOB}.log").read_text().strip().splitlines()[-1]
        self.assertIn("[result_invalid]", line)
        self.assertIn("does not match the job", line)

    def test_no_reason_leaves_the_line_exactly_as_it_was(self):
        log = worker.JobLog(self.dir, JOB)
        log.record("failure published", "result_invalid")
        line = (self.dir / f"{JOB}.log").read_text().strip().splitlines()[-1]
        self.assertTrue(line.endswith("[result_invalid]"))

    def test_the_log_stays_owner_only(self):
        worker.JobLog(self.dir, JOB).record("failure published", "result_invalid", "x")
        self.assertEqual(0o600, (self.dir / f"{JOB}.log").stat().st_mode & 0o777)


class ClientReadsItBackTests(unittest.TestCase):
    """The other half: the sentence has to reach the card."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.logs = self.root / "document-logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def write(self, job_id, body):
        (self.logs / f"{job_id}.log").write_text(body)

    def test_it_reads_the_reason_the_worker_wrote(self):
        self.write(JOB,
                   "- 2026-08-25T18:00:00Z claim accepted\n"
                   "- 2026-08-25T18:00:09Z failure published [result_invalid] "
                   "coverLetter employer or role does not match the job\n")
        self.assertEqual(
            "coverLetter employer or role does not match the job",
            job._worker_failure_reason(JOB, str(self.root)))

    def test_the_last_failure_wins_when_a_job_was_retried(self):
        self.write(JOB,
                   "- 18:00:00Z failure published [result_invalid] the first reason\n"
                   "- 18:05:00Z failure published [result_invalid] the second reason\n")
        self.assertEqual("the second reason",
                         job._worker_failure_reason(JOB, str(self.root)))

    def test_a_deferred_publish_still_carries_its_reason(self):
        self.write(JOB,
                   "- 18:00:00Z failure publish deferred [worker_failed] the transport was down\n")
        self.assertEqual("the transport was down",
                         job._worker_failure_reason(JOB, str(self.root)))

    def test_a_code_with_no_reason_reads_as_nothing(self):
        self.write(JOB, "- 18:00:00Z failure published [result_invalid]\n")
        self.assertEqual("", job._worker_failure_reason(JOB, str(self.root)))

    def test_a_missing_log_is_not_an_error(self):
        # Best effort. No log means the card shows the code alone, which is
        # exactly what it showed before any of this existed.
        self.assertEqual("", job._worker_failure_reason("11111111-1111-1111-1111-111111111111", str(self.root)))

    def test_a_successful_job_yields_no_reason(self):
        self.write(JOB,
                   "- 18:00:00Z response parsed\n- 18:00:01Z result published\n")
        self.assertEqual("", job._worker_failure_reason(JOB, str(self.root)))

    def test_the_client_scrubs_again_on_the_way_in(self):
        self.write(JOB,
                   "- 18:00:00Z failure published [result_invalid] "
                   "read /Users/someone/secret.pdf\n")
        got = job._worker_failure_reason(JOB, str(self.root))
        self.assertNotIn("/Users", got)

    def test_a_job_id_that_is_not_a_job_id_cannot_walk_the_filesystem(self):
        # Plant the exact file the traversal reaches, so this fails if the only
        # thing stopping it is that the path happened not to exist.
        # <root>/document-logs/../planted.log resolves to <root>/planted.log.
        (self.root / "planted.log").write_text(
            "- 18:00:00Z failure published [result_invalid] leaked\n")
        self.assertEqual(
            "", job._worker_failure_reason("../planted", str(self.root)),
            "a job id that is not a UUID reached a file outside document-logs")
        for bad in ("../../etc/passwd", None, 7, "not-a-uuid", ""):
            with self.subTest(bad=bad):
                self.assertEqual("", job._worker_failure_reason(bad, str(self.root)))


if __name__ == "__main__":
    unittest.main()
