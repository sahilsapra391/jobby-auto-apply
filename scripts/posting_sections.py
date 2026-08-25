#!/usr/bin/env python3
"""Split a posting body into the two sections a reader actually wants.

Every applicant tracking system lets the employer write its own headings, so
there is no schema to read. What there is, reliably, is a heading line followed
by bullets, and a small vocabulary that employers draw those headings from.
This finds the headings, decides which of the two buckets each belongs to, and
returns the lines underneath.

Two buckets, deliberately, because that is what the card shows:

- `responsibilities`: what the job is. "Key Responsibilities", "What you'll do",
  "The role", "Your impact", "Day to day".
- `requirements`: what the candidate needs. "Skills & Qualifications", "What you
  bring", "Minimum qualifications", "Requirements", "About you".

Nothing is invented. A posting with no recognisable heading returns empty
buckets rather than a guess, because a wrong "requirement" on a job board is
worse than no requirement, and `extract.years_required` reads this to decide
what a posting asks for.

Standard library only.
"""

import argparse
import json
import re
import sys

# Headings are matched on their words, not on punctuation or case, because
# employers write "Skills & Qualifications-", "SKILLS AND QUALIFICATIONS:" and
# "Skills / Qualifications" for the same thing.
RESPONSIBILITY_HEADINGS = (
    "key responsibilities", "responsibilities", "what you will do",
    "what you'll do", "what youll do", "what you will be doing",
    "the role", "your role", "role overview", "about the role",
    "your impact", "the impact you will have", "what you will own",
    "what you'll own", "day to day", "day-to-day", "in this role",
    "core responsibilities", "duties", "key duties", "what the job involves",
    "how you will contribute", "what you will contribute", "the opportunity",
    "job description", "position summary", "scope of the role",
    # Employers write the same heading a dozen ways; these are the ones seen in
    # the store that the first pass walked past.
    "in your role you'll", "in your role you will", "in your role",
    "what you'll be doing", "what youll be responsible for",
    "what you will be responsible for", "your responsibilities",
    "key accountabilities", "accountabilities", "primary responsibilities",
    "main responsibilities", "essential duties", "essential functions",
    "what the role involves", "the job", "your mission", "the mission",
    "what success looks like", "your day", "a day in the life",
    "you will", "you'll", "responsibilities include", "role responsibilities",
    "about this role", "role summary", "position overview", "overview",
    "what we need from you day to day",
)
REQUIREMENT_HEADINGS = (
    "skills and qualifications", "skills & qualifications", "qualifications",
    "minimum qualifications", "basic qualifications", "preferred qualifications",
    "minimum requirements", "requirements", "what you bring",
    "what you will bring", "what you'll bring", "what we are looking for",
    "what we're looking for", "who you are", "about you", "your experience",
    "experience required", "required experience", "required skills",
    "desired skills", "skills required", "you have", "must have",
    "what you need", "what you need to succeed", "ideal candidate",
    "the ideal candidate", "we are looking for", "our requirements",
    "candidate profile", "eligibility",
    "skills", "your skills", "key skills", "core competencies", "competencies",
    "experience", "experience and skills", "skills and experience",
    "skills & experience", "qualifications and experience",
    "required qualifications", "essential criteria", "essential skills",
    "what you should have", "what you'll bring to the table",
    "what makes you a fit", "who we are looking for", "who we're looking for",
    "you should have", "you might be a fit if", "you may be a good fit if",
    "requirements and qualifications", "job requirements", "prerequisites",
    "background", "your background", "the successful candidate",
    "what you have", "preferred skills", "preferred skills and experience",
    "preferred experience", "additional requirements", "other requirements",
    "minimum experience", "desired experience", "nice to haves",
    "what we'd like to see", "what we would like to see", "you bring",
)
# Headings that end a section without starting one worth showing.
CLOSING_HEADINGS = (
    "why join us", "benefits", "perks", "compensation", "salary",
    "what we offer", "our offer", "equal opportunity", "eeo",
    "diversity", "how to apply", "about us", "about the company",
    "our values", "next steps", "interview process", "location",
    "nice to have", "bonus points",
)

MAX_HEADING_WORDS = 8
# A heading line is short. A sentence that happens to start with "requirements"
# is not a heading, and this is what tells them apart.
MAX_HEADING_CHARS = 70
# How much may follow the part of a heading this recognises. Long enough for
# "qualifications, capabilities, and skills", bounded by the two caps above.
HEADING_TAIL_CHARS = 30


def _words(line):
    """A heading reduced to its words, so punctuation and case cannot matter.

    Both sides go through this. Comparing a raw heading against a written-out
    constant is how "Skills & Qualifications-" failed to match
    "skills & qualifications": the ampersand becomes a space on one side only.
    """
    text = str(line or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(re.sub(r"[^a-z0-9']+", " ", text).split())


def _normalise_all(headings):
    return tuple(dict.fromkeys(_words(h) for h in headings if _words(h)))


RESPONSIBILITY_HEADINGS = _normalise_all(RESPONSIBILITY_HEADINGS)
REQUIREMENT_HEADINGS = _normalise_all(REQUIREMENT_HEADINGS)
CLOSING_HEADINGS = _normalise_all(CLOSING_HEADINGS)


# Employers introduce a heading as often as they just write it.
LEAD_INS = (
    "here's ", "here is ", "heres ", "so ", "now ", "and ", "the ",
    "job ", "key ", "core ", "primary ", "main ", "essential ", "principal ",
    "position ", "role ", "general ", "specific ", "additional ", "other ",
)

# Words title case leaves lowercase, so they cannot be evidence either way.
TITLE_CASE_SMALL_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "from", "by", "as", "is", "are", "you", "your", "our", "we",
}


def _strip_lead(words):
    """"here's what we're looking for" -> "what we're looking for"."""
    changed = True
    while changed:
        changed = False
        for lead in LEAD_INS:
            if words.startswith(lead) and len(words) > len(lead):
                words = words[len(lead):]
                changed = True
    return words


def _looks_like_heading(line):
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_HEADING_CHARS:
        return False
    if len(stripped.split()) > MAX_HEADING_WORDS:
        return False
    # A bullet is content, never a heading, even a short one.
    return not re.match(r"^[-*•·●▪\d]+[.)\s]", stripped)


def _is_strong_heading(line):
    """Does this line announce a section, rather than merely being short?

    This decides whether an unrecognised line is allowed to *end* the section
    it follows, and being wrong about it is expensive: a requirements list
    written as short unbulleted lines ("Network security", "Applied
    cryptography") was closing its own section, so 43.7% of the board came back
    with responsibilities and no requirements at all. A heading announces
    itself, with a colon, a dash, or capitals.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.endswith((":", "-", "—", "–")):
        return True
    letters = [c for c in stripped if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True
    # Title Case reads as a heading, but only real title case: every word that
    # is not a small connecting word has to be capitalised. Testing the first
    # letter alone matches any sentence, and a requirements list written as
    # short unbulleted lines is all sentences.
    words = stripped.split()
    if len(words) < 2 or len(words) > 5 or stripped.endswith((".", ",", ";")):
        return False
    substantive = [w for w in words if w.lower() not in TITLE_CASE_SMALL_WORDS]
    return bool(substantive) and all(w[0].isupper() for w in substantive if w[:1].isalpha())


def _classify(line):
    """`responsibilities`, `requirements`, `closing`, or None."""
    whole = _words(line)
    if not whole:
        return None
    for words in (whole, _strip_lead(whole)):
        if not words:
            continue
        for name, headings in (("requirements", REQUIREMENT_HEADINGS),
                               ("responsibilities", RESPONSIBILITY_HEADINGS),
                               ("closing", CLOSING_HEADINGS)):
            for heading in headings:
                # Anchored at the start so "experience with requirements
                # gathering" is not a requirements heading.
                if words == heading or words.startswith(heading + " ") \
                        or words.startswith(heading):
                    if len(words) <= len(heading) + HEADING_TAIL_CHARS:
                        return name
    return None


def _clean(line):
    text = line.strip()
    text = re.sub(r"^[-*•·●▪]+\s*", "", text)
    text = re.sub(r"^\d+[.)]\s*", "", text)
    return " ".join(text.split())


def split_sections(body):
    """{"responsibilities": [str], "requirements": [str]} from a posting body."""
    lines = str(body or "").replace("\r", "").split("\n")
    buckets = {"responsibilities": [], "requirements": []}
    current = None
    for line in lines:
        if _looks_like_heading(line):
            kind = _classify(line)
            if kind == "closing":
                current = None
                continue
            if kind:
                current = kind
                continue
            # An unrecognised heading ends the section it follows, rather than
            # letting a "Why join us" written as "Life at Acme" leak into it.
            # Only a line that announces itself as a heading gets to do that;
            # a short unbulleted line is far likelier to be a bullet that lost
            # its marker, and closing the section on one loses the rest of it.
            if current and _words(line) and _is_strong_heading(line):
                current = None
                continue
        if current:
            text = _clean(line)
            if text:
                buckets[current].append(text)
    return buckets


def main():
    ap = argparse.ArgumentParser(description="Split a posting into sections.")
    ap.add_argument("--text", required=True, help="the body, or - for stdin")
    args = ap.parse_args()
    body = sys.stdin.read() if args.text == "-" else args.text
    print(json.dumps(split_sections(body), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
