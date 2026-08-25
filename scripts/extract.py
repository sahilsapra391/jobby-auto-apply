#!/usr/bin/env python3
"""Pull the required experience out of a job description.

Read the schema field first: `extracted.yearsMin` is **the floor the posting
demands**, and that is the number worth filtering on. A screen rejects on "you
need at least five years"; nobody is ever rejected for having too few years
above the stated ceiling. So a posting saying "5–8 years" is scored on the 5.

The hard part is not finding a number, it is refusing the wrong one. Job
descriptions are full of years that have nothing to do with the requirement:

    "we've been building this for 10 years"
    "a 3-year vision for the platform"
    "founded 12 years ago"
    "5 years of runway"
    "10 years of combined team experience"

Any of those, taken as a requirement, silently mis-scores the job and silently
mis-filters it. So a figure only counts when it sits next to language that makes
it a requirement of the candidate, and several shapes are rejected outright.

Nothing here guesses. A description that does not state a requirement returns
None, the rubric scores it neutral, and the card says `experience unknown` —
which is true, and is the whole reason unknown is a first-class value.

Not to be confused with `scripts/experience.py`, which is about the opposite
side of the same word: that one lifts *the applicant's* own work history out of
their tailored resume to fill a form's description boxes. This one reads *the
posting's* stated requirement.

A second qualifying route exists because "4+ years in a product role at a SaaS
company" states a requirement without ever using the word "experience". It is
deliberately "in a … role" and never a bare "role", which appears in half the
sentences on a careers page and would let the disqualifiers be outvoted.

  python3 scripts/extract.py --text "We want 3-5 years of product experience."
"""

import argparse
import json
import re
import sys

# Extraction runs over the full body before it is truncated to an excerpt, but
# a runaway description should not turn into a runaway regex scan.
MAX_SCAN_CHARS = 40_000
# Above this, the figure is company history or a strategic horizon, not a
# requirement anyone screens on. Fifteen because a real posting asking for more
# than that is vanishingly rare, and "revolutionising commerce for more than 25
# years" is not.
IMPLAUSIBLE_YEARS = 15

# Words that make a number a requirement of the *candidate*. One of these must
# appear close to the figure or it does not count.
REQUIREMENT_WORDS = (
    "experience", "experienced", "background", "track record", "worked",
    "working", "expertise", "practitioner", "hands-on", "hands on",
    "similar role", "comparable role", "equivalent role", "professional",
    # "4+ years in a product role at a SaaS company" states a requirement
    # without ever using the word "experience". The role noun is doing that
    # work, so it qualifies — but only as "in a … role", never a bare "role",
    # which appears in half the sentences on a careers page.
    "in a product role", "in a similar", "in a product", "in an engineering",
    "in a design", "in a data", "in a technical", "in a leadership",
    "in a relevant", "in a related", "in a comparable", "in product",
    # "you've spent 5–7 years in product marketing" is a requirement written as
    # a description of the reader. The subject has to be the candidate, which
    # is why "we've spent" is disqualified below rather than qualifying here.
    "spent", "you have", "you've", "your career", "seniority",
)

# Shapes that look like a requirement and are not. Checked against the text
# immediately around the figure.
DISQUALIFIERS = (
    "ago", "vision", "roadmap", "runway", "founded", "since", "anniversary",
    "history", "combined", "cumulative", "average tenure", "over the next",
    "in the next", "past few", "last few", "warranty", "contract term",
    "visa", "sponsorship", "401", "vesting", "vest", "tenure", "old",
    "renewal", "lease", "we have been", "we've been", "our team has",
    "we spent", "we've spent", "we have spent", "the company has",
    # Company-history phrasing. "PayPal has been revolutionizing commerce
    # globally for more than 25 years" qualified itself off the word
    # "experiences" in the very next sentence, which is exactly the kind of
    # marketing prose that surrounds a real requirement.
    "has been", "have been", "for more than", "for over", "celebrating",
)

# Spelled out, because plenty of postings write "seven years of experience".
# Stops at twelve: past that a figure is prose rather than a requirement, and
# "a hundred years of combined experience" is company history.
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
WORD_YEARS = re.compile(
    r"\b(" + "|".join(NUMBER_WORDS) + r")\s*\+?\s*(?:or more\s+)?years?", re.I)

RANGE = re.compile(
    r"(\d{1,2})\s*(?:\+)?\s*(?:-|–|—|\bto\b|\bor\b)\s*(\d{1,2})\s*\+?\s*year", re.I)
AT_LEAST = re.compile(
    r"(?:at least|minimum(?:\s+of)?|min\.?|no less than|upwards of|over)\s+"
    r"(\d{1,2})\s*\+?\s*year", re.I)
PLUS = re.compile(r"(\d{1,2})\s*\+\s*year", re.I)
PLAIN = re.compile(r"(\d{1,2})\s*year", re.I)

# How far either side of a figure to look for the words that qualify it.
WINDOW = 90


def _within(match, spans):
    return any(start <= match.start() < end for start, end in spans)


def _plural(value):
    return "year" if value == 1 else "years"


def _context(text, start, end):
    return text[max(0, start - WINDOW):end + WINDOW].lower()


def _line_around(text, start, end):
    """The one line the figure sits on, lowercased.

    Postings arrive one bullet per line after `strip_html`, and a disqualifier
    in a neighbouring bullet says nothing about this figure. Paytm's "at least
    2 years owning device/IoT/fintech hardware products" was refused because the
    next bullet said "roadmap ownership skills", which the window reached.
    """
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    if right == -1:
        right = len(text)
    left = max(left, start - WINDOW)
    right = min(right, end + WINDOW)
    for stop in (". ", "! ", "? ", "; "):
        cut = text.rfind(stop, left, start)
        if cut != -1:
            left = max(left, cut + len(stop))
        cut = text.find(stop, end, right)
        if cut != -1:
            right = min(right, cut)
    return text[left:right].lower()


def _qualifies(text, start, end, assume_requirement=False):
    """Is this figure a requirement of the candidate, or scenery?

    The two halves get different reach on purpose. A disqualifier only counts
    on the figure's own line, because that is the only place it can be talking
    about this number. A requirement word may come from a nearby line, because
    "Minimum qualifications:" is a heading above the bullet it governs.

    `assume_requirement` is for text that is already known to be a requirements
    bullet, where the section heading has done the qualifying. Plenty of real
    requirements never say "experience": "7+ years in growth", "3+ years
    supporting executives". Disqualifiers still apply, because a bullet can
    still mention a roadmap.
    """
    if any(bad in _line_around(text, start, end) for bad in DISQUALIFIERS):
        return False
    if assume_requirement:
        return True
    return any(word in _context(text, start, end) for word in REQUIREMENT_WORDS)


def _plausible(*values):
    return all(v is not None and 0 <= v <= IMPLAUSIBLE_YEARS for v in values)


def years_required(text, assume_requirement=False):
    """{"yearsMin", "yearsMax", "yearsText"} or None.

    The first qualifying figure wins. Requirements are stated near the top of a
    requirements block, and later numbers are far likelier to be scenery.
    """
    body = str(text or "")[:MAX_SCAN_CHARS]
    if not body.strip():
        return None

    # A malformed range ("9-3 years") is not a licence to pick one of its
    # numbers. The span is remembered and skipped by the looser passes below,
    # because half of a nonsense range is still nonsense.
    spoiled = []
    for match in RANGE.finditer(body):
        low, high = int(match.group(1)), int(match.group(2))
        if low <= high and _plausible(low, high) and _qualifies(body, match.start(), match.end(), assume_requirement):
            return {"yearsMin": low, "yearsMax": high,
                    "yearsText": f"{low}–{high} years"}
        spoiled.append((match.start(), match.end()))

    for pattern in (AT_LEAST, PLUS):
        for match in pattern.finditer(body):
            value = int(match.group(1))
            if _within(match, spoiled):
                continue
            if _plausible(value) and _qualifies(body, match.start(), match.end(), assume_requirement):
                return {"yearsMin": value, "yearsMax": value,
                        "yearsText": f"{value}+ {_plural(value)}"}

    for match in WORD_YEARS.finditer(body):
        value = NUMBER_WORDS[match.group(1).lower()]
        if _within(match, spoiled):
            continue
        if _plausible(value) and _qualifies(body, match.start(), match.end(), assume_requirement):
            return {"yearsMin": value, "yearsMax": value,
                    "yearsText": f"{value}+ {_plural(value)}"}

    for match in PLAIN.finditer(body):
        value = int(match.group(1))
        if _within(match, spoiled):
            continue
        if not _plausible(value) or not _qualifies(body, match.start(), match.end(), assume_requirement):
            continue
        # A bare "3 years of experience" is a floor in every posting that says
        # it, but it is the weakest shape, so it is checked last.
        return {"yearsMin": value, "yearsMax": value,
                "yearsText": f"{value} {_plural(value)}"}
    return None


def years_from_sections(sections, text=""):
    """The floor a posting asks for, reading its requirements section first.

    A requirements bullet is where the answer lives, and reading it on its own
    removes every figure in the responsibilities and the company blurb from
    contention. The whole body stays as the fallback, because plenty of
    postings have no heading this can find.
    """
    lines = (sections or {}).get("requirements") or []
    if lines:
        found = years_required("\n".join(lines), assume_requirement=True)
        if found:
            return found
    return years_required(text)


def extract(text, sections=None):
    """Everything this module can pull from a description. Absent stays None."""
    found = {"yearsMin": None, "yearsMax": None, "yearsText": None}
    years = years_from_sections(sections, text) if sections else years_required(text)
    if years:
        found.update(years)
    return found


def main():
    ap = argparse.ArgumentParser(description="Pull attributes out of a description.")
    ap.add_argument("--text", help="the description, or - for stdin", required=True)
    args = ap.parse_args()
    body = sys.stdin.read() if args.text == "-" else args.text
    print(json.dumps(extract(body), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
