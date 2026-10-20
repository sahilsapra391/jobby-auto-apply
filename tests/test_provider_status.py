"""The provider status the device publishes, and the module it comes from.

`provider_login.publish_status` runs on watcher ticks and publishes
`jobby:worker:local:status`, which v2's Settings page polls. It used to call
`settings_panel.public_payload`, a module of the original Python board, and
that board's removal would have taken the live status with it. The payload now
lives in `provider_status.py`, which is device code.
"""

import ast
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import provider_status  # noqa: E402

def imported(path):
    tree = ast.parse(pathlib.Path(path).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class TheDevicePublishesItsOwnStatus(unittest.TestCase):
    def test_provider_login_publishes_through_provider_status(self):
        names = imported(REPO / "scripts" / "provider_login.py")
        self.assertIn("provider_status", names)

    def test_the_payload_still_marks_the_active_provider(self):
        status = {"activeProvider": "anthropic", "providers": [
            {"id": "openai", "enabled": True, "status": "healthy"},
            {"id": "anthropic", "enabled": True, "status": "healthy"},
        ]}
        payload = provider_status.public_payload(status)
        rows = {row["id"]: row for row in payload["providers"]}
        self.assertTrue(rows["anthropic"]["active"])
        self.assertFalse(rows["openai"]["active"])
        self.assertEqual(rows["openai"]["status"], "connected")
        self.assertEqual(payload["activeProvider"], "anthropic")
        self.assertEqual([r["id"] for r in payload["providers"]],
                         ["openai", "anthropic", "gemini"])
