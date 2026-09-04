import base64
import gzip
import importlib.util
import json
import os
import pathlib
import unittest
from pathlib import Path
from unittest import mock


PAGE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "api" / "page.py"
SPEC = importlib.util.spec_from_file_location("vercel_page", PAGE_PATH)
PAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAGE)


class VercelPageTest(unittest.TestCase):
    def test_the_board_has_no_password_and_no_way_to_reintroduce_one(self):
        """The gate is gone, and its machinery with it.

        Removed at the owner's instruction so anyone opening the site sees what
        he sees, with no prompt. Asserted on the absence of the helper rather
        than on a status code, because a status code can be right for a
        deployment-shaped reason while an authorization function sits there
        waiting for a caller. Nothing here should be able to challenge.
        """
        self.assertFalse(hasattr(PAGE, "_authorized"))
        source = pathlib.Path(PAGE.__file__).read_text()
        for banned in ("WWW-Authenticate", "Authentication required", "401"):
            self.assertNotIn(banned, source, f"{banned} is still reachable")

    def test_the_page_serves_a_request_that_carries_nothing(self):
        """Not merely un-gated: it answers a bare request.

        DASHBOARD_PASSWORD is set here on purpose. A leftover value in the
        environment must not resurrect a prompt, which is the way this could
        silently come back.
        """
        with mock.patch.dict(os.environ, {"DASHBOARD_PASSWORD": "leftover"}, clear=False):
            source = pathlib.Path(PAGE.__file__).read_text()
            self.assertNotIn('os.environ.get("DASHBOARD_PASSWORD")', source)

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
