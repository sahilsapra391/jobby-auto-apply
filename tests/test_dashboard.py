"""Regression tests for scripts/dashboard.py.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard", ROOT / "scripts" / "dashboard.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


def build(sessions=(), history=(), profile=None):
    return dashboard.build({"profile": profile or {}, "sessions": list(sessions),
                            "history": list(history)}, "test")


class DashboardTest(unittest.TestCase):
    def test_safe_url_blocks_non_http_schemes(self):
        self.assertEqual(dashboard.safe_url("javascript:alert(1)"), "")
        self.assertEqual(dashboard.safe_url("data:text/html,x"), "")
        self.assertEqual(dashboard.safe_url(None), "")
        self.assertEqual(dashboard.safe_url("https://ok.example"), "https://ok.example")

    def test_scraped_javascript_url_never_reaches_href(self):
        html = build(sessions=[{"company": "X", "role": "PM", "status": "active",
                                "url": "javascript:alert(1)", "pendingFields": []}])
        self.assertNotIn("javascript:", html)

    def test_markup_in_store_data_is_escaped(self):
        html = build(sessions=[{"company": "<script>x</script>", "role": "PM",
                                "status": "active", "pendingFields": []}])
        self.assertNotIn("<script>x", html)

    def test_outcome_keeps_application_on_submitted_tab(self):
        html = build(history=[{"event": "interview", "company": "A", "role": "PM", "at": "2026-08-15"}])
        self.assertIn("interview", html)
        self.assertNotIn("Nothing submitted yet", html)

    def test_orphaned_submitting_is_flagged(self):
        html = build(history=[{"event": "submitting", "company": "C", "role": "PM", "at": "2026-08-15"}])
        self.assertIn("submission attempted, interrupted", html)

    def test_page_declares_utf8(self):
        # Without this, a server that omits charset renders every non-ASCII
        # character in the store as mojibake.
        self.assertTrue(build().lstrip().startswith('<meta charset="utf-8">'))

    def test_non_ascii_store_data_survives_a_round_trip(self):
        html = build(sessions=[{"company": "Renée & Cie", "role": "PM — Agents",
                                "status": "active", "pendingFields": []}])
        self.assertIn("Renée", html)
        self.assertIn("PM — Agents", html)
        self.assertEqual(html, html.encode("utf-8").decode("utf-8"))

    def test_submitted_rows_link_to_the_posting_via_the_role_index(self):
        # Sessions are deleted once an application completes, so the URL has to
        # come from the durable role index or the Submitted tab loses its link.
        html = dashboard.build({
            "profile": {}, "sessions": [], "history": [
                {"event": "completed", "company": "A", "role": "PM",
                 "applicationId": "a-pm", "at": "2026-08-19"}],
            "roles": {"a-pm": {"company": "A", "role": "PM",
                               "url": "https://example.com/a-pm"}},
        }, "test")
        self.assertIn("https://example.com/a-pm", html)

    def test_role_index_url_survives_a_missing_session_url(self):
        html = dashboard.build({
            "profile": {}, "sessions": [
                {"company": "B", "role": "PM", "status": "active",
                 "applicationId": "b-pm", "pendingFields": []}],
            "history": [],
            "roles": {"b-pm": {"url": "https://example.com/b-pm"}},
        }, "test")
        self.assertIn("https://example.com/b-pm", html)

    def test_completed_not_flagged_as_unconfirmed(self):
        html = build(history=[{"event": "completed", "company": "C", "role": "PM", "at": "2026-08-15"}])
        self.assertNotIn("Not confirmed submitted", html)


class BrandTest(unittest.TestCase):
    def test_mark_outline_is_emitted_once_and_referenced(self):
        # The traced outline is ~7KB. It appears in the app bar and again in
        # the hero, so inlining it twice would cost a pointless second copy.
        html = build()
        self.assertEqual(html.count('id="jb-mark"'), 1)
        self.assertEqual(html.count('href="#jb-mark"'), 2)

    def test_logo_inverts_rather_than_dims_in_dark(self):
        # An ink circle on a near-black page has almost no edge, so dark mode
        # swaps to the paper circle instead of just fading the light one.
        html = build()
        self.assertIn("--brand-bg:#2B2D2C; --brand-fg:#FFFFFF;", html)
        # Once for the prefers-color-scheme block, once for the forced one.
        self.assertEqual(html.count("--brand-bg:#F8F8F8; --brand-fg:#2B2D2C;"), 2)

    def test_marks_are_painted_from_theme_tokens(self):
        html = build()
        self.assertIn('fill="var(--brand-bg)"', html)
        self.assertIn('fill="var(--brand-fg)"', html)

    def test_theme_control_offers_all_three_states(self):
        html = build()
        for mode in ("auto", "light", "dark"):
            self.assertIn(f'data-theme-set="{mode}"', html)

    def test_theme_is_applied_before_the_stylesheet(self):
        # Restoring the saved theme after paint shows one frame of the OS
        # theme first, which reads as a flash on every load.
        html = build()
        self.assertLess(html.index("jobby.theme"), html.index("<style>"))

    def test_favicon_is_inlined(self):
        # publish.py inlines the page into a serverless function, so a favicon
        # that pointed at a sibling file would 404 behind the password gate.
        html = build()
        self.assertIn("data:image/svg+xml;base64,", html)
        self.assertNotIn('href="/favicon', html)


if __name__ == "__main__":
    unittest.main()
