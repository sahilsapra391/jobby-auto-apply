import base64
import gzip
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock


PAGE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "api" / "page.py"
SPEC = importlib.util.spec_from_file_location("vercel_page", PAGE_PATH)
PAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAGE)


class VercelPageTest(unittest.TestCase):
    def test_password_comparison_uses_basic_password(self):
        good = "Basic " + base64.b64encode(b"person:secret").decode()
        wrong = "Basic " + base64.b64encode(b"person:nope").decode()
        with mock.patch.dict(os.environ, {"DASHBOARD_PASSWORD": "secret"}, clear=False):
            self.assertTrue(PAGE._authorized(good))
            self.assertFalse(PAGE._authorized(wrong))
            self.assertFalse(PAGE._authorized("Basic not-base64"))

    def test_compressed_state_envelope_round_trips(self):
        state = {
            "profile": {"firstName": "A"}, "sessions": [], "history": [],
            "roles": {}, "meta": {}, "jobs": {}, "searchConfig": None,
            "providers": {},
        }
        payload = base64.b64encode(gzip.compress(json.dumps(state).encode())).decode()
        envelope = json.dumps({
            "schemaVersion": 1,
            "rendererSchemaVersion": 1,
            "encoding": "gzip+base64",
            "generatedAt": "2026-08-24T12:00:00Z",
            "payload": payload,
        }).encode()
        with mock.patch.object(PAGE, "_kv_value", return_value=envelope):
            actual, generated, fingerprint = PAGE._state()
        self.assertEqual(actual, state)
        self.assertEqual(generated, "2026-08-24T12:00:00Z")
        # Absent on this envelope, which is the older publisher's shape. The
        # board still renders; the fingerprint only blocks when it disagrees.
        self.assertEqual(fingerprint, "")

    def test_a_published_fingerprint_is_carried_out_of_the_envelope(self):
        state = {"sessions": [], "history": []}
        payload = base64.b64encode(gzip.compress(json.dumps(state).encode())).decode()
        envelope = json.dumps({
            "schemaVersion": 1,
            "rendererSchemaVersion": 1,
            "encoding": "gzip+base64",
            "generatedAt": "2026-08-24T12:00:00Z",
            "rendererFingerprint": "abc123abc123abc1",
            "payload": payload,
        }).encode()
        with mock.patch.object(PAGE, "_kv_value", return_value=envelope):
            _, _, fingerprint = PAGE._state()
        self.assertEqual(fingerprint, "abc123abc123abc1")

    def test_renderer_rejects_unsupported_state_schema(self):
        envelope = json.dumps({"schemaVersion": 2, "payload": "x"}).encode()
        with mock.patch.object(PAGE, "_kv_value", return_value=envelope):
            with self.assertRaisesRegex(ValueError, "schema"):
                PAGE._state()


if __name__ == "__main__":
    unittest.main()
