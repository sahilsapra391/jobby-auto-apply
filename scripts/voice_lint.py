#!/usr/bin/env python3
"""Make the voice rules bind, instead of reminding somebody about them.

The rules have existed the whole time and a cover letter shipped with an
em-dash in it, because a reminder is not a gate. The same session spent its
length proving what happens to guardrails that do not bind, so this is one:
provider-agnostic, run beside the layout and ATS checks, and it fails a
document rather than annotating it.

Two halves, both deterministic. Banned constructions are the ones already
written down: no em-dash, no "I am excited to apply", no "strongly resonates",
no "thank you for your time and consideration".

Named specifics are the half that separates a letter from a template. A letter
that only repeats the posting's own vocabulary back has said nothing about the
candidate. So it must carry terms that come from HIS record, the base resume and
the story bank, and that the posting did not supply. That is `ats_match` pointed
in reverse: the same tokenizer, asking what is in the letter and in the
candidate's evidence but not in the job description.

Voice is not honesty and this does not check honesty. The resume integrity
gate, the ATS unsourced-terms gate and the layout gate keep their jobs.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import ats_match  # noqa: E402

# Written down already, in the user's own voice rules.
BANNED_CHARACTERS = {
    "—": "em-dash",
    "–": "en-dash used as an em-dash",
}

BANNED_PHRASES = (
    "i am excited to apply",
    "i'm excited to apply",
    "excited to apply for",
    "strongly resonates",
    "resonates with my background",
    "thank you for your time and consideration",
    "thank you for your consideration",
    "i believe i would be a great fit",
    "i am confident that",
    "perfect fit for this role",
    "it is not just",
    "here's the thing",
    "let that sink in",
    "in conclusion",
    "delve",
    "tapestry",
    "pivotal",
    "empower",
    "testament to",
    "moreover,",
    "furthermore,",
)

# A letter with fewer than this many terms of his own is a template with the
# company name changed. Three is deliberately low: the letter is short, and the
# gate is meant to catch the empty ones rather than to grade the good ones.
MIN_SPECIFICS = 3
MAX_REPORTED = 12


class VoiceError(ValueError):
    """The letter breaks a stated voice rule."""


def _plain(html_or_text):
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_or_text)
    text = re.sub(r"(?is)<head[^>]*>.*?</head>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in (("&amp;", "&"), ("&nbsp;", " "), ("&#39;", "'"),
                         ("&quot;", '"'), ("&lt;", "<"), ("&gt;", ">"),
                         ("&mdash;", "—"), ("&ndash;", "–")):
        text = text.replace(entity, char)
    return " ".join(text.split())


def specifics(letter_text, evidence_text, posting_text, limit=60):
    """Terms the letter takes from his record rather than from the posting."""
    letter_tokens = ats_match.tokens(letter_text)
    evidence = set(ats_match.tokens(evidence_text))
    posting = set(ats_match.tokens(posting_text))
    return sorted({
        token for token in letter_tokens
        if token in evidence and token not in posting and len(token) > 3
    })


def check(letter, evidence="", posting="", min_specifics=MIN_SPECIFICS):
    """Return a report. Raises VoiceError on the first rule the letter breaks."""
    text = _plain(letter)
    lowered = text.lower()

    found_characters = sorted({
        name for char, name in BANNED_CHARACTERS.items() if char in text
    })
    if found_characters:
        raise VoiceError(
            "the letter uses " + ", ".join(found_characters)
            + "; the voice rules replace it with a comma, a period or parentheses"
        )

    hits = [phrase for phrase in BANNED_PHRASES if phrase in lowered]
    if hits:
        raise VoiceError(
            "the letter uses banned phrasing: " + ", ".join(sorted(hits)[:6])
        )

    own = specifics(text, evidence, posting) if evidence else []
    if evidence and len(own) < min_specifics:
        raise VoiceError(
            f"the letter carries {len(own)} terms of his own, fewer than the "
            f"{min_specifics} required; it repeats the posting rather than "
            "saying anything about him"
        )

    return {
        "ok": True,
        "characters": [],
        "phrases": [],
        "specifics": own[:MAX_REPORTED],
        "specificsCount": len(own),
    }
