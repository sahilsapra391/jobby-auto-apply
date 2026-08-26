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
        # The base resume's editable wording. The template it renders through is
        # tracked in the repository and the board cannot write there, so an edit
        # lands here and the document worker prefers it when it exists.
        "resumeContent": str(root / "resume-content.json"),
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


def answered(entry):
    """True when this entry actually carries an answer.

    Four of the stored entries put theirs in `answer` rather than `value`,
    which is where the field started, and an unanswered entry written by the
    park loop has neither. Both doors have to be checked, or the loop's own
    placeholders would read back as answers and stop parking the question they
    exist to keep asking.
    """
    if not isinstance(entry, dict):
        return False
    for field in ("value", "answer"):
        text = entry.get(field)
        if text is not None and str(text).strip():
            return True
    return False


# A one-time code is worthless a minute after it arrives and dangerous for as
# long as it is written down, so it is not Memory Bank material however it
# reaches the store. Both doors are checked: an employer can label a field
# "Verification code", and a user can paste one into an ordinary box.
OTP_MARKERS = (
    "one-time code", "one time code", "one-time password", "one time password",
    "onetime code", "onetime password", "verification code", "confirmation code",
    "security code", "authentication code", "auth code", "sms code",
    "login code", "access code", "one-time pin", "one time pin",
    " otp", "otp ", "otp:", "(otp", "2fa", "two-factor", "two factor",
    "passcode",
)

# What a bare code looks like once the wording is gone: six to eight digits and
# nothing else. Checked on the value only, because a question can legitimately
# contain a number and an answer of exactly this shape almost never can.
BARE_CODE = re.compile(r"^\s*\d{4,8}\s*$")


def otp_shaped(question, value=""):
    """Why this looks like a one-time code, or None."""
    text = f" {(question or '').lower()} "
    for marker in OTP_MARKERS:
        if marker in text:
            return f"{marker.strip()!r} makes this a one-time code"
    if BARE_CODE.match(str(value or "")):
        return "the answer is a bare numeric code"
    return None


# The acts the product refuses to perform on the user's behalf. A bank edit
# that made one of these resolve would not remove the refusal, it would make it
# silent: the form gets filled instead of parked, and the user never sees the
# clause they were supposed to read. So an edit is measured against these
# before it is written, and refused by name if it moves any of them.
#
# Note the shape of the check. It does not assert these resolve to nothing --
# "What is your expected compensation?" already resolves today, by topic, and
# pinning an expectation here would encode one corpus forever. It asserts that
# an edit does not *change* what they resolve to, which stays true as the
# corpus grows and needs no maintenance.
REFUSAL_PROBES = (
    "I authorize the Company to obtain a consumer report about me for employment purposes.",
    "Please acknowledge the FCRA disclosure and sign to permit a background check to be conducted.",
    "Have you ever been convicted of a felony?",
    "Have you ever been convicted of a crime?",
    "Please enter the one-time verification code we sent to your email.",
)
# "Do you have a degree in political science?" is deliberately not here. It is a
# boundary against an *accidental* match with the political-contributions topic,
# owned by tests/test_answer_topics.py, and not an act the product refuses. A
# user who is asked it and answers No is entitled to store that; treating it as
# a refusal would make a legitimate answer unstorable to protect a boundary that
# is already tested elsewhere.


# Questions the Memory Bank must not hold an answer to at all, as opposed to
# questions it must not let another entry answer by accident. Two different
# failures: the guard below refuses the entry, refusal_shadow() refuses the
# side effect.
#
# The line is the one §11 already draws and it is narrow on purpose. Being
# *willing* to undergo a background check is a statement of intent and is a
# stored standing answer; *authorising* one is an act performed on the user's
# behalf, and no stored text may perform it.
REFUSAL_CATEGORIES = (
    ("consumer-report-authorisation", (
        ("authorize", "authorise", "authorization", "authorisation", "i consent", "i hereby"),
        ("consumer report", "consumer credit", "investigative report",
         "background check", "background investigation", "background screening"),
    )),
    ("fcra-disclosure", (("fcra", "fair credit reporting act"), ())),
    # "conviction" on its own is not this category. Measured against the real
    # store on 2026-08-25: it caught a story-bank entry about arguing a
    # root-cause conviction against consensus, which is a belief, not a record.
    ("criminal-history", ((
        "convicted of", "been convicted", "criminal conviction",
        "criminal history", "criminal record", "criminal offence",
        "criminal offense", "felony", "misdemeanor", "misdemeanour",
        "plead guilty", "pled guilty", "pleaded guilty",
    ), ())),
    ("captcha", (("captcha", "recaptcha", "hcaptcha"), ())),
    ("credentials", ((
        "password", "passphrase", "security question", "create an account",
        "create your account",
    ), ())),
)


def refusal_category(question):
    """The refusal this question belongs to, or None.

    A group with a second tuple needs a term from each: "authorize" alone is an
    ordinary word and "background check" alone is the willingness question the
    user has answered Yes to for months. Together they are the act.
    """
    text = f" {(question or '').lower()} "
    for name, groups in REFUSAL_CATEGORIES:
        first, second = groups
        if not any(m in text for m in first):
            continue
        if second and not any(m in text for m in second):
            continue
        return name
    return None


def _resolved(answers, question):
    """What find_answer would fill for this question, as a comparable value."""
    found = find_answer(answers, question) or {}
    if found.get("match") == "none":
        return None
    return found.get("value") if found.get("value") is not None else found.get("answer")


def refusal_shadow(before, after):
    """The refusal probe an edit would change the answer to, or None.

    Run against the answers as they *would be*, not as they are, so a shadow is
    refused at the edit rather than discovered on a real form months later.
    """
    for probe in REFUSAL_PROBES:
        was = _resolved(before, probe)
        now_value = _resolved(after, probe)
        if was != now_value:
            return probe
    return None


def bank_version(entry):
    """The monotonic version of a stored entry. Absent means never edited."""
    try:
        return int((entry or {}).get("version") or 0)
    except (TypeError, ValueError):
        return 0


def classify(entry):
    """Add the data-class marking the multi-tenant plan's Class 2 work reads.

    Sensitive answers (compensation, demographics, work authorisation, date of
    birth) are the fields destined for per-user envelope encryption. Nothing
    here encrypts anything; it marks which records that work has to reach, so
    the migration has something to select on rather than re-deriving the set
    from tag spellings.
    """
    sensitive = entry.get("state") == "sensitive" or bool(entry.get("sensitive"))
    entry["sensitive"] = sensitive
    entry["dataClass"] = "class-2" if sensitive else "class-1"
    entry["encryption"] = "pending" if sensitive else "none"
    return entry


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
        # Structured, not just prose, because the Memory Bank has to tell this
        # apart from a question nobody has answered yet. Both hold no value;
        # only one of them is something to go and ask the user about.
        entry["remembered"] = False

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
    ("sponsorship-any-country", (
        "require sponsorship", "requires sponsorship", "need sponsorship",
        "visa sponsorship", "sponsorship for employment", "sponsor an employment",
        "sponsor a visa", "sponsorship now or in the future",
        "require the company to sponsor",
    )),
    ("work-authorisation-any-country", (
        "legally authorized to work", "legally authorised to work",
        "authorized to work in", "authorised to work in",
        "right to work in", "eligible to work in", "work authorization in",
        "work authorisation in",
    )),
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
    # India before the rest, because "expected CTC" is an Indian question and
    # the two figures are in different currencies. Both entries are sensitive,
    # which blocks fuzzy matching on purpose; a marker is exact, not fuzzy, and
    # a figure the user stored to be used is no use if nothing can reach it.
    # Deliberately after salary-range-acknowledgement, which is scanned first:
    # "does this range align with your compensation expectations" is a yes, and
    # answering it with a number instead would be the worst mis-fill here.
    ("expected-compensation-india", (
        "expected ctc", "ctc expectation", "expectation ctc", "expected lpa",
        "expected salary in lpa", "expected annual ctc",
    )),
    ("expected-compensation", (
        "expected salary", "salary expectation", "expected compensation",
        "compensation expectation", "desired salary", "desired compensation",
        "expected base salary", "expected annual salary", "salary requirement",
        "salary requirements", "expected pay", "what are your salary",
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


def topic_answer(answers, question):
    """A standing answer recognised by a distinctive phrase, or None."""
    rated = skill_rating(question)
    if rated:
        return rated
    text = (question or "").lower()
    for topic, markers in TOPIC_MARKERS:
        if not any(m in text for m in markers):
            continue
        for entry in answers.values():
            # An entry the park loop opened carries the topic tag but no answer
            # yet. Returning it would fill a field with nothing and stop the
            # question ever being asked again.
            if topic in (entry.get("tags") or []) and answered(entry):
                return {"match": "topic", "topic": topic, **entry}
    return None


def find_answer(answers, question):
    key = answer_key(question)
    held = answers.get(key)
    # An entry with no answer is a question the bank is tracking, not an answer
    # to it. It has to fall through to "none" or the grow-only loop would close
    # its own question the moment it opened it.
    if held is not None and answered(held):
        return held
    # Fall back to overlap scoring so slightly different phrasings still match.
    # The bar is deliberately high: "product management" vs "project management"
    # scores 0.71 — one word apart, and the wrong answer.
    target = set(key.split("-"))
    scored = []
    for k, v in answers.items():
        other = set(k.split("-"))
        if not other or not answered(v):
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

    # Candidates carry the stored question, never the value; to use one,
    # confirm it means the same thing, then look it up by its own question.
    candidates = [
        {"key": k, "question": v.get("question"), "state": v.get("state"), "score": round(s, 2)}
        for s, k, v in scored[:3] if s >= 0.4
    ]
    out = {"match": "none", "key": key, "candidates": candidates}
    # Say when the bank already knows this question and is still waiting on an
    # answer, so a second application does not open a second entry for it.
    if held is not None:
        out["open"] = True
        out["askedBy"] = list(held.get("askedBy") or [])
    return out


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


# --- the Memory Bank --------------------------------------------------------
#
# Two doors write here and they have to converge on one record. The board edits
# an answer directly; an application that parks on a question it cannot answer
# opens the same question as an unanswered entry. A question answered through
# both doors must not leave two entries behind, so every path keys on
# answer_key() and checks for the existing record first.
#
# Idempotency is a version plus the id of the edit that produced it. The board
# re-sends clicks it remembers, so the same edit arrives more than once: the
# replay carries the same editId, sees it already recorded, and does nothing.
# A genuinely concurrent edit carries a different id at the same version and is
# refused rather than allowed to clobber, because the board can re-read and the
# store cannot un-lose an answer.

MAX_ANSWER_BYTES = 4000
MAX_QUESTION_BYTES = 600


def _bank_text(value, field, maximum):
    text = "" if value is None else str(value)
    if len(text.encode("utf-8")) > maximum:
        raise StoreError(f"{field} is longer than {maximum} bytes")
    return text


def _bank_write(root, key, entry, before):
    """Write one entry after the guards that apply to every door."""
    after = dict(before)
    if entry is None:
        after.pop(key, None)
    else:
        after[key] = entry
    shadowed = refusal_shadow(before, after)
    if shadowed:
        raise StoreError(
            "that would answer a question this product refuses to answer for "
            f"you: {shadowed!r}. The entry was not written.")
    write_private(paths(root)["answers"], after)
    return after


def cmd_answer_edit(args):
    """Save one Memory Bank entry, from the board.

    The edit carries the version it was made against. Matching means it applies
    and the version moves; not matching means either this exact edit already
    landed (same editId, so a replay) or something else moved first (refused).
    """
    payload = load_input(args.input)
    if not isinstance(payload, dict):
        raise StoreError("an answer edit must be a JSON object")
    question = _bank_text(payload.get("question"), "question", MAX_QUESTION_BYTES).strip()
    if not question:
        raise StoreError("an answer edit requires a question")
    value = _bank_text(payload.get("value"), "value", MAX_ANSWER_BYTES).strip()
    state = payload.get("state") or ("sensitive" if payload.get("sensitive") else "confirmed")
    if state not in ANSWER_STATES:
        raise StoreError(f"state must be one of {sorted(ANSWER_STATES)}")
    edit_id = _bank_text(payload.get("editId"), "editId", 120).strip()
    if not edit_id:
        raise StoreError("an answer edit requires an editId")

    reason = otp_shaped(question, value)
    if reason:
        raise StoreError(
            f"one-time codes are never stored: {reason}. Nothing was written.")
    # The category guard stops a *new* answer being created for an act the
    # product refuses to perform. It deliberately does not re-litigate an entry
    # the user already curated: the store holds a policy note titled "background
    # check, willingness versus authorisation" whose whole subject is this
    # distinction, and a guard that made that note uneditable would be reading
    # the word rather than the situation. Every edit still passes the shadow
    # check below, so a grandfathered entry cannot be turned into one that
    # answers a refusal probe.
    refused = refusal_category(question)

    root = root_dir(args.root)
    path = paths(root)["answers"]
    answers = read_json(path, {})
    key = _bank_text(payload.get("key"), "key", 120).strip() or answer_key(question)
    stored = answers.get(key)
    if refused and (stored is None or stored.get("question") != question):
        raise StoreError(
            f"this is a {refused.replace('-', ' ')} question. Jobby does not "
            "answer these for you and will not store an answer to one.")
    seen = int(payload.get("version") or 0)
    if stored is not None and stored.get("lastEditId") == edit_id:
        return {"key": key, "applied": False, "reason": "already applied",
                "version": bank_version(stored)}
    if bank_version(stored) != seen:
        return {"key": key, "applied": False, "reason": "conflict",
                "version": bank_version(stored), "seen": seen}

    entry = dict(stored or {})
    entry.update({
        "key": key,
        "question": question,
        "value": value or None,
        "state": state,
        "updatedAt": now(),
        "version": seen + 1,
        "lastEditId": edit_id,
    })
    # `answer` was where the field started and four entries still use it. An
    # edit that set `value` and left `answer` behind would show the new text and
    # fill the old one.
    entry.pop("answer", None)
    tags = payload.get("tags")
    if isinstance(tags, list):
        entry["tags"] = [str(t)[:60] for t in tags][:20]
    if value:
        # Answering closes the loop's own placeholder. The record stays, so the
        # question is still recognised; it simply stops being outstanding.
        entry["tags"] = [t for t in (entry.get("tags") or []) if t != "unanswered"]
        entry.pop("openedAt", None)
        entry["answeredAt"] = now()
    classify(entry)
    _bank_write(root, key, entry, answers)
    return {"key": key, "applied": True, "version": entry["version"],
            "state": entry["state"], "stored": entry["value"] is not None}


def cmd_answer_open(args):
    """Open a question the bank cannot answer yet, from a parked application.

    This is the grow-only loop. It never overwrites an answer, and a question
    asked by a second employer joins the entry that already exists rather than
    creating a second one, which is what makes the two doors converge.
    """
    payload = load_input(args.input)
    if not isinstance(payload, dict):
        raise StoreError("an opened question must be a JSON object")
    question = _bank_text(payload.get("question"), "question", MAX_QUESTION_BYTES).strip()
    if not question:
        raise StoreError("opening a question requires the question")
    application = _bank_text(payload.get("applicationId"), "applicationId", 120).strip()

    reason = otp_shaped(question)
    if reason:
        return {"opened": False, "reason": f"one-time code: {reason}"}
    refused = refusal_category(question)
    if refused:
        # Not bank material, so it stays a blocker on the card and nothing
        # invites the user to answer it here and be refused a step later.
        return {"opened": False, "reason": f"refusal category: {refused}"}

    root = root_dir(args.root)
    path = paths(root)["answers"]
    answers = read_json(path, {})
    key = answer_key(question)
    stored = answers.get(key)
    if stored is not None and answered(stored):
        return {"key": key, "opened": False, "reason": "already answered"}
    if stored is not None and stored.get("remembered") is False:
        # The user filled this once and chose not to have it kept. Opening it
        # as an outstanding question would ask them to reverse that decision
        # every time an employer asks.
        return {"key": key, "opened": False,
                "reason": "the user chose not to store this value"}

    entry = dict(stored or {})
    asked = [a for a in (entry.get("askedBy") or []) if isinstance(a, str)]
    if application and application not in asked:
        asked.append(application)
    entry.update({
        "key": key,
        "question": entry.get("question") or question,
        "value": None,
        "state": "missing",
        "askedBy": asked[:50],
        "openedAt": entry.get("openedAt") or now(),
        "lastAskedAt": now(),
        "updatedAt": now(),
        "version": bank_version(entry),
    })
    tags = [t for t in (entry.get("tags") or []) if t != "unanswered"]
    entry["tags"] = tags + ["unanswered"]
    classify(entry)
    _bank_write(root, key, entry, answers)
    return {"key": key, "opened": stored is None, "joined": stored is not None,
            "askedBy": entry["askedBy"]}


# --- base resumes -----------------------------------------------------------
#
# `profile.resumes[]` is already the identities table the multi-tenant plan
# names in §8: one entry per right to work, each with its own contact block and
# base document, exactly one marked default. The commands below keep that
# contract rather than replacing it, so the later migration is a lift.

RESUME_TEMPLATE_CONTENT = (
    Path(__file__).resolve().parents[1] / "templates" / "resume-content.json"
)


def resume_content(root):
    """The editable resume wording: the device's copy, else the shipped base."""
    override = read_json(paths(root)["resumeContent"], None)
    if isinstance(override, dict) and override:
        return override
    try:
        return json.loads(RESUME_TEMPLATE_CONTENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # The deployed renderer carries no templates directory. It never calls
        # this, but an import-time surprise there is expensive and a missing
        # base is simply nothing to edit.
        return {}


def cmd_resume_content_get(args):
    return resume_content(root_dir(args.root))


def _same_shape(base, candidate):
    """Why the edited wording does not fit the template, or None.

    The template has a fixed number of slots and the layout gate measures the
    render against the base, so a list that grows or shrinks does not produce a
    worse resume, it produces one that fails to render at all. Refusing here
    names the field; refusing at render time names a slot count.
    """
    for field in ("banner", "summary"):
        if not isinstance(candidate.get(field), str):
            return f"{field} must be text"
    for field in ("areasOfExpertise", "technical", "roles"):
        got, want = candidate.get(field), base.get(field)
        if not isinstance(got, list):
            return f"{field} must be a list"
        if isinstance(want, list) and len(got) != len(want):
            return (f"{field} has {len(got)} entries and the template has "
                    f"{len(want)}; the wording can change, the count cannot")
    for index, row in enumerate(candidate.get("technical") or []):
        if not isinstance(row, dict) or not isinstance(row.get("label"), str) \
                or not isinstance(row.get("items"), str):
            return f"technical row {index + 1} needs a label and items"
    for index, role in enumerate(candidate.get("roles") or []):
        if not isinstance(role, dict):
            return f"role {index + 1} must be an object"
        bullets = role.get("bullets")
        if not isinstance(bullets, list):
            return f"role {index + 1} needs a list of bullets"
        want = ((base.get("roles") or [{}] * (index + 1))[index] or {}).get("bullets")
        if isinstance(want, list) and len(bullets) != len(want):
            return (f"role {index + 1} has {len(bullets)} bullets and the template "
                    f"has {len(want)}; the wording can change, the count cannot")
    return None


def cmd_resume_content_put(args):
    candidate = load_input(args.input)
    if not isinstance(candidate, dict):
        raise StoreError("resume content must be a JSON object")
    root = root_dir(args.root)
    base = resume_content(root)
    problem = _same_shape(base, candidate)
    if problem:
        raise StoreError(problem)
    candidate["updatedAt"] = now()
    write_private(paths(root)["resumeContent"], candidate)
    return {"saved": True, "updatedAt": candidate["updatedAt"]}


def _profile_resumes(root):
    profile = read_json(paths(root)["profile"], {})
    resumes = profile.get("resumes")
    if not isinstance(resumes, list) or not resumes:
        raise StoreError("this profile has no base resumes")
    return profile, resumes


def cmd_resume_default(args):
    root = root_dir(args.root)
    profile, resumes = _profile_resumes(root)
    if not any(entry.get("key") == args.key for entry in resumes):
        raise StoreError(f"no base resume with key {args.key!r}")
    for entry in resumes:
        entry["default"] = entry.get("key") == args.key
    profile["updatedAt"] = now()
    write_private(paths(root)["profile"], profile)
    return {"default": args.key,
            "resumes": [e.get("key") for e in resumes]}


def _open_regions(root):
    """Regions with work still running, from the sessions on disk."""
    live = set()
    sessions = Path(paths(root)["sessions"])
    if not sessions.exists():
        return live
    for f in sessions.glob("*.json"):
        entry = read_json(f, {})
        if entry.get("status") in ("dropped", "completed", "abandoned"):
            continue
        region = entry.get("region")
        if region:
            live.add(str(region).upper())
    return live


def cmd_resume_remove(args):
    """Remove a base resume, unless something still depends on it.

    Both refusals are about not orphaning work already under way. The default is
    what every posting without a region match falls back to, and the last base
    for a region is what every open application in that region will render from.
    """
    root = root_dir(args.root)
    profile, resumes = _profile_resumes(root)
    target = next((e for e in resumes if e.get("key") == args.key), None)
    if target is None:
        raise StoreError(f"no base resume with key {args.key!r}")
    if target.get("default"):
        raise StoreError(
            "that is the default base resume. Make another one the default "
            "first, then remove this one.")
    regions = {str(r).upper() for r in (target.get("appliesTo") or [])}
    remaining = {
        str(r).upper()
        for entry in resumes if entry is not target
        for r in (entry.get("appliesTo") or [])
    }
    stranded = sorted((regions - remaining) & _open_regions(root))
    if stranded:
        raise StoreError(
            f"it is the only base resume for {', '.join(stranded)} and there is "
            "open work there. Finish or drop those applications first.")
    profile["resumes"] = [e for e in resumes if e is not target]
    profile["updatedAt"] = now()
    write_private(paths(root)["profile"], profile)
    return {"removed": args.key,
            "resumes": [e.get("key") for e in profile["resumes"]]}


def cmd_answer_delete(args):
    root = root_dir(args.root)
    path = paths(root)["answers"]
    answers = read_json(path, {})
    key = args.key
    stored = answers.get(key)
    if stored is None:
        return {"key": key, "deleted": False, "reason": "no such entry"}
    edit_id = (args.edit_id or "").strip()
    seen = int(args.version or 0)
    if bank_version(stored) != seen:
        return {"key": key, "deleted": False, "reason": "conflict",
                "version": bank_version(stored), "seen": seen}
    _bank_write(root, key, None, answers)
    return {"key": key, "deleted": True, "editId": edit_id or None}


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
    "answer-edit": cmd_answer_edit,
    "answer-open": cmd_answer_open,
    "answer-delete": cmd_answer_delete,
    "resume-content-get": cmd_resume_content_get,
    "resume-content-put": cmd_resume_content_put,
    "resume-default": cmd_resume_default,
    "resume-remove": cmd_resume_remove,
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

    for name in ("init", "paths", "profile-get", "answer-list", "history-status",
                 "stats", "roles-list", "resume-content-get"):
        sub.add_parser(name)

    sp = sub.add_parser("profile-replace"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("answer-put"); sp.add_argument("--input", required=True); sp.add_argument("--remember-sensitive", action="store_true")
    sp = sub.add_parser("answer-find"); sp.add_argument("--question", required=True)
    sp = sub.add_parser("answer-edit"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("answer-open"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("answer-delete"); sp.add_argument("--key", required=True)
    sp.add_argument("--version", default=0); sp.add_argument("--edit-id")
    sp = sub.add_parser("resume-content-put"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("resume-default"); sp.add_argument("--key", required=True)
    sp = sub.add_parser("resume-remove"); sp.add_argument("--key", required=True)
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
