import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import document_job
import store


class DocumentJobStore:
    """Shared setup, as a plain mixin.

    Not a TestCase. Subclassing one to borrow its setUp re-runs every test it
    holds, and test_suite_integrity refuses that.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "sessions").mkdir()
        self.application_id = "test-application"
        self.session_path = self.root / "sessions" / (self.application_id + ".json")
        store.write_private(self.session_path, {
            "applicationId": self.application_id,
            "company": "Private Company",
            "role": "Private Role",
            "status": "watch",
        })
        self.job_id = "4420d70f-df6d-4d30-95a7-cd3d3e033f04"

    def tearDown(self):
        self.temp.cleanup()

    def session(self):
        return json.loads(self.session_path.read_text())

    def state(self, state="queued", provider="anthropic", **extra):
        value = {
            "schemaVersion": 1,
            "jobId": self.job_id,
            "provider": provider,
            "state": state,
            "createdAt": "2026-08-23T12:00:00Z",
            "updatedAt": "2026-08-23T12:00:00Z",
        }
        value.update(extra)
        session = self.session()
        session["documentJob"] = value
        store.write_private(self.session_path, session)
        return value

    @staticmethod
    def status(job_id, state="queued", **extra):
        value = {
            "version": 1,
            "queue": "documents",
            "jobId": job_id,
            "state": state,
            "createdAt": "2026-08-23T12:00:00Z",
            "updatedAt": "2026-08-23T12:01:00Z",
            "expiresAt": "2026-08-30T12:00:00Z",
        }
        value.update(extra)
        return value

class DocumentJobTest(DocumentJobStore, unittest.TestCase):
    def test_transport_enqueue_uses_private_file_and_keeps_plaintext_out_of_argv(self):
        observed = {}

        def run(command, **kwargs):
            observed["command"] = command
            input_path = Path(command[3])
            observed["mode"] = stat.S_IMODE(input_path.stat().st_mode)
            observed["bundle"] = json.loads(input_path.read_text())
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"jobId": self.job_id, "state": "queued"}),
                stderr="",
            )

        bundle = {"company": "Name That Must Stay Private", "baseResume": "secret"}
        with mock.patch.object(document_job.toolpath, "node", return_value="/usr/bin/node"), \
                mock.patch.object(document_job.toolpath, "queue_env", return_value={"SAFE": "1"}), \
                mock.patch.object(document_job.subprocess, "run", side_effect=run):
            result = document_job.transport_call("enqueue", self.job_id, bundle=bundle)

        self.assertEqual("queued", result["state"])
        self.assertEqual(0o600, observed["mode"])
        self.assertEqual(bundle, observed["bundle"])
        self.assertNotIn("Name That Must Stay Private", " ".join(observed["command"]))
        self.assertNotIn("secret", " ".join(observed["command"]))
        self.assertFalse(Path(observed["command"][3]).exists())

    def test_transport_parses_only_a_fixed_bridge_error(self):
        completed = SimpleNamespace(
            returncode=2,
            stdout="",
            stderr=json.dumps({"ok": False, "error": "transport_write_failed"}),
        )
        with mock.patch.object(document_job.toolpath, "node", return_value="node"), \
                mock.patch.object(document_job.toolpath, "queue_env", return_value={}), \
                mock.patch.object(document_job.subprocess, "run", return_value=completed):
            with self.assertRaises(document_job.DocumentJobError) as raised:
                document_job.transport_call("status", self.job_id)
        self.assertEqual("transport_write_failed", raised.exception.code)

    def test_transport_reports_non_json_bundle_with_a_fixed_code(self):
        with mock.patch.object(document_job.toolpath, "node", return_value="node"):
            with self.assertRaises(document_job.DocumentJobError) as raised:
                document_job.transport_call(
                    "enqueue",
                    self.job_id,
                    bundle={"bad": object()},
                )
        self.assertEqual("bundle_invalid", raised.exception.code)

    def test_transport_discards_arbitrary_stderr(self):
        completed = SimpleNamespace(
            returncode=2,
            stdout="",
            stderr='vendor said private@example.com and token="secret"',
        )
        with mock.patch.object(document_job.toolpath, "node", return_value="node"), \
                mock.patch.object(document_job.toolpath, "queue_env", return_value={}), \
                mock.patch.object(document_job.subprocess, "run", return_value=completed):
            with self.assertRaises(document_job.DocumentJobError) as raised:
                document_job.transport_call("status", self.job_id)
        self.assertEqual("transport_error", raised.exception.code)
        self.assertNotIn("private@example.com", str(raised.exception))

    def test_ensure_saves_the_job_id_before_enqueue(self):
        observed = {}

        def transport(action, job_id, bundle=None):
            observed["action"] = action
            observed["jobId"] = job_id
            observed["saved"] = self.session()["documentJob"].copy()
            return {"jobId": job_id, "state": "queued", "deduped": False}

        with mock.patch.object(document_job, "_brief", return_value={"documentsReady": False}), \
                mock.patch.object(document_job.document_contract, "build_bundle",
                                  return_value={"private": "bundle"}):
            state = document_job.ensure(
                self.application_id,
                "openai",
                self.root,
                transport,
            )

        self.assertEqual("enqueue", observed["action"])
        self.assertEqual("submitting", observed["saved"]["state"])
        self.assertEqual(observed["jobId"], observed["saved"]["jobId"])
        self.assertEqual("queued", state["state"])
        self.assertEqual("openai", state["provider"])

    def test_ensure_does_not_duplicate_an_existing_queued_job(self):
        self.state("queued", "anthropic")
        transport = mock.Mock()
        state = document_job.ensure(
            self.application_id,
            "openai",
            self.root,
            transport,
        )
        transport.assert_not_called()
        self.assertEqual(self.job_id, state["jobId"])
        self.assertEqual("anthropic", state["provider"])

    def test_concurrent_ensure_calls_create_only_one_remote_job(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        results = []
        errors = []

        def transport(action, job_id, bundle=None):
            calls.append((action, job_id))
            entered.set()
            if not release.wait(2):
                raise AssertionError("test transport was not released")
            return {"jobId": job_id, "state": "queued", "deduped": False}

        def run(provider):
            try:
                results.append(document_job.ensure(
                    self.application_id,
                    provider,
                    self.root,
                    transport,
                ))
            except Exception as exc:
                errors.append(exc)

        with mock.patch.object(document_job, "_brief", return_value={"documentsReady": False}), \
                mock.patch.object(document_job.document_contract, "build_bundle",
                                  return_value={"private": "bundle"}):
            first = threading.Thread(target=run, args=("anthropic",))
            second = threading.Thread(target=run, args=("openai",))
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            release.set()
            first.join(2)
            second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(1, len(calls))
        self.assertEqual(2, len(results))
        self.assertEqual({calls[0][1]}, {result["jobId"] for result in results})
        self.assertEqual({"anthropic"}, {result["provider"] for result in results})

    def test_interrupted_enqueue_reuses_the_saved_job_id(self):
        self.state("submitting", "gemini")
        transport = mock.Mock(return_value={
            "jobId": self.job_id,
            "state": "queued",
            "deduped": True,
        })
        with mock.patch.object(document_job.document_contract, "build_bundle",
                               return_value={"private": "bundle"}):
            state = document_job.ensure(
                self.application_id,
                "openai",
                self.root,
                transport,
            )
        transport.assert_called_once_with("enqueue", self.job_id, bundle={"private": "bundle"})
        self.assertEqual("gemini", state["provider"])
        self.assertEqual("queued", state["state"])

    def test_transient_enqueue_error_stays_retryable_and_safe(self):
        with mock.patch.object(document_job, "_brief", return_value={"documentsReady": False}), \
                mock.patch.object(document_job.document_contract, "build_bundle",
                                  return_value={"private": "bundle"}):
            state = document_job.ensure(
                self.application_id,
                "openai",
                self.root,
                mock.Mock(side_effect=document_job.DocumentJobError(
                    "transport_unavailable")),
            )
        self.assertEqual("submitting", state["state"])
        self.assertEqual("transport_unavailable", state["lastErrorCode"])
        self.assertNotIn("errorCode", state)

    def test_invalid_local_bundle_is_retained_as_fixed_failure(self):
        with mock.patch.object(document_job, "_brief", return_value={"documentsReady": False}), \
                mock.patch.object(document_job.document_contract, "build_bundle",
                                  side_effect=RuntimeError("private details")):
            state = document_job.ensure(
                self.application_id,
                "openai",
                self.root,
                mock.Mock(),
            )
        self.assertEqual("failed", state["state"])
        self.assertEqual("invalid_request", state["errorCode"])
        self.assertNotIn("private details", json.dumps(self.session()))

    def test_existing_documents_return_ready_without_creating_a_job(self):
        with mock.patch.object(document_job, "_brief", return_value={"documentsReady": True}):
            state = document_job.ensure(
                self.application_id,
                "anthropic",
                self.root,
                mock.Mock(),
            )
        self.assertEqual("ready", state["state"])
        self.assertTrue(state["existing"])
        self.assertNotIn("documentJob", self.session())

    def test_poll_updates_a_claimed_job_without_result_fetch(self):
        self.state("queued")
        transport = mock.Mock(return_value=self.status(self.job_id, "claimed"))
        state = document_job.poll(self.application_id, self.root, transport)
        transport.assert_called_once_with("status", self.job_id)
        self.assertEqual("claimed", state["state"])
        self.assertEqual("2026-08-30T12:00:00Z", state["expiresAt"])

    def test_poll_installs_a_completed_result_and_saves_exact_basenames(self):
        self.state("claimed", "openai")
        result = {
            "schemaVersion": 1,
            "applicationId": self.application_id,
            "provider": "openai",
            "files": {},
        }
        payload = {"jobId": self.job_id, "result": result}
        transport = mock.Mock(side_effect=[
            self.status(self.job_id, "completed"),
            payload,
            {"jobId": self.job_id, "state": "deleted"},
        ])
        written = {
            "resumeHtml": "/safe/.Person_Company_Role.resume.html",
            "resumePdf": "/safe/Person_Company_Role_Resume.pdf",
            "coverHtml": "/safe/.Person_Company_Role.cover.html",
            "coverPdf": "/safe/Person_Company_Role_CoverLetter.pdf",
        }
        with mock.patch.object(document_job, "_brief", return_value={"folder": "/safe"}), \
                mock.patch.object(document_job.document_contract, "install_result",
                                  return_value=written) as install:
            state = document_job.poll(self.application_id, self.root, transport)

        self.assertEqual("ready", state["state"])
        self.assertEqual({key: Path(value).name for key, value in written.items()}, state["files"])
        install.assert_called_once_with(result, "/safe", self.application_id)
        self.assertEqual(
            [mock.call("status", self.job_id), mock.call("retrieve", self.job_id),
             mock.call("delete", self.job_id)],
            transport.call_args_list,
        )

    def test_cleanup_failure_does_not_hide_installed_documents(self):
        self.state("claimed", "openai")
        payload = {"jobId": self.job_id, "result": {
            "schemaVersion": 1,
            "applicationId": self.application_id,
            "provider": "openai",
            "files": {},
        }}
        transport = mock.Mock(side_effect=[
            self.status(self.job_id, "completed"),
            payload,
            document_job.DocumentJobError("transport_delete_failed"),
        ])
        written = {
            "resumeHtml": "/safe/.Person_Company_Role.resume.html",
            "resumePdf": "/safe/Person_Company_Role_Resume.pdf",
            "coverHtml": "/safe/.Person_Company_Role.cover.html",
            "coverPdf": "/safe/Person_Company_Role_CoverLetter.pdf",
        }
        with mock.patch.object(document_job, "_brief", return_value={"folder": "/safe"}), \
                mock.patch.object(document_job.document_contract, "install_result",
                                  return_value=written):
            state = document_job.poll(self.application_id, self.root, transport)
        self.assertEqual("ready", state["state"])

    def test_poll_rejects_a_result_from_another_provider(self):
        self.state("claimed", "anthropic")
        transport = mock.Mock(side_effect=[
            self.status(self.job_id, "completed"),
            {"jobId": self.job_id, "result": {
                "applicationId": self.application_id,
                "provider": "gemini",
            }},
        ])
        with mock.patch.object(document_job.document_contract, "install_result") as install:
            state = document_job.poll(self.application_id, self.root, transport)
        install.assert_not_called()
        self.assertEqual("failed", state["state"])
        self.assertEqual("result_invalid", state["errorCode"])

    def test_worker_failure_is_saved_with_a_fixed_code(self):
        self.state("claimed")
        transport = mock.Mock(return_value=self.status(
            self.job_id,
            "failed",
            errorCode="generation_failed",
        ))
        state = document_job.poll(self.application_id, self.root, transport)
        self.assertEqual("failed", state["state"])
        self.assertEqual("generation_failed", state["errorCode"])

    def test_explicit_retry_archives_failure_and_uses_a_new_job_id(self):
        self.state("failed", "anthropic", errorCode="generation_failed")
        transport = mock.Mock(side_effect=lambda action, job_id, bundle=None: {
            "jobId": job_id,
            "state": "queued",
            "deduped": False,
        })
        with mock.patch.object(document_job.document_contract, "build_bundle",
                               return_value={"private": "bundle"}):
            state = document_job.retry_failed(
                self.application_id,
                "openai",
                self.root,
                transport,
            )
        self.assertEqual("queued", state["state"])
        self.assertEqual("openai", state["provider"])
        self.assertNotEqual(self.job_id, state["jobId"])
        history = self.session()["documentJobFailures"]
        self.assertEqual([{
            "jobId": self.job_id,
            "provider": "anthropic",
            "errorCode": "generation_failed",
            "failedAt": "2026-08-23T12:00:00Z",
        }], history)

    def test_retry_refuses_to_duplicate_active_work(self):
        self.state("claimed", "anthropic")
        transport = mock.Mock()
        state = document_job.retry_failed(
            self.application_id,
            "openai",
            self.root,
            transport,
        )
        transport.assert_not_called()
        self.assertEqual("claimed", state["state"])
        self.assertEqual(self.job_id, state["jobId"])

    def test_poll_transport_error_keeps_remote_state_for_next_cycle(self):
        self.state("queued")
        transport = mock.Mock(side_effect=document_job.DocumentJobError(
            "transport_read_failed"))
        state = document_job.poll(self.application_id, self.root, transport)
        self.assertEqual("queued", state["state"])
        self.assertEqual("transport_read_failed", state["lastErrorCode"])

    def test_missing_remote_status_does_not_create_a_replacement_job(self):
        self.state("queued")
        state = document_job.poll(
            self.application_id,
            self.root,
            mock.Mock(return_value=None),
        )
        self.assertEqual("queued", state["state"])
        self.assertEqual("job_missing", state["lastErrorCode"])
        self.assertEqual(self.job_id, state["jobId"])

    def test_remote_status_poll_is_throttled_between_watcher_ticks(self):
        self.state("queued", lastRemotePollAt="2026-08-23T12:00:00Z")
        transport = mock.Mock()
        with mock.patch.object(
                document_job, "_utc_now",
                return_value=datetime(2026, 8, 23, 12, 0, 10, tzinfo=timezone.utc)):
            state = document_job.poll(self.application_id, self.root, transport)
        transport.assert_not_called()
        self.assertEqual("queued", state["state"])

    def test_missing_remote_status_becomes_terminal_after_grace_period(self):
        self.state(
            "queued",
            lastErrorCode="job_missing",
            lastErrorAt="2026-08-23T12:00:00Z",
            lastRemotePollAt="2026-08-23T12:00:00Z",
        )
        with mock.patch.object(
                document_job, "_utc_now",
                return_value=datetime(2026, 8, 23, 12, 2, 1, tzinfo=timezone.utc)):
            state = document_job.poll(
                self.application_id,
                self.root,
                mock.Mock(return_value=None),
            )
        self.assertEqual("failed", state["state"])
        self.assertEqual("job_missing", state["errorCode"])

    def test_expired_remote_job_fails_without_another_kv_read(self):
        self.state("claimed", expiresAt="2026-08-23T12:00:00Z")
        transport = mock.Mock()
        with mock.patch.object(
                document_job, "_utc_now",
                return_value=datetime(2026, 8, 23, 12, 0, 1, tzinfo=timezone.utc)):
            state = document_job.poll(self.application_id, self.root, transport)
        transport.assert_not_called()
        self.assertEqual("failed", state["state"])
        self.assertEqual("job_expired", state["errorCode"])

    def test_poll_without_a_job_is_non_destructive(self):
        state = document_job.poll(self.application_id, self.root, mock.Mock())
        self.assertEqual({"schemaVersion": 1, "state": "not_requested"}, state)
        self.assertNotIn("documentJob", self.session())

    def test_malformed_saved_state_is_refused(self):
        session = self.session()
        session["documentJob"] = {
            "schemaVersion": 1,
            "jobId": "../../private",
            "provider": "openai",
            "state": "queued",
        }
        store.write_private(self.session_path, session)
        with self.assertRaises(document_job.DocumentJobError) as raised:
            document_job.get_state(self.application_id, self.root)
        self.assertEqual("state_invalid", raised.exception.code)

    def test_sync_polls_an_existing_job_but_not_a_new_submission(self):
        self.state("queued")
        transport = mock.Mock(return_value=self.status(self.job_id, "claimed"))
        state = document_job.sync(
            self.application_id,
            "openai",
            self.root,
            transport,
        )
        self.assertEqual("claimed", state["state"])
        transport.assert_called_once_with("status", self.job_id)


class ContractRefusalDetailTest(DocumentJobStore, unittest.TestCase):
    """The refusal reason has to reach the card, and nothing else may.

    Six applications parked on the fixed code `invalid_request` for a day. The
    code is correct and stays; what was missing was the sentence beside it. The
    contract refuses on facts about the request, so its own message is safe.
    Everything else stays opaque, which is what the RuntimeError case below
    pins.
    """

    def refuse(self, error):
        with mock.patch.object(document_job, "_brief",
                               return_value={"documentsReady": False}), \
                mock.patch.object(document_job.document_contract, "build_bundle",
                                  side_effect=error):
            return document_job.ensure(
                self.application_id, "openai", self.root, mock.Mock())

    def test_a_contract_refusal_names_the_missing_fact(self):
        state = self.refuse(document_job.document_contract.DocumentContractError(
            "the application has no job description"))
        self.assertEqual("invalid_request", state["errorCode"])
        self.assertEqual("the application has no job description",
                         state["errorDetail"])

    def test_an_unexpected_error_still_carries_no_detail_at_all(self):
        state = self.refuse(RuntimeError("private details"))
        self.assertEqual("invalid_request", state["errorCode"])
        self.assertNotIn("errorDetail", state)
        self.assertNotIn("private details", json.dumps(self.session()))

    def test_a_detail_is_folded_to_one_bounded_line(self):
        state = self.refuse(document_job.document_contract.DocumentContractError(
            "line one\nline two\t" + "x" * 400))
        detail = state["errorDetail"]
        self.assertLessEqual(len(detail), document_job.MAX_ERROR_DETAIL)
        self.assertNotIn("\n", detail)
        self.assertTrue(detail.startswith("line one line two"))

    def test_a_detail_cannot_smuggle_markup_or_a_path(self):
        state = self.refuse(document_job.document_contract.DocumentContractError(
            "<b>/Users/someone/secret.pdf</b> refused"))
        detail = state["errorDetail"]
        for banned in ("<", ">", "/", "\\"):
            self.assertNotIn(banned, detail)
        self.assertIn("refused", detail)

    def test_a_later_success_clears_the_detail_with_the_code(self):
        self.refuse(document_job.document_contract.DocumentContractError(
            "the application has no job description"))
        self.state("submitting")
        transport = mock.Mock(return_value=self.status(self.job_id, "queued"))
        with mock.patch.object(document_job.document_contract, "build_bundle",
                               return_value={"schemaVersion": 1}):
            state = document_job.sync(self.application_id, "anthropic",
                                      self.root, transport)
        self.assertEqual("queued", state["state"])
        self.assertNotIn("errorDetail", state)
        self.assertNotIn("errorCode", state)



if __name__ == "__main__":
    unittest.main()
