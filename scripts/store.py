#!/usr/bin/env python3
"""Local storage for the job-application skill.

Keeps profile, reusable answers, application history and resumable sessions in
~/.job-application/. Standard library only.

Design notes:
  - Every answer and inferred profile field carries a confidence state so a
    guess cannot silently become a fact on the next application.
  - Sensitive values (compensation, visa, demographics) are only persisted with
    an explicit --remember-sensitive flag, because permission to use a value now
    is a different decision from permission to store it.
  - Errors stay terse and never echo stored values, since these files hold
    personal data.
"""

import argparse
import json
import os
import re
import tempfile
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
ANSWER_STATES = {"confirmed", "inferred", "missing", "sensitive"}
# "inflight" means an Apply run is under way: the role has left the watch
# list but nothing has been submitted yet.
SESSION_STATUSES = {"watch", "inflight", "active", "review", "completed",
                    "abandoned", "dropped"}
# Pre-submission lifecycle, then post-submission outcomes reported by the user.
HISTORY_EVENTS = {"started", "reviewed", "submitting", "completed", "abandoned",
                  "response", "interview", "offer", "rejected"}


class StoreError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def root_dir(override=None):
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()


def paths(root):
    return {
        "root": str(root),
        "profile": str(root / "profile.json"),
        "answers": str(root / "answers.json"),
        "history": str(root / "applications.jsonl"),
        "sessions": str(root / "sessions"),
        # Delivered documents live beside the skill, one folder per company, so
        # they are browsable in Finder. Gitignored: they carry a real address.
        "applications": str(Path(__file__).resolve().parents[1] / "applications"),
        # Durable index of every role ever seen. Sessions are deleted once an
        # application completes, which would otherwise lose the posting URL and
        # leave the board unable to link to what was applied to.
        "roles": str(root / "roles.json"),
        "meta": str(root / "meta.json"),
    }


def write_private(path, payload):
    """Write JSON with owner-only permissions, atomically.

    The temp file gets a unique name. It used to be `<name>.tmp`, shared by
    every writer, which is only atomic while there is one: with three
    applications running at once, one process truncated the temp file while
    another was still writing it, and the replace then moved a half-written file
    into place. roles.json is rewritten on every event, so it took the brunt.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path) as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        raise StoreError(f"{path.name} is not valid JSON; refusing to overwrite")


def load_input(spec):
    if spec == "-":
        return json.load(sys.stdin)
    with open(spec) as fh:
        return json.load(fh)


def safe_id(value):
    if not value or not all(c.isalnum() or c in "-_." for c in value):
        raise StoreError("application id must be alphanumeric with - _ . only")
    if ".." in value:
        raise StoreError("application id must not contain ..")
    return value


def answer_key(question):
    """Normalise a question into a stable lookup key.

    Wording varies between employers ("What is your notice period?" versus
    "Official Notice Period ?") so keys are normalised rather than literal.
    """
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in question)
    words = [w for w in cleaned.split() if w not in {"the", "a", "an", "your", "you", "do", "is", "are", "what", "please", "indicate"}]
    return "-".join(words)[:80]


# --- commands ---------------------------------------------------------------


def cmd_init(args):
    root = root_dir(args.root)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    # 0700: sessions name the companies applied to.
    (root / "sessions").mkdir(exist_ok=True)
    os.chmod(root / "sessions", 0o700)
    apps = Path(paths(root)["applications"])
    apps.mkdir(parents=True, exist_ok=True)
    os.chmod(apps, 0o700)
    p = paths(root)
    if not Path(p["profile"]).exists():
        write_private(p["profile"], {})
    if not Path(p["answers"]).exists():
        write_private(p["answers"], {})
    Path(p["history"]).touch()
    os.chmod(p["history"], 0o600)
    out = dict(p)
    out["initialized"] = True
    out["schemaVersion"] = SCHEMA_VERSION
    return out


def cmd_profile_get(args):
    return read_json(paths(root_dir(args.root))["profile"], {})


def cmd_profile_replace(args):
    profile = load_input(args.input)
    if not isinstance(profile, dict):
        raise StoreError("profile must be a JSON object")
    for list_field in ("workHistory", "education", "skills", "resumes"):
        if list_field in profile and not isinstance(profile[list_field], list):
            raise StoreError(f"{list_field} must be a list")
    # Each base resume carries the identity block that must go on that region's
    # forms and cover letter. A base without one would silently fall back to
    # another region's phone number.
    for entry in profile.get("resumes", []):
        if not isinstance(entry, dict) or "key" not in entry or "path" not in entry:
            raise StoreError("each resume needs a key and a path")
        identity = entry.get("identity")
        if not isinstance(identity, dict) or not identity.get("phone") or not identity.get("location"):
            raise StoreError(f"resume '{entry.get('key')}' needs identity.phone and identity.location")
    if profile.get("resumes") and sum(1 for e in profile["resumes"] if e.get("default")) != 1:
        raise StoreError("exactly one resume must be marked default")
    profile["updatedAt"] = now()
    profile["schemaVersion"] = SCHEMA_VERSION
    write_private(paths(root_dir(args.root))["profile"], profile)
    return profile


def cmd_answer_put(args):
    entry = load_input(args.input)
    question = entry.get("question")
    if not question:
        raise StoreError("answer requires a question")
    state = entry.get("state", "confirmed")
    if state not in ANSWER_STATES:
        raise StoreError(f"state must be one of {sorted(ANSWER_STATES)}")

    if state == "sensitive" and not args.remember_sensitive:
        # Record that the question exists and is sensitive, but not the value.
        # Permission to fill is not permission to remember.
        entry["value"] = None
        entry["note"] = "value not stored; user did not consent to remembering"

    key = entry.get("key") or answer_key(question)
    entry["key"] = key
    entry["state"] = state
    entry["updatedAt"] = now()

    path = paths(root_dir(args.root))["answers"]
    answers = read_json(path, {})
    answers[key] = entry
    write_private(path, answers)
    return {"key": key, "state": state, "stored": entry.get("value") is not None}


# Some questions are recognisable by a phrase rather than by overall wording.
# A form asks about a non-compete in a paragraph of legalese, and overlap scoring
# against a stored question cannot see past the surrounding words: measured, a
# real Vanguard question scored under the 0.75 bar against the stored one and
# resolved to nothing.
#
# Only phrases specific enough to be unambiguous belong here, and only for
# questions the user has given a standing answer to. "conflict of interest" is
# in; "employment" is not.
TOPIC_MARKERS = [
    ("post-employment-obligations", (
        "non-compete", "noncompete", "non compete",
        "non-solicitation", "nonsolicitation", "non solicitation",
        "non-dealing", "restrictive covenant", "post-employment obligation",
        "post employment obligation", "garden leave",
        # GitLab asks it as a restriction rather than an obligation, and asks
        # about the agreement itself rather than naming a clause. Measured on
        # 2026-08-25: the real question resolved to nothing and parked the
        # application on a topic the user answered on 2026-08-19.
        "post-employment restriction", "post employment restriction",
        "employment agreements and/or", "employment agreement and/or",
        "subject to any employment agreement",
    )),
    ("political-contributions", (
        "political contribution", "political contributions",
        "political action committee", "contribution to any political",
    )),
    # Willingness only, never authorisation. "background check" on its own is
    # deliberately absent: it appears just as often in "I authorise the company
    # to obtain a consumer report", and signing that is the user's act, not a
    # statement of intent. The marker has to carry the willingness with it.
    ("background-check-willingness", (
        "willing to submit a background", "willing to submit to a background",
        "willing to undergo a background", "willing to complete a background",
        "willing to consent to a background", "willing to take a background",
        "willing to undergo a drug", "willing to submit to a drug",
    )),
    ("interview-recording", (
        "record and auto-transcript", "record and auto-transcribe",
        "record and transcribe", "auto-transcribe your interview",
        "recording of your interview", "record your interview",
        "interview recording", "ai notetaker", "brighthire", "metaview",
    )),
    # Typing a name into a certification block is an electronic signature. The
    # marker resolves the NAME to use; whether signing is appropriate at all is
    # the policy entry's job, and it is conditional. Deliberately narrow: these
    # phrases only appear where a signature is being collected.
    # Acknowledging a posted range, NOT stating a number. The two live one word
    # apart and the wrong one is a disaster: "what are your compensation
    # expectations" must return the figure, never "Yes". Every phrase here
    # carries the acknowledgement shape with it.
    ("salary-range-acknowledgement", (
        "agree that this range", "acknowledge and agree that this range",
        "this range aligns with your compensation",
        "range aligns with your expectations",
        "are you comfortable with this range",
        "does this range work for you",
        "acknowledge this salary range", "agree to this salary range",
    )),
    # Healthcare and government contractors must ask. A factual declaration
    # about his own standing, and he has stated it.
    ("exclusion-debarment", (
        "excluded from, debarred", "debarred or otherwise ineligible",
        "ineligible to participate in state or federally funded",
        "federal healthcare programs", "1320a-7b", "oig exclusion",
        "excluded party", "system for award management", "sam.gov",
        "suspended or debarred",
    )),
    # The accuracy attestation, in all its wordings. Conditional, not automatic:
    # the policy entry carries the condition that nothing on the form was
    # inferred, because ticking this certifies everything else on the page.
    ("accuracy-attestation", (
        "true and correct", "true and complete", "true and accurate",
        "certify that the information provided", "certify that the information i have",
        "information provided is accurate", "certify the above information",
        "to the best of my knowledge and belief",
    )),
    ("signature-name", (
        "type your name", "type your full name", "typing your name",
        "type your legal name", "electronic signature", "e-signature",
        "signature (type", "please sign by typing", "enter your name to sign",
        "name and the date", "your name and today's date",
    )),
    ("source-how-heard", (
        "how did you hear about", "how did you find out about",
        "how did you learn about", "where did you hear about",
        "how did you find this", "how did you come across",
        "where did you find this role", "referral source",
        "how were you referred", "source of application",
    )),
    # Sponsorship and work authorisation, in ANY country. These used to be
    # answered only for the US, and a separate stored answer said sponsorship
    # WAS required everywhere else, so a UK or Canadian form got the opposite
    # answer to a US one on the same standing instruction.
    ("compensation-breakup-india", (
        "compensation breakup", "compensation break-up", "ctc breakup",
        "ctc break-up", "salary breakup", "salary break-up",
        "fixed, variable", "fixed variable and esop", "esops", "esop",
    )),
    ("lgbtq-self-identification", (
        "lgbtq", "lgbtqia", "lgbt community", "lesbian, gay",
        "gay, bisexual", "transgender community", "transgender (lgbt",
    )),
    ("relationships-conflicts", (
        "close associate", "conflict of interest", "conflicts of interest",
        "related to, by birth", "related by birth", "birth, adoption or marriage",
        "immediate family member employed", "relative employed by",
    )),
    # Deliberately AFTER relationships-conflicts, which is scanned first and
    # wins: "is a relative of yours a former employee here" is a question about
    # a relative, and only the ones about the applicant belong to this topic.
    # Every marker therefore carries the applicant with it. "ever worked at" is
    # absent on purpose, because "have you ever worked at a startup" is a
    # different question with a different answer.
    ("prior-employment-with-this-employer", (
        "previously worked at or consulted for", "worked at or consulted for",
        "previously worked for or consulted", "previously worked at",
        "previously worked for", "previously been employed",
        "previously employed by", "ever been employed by",
        "prior employment with", "worked for us before", "worked here before",
        "applied to or worked at", "been an employee of this company",
        "are you a former employee", "a former employee of this",
    )),
    # Accepting the office arrangement the posting already states. This is not
    # the work-setup preference question: the employer has decided, and the
    # user's standing instruction is to accept whatever it is. The answer text
    # carries the field shape, because some of these ask for a number of days.
    ("in-office-acknowledgement", (
        "flexwork", "flex work policy", "in the office at least",
        "in office at least", "days per week in the office",
        "days a week in the office", "days per week in office",
        "days in the office per week", "days onsite per week",
        "onsite at least", "in-office requirement", "office attendance",
        "hybrid work policy", "return to office policy",
        "work from the office at least", "report to the office at least",
    )),
    # Can he show papers for what the resume says. He can, and the answer is
    # about the resume's own history rather than about work authorisation,
    # which has its own topic scanned earlier in this list.
    ("resume-history-documentation", (
        "documentation of the employment", "documentation of your employment",
        "documentation-availability", "documentation availability",
        "documentation confirming", "documentation to confirm",
        "documentation to verify", "documentation to support",
        "documentation supporting the", "provide documentation of",
        "verify the information on your resume", "verify your employment history",
        "proof of your employment history", "documentation of the education",
        "documentation of your education",
    )),
    # A URL, not a declaration. Placed last so "how did you hear about us",
    # whose options list LinkedIn, keeps its own topic.
    ("linkedin-profile-url", (
        "linkedin profile", "linkedin url", "linkedin.com/in", "linkedin link",
        "linkedin page", "your linkedin", "linkedin account",
    )),
    # Greenhouse education blocks are three separate dropdowns, so they are
    # three topics: one answer cannot serve School, Degree and Discipline.
    # Bare one-word labels are matched by their own stored key rather than by a
    # marker here, because "degree" on its own also appears in "do you have a
    # degree in political science", which is a yes/no question and not this.
    ("education-degree", (
        "highest level of education", "level of education", "highest degree",
        "highest qualification", "highest education", "degree earned",
        "degree obtained", "degree completed", "degree type",
        "select your degree", "what degree did you",
    )),
    ("education-school", (
        "school did you attend", "university did you attend",
        "college did you attend", "name of your school",
        "name of the university", "name of your university",
        "institution attended", "school or university", "which school",
    )),
    ("education-discipline", (
        "field of study", "course of study", "area of study", "your major",
        "major or field", "discipline",
    )),
    # A bare field label, which is all Workday and Waymo put next to the
    # control. The long wordings are already handled above by
    # work-authorisation-any-country; these are the one-word ones, which that
    # topic's phrases ("authorized to work in") cannot match.
    ("work-authorisation-label", (
        "work authorization", "work authorisation", "work eligibility",
        "employment eligibility", "authorization status",
    )),
    # The free-text bank. Eighteen answers written in the user's own words were
    # stored with hand-assigned keys and no tags, so exact, fuzzy and topic
    # matching all missed every one of them. Measured on 2026-08-25: thirteen
    # of the commonest essay questions on an application form, including "what
    # motivates you", whose answer was sitting in the store verbatim, resolved
    # to nothing.
    #
    # Every value here is SOURCE MATERIAL, not paste-ready text. The entries
    # carry prefixes addressed to the agent ("HIS OWN WORDS", "Competencies:"),
    # and each one's note says to write the answer out to the field's length.
    ("story-motivation", (
        "what motivates you", "what drives you", "motivates you most",
        "what gets you up in the morning",
    )),
    ("story-next-role", (
        "looking for in your next role", "looking for in a new role",
        "looking for in your next", "ideal next role",
        "want in your next role", "hoping to find in your next",
    )),
    ("story-why-looking", (
        "why are you looking", "why did you leave", "reason for leaving",
        "why are you leaving", "why do you want to leave",
        "reason for your job change", "why are you exploring",
    )),
    ("story-ai-in-work", (
        "ai changed the way", "how has ai changed", "how do you use ai",
        "use ai in your work", "ai in your day to day",
        "ai tools in your work",
    )),
    ("story-side-project", (
        "personal project", "side project", "side experiment",
        "project you built on your own", "something you built outside work",
    )),
    ("story-self-description", (
        "describe yourself in", "tell us about yourself",
        "describe yourself", "in one sentence about you",
    )),
    # The fit essay. This one has no single stored answer and never can: it is
    # different for every posting. The entry it reaches says so, and says what
    # to build it from, which is a better answer than nothing at all. Nothing
    # here is a figure or a claim, so a literal paste would be visibly wrong
    # rather than quietly false.
    ("citizenship-country", (
        "country of citizenship", "countries of citizenship",
        "your nationality", "what is your nationality",
        "citizen of which country", "which country are you a citizen",
        "country of nationality",
    )),
    ("travel-willingness", (
        "willing to travel", "able to travel", "comfortable travelling",
        "comfortable traveling", "commit to travel", "travel requirement",
        "travel requirements", "% travel", "percent travel", "travel up to",
        "overnight travel", "travel domestically", "travel internationally",
        "amount of travel", "ability to travel", "open to travel",
        "travel as needed", "travel for work",
    )),
    ("story-why-this-role", (
        "why do you think you will be successful",
        "why will you be successful", "why are you a good fit",
        "why do you think you are a good fit", "makes you a strong candidate",
        "why should we hire you", "why are you interested in this role",
        "why do you want to work at", "why do you want to join",
        "why this role", "why are you interested in joining",
        "what interests you about this",
    )),
]


# A "rate yourself 1 to 10" question is answered per skill, not per topic. The
# same sentence with a different noun is a different question, and one answer
# for all of them is how Python gets rated with Excel's number: measured, a
# Python rating fuzzy-matched the Excel entry at 0.67. An unknown skill returns
# nothing on purpose, so the run asks rather than guesses.
SKILL_RATINGS = {
    "sql": "9",
    "excel": "10",
}
RATING_SHAPE = re.compile(
    r"(rate|rating|scale|proficiency|how would you rate|skill level)", re.I)
RATING_RANGE = re.compile(r"\b1\s*(?:to|-|–|through)\s*10\b|\b10\b.*\bhighest\b", re.I)


def skill_rating(question):
    """The user's standing self-rating for the skill named, or None."""
    text = (question or "").lower()
    if not (RATING_SHAPE.search(text) and RATING_RANGE.search(text)):
        return None
    named = [skill for skill in SKILL_RATINGS if re.search(r"\b" + skill + r"\b", text)]
    if len(named) != 1:
        # Nothing recognised, or two skills in one question. Either way this is
        # not something to answer from a table.
        return None
    skill = named[0]
    return {"match": "topic", "topic": f"skill-rating-{skill}",
            "answer": SKILL_RATINGS[skill], "question": question,
            "state": "confirmed",
            "note": f"standing self-rating for {skill}, any wording of a 1-10 scale"}


# Years of experience is answered per discipline, not once. The store holds
# five figures and they are not the same number: 4 total, 4 in product
# management, 3 building AI products, 1 managing people, 0 as a software
# developer. A loose marker would hand whichever it reached first to every
# one of those questions, and "years of experience in Java" would come back 4.
#
# So this answers two shapes and nothing else. A question naming exactly one
# known discipline gets that discipline's figure. A question naming no
# discipline at all, in the tight unqualified form below, gets the total.
# Everything else, including every discipline the store has no figure for,
# returns None and goes to the user.
YEARS_EXPERIENCE = (
    ("product-management", r"product manage", "4"),
    ("ai-products", r"\bai\b|artificial intelligence|machine learning|\bml\b", "3"),
    ("software-development",
     r"software (developer|development|engineer)|programming|\bcoding\b", "0"),
    ("people-management",
     r"managing people|people management|managing a team|direct reports|"
     r"leading a team|line management", "1"),
)
YEARS_SHAPE = re.compile(
    r"(years?\b[^.?]{0,20}\bexperience|how many years|total years|years in the (industry|field))",
    re.I)
YEARS_UNQUALIFIED = re.compile(
    r"^(how many |what (is|are) |total |please state )?"
    r"(the |your |his )?"
    r"(total |overall |combined )?"
    r"(number of )?"
    r"years?( of)?"
    r"( total| overall| professional| work| working| relevant| full[- ]time)*"
    r" experience"
    r"( do you have| have you( had)?| in total)?"
    r"[\s?.:*]*(\(required\))?[\s?.:*]*$", re.I)


def years_experience(question):
    """The user's stored years figure for what this question actually asks."""
    text = (question or "").strip()
    if not YEARS_SHAPE.search(text):
        return None
    named = [(topic, value) for topic, pattern, value in YEARS_EXPERIENCE
             if re.search(pattern, text, re.I)]
    if len(named) == 1:
        topic, value = named[0]
        return {"match": "topic", "topic": f"years-experience-{topic}",
                "answer": value, "value": value, "question": question,
                "state": "confirmed",
                "note": f"stored years of experience for {topic.replace('-', ' ')}"}
    if named:
        # Two disciplines in one question is not one figure.
        return None
    if YEARS_UNQUALIFIED.match(text):
        return {"match": "topic", "topic": "years-experience-total",
                "answer": "4", "value": "4", "question": question,
                "state": "confirmed",
                "note": "total years of professional experience, no discipline named"}
    # A discipline this store has no figure for. Never answered from the total.
    return None


# Expected pay is one figure per currency, and the store holds two: $120,000
# for a US role and Rs. 30,00,000 for an India one. A phrase list can only add
# questions, never withhold one, and "Expected Salary (INR)" contains every
# phrase "Expected Salary" does. Measured on 2026-08-25 with markers alone:
# an INR field, a rupee-symbol field and a lakhs field all resolved to the
# dollar figure, which is a wrong number on a real application rather than a
# near miss.
#
# So the currency decides, and a currency the user has no figure for parks.
# Both entries are `sensitive`, which keeps them out of fuzzy matching; this
# path is exact, which is what makes reaching them safe.
EXPECTED_PAY_SHAPE = re.compile(
    r"(expected|desired|required|preferred)\s+(base\s+|annual\s+|total\s+)?"
    r"(salary|compensation|pay|ctc|package|remuneration)"
    r"|salary\s+(expectation|requirement)"
    r"|compensation\s+expectation"
    r"|what\s+(are|is)\s+your\s+(expected\s+)?(salary|compensation|ctc)"
    r"|expected\s+lpa|ctc\s+expectation", re.I)
# Acknowledging a posted band is a yes, never a number, and lives one word away.
NOT_A_FIGURE = re.compile(
    r"acknowledge|agree that this range|range align|comfortable with (this|the) range"
    r"|does this range", re.I)
# "CTC" is cost-to-company, which is an Indian construction and appears on no
# US form. It is what most Indian ATSes actually label the field.
INDIA_PAY = re.compile(
    r"\binr\b|₹|\blakh|\blpa\b|rupee|\brs\.?\b|indian rupee|\bctc\b", re.I)
# Every currency he has no stored figure for. Naming one is not a near miss.
OTHER_CURRENCY = re.compile(
    r"\bgbp\b|£|\beur\b|€|\bcad\b|\baud\b|\bsgd\b|\baed\b|\bchf\b|\bjpy\b|¥"
    r"|\bsek\b|\bnok\b|\bdkk\b|\bpln\b|\bzar\b|\bbrl\b|\bmxn\b|\bnzd\b"
    r"|pounds? sterling|\beuros?\b|dirham", re.I)


def expected_pay(answers, question):
    """The stored expected-pay figure for the currency this field is in."""
    text = (question or "")
    if not EXPECTED_PAY_SHAPE.search(text) or NOT_A_FIGURE.search(text):
        return None
    if INDIA_PAY.search(text):
        topic = "expected-compensation-india"
    elif OTHER_CURRENCY.search(text):
        # He has a US figure and an India figure and nothing else. Converting
        # one of them is inventing a number, so this goes to the user.
        return None
    else:
        topic = "expected-compensation"
    for entry in answers.values():
        if topic in (entry.get("tags") or []):
            return {"match": "topic", "topic": topic, **entry}
    return None


# Acknowledging something and authorising something are not the same act, and
# the difference is the whole reason this is a function rather than a phrase
# list. "I acknowledge I have read the questionnaire" is the user's standing
# yes. "I acknowledge and authorize the Company to obtain a consumer report"
# uses the same first word and grants a third party the right to pull his
# records, which has always been his to give and stays his.
ACKNOWLEDGEMENT_SHAPE = re.compile(
    r"acknowledge|acknowledgment|acknowledgement"
    r"|i have read and (understand|agree)|i confirm (that )?i have read"
    r"|i understand (and|that)"
    r"|by (typing|signing) (my|your) name", re.I)
# Every one of these stays with the user. An authorisation, an assignment and
# a restrictive covenant are things he gives away, not things he confirms.
NOT_A_BARE_ACKNOWLEDGEMENT = re.compile(
    r"authoriz\w*|authoris\w*|consumer report|credit report|investigative report"
    r"|fair credit reporting|\bfcra\b|background (check|investigation|screening)"
    r"|drug (test|screen)|assignment of invention|intellectual property"
    r"|proprietary information|confidentiality agreement|non[- ]disclosure"
    r"|non[- ]compete|non[- ]solicitation|restrictive covenant"
    r"|invention assignment|assignment agreement|arbitration agreement"
    r"|power of attorney|indemnif|waive (my|any) right|class[- ]action", re.I)
# Agreeing to be bound by something is the user's act, and no factual answer
# in this store is an answer to it. "Do you have a non-compete?" is a question
# about the world and resolves to No; "I acknowledge and agree to the
# non-compete above" is a signature, and answering it No was one marker away.
BOUND_BY_AGREEMENT = re.compile(
    r"i acknowledge and agree|i agree to|i consent to|agree to be bound"
    r"|by (signing|typing)[^.]{0,80}(agree|consent|bound)", re.I)


# "US person" is a term of art, and it is not the same as being allowed to
# work here. Under the EAR and ITAR it means a citizen, a lawful permanent
# resident, or an asylee or refugee, and a deemed export licence turns on it.
# A substring list is the wrong tool: "us person" appears inside ordinary
# prose, and a false No here is a false statement on a federal export-control
# declaration rather than a near miss. So the phrase is matched with word
# boundaries and nothing looser.
US_PERSON_QUESTION = re.compile(
    r"\bu\.?\s?s\.?\s+person\b|\bunited states person\b|\bus persons?\b"
    r"|deemed export|\bitar\b|\bear\b(?=[^a-z])|export[- ]control", re.I)
# Asking whether a LICENCE is needed is a different question from asking what
# he is, and it depends on the technology the role touches. He has answered
# the first and not the second, so the second still goes to him.
LICENCE_REQUIREMENT = re.compile(
    r"require (a |an )?(deemed export )?licen[cs]e|licen[cs]e (be )?required"
    r"|need (a |an )?(deemed export )?licen[cs]e", re.I)


# Work authorisation and sponsorship, told truthfully, are three different
# answers that depend on the country and on which of them the form is asking.
# The store held twelve entries saying sponsorship would never be required
# anywhere, alongside one saying he is on a student visa, which cannot both be
# true. The user corrected it on 2026-08-25: authorised now, will require
# sponsorship in the future.
#
# Three shapes, and conflating any two of them puts a false statement on a
# form he signs:
#   "are you authorised to work here"          -> now.     US: yes.
#   "will you require sponsorship"             -> future.  US: yes.
#   "are you authorised to work WITHOUT
#    sponsorship, now or in the future"        -> both.    US: no.
#
# India is the real exception rather than a special case: he is a citizen, so
# all three flip. Everywhere else he is neither authorised nor sponsored yet.
SPONSORSHIP_WORD = re.compile(r"sponsor", re.I)
# Deliberately not a bare "authoris": that word also carries a background-check
# authorisation and a consumer-report authorisation, neither of which is a
# question about being allowed to work, and both of which stay with the user.
WORK_AUTH_WORD = re.compile(
    r"(authoriz\w*|authoris\w*|eligib\w*|permitted|legally able|entitled)\s+to\s+work"
    r"|work\s+(authoriz\w*|authoris\w*|permit|eligibility|status)"
    r"|right to work|legally (authoriz\w*|authoris\w*)\b(?!.*\b(background|consumer|credit)\b)",
    re.I)
WITHOUT_SPONSORSHIP = re.compile(
    r"without (any |a |visa |employment |the need for |requiring |needing |"
    r"the need to )?(be )?sponsor"
    r"|(not|never) (require|need)\w*\s+(any |visa |employment )?sponsor"
    r"|no sponsorship (required|needed|necessary)"
    r"|do not (require|need) sponsor", re.I)
INDIA_COUNTRY = re.compile(r"\bindia\b|\bindian\b", re.I)
US_COUNTRY = re.compile(
    r"united states|\bu\.?\s?s\.?\s?a?\b|\bamerica\b|\busa\b", re.I)
THIRD_COUNTRY = re.compile(
    r"united kingdom|\bu\.?k\.?\b|\bengland\b|\bcanada\b|\bcanadian\b|\baustralia\b"
    r"|\bgermany\b|\bireland\b|\bsingapore\b|\bnetherlands\b|\bfrance\b|\bjapan\b"
    r"|\bspain\b|\bpoland\b|\bswitzerland\b|\bsweden\b|\bu\.?a\.?e\.?\b"
    r"|united arab emirates|\beurope\b|\beea\b|\bschengen\b", re.I)

_IN_NOTE = ("India: he is a citizen, authorised without sponsorship. "
            "Answer for the country the posting is in; when the field names no country, "
            "use REGION.")
_US_NOTE = ("United States: authorised to work now on a student visa, and sponsorship "
            "WILL be required in the future. Standing correction from the user on "
            "2026-08-25, replacing the earlier answer that said sponsorship would never "
            "be required. Never answer that he needs no sponsorship in the US. " + _IN_NOTE)
_THIRD_NOTE = ("Outside the United States and India he holds no work authorisation, so "
               "sponsorship would be required. " + _IN_NOTE)

# question shape -> (india, united states, anywhere else)
_SPONSORSHIP_ANSWERS = {
    # "will you require sponsorship, now or in the future"
    "sponsorship": (("No", _IN_NOTE), ("Yes", _US_NOTE), ("Yes", _THIRD_NOTE)),
    # "are you authorised to work here" (today)
    "authorised-now": (("Yes, Indian citizen", _IN_NOTE),
                       ("Yes, authorised to work now on a student visa", _US_NOTE),
                       ("No", _THIRD_NOTE)),
    # "are you authorised to work here WITHOUT sponsorship" (both at once)
    "without-sponsorship": (("Yes, Indian citizen", _IN_NOTE),
                            ("No, authorised to work now but sponsorship will be "
                             "required in the future", _US_NOTE),
                            ("No", _THIRD_NOTE)),
}


def sponsorship_status(question):
    """Work authorisation or sponsorship, for the country the question names."""
    text = (question or "")
    has_sponsor = bool(SPONSORSHIP_WORD.search(text))
    has_auth = bool(WORK_AUTH_WORD.search(text))
    if not (has_sponsor or has_auth):
        return None
    if WITHOUT_SPONSORSHIP.search(text):
        shape = "without-sponsorship"
    elif has_sponsor:
        shape = "sponsorship"
    elif has_auth:
        shape = "authorised-now"
    else:
        return None

    if INDIA_COUNTRY.search(text):
        index, country = 0, "India"
    elif US_COUNTRY.search(text) or not THIRD_COUNTRY.search(text):
        # No country named defaults to the United States, which is where the
        # overwhelming majority of these forms are. The note names the India
        # answer explicitly so an India posting is not answered from here.
        index, country = 1, "the United States"
    else:
        index, country = 2, "a third country"
    value, note = _SPONSORSHIP_ANSWERS[shape][index]
    return {"match": "topic", "topic": f"{shape}-{country.split()[-1].lower()}",
            "value": value, "answer": value, "question": question,
            "state": "confirmed", "note": note}


def us_person(answers, question):
    """The standing answer to "are you a US person", never to "do you need a licence"."""
    text = (question or "")
    if not US_PERSON_QUESTION.search(text):
        return None
    if LICENCE_REQUIREMENT.search(text):
        return None
    for entry in answers.values():
        if "us-person-status" in (entry.get("tags") or []):
            return {"match": "topic", "topic": "us-person-status", **entry}
    return None


def acknowledgement(answers, question):
    """A standing yes to an acknowledgement, never to an authorisation."""
    text = (question or "")
    if not ACKNOWLEDGEMENT_SHAPE.search(text):
        return None
    if NOT_A_BARE_ACKNOWLEDGEMENT.search(text):
        return None
    for entry in answers.values():
        if "acknowledgement-standing" in (entry.get("tags") or []):
            return {"match": "topic", "topic": "acknowledgement-standing", **entry}
    return None


def topic_answer(answers, question):
    """A standing answer recognised by a distinctive phrase, or None."""
    rated = skill_rating(question)
    if rated:
        return rated
    if (BOUND_BY_AGREEMENT.search(question or "")
            and NOT_A_BARE_ACKNOWLEDGEMENT.search(question or "")):
        # Signing away a right. Never answered from a stored fact.
        return None
    years = years_experience(question)
    if years:
        return years
    pay = expected_pay(answers, question)
    if pay:
        return pay
    person = us_person(answers, question)
    if person:
        return person
    visa = sponsorship_status(question)
    if visa:
        return visa
    text = (question or "").lower()
    for topic, markers in TOPIC_MARKERS:
        if not any(m in text for m in markers):
            continue
        for entry in answers.values():
            if topic in (entry.get("tags") or []):
                return {"match": "topic", "topic": topic, **entry}
    return None


def find_answer(answers, question):
    key = answer_key(question)
    if key in answers:
        return answers[key]
    # Fall back to overlap scoring so slightly different phrasings still match.
    # The bar is deliberately high: "product management" vs "project management"
    # scores 0.71 — one word apart, and the wrong answer.
    target = set(key.split("-"))
    scored = []
    for k, v in answers.items():
        other = set(k.split("-"))
        if not other:
            continue
        score = len(target & other) / len(target | other)
        if score > 0:
            scored.append((score, k, v))
    scored.sort(key=lambda t: (-t[0], t[1]))
    if scored:
        score, _, entry = scored[0]
        # Sensitive answers never fuzzy-match: compensation landing in a
        # near-miss field is the worst mis-fill this store can produce.
        if score >= 0.75 and entry.get("state") != "sensitive":
            return {"match": "fuzzy", "score": round(score, 2), **entry}
    # Before giving up, try recognising the question by a distinctive phrase.
    # Overlap scoring cannot see a non-compete clause buried in a paragraph.
    topical = topic_answer(answers, question)
    if topical is not None:
        return topical
    ack = acknowledgement(answers, question)
    if ack is not None:
        return ack

    # Candidates carry the stored question, never the value; to use one,
    # confirm it means the same thing, then look it up by its own question.
    candidates = [
        {"key": k, "question": v.get("question"), "state": v.get("state"), "score": round(s, 2)}
        for s, k, v in scored[:3] if s >= 0.4
    ]
    return {"match": "none", "key": key, "candidates": candidates}


def cmd_answer_find(args):
    answers = read_json(paths(root_dir(args.root))["answers"], {})
    return find_answer(answers, args.question)


def cmd_answers_resolve(args):
    """Resolve a whole form's questions in one call instead of one per field."""
    questions = load_input(args.input)
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise StoreError("input must be a JSON list of question strings")
    answers = read_json(paths(root_dir(args.root))["answers"], {})
    return {q: find_answer(answers, q) for q in questions}


def cmd_answer_list(args):
    return read_json(paths(root_dir(args.root))["answers"], {})


def cmd_history_append(args):
    event = load_input(args.input)
    if event.get("event") not in HISTORY_EVENTS:
        raise StoreError(f"event must be one of {sorted(HISTORY_EVENTS)}")
    for banned in ("answers", "values", "password", "profile"):
        if banned in event:
            raise StoreError(f"history must not contain {banned}")
    event["at"] = now()
    event["eventId"] = str(uuid.uuid4())
    remember_role(root_dir(args.root), event)
    path = paths(root_dir(args.root))["history"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
    return event


def cmd_history_list(args):
    path = Path(paths(root_dir(args.root))["history"])
    if not path.exists():
        return []
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    if args.company:
        events = [e for e in events if e.get("company", "").lower() == args.company.lower()]
    return events


def cmd_history_status(args):
    """Latest event per application: the quick check against reapplying."""
    events = cmd_history_list(argparse.Namespace(root=args.root, company=None))
    latest = {}
    for e in events:
        key = e.get("applicationId") or f"{e.get('company')}|{e.get('role')}"
        latest[key] = {"company": e.get("company"), "role": e.get("role"), "event": e.get("event"), "at": e.get("at")}
    return list(latest.values())


def remember_role(root, data):
    """Upsert the durable role index. Never deletes; sessions come and go."""
    aid = data.get("applicationId")
    if not aid:
        return
    path = paths(root)["roles"]
    roles = read_json(path, {})
    entry = roles.get(aid, {})
    for field in ("company", "role", "url", "ats", "region", "source"):
        if data.get(field):
            entry[field] = data[field]
    entry["applicationId"] = aid
    roles[aid] = entry
    write_private(path, roles)


def cmd_roles_list(args):
    return read_json(paths(root_dir(args.root))["roles"], {})


def cmd_role_put(args):
    root = root_dir(args.root)
    remember_role(root, load_input(args.input))
    return {"remembered": True}


def cmd_session_save(args):
    session = load_input(args.input)
    app_id = safe_id(args.id)
    status = session.get("status", "active")
    if status not in SESSION_STATUSES:
        raise StoreError(f"status must be one of {sorted(SESSION_STATUSES)}")
    for field in session.get("pendingFields", []):
        if not isinstance(field, dict) or "question" not in field:
            raise StoreError("each pendingField needs a question")
        if "value" in field:
            raise StoreError("pendingFields must not contain values; store those as answers")
    session["applicationId"] = app_id
    session["status"] = status
    session["updatedAt"] = now()
    sessions = Path(paths(root_dir(args.root))["sessions"])
    sessions.mkdir(parents=True, exist_ok=True)

    # A new watch entry has to be a posting we have not seen. This is where a
    # sourcing sweep adds what it found, and the sweep is the same agent that
    # decided the role was new, from three lists it read by hand. Checking here
    # is the difference between an instruction and a guarantee: the same job
    # arriving from a second board, or from next week's rescan, is refused
    # whatever the caller believed.
    #
    # Only for NEW watch entries. Updating an existing session (a phase, a park,
    # a drop) keeps working, and so does any other status.
    new_watch = status == "watch" and not (sessions / f"{app_id}.json").exists()
    if new_watch and not getattr(args, "allow_known", False):
        import seen  # local: seen imports this module
        verdict = seen.check(session, seen.known_records(args.root))
        if verdict["known"]:
            raise StoreError(
                f"{app_id} is already in the system: {verdict['reason']} "
                f"({verdict['where']}, matched on {verdict['match']}). "
                f"Sourcing adds new postings only. Pass --allow-known to override, "
                f"which is for deliberately restoring a role, not for sweeps.")
    write_private(sessions / f"{app_id}.json", session)
    remember_role(root_dir(args.root), session)
    return session


def cmd_session_load(args):
    path = Path(paths(root_dir(args.root))["sessions"]) / f"{safe_id(args.id)}.json"
    if not path.exists():
        raise StoreError("no session with that id")
    return read_json(path, {})


def cmd_session_list(args):
    sessions = Path(paths(root_dir(args.root))["sessions"])
    if not sessions.exists():
        return []
    out = []
    for f in sorted(sessions.glob("*.json")):
        data = read_json(f, {})
        out.append({
            "applicationId": data.get("applicationId", f.stem),
            "company": data.get("company"),
            "role": data.get("role"),
            "status": data.get("status"),
            "step": data.get("step"),
            "pending": len(data.get("pendingFields", [])),
            "updatedAt": data.get("updatedAt"),
        })
    return out


def cmd_session_delete(args):
    path = Path(paths(root_dir(args.root))["sessions"]) / f"{safe_id(args.id)}.json"
    if path.exists():
        path.unlink()
        return {"deleted": args.id}
    return {"deleted": None, "note": "no such session"}


FUNNEL_ORDER = ["started", "reviewed", "submitting", "completed",
                "response", "interview", "offer", "rejected"]


def cmd_stats(args):
    """The search as a funnel: what was started, what went out, what converted.

    bySource only counts submitted applications, so it reads as conversion —
    which channels produce responses — rather than raw activity.
    """
    events = cmd_history_list(argparse.Namespace(root=args.root, company=None))
    apps = {}
    for e in events:
        key = e.get("applicationId") or f"{e.get('company')}|{e.get('role')}"
        a = apps.setdefault(key, {"stages": set(), "source": None,
                                  "company": e.get("company"), "role": e.get("role")})
        a["stages"].add(e.get("event"))
        if e.get("source") and not a["source"]:
            a["source"] = e["source"]

    funnel = {s: sum(1 for a in apps.values() if s in a["stages"]) for s in FUNNEL_ORDER}

    by_source = {}
    for a in apps.values():
        if "completed" not in a["stages"]:
            continue
        src = a["source"] or "unknown"
        row = by_source.setdefault(src, {"submitted": 0, "response": 0,
                                         "interview": 0, "offer": 0, "rejected": 0})
        row["submitted"] += 1
        for s in ("response", "interview", "offer", "rejected"):
            if s in a["stages"]:
                row[s] += 1

    needs_verification = sorted(
        f"{a['company']} — {a['role']}" for a in apps.values()
        if "submitting" in a["stages"] and not ({"completed", "abandoned"} & a["stages"])
    )
    return {"applications": len(apps), "funnel": funnel,
            "bySource": by_source, "needsVerification": needs_verification}


def cmd_meta_set(args):
    """Small operational values (dashboard artifact URL, last sweep time).

    Never answer or profile data — those have confidence machinery this bypasses.
    """
    path = paths(root_dir(args.root))["meta"]
    meta = read_json(path, {})
    meta[args.key] = args.value
    meta["updatedAt"] = now()
    write_private(path, meta)
    return {args.key: args.value}


def cmd_meta_set_json(args):
    """A structured meta value, for things that are not one string.

    `meta-set` takes --value, so anything with shape had to be squeezed through
    a JSON string and squeezed back out by every reader. lastIngest is an object
    with per-channel counts inside it, and meta.json has one owner for the same
    reason it always did: two writers on one file is how a read-modify-write
    loses somebody's key.
    """
    value = load_input(args.input)
    path = paths(root_dir(args.root))["meta"]
    meta = read_json(path, {})
    meta[args.key] = value
    meta["updatedAt"] = now()
    write_private(path, meta)
    return {args.key: value}


def cmd_meta_get(args):
    meta = read_json(paths(root_dir(args.root))["meta"], {})
    if args.key:
        return {args.key: meta.get(args.key)}
    return meta


def cmd_paths(args):
    return paths(root_dir(args.root))


COMMANDS = {
    "init": cmd_init,
    "paths": cmd_paths,
    "profile-get": cmd_profile_get,
    "profile-replace": cmd_profile_replace,
    "answer-put": cmd_answer_put,
    "answer-find": cmd_answer_find,
    "answers-resolve": cmd_answers_resolve,
    "answer-list": cmd_answer_list,
    "history-append": cmd_history_append,
    "history-list": cmd_history_list,
    "history-status": cmd_history_status,
    "stats": cmd_stats,
    "session-save": cmd_session_save,
    "session-load": cmd_session_load,
    "session-list": cmd_session_list,
    "session-delete": cmd_session_delete,
    "roles-list": cmd_roles_list,
    "role-put": cmd_role_put,
    "meta-set": cmd_meta_set,
    "meta-set-json": cmd_meta_set_json,
    "meta-get": cmd_meta_get,
}


def build_parser():
    p = argparse.ArgumentParser(description="Local store for the job-application skill.")
    p.add_argument("--root", help="store directory (default ~/.job-application)")
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("init", "paths", "profile-get", "answer-list", "history-status", "stats", "roles-list"):
        sub.add_parser(name)

    sp = sub.add_parser("profile-replace"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("answer-put"); sp.add_argument("--input", required=True); sp.add_argument("--remember-sensitive", action="store_true")
    sp = sub.add_parser("answer-find"); sp.add_argument("--question", required=True)
    sp = sub.add_parser("answers-resolve"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("history-append"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("role-put"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("history-list"); sp.add_argument("--company")
    sp = sub.add_parser("session-save"); sp.add_argument("--id", required=True); sp.add_argument("--input", required=True)
    sp.add_argument("--allow-known", action="store_true",
                    help="save a new watch entry even though the posting is already on "
                         "record. For restoring a role deliberately, never for a sweep.")
    for name in ("session-load", "session-delete"):
        sp = sub.add_parser(name); sp.add_argument("--id", required=True)
    sub.add_parser("session-list")
    sp = sub.add_parser("meta-set"); sp.add_argument("--key", required=True); sp.add_argument("--value", required=True)
    sp = sub.add_parser("meta-set-json"); sp.add_argument("--key", required=True); sp.add_argument("--input", required=True)
    sp = sub.add_parser("meta-get"); sp.add_argument("--key")
    return p


def main():
    args = build_parser().parse_args()
    try:
        result = COMMANDS[args.command](args)
    except StoreError as exc:
        print(f"job-application store: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"job-application store: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except json.JSONDecodeError:
        print("job-application store: input is not valid JSON", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
