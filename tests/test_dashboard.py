"""Regression tests for scripts/dashboard.py.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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

    def test_logo_is_the_same_mark_in_every_theme(self):
        # One logo everywhere: white mark on an ink circle, on the board, in
        # the tab strip and on a home screen. Defined once, never overridden.
        html = build()
        self.assertEqual(html.count("--brand-bg:#2B2D2C; --brand-fg:#FFFFFF;"), 1)
        self.assertNotIn("--brand-bg:#F8F8F8", html)
        self.assertNotIn("--brand-fg:#2B2D2C", html)

    def test_favicon_does_not_flip_in_dark_chrome(self):
        # The pack ships the icon with a prefers-color-scheme swap built in.
        # Shipping that unchanged would give a paper circle in a dark tab bar.
        import base64
        html = build()
        blob = html.split('data:image/svg+xml;base64,')[1].split('"')[0]
        self.assertNotIn("prefers-color-scheme", base64.b64decode(blob).decode())

    def test_marks_are_not_clipped_by_their_own_viewbox(self):
        # The circle is tangent to the viewBox on all four sides, so the
        # default SVG overflow:hidden shaves the antialiased edge flat.
        html = build()
        self.assertIn("overflow:visible", html)

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


class PostedDateTests(unittest.TestCase):
    """The Posted column has to lead with a real date, because it is the sort key.

    A role found "2 hours ago" recorded only that string, so it had nothing
    sortable and sank to the bottom of a list ordered newest first, which is the
    exact opposite of where it belonged.
    """

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "init"], capture_output=True)

    def add(self, name, **fields):
        session = {"applicationId": name, "company": name.title(), "role": "PM",
                   "status": "watch", **fields}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(session, f)
            path = f.name
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "session-save", "--id", name,
                        "--input", path], capture_output=True)
        os.unlink(path)

    def render(self):
        out = pathlib.Path(tempfile.mkdtemp()) / "b.html"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "dashboard.py"),
                        "--root", str(self.root), "--out", str(out)], capture_output=True)
        html = out.read_text(encoding="utf-8")
        body = html[html.index('id="p-watch"'):]
        return body[body.index("<tbody>"):body.index("</tbody>")]

    def order(self):
        return re.findall(r"<strong>([^<]+)</strong>", self.render())

    def test_a_relative_only_posting_still_gets_a_date(self):
        self.add("zeta", postedAgo="2 hours ago")
        today = datetime.now(timezone.utc).date().isoformat()
        self.assertIn(today, self.render())

    def test_hours_ago_sorts_above_an_older_dated_posting(self):
        self.add("older", postedDate="2020-01-01")
        self.add("fresh", postedAgo="2 hours ago")
        self.assertEqual(self.order()[0], "Fresh")

    def test_the_relative_string_is_kept_underneath_the_date(self):
        self.add("zeta", postedAgo="2 hours ago")
        cell = self.render()
        self.assertIn("2 hours ago", cell)
        self.assertIn('class="ago', cell)

    def test_weeks_and_months_resolve_to_real_dates(self):
        self.add("weeks", postedAgo="3 weeks ago")
        self.add("days", postedAgo="2 days ago")
        self.assertEqual(self.order(), ["Days", "Weeks"])

    def test_a_posting_with_no_date_at_all_sorts_last(self):
        self.add("nodate")
        self.add("dated", postedDate="2020-01-01")
        self.assertEqual(self.order()[-1], "Nodate")
        self.assertIn("unknown", self.render())

    def test_postedDays_still_works(self):
        self.add("byday", postedDays=1)
        expected = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        self.assertIn(expected, self.render())

    def test_the_shapes_sweeps_actually_write(self):
        # Every one of these came off a real watch entry. Requiring the word
        # "ago" matched almost none of them.
        for name, note in [("a", "~23 hours"), ("b", "~20-21 hours"),
                           ("c", "LinkedIn feed said 3 hours"), ("d", "~3 weeks"),
                           ("e", "22 days (released)")]:
            self.add(name, postedAgo=note)
        rendered = self.render()
        self.assertNotIn("unknown", rendered,
                         "every one of these carries enough to date it")

    def test_a_date_written_into_the_note_beats_a_misleading_relative(self):
        # A LinkedIn feed says "3 hours" for a posting Greenhouse dates months
        # back. The real date is the one in the note.
        self.add("klaviyo", postedAgo="LinkedIn feed said 3 hours, but Greenhouse says 2026-07-28")
        self.assertIn("2026-07-28", self.render())

    def test_a_relative_note_is_measured_from_when_the_sweep_ran(self):
        # "2 hours" recorded three days ago means three days ago, not today.
        # Measuring from now would make stale finds look brand new.
        old_scan = (datetime.now(timezone.utc) - timedelta(days=3))
        self.add("stale", postedAgo="2 hours")
        path = self.root / "sessions" / "stale.json"
        session = json.loads(path.read_text())
        session["updatedAt"] = old_scan.strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(json.dumps(session))
        self.assertIn(old_scan.date().isoformat(), self.render())

    def test_unparseable_text_is_shown_rather_than_invented(self):
        self.add("vague", postedAgo="not shown")
        self.assertIn("not shown", self.render())


class DocumentLinkTests(unittest.TestCase):
    """Links to the resume and cover letter an employer received.

    Runs record the document either way: some as a bare filename, some as
    "applications/Cohere/Sahil_Sapra_Cohere_Resume.pdf". Documents are published
    under the basename and the route rejects a name containing slashes, so the
    path-recorded ones produced links that resolved to "No such document" while
    the files themselves were published and fine.
    """

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "init"], capture_output=True)

    def completed(self, aid, resume, cover):
        event = {"event": "completed", "applicationId": aid, "company": aid.title(),
                 "role": "PM", "confirmation": "submitted",
                 "resumeVariant": resume, "coverLetter": cover}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(event, f)
            path = f.name
        subprocess.run([sys.executable, str(ROOT / "scripts" / "store.py"),
                        "--root", str(self.root), "history-append", "--input", path],
                       capture_output=True)
        os.unlink(path)

    def links(self):
        out = pathlib.Path(tempfile.mkdtemp()) / "b.html"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "dashboard.py"),
                        "--root", str(self.root), "--out", str(out)], capture_output=True)
        return re.findall(r'href="/doc/([^"]+)"', out.read_text(encoding="utf-8"))

    def test_a_path_recorded_document_links_by_basename(self):
        self.completed("cohere", "applications/Cohere/Sahil_Sapra_Cohere_Resume.pdf",
                       "applications/Cohere/Sahil_Sapra_Cohere_CoverLetter.pdf")
        self.assertEqual(sorted(self.links()),
                         ["Sahil_Sapra_Cohere_CoverLetter.pdf",
                          "Sahil_Sapra_Cohere_Resume.pdf"])

    def test_a_filename_recorded_document_is_unchanged(self):
        self.completed("criteo", "Sahil_Sapra_Criteo_Resume.pdf",
                       "Sahil_Sapra_Criteo_CoverLetter.pdf")
        self.assertIn("Sahil_Sapra_Criteo_Resume.pdf", self.links())

    def test_no_link_ever_contains_a_path_separator(self):
        # The route only accepts [A-Za-z0-9._-], so a slash is a guaranteed 404.
        self.completed("a", "applications/A/x.pdf", "applications/A/y.pdf")
        self.completed("b", "z.pdf", "w.pdf")
        for link in self.links():
            self.assertNotIn("/", link)
            self.assertNotIn("%2F", link)
