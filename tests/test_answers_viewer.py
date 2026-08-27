"""The Submitted lane's Answers column and the modal behind it.

The board is one self-contained page under `default-src 'none'`, and every
question in a record was written by a stranger. So the interesting properties
here are negative: the record text is not in the page at all, the modal never
builds a row out of markup, and a row with no record says so rather than opening
empty.
"""

import importlib.util
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("dashboard", ROOT / "scripts" / "dashboard.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)
import answer_record  # noqa: E402
import premium_ui  # noqa: E402


SUBMITTED = [
    {"event": "completed", "applicationId": "app-captured", "company": "Acme",
     "role": "Product Manager", "at": "2026-08-20T10:00:00Z",
     "confirmation": "Thanks for applying.",
     "resumeVariant": "Sahil_Acme_Resume.pdf",
     "coverLetter": "Sahil_Acme_CoverLetter.pdf"},
    {"event": "completed", "applicationId": "app-old", "company": "Older Ltd",
     "role": "Staff PM", "at": "2026-07-01T10:00:00Z",
     "confirmation": "Application received."},
]


def build(history=SUBMITTED, captured=()):
    return dashboard.build({
        "profile": {}, "sessions": [], "history": list(history), "roles": {},
        "meta": {"answersCaptured": list(captured)}, "jobs": {}, "searchConfig": None,
    }, "test")


def submitted_rows(page):
    """Just the Submitted lane's rows.

    The class name lives in the page script whether or not a row uses it, so
    "does any row offer the modal" has to be asked of the rows.
    """
    panel = page[page.index('id="p-done"'):]
    panel = panel[:panel.index('id="p-watch"')]
    return re.findall(r"<tr data-hay=.*?</tr>", panel, re.S)


class AnswersColumnTests(unittest.TestCase):
    def test_submitted_carries_an_answers_column_beside_the_documents(self):
        page = build(captured=["app-captured"])
        header = page[page.index("<th>Company</th>"):]
        header = header[:header.index("</tr>")]
        self.assertEqual(
            re.findall(r"<th>([^<]*)</th>", header),
            ["Company", "Role", "Status", "When", "Posting", "Proof", "Resume",
             "Cover letter", "Answers"])

    def test_a_captured_application_offers_the_modal(self):
        page = build(captured=["app-captured"])
        self.assertIn('class="btn ghost answers-open" type="button" '
                      'data-id="app-captured"', page)
        self.assertIn('aria-haspopup="dialog" aria-controls="p-answers"', page)

    def test_an_application_with_no_record_says_so_rather_than_opening_empty(self):
        page = build(captured=["app-captured"])
        old = [r for r in submitted_rows(page) if "Older Ltd" in r]
        self.assertEqual(len(old), 1)
        self.assertIn('<span class="muted">not captured</span>', old[0])
        self.assertNotIn("answers-open", old[0])

    def test_the_page_explains_not_captured_once_rather_than_per_row(self):
        page = build(captured=["app-captured"])
        self.assertEqual(page.count("nothing is reconstructed for them"), 1)
        self.assertIn("submitted before Jobby started writing that record down", page)

    def test_the_explanation_disappears_once_everything_is_captured(self):
        page = build(captured=["app-captured", "app-old"])
        self.assertNotIn("nothing is reconstructed for them", page)
        for row in submitted_rows(page):
            self.assertNotIn("not captured", row)
            self.assertIn("answers-open", row)

    def test_every_submitted_row_reads_not_captured_when_nothing_was(self):
        # Today's state: capture starts on this branch, so a hundred rows are
        # honest about having no record and none of them pretends otherwise.
        rows = submitted_rows(build(captured=[]))
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn('<span class="muted">not captured</span>', row)
            self.assertNotIn("answers-open", row)

    def test_the_row_count_matches_the_column_count(self):
        self.assertEqual(submitted_rows(build(captured=[]))[0].count("<td"), 9)

    def test_the_empty_lane_spans_every_column(self):
        page = build(history=[])
        self.assertNotIn("colspan='8'", page)

    def test_the_modal_exists_once_and_starts_closed(self):
        page = build(captured=["app-captured"])
        self.assertEqual(page.count('<dialog class="answers-dialog" id="p-answers"'), 1)
        dialog = page[page.index('id="p-answers"'):]
        dialog = dialog[:dialog.index("</dialog>")]
        self.assertNotIn(" open", dialog.split(">", 1)[0])
        self.assertIn('id="answers-body"', dialog)
        self.assertIn('aria-labelledby="answers-title"', dialog)


class UntrustedTextTests(unittest.TestCase):
    """A question, a company and a role are all somebody else's text."""

    def test_a_hostile_company_and_role_cannot_escape_the_button(self):
        page = build(history=[{
            "event": "completed", "applicationId": "app-x",
            "company": '</script><img src=x onerror=alert(1)>',
            "role": '"><svg onload=alert(2)>', "at": "2026-08-20T10:00:00Z",
        }], captured=["app-x"])
        self.assertNotIn("<img src=x onerror", page)
        self.assertNotIn("<svg onload=", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertIn("&lt;svg onload=alert(2)&gt;", page)

    def test_a_hostile_application_id_cannot_escape_the_data_attribute(self):
        page = build(history=[{
            "event": "completed", "applicationId": '" onclick="alert(1)',
            "company": "Acme", "role": "PM", "at": "2026-08-20T10:00:00Z",
        }], captured=['" onclick="alert(1)'])
        self.assertNotIn('data-id="" onclick="alert(1)"', page)
        self.assertIn("&quot; onclick=&quot;alert(1)", page)

    def test_no_question_or_answer_text_travels_in_the_page_at_all(self):
        # The strongest form of "hostile question text cannot escape the data
        # block": there is no data block. Records are fetched when a modal
        # opens, so the only per-application strings the page carries are the
        # id, the company and the role it already carried.
        page = build(captured=["app-captured"])
        self.assertNotIn("jb-answers-data", page)
        for marker in ("sourceKey", "sensitiveReason", '"withheld"'):
            self.assertNotIn(marker, page)

    def test_the_modal_reads_through_the_document_route_and_no_other(self):
        # A record is the same class of per-application artifact the resume and
        # the cover letter are, so it comes down the same authenticated proxy.
        # A route of its own would be a second auth gate to keep honest.
        page = build(captured=["app-captured"])
        self.assertIn("/doc/answers-", page)
        self.assertNotIn("/api/answers", page)

    def test_the_modal_builds_every_row_through_text_rather_than_markup(self):
        page = build(captured=["app-captured"])
        block = page[page.index("var answersDialog"):page.index("document.querySelectorAll('.answers-open')")]
        self.assertIn("textContent", block)
        # innerHTML, insertAdjacentHTML and outerHTML are the three ways a
        # question could stop being text. None of them appears in this block.
        for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML",
                       "document.write", "eval("):
            self.assertNotIn(banned, block, f"the answers modal must not use {banned}")

    def test_the_page_still_loads_nothing_from_anywhere(self):
        page = build(captured=["app-captured"])
        for banned in ('src="http', "src='http", "@import", "fonts.googleapis"):
            self.assertNotIn(banned, page)


class ValueDisplayTests(unittest.TestCase):
    """Sensitive answers show plainly. Withheld ones have nothing to show.

    Those are two different categories and only the first is a display choice.
    A covered value is a decision about a reader; a withheld one is a value that
    was never written, and no amount of clicking produces it.
    """

    def block(self):
        page = build(captured=["app-captured"])
        return page[page.index("function answerRow"):page.index("function renderAnswers")]

    def test_a_sensitive_answer_renders_like_any_other_answer(self):
        # Sahil's own board, Sahil's own salary. Covering it was friction with
        # no reader it protected against.
        block = self.block()
        self.assertNotIn("field.sensitive", block)
        # One value branch for everything that has a value, and it is the plain
        # one every other row uses.
        self.assertEqual(block.count("ansEl('span', 'ans-value'"), 1)

    def test_nothing_is_left_behind_that_looks_like_it_should_reveal(self):
        # A disabled reveal path is worse than either choice: it reads as a
        # control and does nothing. So the affordance is gone from the modal's
        # own script, from the stylesheet, and from the copy. Scoped to the
        # modal, because aria-expanded is how the location picker and the
        # styled selects legitimately describe themselves.
        page = build(captured=["app-captured"])
        modal = page[page.index("var answersDialog"):
                     page.index("document.querySelectorAll('.answers-open')")]
        for dead in ("ans-mask", "tap to reveal", "shown.hidden", "aria-expanded",
                     "'Hide'"):
            self.assertNotIn(dead, modal, f"{dead!r} is a leftover reveal affordance")
        self.assertNotIn("ans-mask", premium_ui.CSS)
        self.assertNotIn("stay covered", page)

    def test_a_withheld_row_says_so_and_offers_nothing_to_open(self):
        block = self.block()
        withheld = block[block.index("if (field.withheld)"):block.index("} else {")]
        self.assertIn("Not recorded", withheld)
        self.assertNotIn("addEventListener", withheld)
        self.assertNotIn("button", withheld)
        self.assertIn("ans-withheld", premium_ui.CSS)

    def test_every_source_the_record_can_carry_has_a_badge_and_a_style(self):
        page = build(captured=["app-captured"])
        for source in answer_record.SOURCES:
            self.assertIn(f"ans-src-{source} {{", premium_ui.CSS,
                          f"source {source} has no badge colour")
            self.assertIn(f"{source}:", page)

    def test_a_read_back_mismatch_is_never_folded_into_the_answer(self):
        page = build(captured=["app-captured"])
        self.assertIn("read back differently", page)
        self.assertIn("ans-differs", premium_ui.CSS)


class SalvagedRecordTests(unittest.TestCase):
    """A questions-only record must not read as an application with blank answers."""

    def block(self):
        page = build(captured=["app-captured"])
        return page[page.index("function answerRow"):page.index("function openAnswers")]

    def test_an_unrecorded_row_says_so_instead_of_rendering_blank(self):
        block = self.block()
        self.assertIn("if (field.unrecorded)", block)
        self.assertIn("Answer not recorded", block)

    def test_an_unrecorded_row_gets_no_source_or_read_back_badge(self):
        # Both would be invented. The row returns before the badge block.
        block = self.block()
        head = block[:block.index("var meta = ansEl")]
        self.assertIn("row.appendChild(ansEl('div', 'ans-meta'));", head)
        self.assertIn("return row;", head)

    def test_the_modal_explains_a_salvaged_record_before_the_rows(self):
        page = build(captured=["app-captured"])
        script = page[page.index("function renderAnswers"):page.index("function openAnswers")]
        self.assertIn("record.capture === 'questions-only'", script)
        self.assertIn("never written down", script)
        self.assertIn("nothing here", script)
        # Before the list, so a column of "Answer not recorded" is explained
        # by the time it is read.
        self.assertLess(script.index("ans-salvage"), script.index("ans-list"))

    def test_the_salvage_styles_exist_in_the_stylesheet(self):
        for name in (".ans-unrecorded", ".ans-salvage"):
            self.assertIn(name, premium_ui.CSS)


class ThemeTests(unittest.TestCase):
    """The modal adds no colour of its own, so it cannot miss a theme block."""

    def blocks(self):
        """The three token blocks, gathered across both stylesheets.

        The page concatenates the base stylesheet and premium_ui's, and each
        writes its own :root, its own OS-dark override and its own explicit
        dark block. Slicing between the first of each finds only the base one,
        which is how a premium-only token can look undefined when it is not.
        """
        page = build(captured=["app-captured"])
        style = page[page.index("<style>"):page.index("</style>")]

        def gather(selector):
            out, index = [], 0
            while True:
                start = style.find(selector + " {", index)
                if start < 0:
                    return "\n".join(out)
                end = style.index("}", start)
                out.append(style[start:end])
                index = end

        return (gather(":root"),
                gather(':root:not([data-theme="light"])'),
                gather(':root[data-theme="dark"]'))

    def answers_css(self):
        css = premium_ui.CSS
        start = css.index("  .answers-dialog {")
        end = css.index("  @media (max-width:640px) {", start)
        return css[start:end]

    def test_the_answers_styles_define_no_token_of_their_own(self):
        # A token defined here rather than in the theme blocks is exactly the
        # failure the three-block rule exists to stop, so the modal defines
        # none: it only spends what the palette already has.
        self.assertEqual(set(re.findall(r"(--[a-z0-9-]+)\s*:", self.answers_css())),
                         set())

    def test_every_colour_the_modal_spends_exists_in_all_three_blocks(self):
        light, dark_auto, dark_set = self.blocks()
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", self.answers_css()))
        self.assertTrue(used, "the answers styles reference no tokens at all")
        # --ease is a timing curve, not a colour, and is correctly root-only.
        non_colour = {"--ease", "--radius", "--shell"}
        for name in sorted(used):
            self.assertIn(f"{name}:", light, f"{name} has no light definition")
            if name in non_colour:
                continue
            self.assertIn(f"{name}:", dark_auto, f"{name} is missing under the OS dark theme")
            self.assertIn(f"{name}:", dark_set, f"{name} is missing under an explicit dark theme")

    def test_decorative_motion_stops_under_reduced_motion(self):
        self.assertIn(".answers-dialog[open],.answers-dialog::backdrop { animation:none; }",
                      premium_ui.CSS)

    def test_the_modal_narrows_on_a_phone(self):
        self.assertIn("@media (max-width:640px)", premium_ui.CSS)
        phone = premium_ui.CSS[premium_ui.CSS.index("  @media (max-width:640px) {"):]
        self.assertIn(".answers-dialog { width:calc(100vw - 1rem)", phone)


class StateCostTests(unittest.TestCase):
    def test_the_state_carries_ids_and_not_records(self):
        state = dashboard.publishable_state({
            "profile": {}, "sessions": [], "history": [], "roles": {},
            "meta": {"answersCaptured": ["app-captured"]}, "jobs": {},
            "searchConfig": None, "providers": {},
        })
        self.assertEqual(state["meta"]["answersCaptured"], ["app-captured"])
        serialized = json.dumps(state)
        self.assertNotIn("fields", serialized)

    def test_a_missing_or_broken_captured_list_renders_as_nothing_captured(self):
        for meta in ({}, {"answersCaptured": None}, {"answersCaptured": "app-1"},
                     {"answersCaptured": [None, 3, ""]}):
            with self.subTest(meta=meta):
                page = dashboard.build({
                    "profile": {}, "sessions": [], "history": list(SUBMITTED),
                    "roles": {}, "meta": meta, "jobs": {}, "searchConfig": None,
                }, "test")
                for row in submitted_rows(page):
                    self.assertNotIn("answers-open", row)
                    self.assertIn("not captured", row)


if __name__ == "__main__":
    unittest.main()
