"""The device's provider status no longer depends on the retired board.

`provider_login.publish_status` runs on watcher ticks and publishes
`jobby:worker:local:status`, which v2's Settings page polls. It used to call
`settings_panel.public_payload`, and `settings_panel.py` is v1's Settings
renderer, due for deletion. These pin that the device path reaches the payload
without importing any v1 module, so that deletion cannot take the live status
with it.
"""

import ast
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import provider_status  # noqa: E402

V1_MODULES = {"settings_panel", "dashboard", "profile_panel", "found_lane", "premium_ui"}


def imported(path):
    tree = ast.parse(pathlib.Path(path).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class TheDevicePathNeedsNoBoard(unittest.TestCase):
    def test_provider_login_imports_no_v1_module(self):
        names = imported(REPO / "scripts" / "provider_login.py")
        self.assertIn("provider_status", names)
        self.assertFalse(names & V1_MODULES, names & V1_MODULES)

    def test_provider_status_imports_no_v1_module(self):
        self.assertFalse(imported(REPO / "scripts" / "provider_status.py") & V1_MODULES)

    def test_settings_panel_reexports_rather_than_copies(self):
        import settings_panel
        self.assertIs(settings_panel.public_payload, provider_status.public_payload)
        self.assertIs(settings_panel.PROVIDERS, provider_status.PROVIDERS)

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
