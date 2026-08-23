"""Live output limits and strong provider-auth classification tests."""

import os
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import provider_process


class BoundedProcessTests(unittest.TestCase):
    def test_stdout_overflow_terminates_the_process_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            result = provider_process.run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import os,time; os.write(1,b'x'*65536); time.sleep(30)",
                ],
                cwd=directory,
                env=os.environ,
                stdin_payload=None,
                timeout_seconds=10,
                max_stdout_bytes=64,
                max_stderr_bytes=64,
            )
        self.assertEqual(result.overflow_stream, "stdout")
        self.assertEqual(len(result.stdout.encode("utf-8")), 64)
        self.assertLess(time.monotonic() - started, 5)

    def test_employer_sign_in_text_is_not_provider_auth_evidence(self):
        message = "Employer page says: Sign in to continue your application."
        for provider in ("openai", "anthropic", "gemini"):
            with self.subTest(provider=provider):
                self.assertFalse(
                    provider_process.looks_like_provider_auth_failure(
                        provider, message
                    )
                )

    def test_nonzero_cli_auth_stderr_has_strong_provider_evidence(self):
        cases = {
            "openai": "Not logged in to Codex. Run `codex login`.",
            "anthropic": "Authentication failed. Run claude auth login.",
            "gemini": "Gemini authentication required, select an authentication method.",
        }
        for provider, message in cases.items():
            with self.subTest(provider=provider):
                self.assertTrue(
                    provider_process.looks_like_provider_auth_failure(
                        provider, message
                    )
                )


if __name__ == "__main__":
    unittest.main()
