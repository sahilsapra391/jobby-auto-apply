"""Security and staging tests for the shared document-worker key."""

import base64
import contextlib
import importlib.util
import io
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "worker_key", ROOT / "scripts" / "worker_key.py"
)
worker_key = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker_key
spec.loader.exec_module(worker_key)


def read_assignments(path):
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            result[name] = value
    return result


class EnsureWorkerKeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.temporary.name)
        self.config = self.home / ".job-application" / "config.env"

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_valid_key_with_private_permissions(self):
        result = worker_key.ensure_worker_key(self.config)
        values = read_assignments(self.config)

        self.assertEqual(len(base64.b64decode(values[worker_key.KEY_NAME], validate=True)), 32)
        self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(result["state"], "created")
        self.assertNotIn(values[worker_key.KEY_NAME], repr(result))

    def test_preserves_other_lines_and_retains_valid_key(self):
        self.config.parent.mkdir(mode=0o755)
        key = base64.b64encode(b"k" * 32).decode("ascii")
        original = f"# local settings\nCF_ACCOUNT_ID=account\n\nJOBBY_WORKER_KEY={key}\nTAIL=value\n"
        self.config.write_text(original)
        os.chmod(self.config, 0o644)

        result = worker_key.ensure_worker_key(self.config)

        self.assertEqual(self.config.read_text(), original)
        self.assertEqual(result["state"], "retained")
        self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_replaces_invalid_key_and_collapses_duplicates(self):
        self.config.parent.mkdir()
        self.config.write_text(
            "FIRST=one\nJOBBY_WORKER_KEY=invalid\nMIDDLE=two\n"
            "JOBBY_WORKER_KEY=also-invalid\nLAST=three\n"
        )

        result = worker_key.ensure_worker_key(self.config)
        content = self.config.read_text()
        values = read_assignments(self.config)

        self.assertEqual(result["state"], "replaced")
        self.assertEqual(content.count("JOBBY_WORKER_KEY="), 1)
        self.assertIn("FIRST=one\n", content)
        self.assertIn("MIDDLE=two\n", content)
        self.assertIn("LAST=three\n", content)
        self.assertEqual(len(base64.b64decode(values[worker_key.KEY_NAME], validate=True)), 32)

    def test_concurrent_writers_leave_one_valid_key(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(
                lambda unused: worker_key.ensure_worker_key(self.config), range(40)
            ))

        content = self.config.read_text()
        key = read_assignments(self.config)[worker_key.KEY_NAME]
        self.assertEqual(content.count("JOBBY_WORKER_KEY="), 1)
        self.assertEqual(len(base64.b64decode(key, validate=True)), 32)
        self.assertTrue(all(key not in repr(item) for item in results))

    def test_rejects_symlinked_config_target(self):
        self.config.parent.mkdir()
        target = self.home / "target.env"
        target.write_text("DO_NOT_CHANGE=yes\n")
        self.config.symlink_to(target)

        with self.assertRaises(worker_key.WorkerKeyError) as raised:
            worker_key.ensure_worker_key(self.config)

        self.assertEqual(raised.exception.code, "unsafe_file_target")
        self.assertEqual(target.read_text(), "DO_NOT_CHANGE=yes\n")


class HealthchecksConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config = pathlib.Path(self.temporary.name) / "local" / "config.env"
        self.url = "https://hc-ping.com/33333333-3333-3333-3333-333333333333"

    def tearDown(self):
        self.temporary.cleanup()

    def test_replaces_only_healthchecks_and_collapses_duplicates(self):
        self.config.parent.mkdir()
        self.config.write_text(
            "FIRST=one\nHEALTHCHECKS_URL=https://hc-ping.com/11111111-1111-1111-1111-111111111111\n"
            "JOBBY_WORKER_KEY=keep-me\nHEALTHCHECKS_URL=https://hc-ping.com/22222222-2222-2222-2222-222222222222\n"
        )

        result = worker_key.set_healthchecks_url(self.url, self.config)
        content = self.config.read_text()

        self.assertEqual(content.count("HEALTHCHECKS_URL="), 1)
        self.assertIn(f"HEALTHCHECKS_URL={self.url}\n", content)
        self.assertIn("JOBBY_WORKER_KEY=keep-me\n", content)
        self.assertNotIn(self.url, repr(result))
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_rejects_non_healthchecks_urls(self):
        for value in (
            "http://hc-ping.com/33333333-3333-3333-3333-333333333333",
            "https://example.com/33333333-3333-3333-3333-333333333333",
            "https://hc-ping.com/not-a-uuid",
            "https://hc-ping.com/33333333-3333-3333-3333-333333333333?secret=x",
        ):
            with self.assertRaises(worker_key.WorkerKeyError):
                worker_key.set_healthchecks_url(value, self.config)
        self.assertFalse(self.config.exists())

    def test_cli_does_not_print_the_url(self):
        stdout = io.StringIO()
        with mock.patch.object(worker_key.toolpath, "LOCAL_CONFIG", self.config), \
                contextlib.redirect_stdout(stdout):
            code = worker_key.main(["set-healthchecks", self.url])

        self.assertEqual(code, 0)
        self.assertNotIn(self.url, stdout.getvalue())
        self.assertIn('"action": "set-healthchecks"', stdout.getvalue())


class StageWorkerEnvTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self.temporary.name)
        self.config = self.directory / "local" / "config.env"
        self.key = base64.b64encode(b"s" * 32).decode("ascii")
        self.settings = {
            "CF_ACCOUNT_ID": "account-id",
            "CF_KV_NAMESPACE_ID": "namespace-id",
            "CF_API_TOKEN": "api-token",
            "JOBBY_WORKER_KEY": self.key,
            "DASHBOARD_PASSWORD": "must-not-be-staged",
            "HEALTHCHECKS_URL": "must-not-be-staged",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_requires_absolute_output_path(self):
        with self.assertRaises(worker_key.WorkerKeyError) as raised:
            worker_key.stage_worker_env("worker.env", self.config)
        self.assertEqual(raised.exception.code, "output_path_not_absolute")

    def test_stages_only_four_settings_with_private_mode(self):
        output = self.directory / "worker.env"
        with mock.patch.object(worker_key.toolpath, "settings", return_value=self.settings):
            result = worker_key.stage_worker_env(output, self.config)

        self.assertEqual(
            output.read_text().splitlines(),
            [f"{name}={self.settings[name]}" for name in worker_key.WORKER_VARIABLES],
        )
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(result["variables"], list(worker_key.WORKER_VARIABLES))
        for value in self.settings.values():
            self.assertNotIn(value, repr(result))

    def test_overwrites_existing_output_atomically_and_fixes_mode(self):
        output = self.directory / "worker.env"
        output.write_text("OLD=secret\n")
        os.chmod(output, 0o666)
        with mock.patch.object(worker_key.toolpath, "settings", return_value=self.settings):
            worker_key.stage_worker_env(output, self.config)

        self.assertNotIn("OLD", output.read_text())
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_missing_setting_fails_without_creating_output(self):
        output = self.directory / "worker.env"
        incomplete = dict(self.settings)
        del incomplete["CF_API_TOKEN"]
        with mock.patch.object(worker_key.toolpath, "settings", return_value=incomplete):
            with self.assertRaises(worker_key.WorkerKeyError) as raised:
                worker_key.stage_worker_env(output, self.config)

        self.assertEqual(raised.exception.code, "missing_worker_settings")
        self.assertEqual(raised.exception.detail, ["CF_API_TOKEN"])
        self.assertFalse(output.exists())

    def test_rejects_noncanonical_or_nontext_worker_key(self):
        output = self.directory / "worker.env"
        for invalid in (self.key + "=", b"not-text"):
            settings = dict(self.settings, JOBBY_WORKER_KEY=invalid)
            with mock.patch.object(worker_key.toolpath, "settings", return_value=settings):
                with self.assertRaises(worker_key.WorkerKeyError):
                    worker_key.stage_worker_env(output, self.config)
            self.assertFalse(output.exists())

    def test_cli_stage_prints_metadata_without_secrets(self):
        output = self.directory / "worker.env"
        stdout = io.StringIO()
        with mock.patch.object(worker_key.toolpath, "LOCAL_CONFIG", self.config), \
                mock.patch.object(worker_key.toolpath, "settings", return_value=self.settings), \
                contextlib.redirect_stdout(stdout):
            code = worker_key.main(["stage", str(output)])

        printed = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertIn('"action": "stage"', printed)
        for value in self.settings.values():
            self.assertNotIn(value, printed)


if __name__ == "__main__":
    unittest.main()
