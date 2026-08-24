"""Private provider-key storage tests."""

import importlib.util
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "provider_secrets", ROOT / "scripts" / "provider_secrets.py"
)
secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secrets)


class ProviderSecretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name) / "provider-secrets"

    def tearDown(self):
        self.temp.cleanup()

    def test_store_load_and_delete_are_private(self):
        secrets.store("gemini", "gemini-test-key", self.root)
        self.assertEqual(secrets.load("gemini", self.root), "gemini-test-key")
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.root / "gemini").st_mode & 0o777, 0o600)
        self.assertTrue(secrets.delete("gemini", self.root))
        self.assertIsNone(secrets.load("gemini", self.root))

    def test_unknown_provider_and_malformed_keys_are_rejected(self):
        with self.assertRaises(secrets.ProviderSecretError):
            secrets.store("openai", "not-used", self.root)
        for value in ("", "contains whitespace"):
            with self.assertRaises(secrets.ProviderSecretError):
                secrets.store("gemini", value, self.root)

    def test_symlink_key_is_refused(self):
        self.root.mkdir(parents=True)
        target = pathlib.Path(self.temp.name) / "elsewhere"
        target.write_text("secret")
        (self.root / "anthropic").symlink_to(target)
        with self.assertRaises(secrets.ProviderSecretError):
            secrets.load("anthropic", self.root)


if __name__ == "__main__":
    unittest.main()
