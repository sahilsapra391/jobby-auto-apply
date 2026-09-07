"""Propose the employer a posting is actually for, from how the posting
introduces itself.

THE RULE, stated once so it can be argued with:

    A candidate employer is extracted only when the description *introduces*
    an employer in one of six self-introduction frames, and only when the
    candidate survives three refusals. Everything else refuses, with a reason.

**It proposes. It does not decide.** Nothing here writes an employer edge. It
writes a proposal carrying the phrase it matched, where in the description it
matched, and how confident it is, and a person confirms the batch. That is not
caution for its own sake: the corpus this runs against is 222 postings filed
under a feed publisher, and the cost of being wrong is a stranger's name on
somebody's job application.

WHY THE DESCRIPTION AT ALL. `scripts/sources/reed.py:34` already reads
`entry.get("employerName")`, so the connector is not dropping a field: reed's
own API returns "Appcast Enterprise" (and "Appcastenterprise", both spellings,
which is itself reed's data rather than ours). Measured over all 222 records:
no `sources` entry, no `sections` entry and no `extracted` field carries an
employer. The description is the only place the name exists.

THE FRAMES, taken from the corpus rather than guessed. Each is a way a company
introduces itself in its own copy:

    At <Name>,                  "At Artex, we reimagine what risk..."
    Welcome to <Name>           "Welcome to Gallagher - a global community..."
    Join <Name>
    <Name> is looking/seeking/hiring/recruiting
    About <Name>
    We are <Name>

THE REFUSALS, which are the interesting output:

    no_frame      nothing introduces an employer. 156 of 222 on the Appcast
                  corpus, and that is a property of the corpus rather than a
                  weak rule: a reed listing frequently opens with a salary and
                  a location and never names the company.
    disagreement  two or more distinct names. "At Pen, we don't just underwrite
                  risk... backed by the strength of Gallagher" is one real
                  example, and it is genuinely ambiguous: Pen is an MGA owned
                  by Gallagher, and which one an application should name is not
                  a thing a regex knows.
    intermediary  the name is a staffing agency, a job board or the feed
                  publisher. This is the michaelpage check, and it is the one
                  that matters most: a description saying "Michael Page is
                  recruiting for a leading firm" introduces the recruiter, and
                  filing the posting under it would replace one wrong employer
                  with another wrong employer while looking like progress.
    not_a_name    the frame matched a phrase rather than a company: "Join Us",
                  "About Us", "We are Hiring".

CONFIDENCE is about how the name was found, never about how good the name
looks:

    high    two or more distinct frames yield the same name, or one frame
            inside the first 200 characters. A company that introduces itself
            in its opening sentence is stating its own name.
    medium  exactly one frame, later in the excerpt.

There is no `low`. A candidate that would earn it is a refusal instead, because
a low-confidence proposal in a queue a person is scanning is a proposal that
gets confirmed.
"""

from __future__ import annotations

import re
import unicodedata

#: How far into the description a frame still counts as an introduction.
#: Beyond this the excerpt is describing the role, and a capitalised word after
#: "Join" is as likely to be "Join Our Team" as a company.
INTRO_WINDOW = 200

#: The excerpt is 1,500 characters, and a frame in its last third is usually a
#: benefits paragraph naming a pension provider. Measured: extending past this
#: added 9 candidates on the Appcast corpus of which 6 were not employers.
SEARCH_WINDOW = 900

#: A candidate name: one to four capitalised words, allowing the punctuation
#: real company names carry. Deliberately not `\w+` -- an unanchored word run
#: swallows the rest of the sentence.
#: `&` and `+` join words inside a name rather than ending it: "Pipe & Climate
#: Centre" truncated to "Pipe" until they were allowed through, which read as
#: the rule finding a company called Pipe.
_NAME = r"[A-Z][\w.\-']*(?:[ ](?:[&+]|[A-Z][\w.\-']*)){0,4}"

#: Each frame, with the group that holds the name. Order is not precedence:
#: every frame is tried and the results are compared, because agreement between
#: two frames is the strongest signal available here.
#: Frames whose keyword follows the name. See the note beside `hiring`.
_TRAILING_KEYWORD = {"hiring"}

FRAMES = (
    ("at", re.compile(rf"\bAt ({_NAME}),")),
    ("welcome", re.compile(rf"\bWelcome to ({_NAME})\b")),
    ("join", re.compile(rf"\bJoin ({_NAME})\b")),
    # **The one frame whose keyword follows the name**, which is why it alone
    # refuses on an interior sentence boundary instead of cutting at it. In
    # "At X," the keyword leads and a period inside X only means the pattern
    # ran on, so the name is cut back. In "X is seeking" the verb is what
    # identifies the frame, so a period between them means the name and the
    # verb are in different sentences and the frame never applied: "in
    # Bengaluru. We are seeking an Engineering Manager" is a city followed by
    # a new sentence, and cutting would have proposed the city.
    ("hiring", re.compile(rf"\b({_NAME}) (?:is|are) "
                          rf"(?:looking|seeking|hiring|recruiting)\b")),
    ("about", re.compile(rf"\bAbout ({_NAME})\b")),
    ("weare", re.compile(rf"\bWe are ({_NAME})\b")),
)

#: Words a frame catches that are not companies. This is an exclusion list on a
#: **phrase's role** rather than on a name, which is the distinction §11 of
#: `phase3-inputs.md` draws: a closed set that changes slowly, not a list of
#: companies that is always one short.
_NOT_A_NAME = {
    "us", "our", "the", "this", "we", "you", "your", "a", "an",
    "hiring", "recruiting", "looking", "seeking", "currently",
    "team", "company", "business", "client", "clients", "group",
    "role", "position", "job", "opportunity", "candidate", "candidates",
    "home", "work", "working", "present", "least", "part", "all",
    "when", "where", "who", "what", "why", "how", "while", "since",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "uk", "usa", "london", "england",
}

#: Intermediaries: whoever is between the applicant and the employer. Agencies,
#: job boards and feed publishers. **The name is not the point; the role is.**
#: A posting introducing one of these is introducing the party that listed the
#: job, and filing the posting under it swaps one wrong employer for another.
#:
#: Matched on the normalised whole name, never as a substring, for the reason
#: CLAUDE.md gives at length: `"india" in "Indiana"` and `"race" in "embrace"`
#: are the same bug and this list would have `"reed" in "Reed Elsevier"`.
INTERMEDIARIES = {
    "michael page", "michaelpage", "page personnel", "page group",
    "hays", "randstad", "adecco", "manpower", "manpowergroup",
    "robert half", "robert walters", "reed", "reed co uk",
    "hudson", "morson", "sthree", "harvey nash", "nigel frank",
    "gi group", "pertemps", "brook street", "office angels",
    "search consultancy", "the recruitment co", "recruitment",
    "appcast", "appcast enterprise", "appcastenterprise",
    "indeed", "linkedin", "glassdoor", "ziprecruiter", "totaljobs",
    "cv library", "cvlibrary", "jobsite", "monster", "adzuna",
    "talent", "talentpool", "aggregator",
}


class Proposal:
    """A candidate employer, with the evidence that produced it."""

    def __init__(self, legacy_id, name, confidence, frames, phrase, offset):
        self.legacy_id = legacy_id
        self.name = name
        self.confidence = confidence
        self.frames = frames
        self.phrase = phrase
        self.offset = offset

    @property
    def refused(self):
        return False

    def as_dict(self):
        return {"id": self.legacy_id, "name": self.name,
                "confidence": self.confidence, "frames": sorted(self.frames),
                "phrase": self.phrase, "offset": self.offset}


class Refusal:
    """Why no candidate was proposed, in the rule's own words."""

    def __init__(self, legacy_id, reason, detail, candidates=()):
        self.legacy_id = legacy_id
        self.reason = reason
        self.detail = detail
        self.candidates = sorted(candidates)

    @property
    def refused(self):
        return True

    def as_dict(self):
        return {"id": self.legacy_id, "reason": self.reason,
                "detail": self.detail, "candidates": self.candidates}


def normalise(name):
    """A name reduced to comparable words. Not `norm_company`.

    `backfill.norm_company` exists to key the employers table and is the right
    function there. This one only has to decide whether two extracted strings
    are the same phrase and whether a phrase is in a closed list, so it stays
    local rather than borrowing a function whose job is different and whose
    behaviour would then have to stay compatible with both.
    """
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


#: Words that belong to a document's scaffolding, never to a company name.
#: A candidate containing one means the pattern crossed a heading boundary, so
#: the two ends of the match are not part of one phrase.
#:
#: Leading ones are trimmed and interior ones refuse, and the difference is
#: measured rather than stylistic. "Description Cleveland House is looking to
#: recruit" is a heading followed by a real name, so trimming recovers it.
#: "Clearance: SC Clearance We are looking for" has no company in it at all,
#: and an interior hit is the only signal that says so.
_STRUCTURE = {
    "description", "summary", "overview", "introduction", "intro",
    "clearance", "level", "salary", "location", "hours", "benefits",
    "role", "responsibilities", "requirements", "qualifications",
    "package", "contract", "reference", "ref", "vacancy", "apply",
    "organization", "organisation", "department", "division", "team",
}


def _trim(name):
    """Drop the scaffolding a frame's pattern reached across.

    Leading words are dropped while they are stop words, structure words, or
    an all-capitals run longer than three letters -- "DV UNLEASH YOUR POTENTIAL
    Leidos is seeking" is a banner above a name, and real names in prose are
    title case. `BAE` and `GE` survive on length, which is why the bound is
    three rather than two.

    Trailing stop words go too: the hiring frame reads "<X> is looking", and
    "Description We are looking" puts the pronoun at the end of X.
    """
    words = name.split()

    def head(w):
        """The word's first normalised token: `We're` -> `we`, `BAE` -> `bae`.
        Without this a contraction slipped every stop-word check, because
        `normalise("We're")` is `"we re"` and that is in no list."""
        return (normalise(w).split() or [""])[0]

    # A banner is dropped with everything before it, not only when it leads.
    # "DV UNLEASH YOUR POTENTIAL Leidos is seeking" opens with a two-letter
    # clearance code, so a leading-only trim stopped on the first word and kept
    # the whole banner.
    banner = [i for i, w in enumerate(words) if w.isupper() and len(w) > 3]
    if banner:
        words = words[banner[-1] + 1:]

    def junk(w):
        low = head(w)
        return not low or low in _NOT_A_NAME or low in _STRUCTURE
    while words and junk(words[0]):
        words.pop(0)
    while words and head(words[-1]) in _NOT_A_NAME:
        words.pop()
    while words and words[-1] in "&+":
        words.pop()
    return " ".join(words)


#: A name followed immediately by more names is a list of brands, not a
#: self-introduction. Measured against the wrong-domain employers: "At
#: Doordash, Deliveroo and Wolt, we're building" made the rule propose Doordash
#: for a Deliveroo posting, at **high** confidence, which is the shape that has
#: to be caught before the rule touches anything.
_ENUMERATION = re.compile(r"^[ ]*(?:,|and\b|&)[ ]*[A-Z]")


def _is_enumeration(text, end):
    """Whether the text right after a matched name continues the list."""
    return bool(_ENUMERATION.match(text[end:end + 24]))


def _ends_a_sentence(name):
    """A single word ending in a full stop is the end of the previous sentence.

    "in Bengaluru. We are seeking an Engineering Manager" put a city into the
    hiring frame. Real names carry an interior period -- "Warner Bros.
    Discovery" -- so the test is a trailing period on a one-word candidate
    rather than a period anywhere, which would refuse that one too.
    """
    return name.endswith(".") and len(name.split()) == 1


#: The longest token that can precede a period and still be an abbreviation
#: inside a name. `Bros.` is four letters; `Gallagher.` is nine and is the end
#: of a sentence. Measured against the corpus: cutting at every interior period
#: refuses "Warner Bros. Discovery", and cutting at none of them produced
#: "Gallagher. Join Gallagher Bassett" as a company.
_ABBREVIATION = 4


def _cut_at_a_sentence(name):
    """The candidate up to the first period that ends a sentence.

    `X. Y` is two sentences when `X` is a word and one name when `X` is an
    abbreviation, and length is the only signal available without a dictionary.
    It is a heuristic and it is bounded: at worst it truncates a name whose
    first word is longer than four letters and which really does contain a
    period, and no such name appears in the corpus.
    """
    parts = name.split(" ")
    out = []
    for word in parts:
        out.append(word)
        if word.endswith(".") and len(word.rstrip(".")) > _ABBREVIATION:
            out[-1] = word.rstrip(".")
            break
    return " ".join(out)


def _crosses_a_heading(name):
    return any(normalise(w) in _STRUCTURE for w in name.split())


def _is_intermediary(name):
    return normalise(name) in INTERMEDIARIES


def _is_not_a_name(name):
    words = normalise(name).split()
    if not words:
        return True
    # Every word being a stop word is a phrase, not a company. One stop word
    # inside a real name ("Bank of the West") is fine, which is why this is
    # `all` rather than `any`.
    return all(w in _NOT_A_NAME for w in words)


def _same_company(a, b):
    """Whether two extracted names are the same company.

    A strict prefix counts: "Gallagher" and "Gallagher Bassett" introduced in
    one description are one company naming itself twice, and refusing that pair
    as a disagreement would throw away the clearest cases in the corpus.
    """
    x, y = normalise(a), normalise(b)
    if x == y:
        return True
    short, long = sorted((x, y), key=len)
    # Prefix *and* suffix. Measured over the pool's disagreements: pairs like
    # ("Capital One", "Cyber Product Owner Capital One") are a job title with
    # the company on the end, and a prefix-only test called them two employers.
    #
    # **A word-count floor was tried here and made it worse.** Requiring the
    # shorter side to be two words, so that a single common word could not
    # absorb a real name, took the pool's disagreements from 30 to 69 by
    # splitting ("AVA", "AVA Technologies") and every other one-word company
    # beside its longer form. The fragment case it was aimed at --
    # ("One", "Prospa") -- is already refused by this test, because neither is
    # a prefix or a suffix of the other. Measured before and after.
    return long.startswith(short + " ") or long.endswith(" " + short)


def propose(record):
    """One posting's proposal, or the refusal that explains why there is none."""
    legacy_id = record.get("id")
    text = (record.get("descriptionExcerpt") or "")[:SEARCH_WINDOW]
    if not text.strip():
        return Refusal(legacy_id, "no_description",
                       "the record carries no description to read")

    found = {}          # normalised name -> (display name, frame, offset)
    listed = set()      # names that appeared inside an enumeration
    for frame, pattern in FRAMES:
        for match in pattern.finditer(text):
            raw_name = match.group(1)
            if _ends_a_sentence(raw_name):
                continue
            if frame in _TRAILING_KEYWORD and _cut_at_a_sentence(raw_name) != raw_name:
                continue
            if _is_enumeration(text, match.end(1)):
                # The description is naming a group's brands. Recorded as a
                # candidate so the refusal can say what it saw, rather than
                # skipped into a `no_frame` that claims nothing was there.
                listed.add(raw_name.strip(" .,-'"))
                continue
            name = _trim(_cut_at_a_sentence(raw_name.strip(" ,-'")))
            # **Checked after the trim as well as before it.** "Bengaluru. We
            # are seeking" matches with the pronoun on the end, so the raw
            # candidate does not end in a period and only the trimmed one does.
            # Checking once, before, let a city through at medium confidence.
            if not name or _ends_a_sentence(name):
                continue
            name = name.rstrip(".")
            if not name or _is_not_a_name(name) or _crosses_a_heading(name):
                continue
            key = normalise(name)
            if key not in found or match.start() < found[key][2]:
                found[key] = (name, frame, match.start())
            break                       # first hit per frame, nearest the top

    if listed and not found:
        return Refusal(legacy_id, "enumeration",
                       "the description lists several companies rather than "
                       "introducing one, so which of them is hiring is not "
                       "something the text says", listed)
    if not found:
        # The opening travels with the refusal, because 162 identical reasons
        # are not a list anybody can read. What makes them readable is seeing
        # that one opens with a salary, the next with a location, the next
        # with a role title, and none of them with a company.
        opening = " ".join(text[:110].split())
        return Refusal(legacy_id, "no_frame",
                       f"nothing in the description introduces an employer; "
                       f"it opens \u201c{opening}\u2026\u201d")

    intermediaries = [v[0] for v in found.values() if _is_intermediary(v[0])]
    real = {k: v for k, v in found.items() if not _is_intermediary(v[0])}
    if not real:
        return Refusal(legacy_id, "intermediary",
                       "the only name introduced is an agency, a job board or "
                       "the feed publisher, which is who listed the job rather "
                       "than who is hiring", intermediaries)

    names = [v[0] for v in real.values()]
    distinct = []
    for name in names:
        if not any(_same_company(name, other) for other in distinct):
            distinct.append(name)
    if len(distinct) > 1:
        return Refusal(legacy_id, "disagreement",
                       "the description introduces more than one employer and "
                       "the rule cannot tell which one is hiring", distinct)

    # The longest spelling of the one company, and the earliest frame that
    # found it: a description saying "Gallagher" and "Gallagher Bassett" is
    # naming the subsidiary, which is the more specific truth.
    chosen = max(names, key=len)
    frames = {v[1] for v in real.values()}
    name, frame, offset = min(real.values(), key=lambda v: v[2])
    phrase = text[max(0, offset - 12):offset + len(chosen) + 40].strip()

    confidence = "high" if (len(frames) > 1 or offset < INTRO_WINDOW) else "medium"
    return Proposal(legacy_id, chosen, confidence, frames, phrase, offset)
