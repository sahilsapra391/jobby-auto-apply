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
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("dashboard", ROOT / "scripts" / "dashboard.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)
import found_lane  # noqa: E402


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
        # Two hours before the sweep, not "today": at 00:30 UTC that is
        # yesterday, and asserting on today fails once a day for the wrong
        # reason.
        self.add("zeta", postedAgo="2 hours ago")
        expected = (datetime.now(timezone.utc) - timedelta(hours=2)).date().isoformat()
        self.assertIn(expected, self.render())

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
        expected = (old_scan - timedelta(hours=2)).date().isoformat()
        self.assertIn(expected, self.render())
        self.assertNotIn(datetime.now(timezone.utc).date().isoformat(), self.render())

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


class FoundLaneTests(unittest.TestCase):
    """The Found lane.

    It is the only lane rendered on the client, which changes what can go
    wrong. Job titles and descriptions come off the open internet, and they
    travel to the browser inside a script element — so the tests that matter
    most here are the ones about a posting being unable to escape the box it
    arrives in.
    """

    def job(self, job_id="j1", **fields):
        base = {
            "id": job_id, "company": "Northwind Analytics",
            "title": "Product Manager, Internal Reporting",
            "canonicalUrl": "https://job-boards.greenhouse.io/northwind/jobs/4471002",
            "canonicalSource": "greenhouse", "state": "new",
            "location": {"raw": "New York, NY", "city": "New York",
                         "region": "NY", "country": "US"},
            "workplace": "hybrid", "postedAt": "2026-08-13T00:00:00Z",
            "firstSeenAt": "2026-08-19T20:00:00Z",
            "descriptionExcerpt": "Own the internal reporting platform.",
            "tags": ["sql", "analytics"],
            "sources": [{"source": "greenhouse", "sourceClass": "ats",
                         "url": "https://job-boards.greenhouse.io/northwind/jobs/4471002"}],
            "extracted": {"yearsMax": None, "degree": None,
                          "employmentType": None, "roleCategory": None},
            "score": {"total": 82, "raw": 82,
                      "parts": {"title": 35, "experience": 10, "location": 15,
                                "keywords": 14, "freshness": 8},
                      "weights": {"title": 35, "experience": 20, "location": 15,
                                  "keywords": 20, "freshness": 10},
                      "why": {"title": "title matches a searched title"},
                      "unknowns": ["experience"], "gates": []},
        }
        base.update(fields)
        return base

    def render(self, jobs=(), meta=None, config=None, **state):
        return dashboard.build({
            "profile": {}, "sessions": [], "history": [], "roles": {},
            "meta": meta or {}, "jobs": {j["id"]: j for j in jobs},
            "searchConfig": config, **state}, "test")

    def payload(self, html):
        start = html.index('id="jb-found-data">') + len('id="jb-found-data">')
        return html[start:html.index("</script>", start)]

    def test_found_is_the_first_tab_and_leads_when_it_has_anything(self):
        html = self.render([self.job()])
        self.assertLess(html.index('data-tab="found"'), html.index('data-tab="act"'))
        self.assertIn('class="tab on" data-tab="found"', html)
        self.assertIn('<div class="panel on" id="p-found"', html)

    def test_needs_you_still_leads_when_nothing_has_been_found(self):
        # The lane is new; the habit of opening the board to see what is
        # blocking is not.
        html = self.render([])
        self.assertIn('class="tab on" data-tab="act"', html)
        self.assertIn('<div class="panel on" id="p-act"', html)

    def test_the_header_line_still_says_what_is_waiting(self):
        html = dashboard.build({
            "profile": {}, "sessions": [{"company": "A", "role": "PM", "status": "review",
                                         "pendingFields": [{"question": "x"}]}],
            "history": [], "roles": {}, "meta": {},
            "jobs": {"j1": self.job()}, "searchConfig": None}, "test")
        self.assertIn("1 waiting on you", html)

    def test_only_unacted_on_jobs_reach_the_lane(self):
        jobs = [self.job("a"), self.job("b", state="watched"),
                self.job("c", state="passed"), self.job("d", state="applied")]
        html = self.render(jobs)
        self.assertEqual(len(json.loads(self.payload(html))), 1)
        tab = re.search(r'data-tab="found"[^>]*>Found<span class="n">(\d+)</span>', html)
        self.assertEqual(tab.group(1), "1", "the tab count must match what the lane holds")

    def test_a_hostile_title_cannot_escape_the_data_block(self):
        # This is the one that matters. The blob rides inside a script element,
        # and `</` is the only sequence that can terminate one from inside a
        # string. A posting is untrusted text written by a stranger.
        nasty = self.job(title='</script><img src=x onerror=alert(1)>')
        html = self.render([nasty])
        self.assertNotIn("</script><img", html)
        self.assertIn("<\\/script>", self.payload(html))
        # And the blob still parses, so the lane is not broken by the escaping.
        json.loads(self.payload(html).replace("<\\/", "</"))

    def test_hostile_text_anywhere_in_a_posting_is_escaped(self):
        html = self.render([self.job(
            company="<script>x</script>",
            descriptionExcerpt='</script><script>alert(1)</script>',
            tags=["</script>"])])
        self.assertNotIn("<script>x</script>", html)
        self.assertNotIn("</script><script>alert(1)", html)

    def test_a_javascript_url_never_becomes_a_link(self):
        html = self.render([self.job(
            canonicalUrl="javascript:alert(1)",
            sources=[{"source": "x", "sourceClass": "ats", "url": "javascript:alert(1)"}])])
        self.assertNotIn("javascript:", html)

    def test_the_payload_carries_what_the_card_needs(self):
        rows = json.loads(self.payload(self.render([self.job()])))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for key in ("id", "co", "ti", "url", "sc", "pt", "wt", "un", "bd", "sg", "ex"):
            self.assertIn(key, row)
        self.assertEqual(row["sc"], 82)
        self.assertEqual(row["un"], ["experience"])

    def test_the_embed_cap_holds_and_the_footer_says_what_is_missing(self):
        # The stored pool is 3,000. Showing a slice as though it were
        # everything is the failure; saying which slice is the fix.
        jobs = [self.job(f"j{i}", score={"total": i % 100, "parts": {}, "weights": {},
                                         "why": {}, "unknowns": [], "gates": []})
                for i in range(found_lane.EMBED_CAP + 120)]
        html = self.render(jobs)
        self.assertEqual(len(json.loads(self.payload(html))), found_lane.EMBED_CAP)
        self.assertIn(f"Showing {found_lane.EMBED_CAP} of {found_lane.EMBED_CAP + 120}", html)

    def test_the_highest_scoring_jobs_are_the_ones_embedded(self):
        jobs = [self.job(f"j{i}", score={"total": i, "parts": {}, "weights": {},
                                         "why": {}, "unknowns": [], "gates": []})
                for i in range(found_lane.EMBED_CAP + 50)]
        rows = json.loads(self.payload(self.render(jobs)))
        self.assertEqual(min(r["sc"] for r in rows), 50)

    def test_the_page_stays_under_four_megabytes_at_the_cap(self):
        long_body = "Own the internal reporting platform. " * 60
        jobs = [self.job(f"j{i}", descriptionExcerpt=long_body,
                         tags=[f"tag{n}" for n in range(12)])
                for i in range(found_lane.EMBED_CAP)]
        size = len(self.render(jobs).encode("utf-8"))
        self.assertLess(size, 4 * 1024 * 1024, f"page is {size / 1024 / 1024:.2f} MB")

    def test_the_embedded_excerpt_is_trimmed(self):
        rows = json.loads(self.payload(self.render([self.job(descriptionExcerpt="x" * 5000)])))
        self.assertLessEqual(len(rows[0]["ex"]), found_lane.EMBED_EXCERPT_CHARS)

    def test_a_merged_job_says_how_many_boards_carried_it(self):
        rows = json.loads(self.payload(self.render([self.job(sources=[
            {"source": "greenhouse", "sourceClass": "ats", "url": "https://a.test/1"},
            {"source": "adzuna", "sourceClass": "aggregator", "url": "https://b.test/1"},
            {"source": "linkedin", "sourceClass": "aggregator", "url": "https://c.test/1"}])])))
        self.assertEqual(rows[0]["bd"], 3)
        self.assertEqual(len(rows[0]["sg"]), 3)


class FoundHeaderTests(unittest.TestCase):
    """The last-ingest line, which is where a silent failure would show up."""

    def render(self, meta=None, config=None, jobs=()):
        return dashboard.build({
            "profile": {}, "sessions": [], "history": [], "roles": {},
            "meta": meta or {}, "jobs": {j["id"]: j for j in jobs},
            "searchConfig": config}, "test")

    def test_no_scan_yet_says_so_rather_than_nothing(self):
        self.assertIn("No scan has run yet", self.render())

    def test_a_scan_that_found_nothing_reads_differently_from_one_that_never_ran(self):
        quiet = self.render(meta={"lastIngest": {
            "at": "2026-08-19T20:58:00Z", "found": 0, "kept": 0, "deduped": 0,
            "gatedOut": 0, "byChannel": {}, "channelsSkipped": [], "errors": []}})
        self.assertIn("Last scan 20:58", quiet)
        self.assertNotIn("No scan has run yet", quiet)

    def test_skipped_channels_are_named_on_the_board(self):
        # A missing key that is only visible in a log is a missing key nobody
        # ever installs.
        html = self.render(meta={"lastIngest": {
            "at": "2026-08-19T20:58:00Z", "found": 12, "kept": 9, "deduped": 2,
            "gatedOut": 1, "byChannel": {},
            "channelsSkipped": [{"channel": "adzuna", "why": "no key (JOBBY_ADZUNA_ID is not set)"},
                                {"channel": "reed", "why": "the saved search does not include the UK"}],
            "errors": []}})
        self.assertIn("2 channels skipped", html)
        self.assertIn("adzuna", html)
        self.assertIn("JOBBY_ADZUNA_ID", html)

    def test_a_connector_outage_is_on_the_board(self):
        html = self.render(meta={"lastIngest": {
            "at": "2026-08-19T20:58:00Z", "found": 0, "kept": 0, "deduped": 0,
            "gatedOut": 0, "byChannel": {}, "channelsSkipped": [],
            "errors": [{"channel": "remotive", "why": "HTTP 503"}]}})
        self.assertIn("1 problem", html)
        self.assertIn("remotive", html)
        self.assertIn("503", html)

    def test_a_failed_scan_says_the_scan_failed(self):
        html = self.render(meta={"lastIngest": {
            "at": "2026-08-19T20:58:00Z", "found": 0, "kept": 0, "deduped": 0,
            "gatedOut": 0, "byChannel": {}, "channelsSkipped": [],
            "errors": [{"channel": "ingest", "why": "store unreadable"}], "failed": True}})
        self.assertIn("the scan itself failed", html)

    def test_running_on_defaults_is_admitted(self):
        html = self.render(meta={"lastIngest": {
            "at": "2026-08-19T20:58:00Z", "found": 3, "kept": 3, "deduped": 0,
            "gatedOut": 0, "byChannel": {}, "channelsSkipped": [], "errors": [],
            "usingDefaults": True}})
        self.assertIn("no saved search yet", html)

    def test_no_saved_search_says_the_hourly_scan_is_off(self):
        html = self.render()
        self.assertIn("No saved search", html)
        self.assertIn("stays off", html)

    def test_a_saved_search_shows_its_version_and_next_run(self):
        html = self.render(config={"version": 7, "tier1IntervalMinutes": 60,
                                   "tier2SweepsPerDay": 6},
                           meta={"nextTier1ScanAt": "2026-08-19T21:58:00Z"})
        self.assertIn("Search v7 is live", html)
        self.assertIn("next run 21:58", html)
        self.assertIn("6 sweeps a day", html)

    def test_the_add_your_own_link_card_is_there(self):
        html = self.render()
        self.assertIn('id="jb-link"', html)
        self.assertIn('id="jb-addlink"', html)


class ThemeCompletenessTests(unittest.TestCase):
    """Every colour defined in all three theme blocks.

    A colour defined only inside the media query disappears the moment somebody
    picks the other theme explicitly, and the failure is invisible until then.
    """

    def blocks(self, html):
        style = html[html.index("<style>"):html.index("</style>")]
        light = style[style.index(":root {"):style.index("@media (prefers-color-scheme:dark)")]
        dark_auto = style[style.index("@media (prefers-color-scheme:dark)"):
                          style.index(':root[data-theme="dark"]')]
        dark_set = style[style.index(':root[data-theme="dark"]'):
                         style.index("*,*::before,*::after")]
        return light, dark_auto, dark_set

    def names(self, block):
        return set(re.findall(r"(--[a-z0-9-]+):", block))

    def test_the_two_dark_blocks_define_the_same_colours(self):
        light, dark_auto, dark_set = self.blocks(dashboard.build(
            {"profile": {}, "sessions": [], "history": [], "roles": {}, "meta": {},
             "jobs": {}, "searchConfig": None}, "test"))
        self.assertEqual(self.names(dark_auto), self.names(dark_set))

    def test_every_dark_override_has_a_light_definition(self):
        light, dark_auto, _ = self.blocks(dashboard.build(
            {"profile": {}, "sessions": [], "history": [], "roles": {}, "meta": {},
             "jobs": {}, "searchConfig": None}, "test"))
        self.assertEqual(self.names(dark_auto) - self.names(light), set())

    def test_the_found_lane_colours_exist_in_all_three(self):
        light, dark_auto, dark_set = self.blocks(dashboard.build(
            {"profile": {}, "sessions": [], "history": [], "roles": {}, "meta": {},
             "jobs": {}, "searchConfig": None}, "test"))
        for name in ("--t-red", "--red-ink"):
            for block in (light, dark_auto, dark_set):
                self.assertIn(f"{name}:", block)


class CspTests(unittest.TestCase):
    def test_the_page_loads_nothing_from_anywhere(self):
        # default-src 'none' means a posting that smuggled markup through still
        # cannot phone home. Any external reference would break under it.
        html = dashboard.build({"profile": {}, "sessions": [], "history": [], "roles": {},
                                "meta": {}, "jobs": {}, "searchConfig": None}, "test")
        for banned in ("src=\"http", "src='http", "@import", "href=\"http://cdn",
                       "fonts.googleapis", "unpkg.com", "jsdelivr"):
            self.assertNotIn(banned, html)
        for link in re.findall(r'<link[^>]+href="([^"]+)"', html):
            self.assertFalse(link.startswith("http"), link)
