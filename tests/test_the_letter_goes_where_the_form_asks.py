"""Where a cover letter may be pasted, and what happens when it may not.

**36 of 38 recorded runs report the form had no cover-letter field, against 2
where one attached.** Measured over `apply-runs.log` on 2026-09-10. That is the
normal case: the letter is built, most employers never ask for it, and a run
treating "no field" as a failure reports a problem that does not exist.

What is worth doing is the case one run found on its own initiative -- a form
with no upload but an open-ended box at the end -- and the danger is the case
next to it. "Why did you leave your last role" is free text and is not an
invitation; a cover letter in it is a non-answer under a real name.

**The vocabulary lives in trusted Python, not in the prompt.** A prompt is
advice to one model. The agent still has to read the page, because only it can
see what a field says, but what counts as open-ended is not its call. Same
argument `cover_letter.validate_content` already makes about greetings.
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402
import run_apply  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


#: Real question labels an application form asks, and whether the letter may go
#: in. The "no" side is the point: every one of these is free text.
SPECIFIC = [
    "Why did you leave your last role?",
    "What is your notice period?",
    "Describe a project you are proud of",
    "What is your expected salary?",
    "How did you hear about us?",
    "What is your average deal size?",
    "Do you require sponsorship now or in the future?",
]
OPEN_ENDED = [
    "Additional information",
    "Is there anything else you would like us to know?",
    "Why this role?",
    "Why do you want to work here?",
    "Anything else?",
]


def prompt_text():
    """The prompt the provider is handed, with wrapping removed.

    Asserted on the meaning rather than the line breaks: the rule is written to
    a 79-column margin, so "that is a finished run rather than a blocked one"
    is split across two lines in the source and is one sentence to a reader.
    A check that fails on a re-wrap is a check whose subject is the layout.
    """
    raw = run_apply.build_prompt(
        summary={"company": "Acme", "role": "Product Manager",
                 "applicationId": "test-app", "url": "https://example.test"},
        artifacts={"resume": {"path": "/tmp/r.pdf"},
                   "coverLetter": {"path": "/tmp/c.pdf"}},
    )
    return " ".join(raw.split())


def qualifies(label):
    """The rule as a reader of the list would apply it: a word-bounded match of
    a listed question inside the field's label."""
    text = label.lower()
    return any(re.search(r"\b" + re.escape(q) + r"\b", text)
               for q in run_apply.COVER_LETTER_TARGETS)


class WhereTheLetterMayGoTests(unittest.TestCase):
    def test_an_open_ended_question_qualifies(self):
        for label in OPEN_ENDED:
            self.assertTrue(qualifies(label), f"{label!r} should qualify")

    def test_a_specific_question_never_qualifies(self):
        """The half that matters. A cover letter pasted into "what is your
        notice period" is worse than no letter at all."""
        for label in SPECIFIC:
            self.assertFalse(qualifies(label),
                             f"{label!r} asks something specific and would "
                             f"have taken the letter")

    def test_the_list_is_word_bounded_in_effect(self):
        """`marker in text` has cost this repository three afternoons. "why"
        alone would match "Why did you leave your last role", which is the one
        field this must never fill, so no entry may be a bare stem."""
        for q in run_apply.COVER_LETTER_TARGETS:
            self.assertGreaterEqual(
                len(q.split()), 2,
                f"{q!r} is a single word, so it matches every question "
                f"containing it")

    def test_the_rule_reaches_the_prompt_the_agent_gets(self):
        """**A constant nothing reads is the shape this file is full of.**
        `build_prompt` is what the provider is handed, so the assertion is
        against its output rather than against the constant."""
        prompt = prompt_text()
        self.assertIn("do not park on it", prompt,
                      "the no-field case is not stated in the prompt")
        self.assertIn("Never paste it into a field that asks something "
                      "specific", prompt)
        for q in run_apply.COVER_LETTER_TARGETS:
            self.assertIn(q, prompt,
                          f"{q!r} is in the list and not in the prompt, so the "
                          f"agent is choosing from a different vocabulary")

    def test_a_form_with_no_field_is_not_a_failure(self):
        """The wording is the behaviour here: there is no code path that fails,
        so the only thing that can make a run park on this is the prompt not
        saying so."""
        prompt = prompt_text()
        self.assertIn("that is a finished run rather than a blocked one",
                      prompt)
        self.assertIn("36 forms of 38", prompt,
                      "the measurement is what makes this a normal case rather "
                      "than an excuse")
