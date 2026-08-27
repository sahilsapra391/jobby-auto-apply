#!/usr/bin/env python3
r"""What a Memory Bank entry is, and what it is about.

Two separate questions, and conflating them is what made the bank hard to read.

**What it is.** The store holds three kinds of record that happen to share a
file. Measured over the real 156 on 2026-08-27: 121 questions an employer
actually asked, 23 of the user's own standing policies about how to answer, and
12 story-bank entries which are prepared material rather than anything anyone
asked. Only the first kind is a question, and a reader hunting for one should
not scroll past twelve of the third.

**What it is about.** Only meaningful for the first kind. Thirteen categories
cover all 121, measured on 2026-08-27. The last two took explicit terms rather
than a widened pattern: `immigration` for "do you currently have an active
immigration case", and `successful in this role` for "why do you think you will
be successful in this role". `other` stays reachable and stays in the UI even
at zero, because a category set that cannot miss has stopped classifying and
the next corpus will not be this one.

The existing `tags` cannot do this job and that was measured before this file
existed: 55 distinct tags across 156 entries, 26 of them on exactly one entry,
and 35% of entries carrying no tag at all, including relocation, base salary,
date of birth, ethnicity and work authorisation. They are annotations somebody
added while answering, not a taxonomy.

**Everything here is word-bounded, and that is not a style preference.** This
project has lost an afternoon to substring matching three times: `india` inside
`Indiana`, `age` inside `average` and `manage`, `race` inside `embrace`. The
first draft of this very file failed to match "Are you legally authorized to
work in the United States?" because `authoriz\w*to work` cannot cross the space
in "authorized to work". Compile with `\b`, then measure against the real store
before believing it.

Standard library only.
"""

import re

KIND_QUESTION = "question"
KIND_POLICY = "policy"
KIND_STORY = "story"

# The user's own standing instructions, written by them, not asked by anyone.
# They are prefixed in the real store and also tagged, and either is enough.
_POLICY_PREFIX = re.compile(r"^\s*(POLICY\b|When may\b)", re.I)
_STORY_PREFIX = re.compile(r"^\s*Story\s*:", re.I)


def kind_of(entry):
    """Which of the three kinds of record this is.

    Decided by the prefix the user writes, not by the `policy` tag.

    That distinction was measured and it matters: 36 entries carry the `policy`
    tag but only 23 are the user's own notes. The tag means "this question has a
    standing answer", which is a property of an employer question rather than a
    different kind of record. Keying on it moved 18 real questions out of the
    question list, where a reader looking for them would never find them.
    """
    question = str((entry or {}).get("question") or "")
    tags = [str(t) for t in ((entry or {}).get("tags") or [])]
    if _STORY_PREFIX.search(question) or "story-bank" in tags:
        return KIND_STORY
    if _POLICY_PREFIX.search(question):
        return KIND_POLICY
    return KIND_QUESTION


# Ordered. First match wins, so the narrow and unambiguous ones lead: a question
# about being authorised to work names a country, and would otherwise be taken
# by "Location & travel".
CATEGORIES = (
    ("this-employer", "This employer", r"\b(previously worked|worked at or|interviewed at|interviewed with|know anyone|currently working at|association with|prior employment)\b"),
    ("how-you-heard", "How you heard", r"\b(how did you hear|hear about (?:us|this)|referred by)\b"),
    ("consent", "Consent & privacy", r"\b(consent|privacy notice|record(?:ed|ing)?|transcri\w+|retain your|future (?:job )?opportunit\w+|contact you about)\b"),
    ("work-authorisation", "Work authorisation", r"\b(sponsor\w*|visa|work permit|authoriz\w*\s+to\s+work|authoris\w*\s+to\s+work|right to work|citizenship|us person|work authoriz\w*|work authoris\w*|immigration)\b"),
    ("compensation", "Compensation", r"\b(compensation|salary|salaries|ctc|remuneration|base pay|expected pay|current pay|pay rate|bonus|esops?)\b"),
    ("demographics", "Demographics", r"\b(race|ethnicity|hispanic|latino|gender|sex|sexual orientation|lgbtq?\w*|transgender|disabilit\w+|disabled|veteran|date of birth|age range|marital status|religion|national origin|diversity|communities)\b"),
    ("background-check", "Background check", r"\b(background check|consumer report|criminal|convict\w+|felony|misdemean\w+|fcra|drug screen\w*|debarred|exclusion|firearms|government official)\b"),
    ("legal", "Legal declarations", r"\b(non-?compete|non-?solicit\w*|restrictive covenant|arbitration|conflicts? of interest|political contribution|attest\w*|certif\w+|acknowledge\w*|declaration|obligation|signature)\b"),
    ("location", "Location & travel", r"\b(relocat\w+|address|city|country|located|location|onsite|on-?site|hybrid|remote|commute|travel|in-?office)\b"),
    ("education", "Education", r"\b(degree|university|college|school|gpa|major|graduat\w+|education|diploma)\b"),
    ("skills", "Skills & experience", r"\b(years of|experience|rate yourself|proficien\w+|describe|tell us|motivat\w+|project|built|shipped|looking for|successful in this role|approach your work)\b"),
    ("availability", "Availability", r"\b(notice period|start date|available|availability|hours|shift|part-?time|full-?time)\b"),
    ("contact", "Contact & links", r"\b(linkedin|github|portfolio|website|phone|email|first name|last name|full name)\b"),
)
_COMPILED = tuple((slug, label, re.compile(pattern, re.I)) for slug, label, pattern in CATEGORIES)

LABELS = {slug: label for slug, label, _ in CATEGORIES}
UNCATEGORISED = "other"
LABELS[UNCATEGORISED] = "Uncategorised"


def category_of(entry):
    """The category slug for an employer question, or `other`.

    `other` is deliberately reachable. Nothing in the current 121 lands there,
    which is a fact about this corpus rather than a property of the patterns,
    and the bucket stays so the next question that fits nothing is visible
    instead of being forced into the nearest category.
    """
    if kind_of(entry) != KIND_QUESTION:
        return None
    text = str((entry or {}).get("question") or "") + " " + \
        " ".join(str(t) for t in ((entry or {}).get("tags") or []))
    for slug, _label, pattern in _COMPILED:
        if pattern.search(text):
            return slug
    return UNCATEGORISED


# --- grouping ---------------------------------------------------------------
#
# **Group for display. Never merge records. Keep every key.**
#
# The bank holds fourteen sponsorship-and-authorisation entries because fourteen
# employers word the question fourteen ways, and every one of those keys is what
# makes the lookup match that employer's wording. Deleting any of them breaks
# matching for the form it came from, silently, on a real application.
#
# What the reader sees as duplication is real: six sponsorship questions all
# answer "Yes" and five relocation questions all answer "Yes". Eleven rows
# saying the same thing. Collapsing those to two rows is the fix.
#
# **And this is why the rule is grouping rather than merging.** Seven of those
# sponsorship-and-authorisation questions answer a bare "Yes" and mean opposite
# facts: "Do you require visa sponsorship?" is Yes, and "Are you legally
# authorized to work in the United States?" is also Yes. Merge on the answer
# alone and one of them starts filling forms with the opposite of the truth,
# under the user's name, unrecallable. `main` split those two apart deliberately
# in the sponsorship work; nothing here may put them back together.
#
# So the group key is a NARROW topic plus the exact answer, and the topics below
# are written so that opposite-polarity questions cannot land in the same one.
# `sponsorship-required` needs a sponsorship word AND a requiring word;
# `authorised-to-work` needs an authorisation word AND "to work". A question
# that matches neither is never grouped and always shows on its own row, which
# is the safe default.
GROUP_TOPICS = (
    ("sponsorship-required", r"(?=.*\b(?:sponsor\w*)\b)(?=.*\b(?:require\w*|need\w*|will you|do you now)\b)"),
    ("authorised-to-work", r"(?=.*\b(?:authoriz\w*|authoris\w*|eligible|right)\b)(?=.*\bto work\b)"),
    ("relocation", r"\brelocat\w+"),
    ("current-location", r"\b(current location|currently based|where are you based)\b"),
    ("notice-period", r"\bnotice period\b"),
    ("veteran-status", r"\bveteran\b"),
    ("disability-status", r"\bdisabilit\w+\b"),
)
_GROUPS = tuple((slug, re.compile(pattern, re.I)) for slug, pattern in GROUP_TOPICS)


def group_topic(entry):
    """The narrow topic this question can be grouped under, or None.

    None is the common and safe answer. A question with no topic is shown on its
    own row, which is what the bank does today.
    """
    if kind_of(entry) != KIND_QUESTION:
        return None
    question = str((entry or {}).get("question") or "")
    for slug, pattern in _GROUPS:
        if pattern.search(question):
            return slug
    return None


def group_key(entry):
    """(topic, normalised answer), or None when this row stands alone.

    The answer is part of the key on purpose. Two questions on the same topic
    with different answers are two different facts and stay two rows.
    """
    topic = group_topic(entry)
    if not topic:
        return None
    value = (entry or {}).get("value")
    if value is None:
        value = (entry or {}).get("answer")
    value = str(value or "").strip().lower()
    if not value:
        return None
    return f"{topic}|{value}"


def annotate(entry):
    """Add `kind`, `category` and `groupKey` to a record, in place."""
    entry["kind"] = kind_of(entry)
    category = category_of(entry)
    if category:
        entry["category"] = category
    key = group_key(entry)
    if key:
        entry["groupKey"] = key
    return entry
