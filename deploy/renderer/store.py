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
# "dismissed" is the user saying they will never answer this. It is the only
# state that means something about their intent rather than about the value, and
# it is the one the grow-only loop consults before opening a question again.
ANSWER_STATES = {"confirmed", "inferred", "missing", "sensitive", "dismissed"}
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

    # Class B mirror, after the file and never before it. The file is on disk
    # and this caller has already succeeded, so nothing below may change that:
    # `after_write` swallows its own failures and logs them, and parity is what
    # makes the resulting drift visible. See docs/write-paths.md for why this
    # ordering is the opposite of the pool's.
    _mirror_class_b(path, payload)
    _observe_write_path()


def _observe_write_path():
    """Tick the write paths that led here, for the parity gate's checklist.

    Under the same never-affect-the-caller contract as the mirror above, and
    for a stronger reason: that one at least mirrors real data, while this is
    only bookkeeping. A coverage counter that could fail a real application
    would be a worse bug than the gap it exists to close.

    The hook is here rather than at each write path because `write_private` is
    the only function that puts bytes in the store, and `jobs_store._persist_
    pool` reaches disk through it -- so the pool is observed without being
    modified, which matters because `_persist_pool` is a parity gate path and
    changing one restarts the clock.
    """
    try:
        import write_path_coverage
        write_path_coverage.observe()
    except Exception:
        # Deliberately silent here and loud inside `observe`, which writes its
        # own reason to stderr. An ImportError on a machine without the sidecar
        # is not worth a line on every write in the system.
        pass


def _mirror_class_b(path, payload):
    """Imported lazily, and failing quietly on purpose.

    Lazy because `store` travels in the Vercel renderer bundle, which carries
    no packages/schema and no psycopg: a top-level import here 502s every
    deployed request while every local test passes, which is the failure
    tests/test_renderer_sync.py caught once already.

    Quiet because a class B write must never fail on the mirror's account. The
    loudness lives inside `class_b.after_write`, which writes to stderr and to
    logs/dual-write.log.
    """
    try:
        import class_b
    except Exception:
        return
    try:
        class_b.after_write(path, payload)
    except Exception as exc:                             # pragma: no cover
        # after_write is supposed to swallow its own failures. If one escapes,
        # it still must not fail the write, and it must not be silent either.
        sys.stderr.write(f"store: class B mirror escaped: {exc!r}\n")


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


def dismissed(entry):
    """True when the user has said they will never answer this question.

    Distinct from every other reason an entry carries no value, and the
    distinction is the whole feature. `missing` is a question nobody has got to
    yet. `remembered is False` is a value that was filled once and deliberately
    not kept. This is a question the user has refused, and it is the only one of
    the three that must stop the grow-only loop from opening it again.
    """
    return isinstance(entry, dict) and entry.get("state") == "dismissed"


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


# What the loop must open as sensitive rather than as an ordinary question.
#
# An opened entry is born with no value and no tags, so without this a parked
# "What is your expected compensation?" becomes a plain entry, the Profile page
# offers it as an ordinary answer, and a salary gets stored unflagged and
# unmasked. The state has to be right at the moment the question is recorded,
# because that is the only moment anything knows what the question was about.
#
# The set is §4's, from the north star: compensation, demographics, work
# authorisation and date of birth.
# Word-bounded, and that is not a detail. Measured against the real 156-entry
# store on 2026-08-26, a substring list flagged "What is your average deal
# size?", "How do you manage a team?" and "Do you embrace ambiguity?" as
# sensitive, because "age " lives inside "average" and "manage", and "race"
# inside "embrace". A classifier that masks half the bank is worse than none.
SENSITIVE_SHAPE = re.compile(
    r"\b("
    r"compensation|salary|salaries|ctc|remuneration|"
    r"(?:expected|current|desired|base|hourly|target)\s+pay|pay\s+rate|"
    r"race|ethnicity|ethnic\s+origin|hispanic|latino|"
    r"gender|sex|sexual\s+orientation|lgbt\w*|transgender|"
    r"disability|disabled|veteran|"
    r"date\s+of\s+birth|birth\s+date|age\s+(?:range|bracket|group)|"
    r"marital\s+status|religion|"
    r"national\s+origin|visa\s+sponsorship|sponsorship"
    r")\b",
    re.I,
)


def sensitive_question(question):
    """True when a question is one the store must treat as Class 2 on sight."""
    return bool(SENSITIVE_SHAPE.search(question or ""))


# Questions the boundary suite is actively protecting, which the loop must
# never plant on its own.
#
# This is a narrower rule than refusal_category, and a different one. Nothing
# here is an act the product refuses; a person may answer any of it deliberately
# from the Profile page. What must not happen is Jobby *planting* the question
# unprompted, because each of these sits one word from a topic that already has
# a standing answer, and an entry here is the shortest path to a wrong field
# being filled quietly. §10 lists them as tested boundaries and they stay that
# way.
BOUNDARY_QUESTIONS = (
    "Do you have a degree in political science?",
)


def boundary_question(question):
    """True when the loop must not open this question by itself."""
    target = answer_key(question)
    return any(answer_key(probe) == target for probe in BOUNDARY_QUESTIONS)


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
    birth) are the fields destined for per-user envelope encryption, per §6 of
    docs/plans/multi-tenant-architecture.md. Nothing here encrypts anything; it
    marks which records that work has to reach, so the migration has something
    to select on rather than re-deriving the set from tag spellings.

    That work is issue #82, which also carries the reason a sensitive answer's
    value is withheld from the board entirely today rather than published and
    masked. The marker points at a task with an acceptance list, not at an
    intention.
    """
    sensitive = entry.get("state") == "sensitive" or bool(entry.get("sensitive"))
    entry["sensitive"] = sensitive
    entry["dataClass"] = "class-2" if sensitive else "class-1"
    # There is deliberately no `encryption` field beside this. It existed, held
    # the constant "pending" on every sensitive record and "none" on every other
    # one, was written by nobody else and read by nothing. A status field with
    # one value and no writer is not a status, it is a promise, and it ages into
    # a lie the longer it sits there looking like it is tracking something.
    # `dataClass` says what the record is, #82 says what is owed about it, and
    # neither of those pretends to be a state machine.
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
    # The EEO block, which every US application ends with and which overlap
    # scoring cannot reach. Two reasons it cannot, and both had to be true for
    # this to keep happening. The words differ: "military status" and "veteran"
    # share nothing at all, and "disability-status" against "have-disability"
    # scores 0.33, under the 0.4 needed even to be offered as a candidate. And
    # a sensitive answer is barred from fuzzy matching on purpose, so the one
    # path left open to the others was closed to these by design.
    #
    # So the Robinhood application parked on gender, race, military and
    # disability with every one of them answered on 2026-08-19, and the agent
    # was shown no candidate to follow up because there was none to show.
    #
    # Every phrase here carries the question's own shape with it, because the
    # near misses are questions with different answers rather than different
    # wordings of one question. Measured over 254 real questions from the store
    # and the captured forms, plus nine adversarial probes: 2 correct hits each
    # and 0 false positives. Two phrases were cut by that measurement and are
    # listed so nobody adds them back. "armed forces" matched Axon's "discharged
    # from the Armed Forces under dishonorable conditions", which is conduct,
    # not status. "hispanic or latino" matched Versant's "Are you Hispanic or
    # Latino?", which wants Yes or No and would have been handed a descriptive
    # ethnicity string. A bare "a disability" is absent too: it appears in
    # accommodation requests, which ask for something rather than state a fact.
    ("gender-identity", (
        "gender identity", "what is your gender", "select your gender",
        "gender (select",
    )),
    ("race-ethnicity", (
        "race or ethnicity", "race/ethnicity", "what is your ethnicity",
        "what is your race", "racial or ethnic", "ethnic background",
    )),
    ("military-status", (
        "military status", "veteran status", "protected veteran",
        "are you a veteran",
    )),
    ("disability-status", (
        "disability status", "do you have a disability", "form cc-305",
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
    # BEFORE relationships-conflicts, because the two topics hold different
    # answers to questions that share vocabulary. Scanning is in list order and
    # the first topic with a tagged answer wins, so the position IS the
    # behaviour, and it is the whole of the fix.
    #
    # What the position does not do is close the fallback. A question about
    # government officials usually also says "close personal relationship",
    # which is a conflicts marker on its own account, so with no government
    # answer tagged it still reaches the client entry. Ordering makes the live
    # store correct rather than making the fault impossible; the test says so
    # rather than asserting a guard that is not here.
    #
    # The first version put "government official" into relationships-conflicts
    # and it matched there. `topic_answer` returned that topic's tagged entry,
    # which is "Are you related to ... a director, officer or senior employee
    # of a CLIENT". The agent read a "No" whose stored question was about
    # clients, judged it did not cover a question about governments, and parked
    # rather than answer a bribery-and-corruption declaration out of the wrong
    # source. It was right to, and the fault was mine: a topic answers with one
    # of its own tagged entries, so a marker filed under the wrong topic
    # returns the wrong question's answer.
    #
    # Only the clean entry is tagged. `related-to-government-official` carries
    # the same "No" wrapped in prose about a standing instruction, which is
    # right to read and wrong to type into a form field.
    ("government-official", (
        "government official", "public official", "politically exposed",
    )),
    ("relationships-conflicts", (
        "close associate", "conflict of interest", "conflicts of interest",
        "related to, by birth", "related by birth", "birth, adoption or marriage",
        "immediate family member employed", "relative employed by",
        # Finance compliance blocks ask this without using either of the two
        # words above. Robinhood asks for "Personal/Familial Relationships,
        # Outside Business Activities, qualifying investments, or Intellectual
        # Property Ownership" in one field, and asks separately whether the
        # applicant holds or has held a government position, was referred by
        # someone who does, or has a close relationship with one. Both parked
        # the 2026-08-28 application against a standing "NO, always" recorded
        # on 2026-08-19.
        #
        # "close relationship with one" rather than "close relationship":
        # measured against 254 real questions plus six probes, the bare phrase
        # also takes "Do you have a close relationship with your current
        # manager?", which is not a conflict disclosure. With these phrases the
        # additions hit 5 questions, all of them the intended ones, and none of
        # the probes.
        "familial relationship", "personal/familial",
        "outside business activities", "close personal relationship",
        "close relationship with one",
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
    # Every resolver below sees only entries that actually carry an answer.
    #
    # Filtered once, here, rather than guarded inside each resolver. Guarding
    # them one at a time is exactly how this broke: the grow-only loop added
    # the rule, `topic_answer` was taught it, and the next merge brought
    # `acknowledgement`, `expected_pay` and `us_person` from another branch,
    # each iterating `answers.values()` with no idea there was a rule to
    # follow. A resolver added tomorrow inherits the guard by construction.
    usable = {k: v for k, v in answers.items() if answered(v)}

    # Fall back to overlap scoring so slightly different phrasings still match.
    # The bar is deliberately high: "product management" vs "project management"
    # scores 0.71 — one word apart, and the wrong answer.
    target = set(key.split("-"))
    scored = []
    for k, v in usable.items():
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
    topical = topic_answer(usable, question)
    if topical is not None:
        return topical
    ack = acknowledgement(usable, question)
    if ack is not None:
        return ack

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


# Facts the Profile page states outright, recognised with word boundaries
# because substring matching on a semantic category is a bug waiting for a
# corpus (see CLAUDE.md). "Company website" under "where did you hear about
# us" must never read as a request for the user's own website, which is why
# the website label match is exact rather than a search.
RELOCATION_QUESTION = re.compile(r"\brelocat|\bwilling to move\b", re.I)
RELOCATION_WHERE = re.compile(
    r"\bwhere\b|\bwhich (city|cities|location|locations|office|offices)\b", re.I)
FUTURE_CONTACT_TOPIC = re.compile(
    r"\bfuture\b[^.?]{0,60}\b(opportunit\w*|roles?|positions?|openings?)\b", re.I)
FUTURE_CONTACT_VERB = re.compile(
    r"\b(contact|consider|keep|notify|inform|reach)\b", re.I)
PRIVACY_NOTICE = re.compile(
    r"\bprivacy\b[^.?]{0,40}\b(policy|notice|statement)\b"
    r"|\b(policy|notice|statement)\b[^.?]{0,40}\bprivacy\b", re.I)
PRIVACY_ACT = re.compile(r"\b(agree|acknowledge|consent|read|reviewed|accept)\b", re.I)
LINKEDIN_QUESTION = re.compile(r"\blinkedin\b", re.I)
GITHUB_QUESTION = re.compile(r"\bgithub\b", re.I)
PORTFOLIO_QUESTION = re.compile(r"\bportfolio\b", re.I)
WEBSITE_LABELS = {"website", "personal-website", "website-url", "personal-site",
                  "portfolio-url", "personal-website-url"}
CERTIFICATION_LABELS = {"certifications", "certificates", "relevant-certifications",
                        "licenses-and-certifications", "list-any-certifications"}


def profile_answer(profile, question):
    """An answer derived from a fact the profile states outright, or None.

    The answer bank never held the whole account of the user: links,
    relocation, standing consents and certifications live on the Profile
    page, and nothing consulted it. Measured on 2026-08-28: the TELUS run
    parked "I agree to the Recruiting Privacy Policy" into Needs you while
    profile.preferences.standingConsents.recruitmentPrivacyNotice said
    "confirm on every application".

    Only facts the profile states directly are derived; nothing is inferred.
    Education, enrollment and graduation are deliberately absent: measured
    the same day, the profile's education list lacked the user's current
    graduate program entirely, so deriving "anticipated graduation" from it
    would have answered "already graduated 2023" against a true answer of
    Dec 2027. A stale profile must fail toward asking, never toward a
    confident wrong answer on a real form.

    Consents follow the profile's own carve-out and the store's existing
    fence: an acknowledgment of a document may be standing, an authorisation
    or truth attestation never is (NOT_A_BARE_ACKNOWLEDGEMENT).
    """
    if not isinstance(profile, dict):
        return None
    text = question or ""
    prefs = profile.get("preferences") or {}
    links = profile.get("links") or {}
    label = answer_key(text)

    def hit(key, value):
        return {"match": "profile", "profileKey": key, "value": str(value),
                "state": "confirmed"}

    if links.get("linkedin") and LINKEDIN_QUESTION.search(text):
        return hit("links.linkedin", links["linkedin"])
    if links.get("github") and GITHUB_QUESTION.search(text):
        return hit("links.github", links["github"])
    if links.get("website") and (PORTFOLIO_QUESTION.search(text)
                                 or label in WEBSITE_LABELS):
        return hit("links.website", links["website"])

    note = str(prefs.get("relocationNote") or "")
    if RELOCATION_QUESTION.search(text) and re.search(r"\byes\b", note, re.I):
        targets = prefs.get("relocationTargets") or []
        if targets and RELOCATION_WHERE.search(text):
            return hit("preferences.relocationTargets",
                       "Yes. Preferred: " + "; ".join(str(t) for t in targets))
        return hit("preferences.relocationNote", "Yes")

    consents = prefs.get("standingConsents") or {}
    if (str(consents.get("futureOpportunityContact") or "").lower().startswith("always yes")
            and FUTURE_CONTACT_TOPIC.search(text)
            and FUTURE_CONTACT_VERB.search(text)):
        return hit("preferences.standingConsents.futureOpportunityContact", "Yes")
    if (str(consents.get("recruitmentPrivacyNotice") or "").lower().startswith("confirm")
            and PRIVACY_NOTICE.search(text) and PRIVACY_ACT.search(text)
            and not NOT_A_BARE_ACKNOWLEDGEMENT.search(text)):
        return hit("preferences.standingConsents.recruitmentPrivacyNotice", "Yes")

    certs = profile.get("certifications") or []
    if certs and label in CERTIFICATION_LABELS:
        return hit("certifications", ", ".join(str(c) for c in certs))
    return None


def resolve_answer(answers, profile, question):
    """One resolution for every consumer: the bank first, then the profile.

    The agent's fill path (resolve_answers), the park gate and the blocker
    close loop must all see the same answer, or the gate refuses parks the
    agent cannot satisfy and the loop closes rows the agent could not fill.
    This is that one place.
    """
    found = find_answer(answers, question)
    if isinstance(found, dict) and found.get("match") == "none":
        derived = profile_answer(profile, question)
        if derived is not None:
            return derived
    return found


def cmd_answer_find(args):
    root = root_dir(args.root)
    answers = read_json(paths(root)["answers"], {})
    profile = read_json(paths(root)["profile"], {})
    return resolve_answer(answers, profile, args.question)


def cmd_answers_resolve(args):
    """Resolve a whole form's questions in one call instead of one per field."""
    questions = load_input(args.input)
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise StoreError("input must be a JSON list of question strings")
    root = root_dir(args.root)
    answers = read_json(paths(root)["answers"], {})
    profile = read_json(paths(root)["profile"], {})
    return {q: resolve_answer(answers, profile, q) for q in questions}


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
    if boundary_question(question):
        return {"opened": False, "reason": "tested boundary: the loop does not plant this"}

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
    if dismissed(stored):
        # The tombstone, and the reason dismissal is a state on the record
        # rather than a delete. A deleted row is simply absent, and absent is
        # what this loop reads as "never asked", so the next park would open the
        # same question again and the user would dismiss it again forever. The
        # `askedBy` list still grows, so a question dismissed once and asked by
        # nine more employers can still be found and reconsidered.
        asked = [a for a in (stored.get("askedBy") or []) if isinstance(a, str)]
        if application and application not in asked:
            entry = dict(stored)
            entry["askedBy"] = (asked + [application])[:50]
            entry["lastAskedAt"] = now()
            _bank_write(root, key, entry, answers)
        return {"key": key, "opened": False,
                "reason": "the user dismissed this question"}

    entry = dict(stored or {})
    asked = [a for a in (entry.get("askedBy") or []) if isinstance(a, str)]
    if application and application not in asked:
        asked.append(application)
    entry.update({
        "key": key,
        "question": entry.get("question") or question,
        "value": None,
        # `missing` unless the question itself is Class 2, in which case it is
        # born sensitive and the Profile page masks it from the first render.
        # Deciding this later, when someone answers it, means deciding it with
        # the question's subject already forgotten.
        "state": "sensitive" if sensitive_question(question) else "missing",
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


def _bank_state_change(args, dismiss, verb):
    """Move one entry between `dismissed` and outstanding, from the board.

    The same version-and-editId discipline `cmd_answer_edit` uses, for the same
    reason: the board replays clicks it remembers, so this arrives twice and the
    second one must do nothing rather than fight the first.
    """
    root = root_dir(args.root)
    answers = read_json(paths(root)["answers"], {})
    key = (args.key or "").strip()
    stored = answers.get(key)
    if stored is None:
        return {"key": key, verb: False, "reason": "no such entry"}
    edit_id = (args.edit_id or "").strip()
    if stored.get("lastEditId") == edit_id and edit_id:
        return {"key": key, verb: False, "reason": "already applied",
                "version": bank_version(stored)}
    seen = int(args.version or 0)
    if bank_version(stored) != seen:
        return {"key": key, verb: False, "reason": "conflict",
                "version": bank_version(stored), "seen": seen}
    if answered(stored):
        # An answer that stayed on a dismissed record would still fill forms,
        # because `find_answer` selects on the value and not on the state. So
        # the two acts stay separate: Forget removes an answer, this refuses a
        # question. Neither one quietly does the other's job.
        return {"key": key, verb: False, "reason": "this entry holds an answer",
                "hint": "forget the answer first; dismissing does not delete one"}

    entry = dict(stored)
    entry["updatedAt"] = now()
    entry["version"] = seen + 1
    if edit_id:
        entry["lastEditId"] = edit_id
    tags = [t for t in (entry.get("tags") or []) if t != "unanswered"]
    if dismiss:
        entry["state"] = "dismissed"
        entry["dismissedAt"] = now()
        entry.pop("openedAt", None)
        entry["tags"] = tags
    else:
        # Restored to exactly the state the loop opens a question in, derived
        # from the question rather than from whatever it held before, so an
        # entry that has been round the loop is indistinguishable from one that
        # never left it.
        entry["state"] = ("sensitive" if sensitive_question(entry.get("question") or "")
                          else "missing")
        entry.pop("dismissedAt", None)
        entry["openedAt"] = now()
        entry["tags"] = tags + ["unanswered"]
    classify(entry)
    _bank_write(root, key, entry, answers)
    return {"key": key, verb: True, "version": entry["version"],
            "state": entry["state"]}


def cmd_answer_dismiss(args):
    """Refuse a question for good, without answering it.

    The bank grows on its own: every application that parks on a question it
    cannot answer plants that question here. Nothing removed one that the user
    was never going to answer, so five Korean-language questions from one
    dropped Coupang application sat in Needs you badging the nav with a number
    that could not reach zero.
    """
    return _bank_state_change(args, True, "dismissed")


def cmd_answer_restore(args):
    """Undo a dismissal, putting the question back among the outstanding."""
    return _bank_state_change(args, False, "restored")


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


# --- base resume selectors --------------------------------------------------
#
# A base was a region and is now a rule. `appliesTo` held a list of country
# codes and nothing else, which is one axis, and it happened to be the axis that
# got built first. A selector is the same idea with a name, a priority and room
# for the dimensions the multi-base plan measured as actually available.
#
# v1 is location only, and that is a measurement rather than a preference:
# against the real 14,293-job pool, `location.country` is populated on 94.5% of
# records while `roleCategory`, `degree`, `employmentType` and `industry` are
# populated on 0%. A selector over role category would match nothing, forever,
# silently. Title keywords and the years band come next, on the `score_*`
# extraction, per docs/plans/multi-base-resumes.md.


def resume_selector(entry):
    """The normalised rule for one base: which countries it claims.

    `appliesTo` is read when no selector is present, so a profile written before
    this existed keeps behaving exactly as it did.
    """
    entry = entry or {}
    selector = entry.get("selector")
    if not isinstance(selector, dict):
        selector = {}
    countries = selector.get("countries")
    if not isinstance(countries, list):
        countries = entry.get("appliesTo") or []
    packs = selector.get("regionPacks")
    if not isinstance(packs, list):
        packs = []
    return {
        "countries": sorted({str(c).upper() for c in countries if str(c).strip()}),
        "regionPacks": sorted({str(p).upper() for p in packs if str(p).strip()}),
    }


def resume_countries(entry):
    """Every country a base claims, with its region packs expanded."""
    selector = resume_selector(entry)
    out = set(selector["countries"])
    if selector["regionPacks"]:
        try:
            import search_config
            for pack in selector["regionPacks"]:
                out |= {str(c).upper() for c in search_config.REGION_PACKS.get(pack, [])}
        except ImportError:
            pass
    return out


def resume_priority(entry):
    try:
        return int((entry or {}).get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def select_resume(resumes, country=None):
    """(entry, why) -- which base a posting in this country renders from.

    Deterministic and explainable, and no model is involved: the highest
    priority base whose selector names the country wins, ties break on the
    marked default and then on key so the answer never depends on dict order,
    and anything unmatched falls to the default. The reason travels with the
    choice because a wrong resume on a posting is otherwise the least
    diagnosable failure in the system, the document being plausible and nothing
    erroring.
    """
    resumes = [r for r in (resumes or []) if isinstance(r, dict)]
    if not resumes:
        return None, "no base resume is on record"
    default = next((r for r in resumes if r.get("default")), resumes[0])
    want = str(country or "").upper()
    if not want:
        return default, "the posting names no country, so the default applies"
    matches = [r for r in resumes if want in resume_countries(r)]
    if not matches:
        return default, f"no base claims {want}, so the default applies"
    matches.sort(key=lambda r: (-resume_priority(r), not r.get("default"),
                                str(r.get("key") or "")))
    chosen = matches[0]
    why = f"{want} is claimed by {chosen.get('key')}"
    if len(matches) > 1:
        why += (f", which outranks {len(matches) - 1} other base"
                f"{'s' if len(matches) > 2 else ''} claiming it")
    return chosen, why


def resume_overlaps(resumes):
    """Countries claimed by more than one base, with who claims them.

    Surfaced, never resolved silently. Two bases matching one posting is a
    priority question, and the person who wrote both rules is the one who should
    answer it.
    """
    claims = {}
    for entry in (resumes or []):
        for country in resume_countries(entry):
            claims.setdefault(country, []).append(entry.get("key"))
    return {c: sorted(k for k in keys if k)
            for c, keys in sorted(claims.items()) if len(keys) > 1}


def cmd_resume_add(args):
    """Add a base resume, copying identity and document from an existing one.

    Deliberately a copy rather than an upload. `resume_build.extract` matches
    the exact markup Jobby's own template emits and the corpus test that proves
    byte-for-byte fidelity runs over resumes Jobby rendered itself, so an
    arbitrary PDF cannot round-trip through it. Cloning one belongs to the
    onboarding phase and the UI must not imply it exists.
    """
    payload = load_input(args.input)
    if not isinstance(payload, dict):
        raise StoreError("a base resume must be a JSON object")
    key = _bank_text(payload.get("key"), "key", 60).strip()
    if not key or not all(c.isalnum() or c in "-_" for c in key):
        raise StoreError("a base resume key must be alphanumeric with - or _")
    root = root_dir(args.root)
    profile, resumes = _profile_resumes(root)
    if any(r.get("key") == key for r in resumes):
        raise StoreError(f"a base resume with key {key!r} already exists")
    source = next((r for r in resumes if r.get("key") == payload.get("copyFrom")), None) \
        or next((r for r in resumes if r.get("default")), resumes[0])
    # An uploaded document, already written to disk by the caller, becomes this
    # base's own PDF. Without one the new base points at the source's document,
    # which is the copy case and stays the default.
    #
    # **The document is not the wording.** The base PDF is what the model reads
    # as source text and what the layout is measured against; the words a
    # tailored resume is built from still come from the copied content, because
    # resume_build.extract matches the exact markup Jobby's own template emits
    # and cannot read an arbitrary PDF. Uploading changes what the resume is
    # based on, not what it says.
    document = payload.get("documentPath")
    if document:
        document = Path(str(document)).expanduser()
        if not document.is_file():
            raise StoreError(f"no uploaded document at {document}")
        if document.suffix.lower() != ".pdf":
            raise StoreError("a base resume document must be a PDF")
    countries = payload.get("countries")
    if not isinstance(countries, list) or not countries:
        raise StoreError("a new base resume needs at least one country")
    # Sorted and de-duplicated on write, so the stored shape and what
    # resume_selector() reports are the same list rather than two orders of it.
    codes = sorted({str(c).upper() for c in countries if str(c).strip()})
    entry = {
        "key": key,
        "label": _bank_text(payload.get("label"), "label", 120).strip() or key.upper(),
        "path": str(document) if document else source.get("path"),
        "identity": json.loads(json.dumps(source.get("identity") or {})),
        "appliesTo": codes,
        "selector": {"countries": codes,
                     "regionPacks": sorted({str(p).upper() for p in (payload.get("regionPacks") or [])})},
        "priority": int(payload.get("priority") or 0),
        "default": False,
        "copiedFrom": source.get("key"),
    }
    profile["resumes"] = resumes + [entry]
    profile["updatedAt"] = now()
    write_private(paths(root)["profile"], profile)
    return {"added": key, "copiedFrom": source.get("key"),
            "document": "uploaded" if document else "copied",
            "countries": entry["selector"]["countries"]}


def cmd_resume_update(args):
    """Change a base resume's label, the countries it claims, or its priority."""
    payload = load_input(args.input)
    if not isinstance(payload, dict):
        raise StoreError("a base resume update must be a JSON object")
    key = _bank_text(payload.get("key"), "key", 60).strip()
    root = root_dir(args.root)
    profile, resumes = _profile_resumes(root)
    entry = next((r for r in resumes if r.get("key") == key), None)
    if entry is None:
        raise StoreError(f"no base resume with key {key!r}")
    if "label" in payload:
        entry["label"] = _bank_text(payload.get("label"), "label", 120).strip() or key.upper()
    if "countries" in payload:
        countries = payload.get("countries")
        if not isinstance(countries, list) or not countries:
            raise StoreError("a base resume must claim at least one country")
        codes = sorted({str(c).upper() for c in countries if str(c).strip()})
        entry["appliesTo"] = codes
        entry.setdefault("selector", {})["countries"] = codes
    if "regionPacks" in payload:
        entry.setdefault("selector", {})["regionPacks"] = \
            [str(p).upper() for p in (payload.get("regionPacks") or [])]
    if "priority" in payload:
        entry["priority"] = int(payload.get("priority") or 0)
    profile["updatedAt"] = now()
    write_private(paths(root)["profile"], profile)
    return {"updated": key, "selector": resume_selector(entry),
            "priority": resume_priority(entry)}


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
    # The history file is appended rather than written through write_private,
    # so it needs its own call. Same ordering and same contract: the line is
    # already on disk.
    _mirror_history_event(event)
    return event


def _mirror_history_event(event):
    try:
        import class_b
    except Exception:
        return
    conn = None
    try:
        import dual_write
        if not dual_write.enabled():
            return
        conn = dual_write.connect()
        class_b.mirror(conn, dual_write.account_id(conn), "history", event)
    except Exception as exc:
        class_b_module = sys.modules.get("class_b")
        if class_b_module is not None:
            class_b_module._report("applications.jsonl", "history", exc)
        else:                                            # pragma: no cover
            sys.stderr.write(f"store: history mirror failed: {exc!r}\n")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                            # pragma: no cover
                pass


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
    "answer-dismiss": cmd_answer_dismiss,
    "answer-restore": cmd_answer_restore,
    "resume-content-get": cmd_resume_content_get,
    "resume-content-put": cmd_resume_content_put,
    "resume-default": cmd_resume_default,
    "resume-add": cmd_resume_add,
    "resume-update": cmd_resume_update,
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
    for name in ("answer-dismiss", "answer-restore"):
        sp = sub.add_parser(name); sp.add_argument("--key", required=True)
        sp.add_argument("--version", default=0); sp.add_argument("--edit-id")
    sp = sub.add_parser("resume-content-put"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("resume-default"); sp.add_argument("--key", required=True)
    sp = sub.add_parser("resume-add"); sp.add_argument("--input", required=True)
    sp = sub.add_parser("resume-update"); sp.add_argument("--input", required=True)
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
