#!/usr/bin/env python3
"""What the employer's form actually said, and what went into it.

SKILL.md has always told the agent to build a manifest before the final review
-- every filled field read back off the page, diffed against the fill plan --
and then told it, in the same paragraph, not to persist it. So the audit trail
for a hundred submitted applications existed for the length of one process and
was then thrown away. The user can see the resume and the cover letter an
employer received and cannot see the twenty questions it asked underneath them.

This is the manifest, written down. One JSON record per application, filed with
that application's documents, so a record rides the same publish path the PDFs
already use rather than swelling the embedded board state.

Three rules the caller does not get to argue with, because the caller is a
language model reading somebody else's form:

  - **A secret is never written.** OTPs, verification codes, passwords and
    national identifiers are withheld at the door: the question is recorded so
    the row is not silently missing, the value never reaches the disk. This is
    matched on the question text, which is the only thing available before the
    value would be stored.
  - **Sensitivity is derived here, not declared by the caller.** An answer whose
    stored entry is marked `sensitive`, or whose question is about pay,
    demographics, EEO or date of birth, is flagged regardless of what the caller
    said. A caller that forgets the flag cannot unmask a row.
  - **A claimed source is checked.** `stored` needs a key that exists in the
    answer store. An invented key is a refusal, not a footnote.

    python3 scripts/answer_record.py write --id <app> --input manifest.json
    python3 scripts/answer_record.py read  --id <app>
    python3 scripts/answer_record.py list

Standard library only.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"


def applications_dir(root=None):
    """Where tailored documents live, following a redirected store.

    This has to be `r2_artifacts.applications_dir` character for character.
    That module is the one collector deciding which local files are Class 1
    artifacts and what their R2 key is, and it already matches
    `answers-<id>.json` by name. If this file writes to REPO/applications while
    a redirected store sends the collector to <root>/applications, the record is
    written, nothing reads it, and the board shows "not captured" for an
    application that has one. `AnswerRecordArtifactTests` compares the two
    rather than restating the rule.

    Not imported from there: r2_artifacts pulls in document_contract, file_docs
    and r2, and this module ships in the renderer bundle, which must not.
    """
    redirect = root or os.environ.get("JOB_APPLICATION_DIR")
    if redirect:
        return Path(redirect).expanduser() / "applications"
    return REPO / "applications"


# None means "resolve from the store root or the environment on every call",
# which is what r2_artifacts does. Tests set it to a temporary directory. It is
# not resolved once at import, because finish.py exports JOB_APPLICATION_DIR
# after this module is already loaded.
APPS = None


def _apps(root=None):
    return Path(APPS) if APPS else applications_dir(root)


SCHEMA_VERSION = 1

# Where a value came from. The four the board draws a badge for, and the only
# four a caller may name.
SOURCES = ("stored", "profile", "generated", "provided")
SOURCE_LABELS = {
    "stored": "answer store",
    "profile": "profile",
    "generated": "generated",
    "provided": "you",
}

# Whether the value was read back off the page after it was typed. `unverified`
# is honest and allowed; claiming `match` without looking is the caller's lie to
# tell, and the read-back column exists so the user can see which it was.
READ_BACK = ("match", "differs", "unverified")

MAX_FIELDS = 200
MAX_QUESTION_CHARS = 1_000
MAX_ANSWER_CHARS = 4_000

# Bounds on the record itself, so one pathological form cannot produce a file
# the publish bundle then has to carry to every board load.
MAX_RECORD_BYTES = 96 * 1024


class RecordError(ValueError):
    """The manifest is malformed, or asks for something that will not be stored."""


# --- what never gets written ------------------------------------------------
#
# Matched against the question as the employer worded it. A withheld field keeps
# its question and loses its value, because a row that vanishes reads as a form
# that never asked, and that is the one thing this file exists to prevent.
#
# The named three are OTPs, verification codes and passwords. National
# identifiers are here too: an SSN sitting in a JSON record that is about to
# migrate to object storage is strictly worse than an OTP, and the rule that
# covers one obviously covers the other.
WITHHELD_PATTERNS = (
    ("one-time or verification code", re.compile(
        r"\b(one[\s-]?time\s+(code|password|passcode|pin)"
        r"|otp"
        r"|verification\s+code"
        r"|confirmation\s+code"
        r"|security\s+code"
        r"|access\s+code"
        r"|auth(entication)?\s+code"
        r"|sms\s+code"
        r"|(^|\W)2fa(\W|$)"
        r"|two[\s-]?factor"
        r"|multi[\s-]?factor"
        r"|authenticator\s+(code|app)"
        r"|\bmfa\b)", re.I)),
    ("password or passcode", re.compile(
        r"\b(password|passphrase|passcode"
        r"|\bpin\s*(number|code)?\b"
        r"|create\s+a\s+(password|login)"
        r"|choose\s+a\s+password)\b", re.I)),
    ("national or government identifier", re.compile(
        r"\b(social\s+security(\s+(number|no\.?|#))?"
        r"|\bssn\b"
        r"|\bsin\b\s*(number)?"
        r"|national\s+(id(entity|entification)?|insurance)\s*(number|no\.?|#)"
        r"|passport\s*(number|no\.?|#)"
        r"|aadhaar"
        r"|\bpan\s+(card|number)"
        r"|tax\s*(payer)?\s*(id|identification)\s*(number|no\.?|#)"
        r"|\btin\b\s*(number)?"
        r"|driver'?s?\s+licen[cs]e\s*(number|no\.?|#))", re.I)),
    ("bank or card detail", re.compile(
        r"\b(bank\s+account\s*(number|no\.?|#)"
        r"|routing\s+number"
        r"|sort\s+code"
        r"|\biban\b"
        r"|card\s+number"
        r"|\bcvv\b|\bcvc\b)", re.I)),
)

# --- what gets written but masked -------------------------------------------
#
# Stored, because the user asking "what did it say about pay?" is the whole
# point of the feature, and masked, because a board left open on a desk should
# not answer that question for a passer-by.
SENSITIVE_PATTERNS = (
    ("compensation", re.compile(
        r"\b(salary|compensation|\bpay\b|\bctc\b|remuneration|wage"
        r"|base\s+pay|pay\s+(rate|range|expectation)|hourly\s+rate"
        r"|desired\s+(pay|salary)|expected\s+(pay|salary|ctc)"
        r"|current\s+(pay|salary|ctc)|bonus|equity\s+expectation)\b", re.I)),
    ("date of birth", re.compile(
        r"\b(date\s+of\s+birth|\bdob\b|birth\s*date|birthday"
        r"|year\s+of\s+birth|month\s+of\s+birth)\b", re.I)),
    ("EEO or demographics", re.compile(
        r"\b(race|ethnicity|ethnic\s+(origin|group)|hispanic|latino"
        r"|\bgender\b|gender\s+identity|\bsex\b|transgender"
        r"|sexual\s+orientation|\blgbtq?\b"
        r"|veteran|military\s+(service|status)|protected\s+veteran"
        r"|disab(led|ility)|\beeo\b|equal\s+employment"
        r"|marital\s+status|religio(n|us)|\bcaste\b"
        r"|political\s+(contribution|affiliation|donation)"
        r"|pronouns)\b", re.I)),
)

# Tags the answer store already puts on entries whose values are the user's
# private business. Kept in step with store.py's own `sensitive` state rather
# than replacing it: both are consulted and either one masks the row.
SENSITIVE_TAGS = frozenset({
    "compensation", "expected-compensation", "expected-compensation-india",
    "compensation-breakup-india", "salary-range-acknowledgement",
    "diversity", "lgbtq-self-identification", "political-contributions",
    "date-of-birth",
})


def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def root_dir(override=None):
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def withheld_reason(question):
    """Why this question's value will never be written, or None."""
    text = question or ""
    for reason, pattern in WITHHELD_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def sensitive_reason(question, entry=None):
    """Why this row is masked by default, or None.

    Two independent sources agree to unmask: the question has to look ordinary
    *and* the stored entry, when there is one, has to be an ordinary entry. A
    caller cannot reach either.
    """
    if isinstance(entry, dict):
        if entry.get("state") == "sensitive":
            return "the answer store marks this answer sensitive"
        tags = entry.get("tags")
        if isinstance(tags, list) and SENSITIVE_TAGS.intersection(
                str(tag) for tag in tags):
            return "the answer store tags this answer sensitive"
    text = question or ""
    for reason, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def _text(value, name, maximum, *, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise RecordError(f"{name} must be a string")
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not cleaned:
        raise RecordError(f"{name} must not be empty")
    if len(cleaned) > maximum:
        raise RecordError(f"{name} must be at most {maximum} characters")
    # A control character in a form label is either an artefact of scraping the
    # page or somebody's idea of a joke. Neither belongs in a stored record.
    return "".join(ch for ch in cleaned if ch == "\n" or ch >= " ")


def normalise_fields(fields, answers):
    """Validate one manifest into the rows that will be written.

    Returns (rows, counts). Raises RecordError on anything malformed; the write
    is all-or-nothing, because a half-written record is a record that lies about
    what the form asked.
    """
    if not isinstance(fields, list) or not fields:
        raise RecordError("fields must be a non-empty list")
    if len(fields) > MAX_FIELDS:
        raise RecordError(f"fields must hold at most {MAX_FIELDS} entries")

    rows, seen = [], set()
    for index, raw in enumerate(fields):
        if not isinstance(raw, dict):
            raise RecordError(f"fields[{index}] must be an object")
        unknown = set(raw) - {"question", "answer", "source", "sourceKey",
                              "readBack", "readBackValue", "note"}
        if unknown:
            raise RecordError(
                f"fields[{index}] has unsupported keys: {', '.join(sorted(unknown))}")

        question = _text(raw.get("question"), f"fields[{index}].question",
                         MAX_QUESTION_CHARS)
        # The same label twice is a form read twice, not a form that asked
        # twice, and two rows saying different things is worse than one refusal.
        marker = question.lower()
        if marker in seen:
            raise RecordError(f"fields[{index}] repeats the question {question!r}")
        seen.add(marker)

        source = raw.get("source")
        if source not in SOURCES:
            raise RecordError(
                f"fields[{index}].source must be one of {', '.join(SOURCES)}")

        read_back = raw.get("readBack", "unverified")
        if read_back not in READ_BACK:
            raise RecordError(
                f"fields[{index}].readBack must be one of {', '.join(READ_BACK)}")

        source_key = _text(raw.get("sourceKey"), f"fields[{index}].sourceKey",
                           200, required=False)
        entry = None
        if source == "stored":
            if not source_key:
                raise RecordError(
                    f"fields[{index}] claims the answer store but names no sourceKey")
            entry = answers.get(source_key)
            if not isinstance(entry, dict):
                # An invented key would make the record cite an authority that
                # does not exist. The whole value of a source badge is that it
                # can be checked, so it is checked.
                raise RecordError(
                    f"fields[{index}].sourceKey {source_key!r} is not in the answer store")
        elif source_key:
            raise RecordError(
                f"fields[{index}].sourceKey is only meaningful when source is 'stored'")

        row = {"question": question, "source": source, "readBack": read_back}
        if source_key:
            row["sourceKey"] = source_key

        blocked = withheld_reason(question)
        if blocked:
            # The value is dropped here, before anything is serialised. Nothing
            # downstream is trusted to drop it later.
            row["withheld"] = blocked
            row["sensitive"] = True
            row["answer"] = ""
        else:
            row["answer"] = _text(raw.get("answer"), f"fields[{index}].answer",
                                  MAX_ANSWER_CHARS, required=False)
            masked = sensitive_reason(question, entry)
            row["sensitive"] = bool(masked)
            if masked:
                row["sensitiveReason"] = masked

        if read_back == "differs" and not row.get("withheld"):
            row["readBackValue"] = _text(
                raw.get("readBackValue"), f"fields[{index}].readBackValue",
                MAX_ANSWER_CHARS, required=False)

        note = _text(raw.get("note"), f"fields[{index}].note", 500, required=False)
        if note:
            row["note"] = note
        rows.append(row)

    counts = {
        "fields": len(rows),
        "sensitive": sum(1 for r in rows if r.get("sensitive")),
        "withheld": sum(1 for r in rows if r.get("withheld")),
        "readBackDiffers": sum(1 for r in rows if r.get("readBack") == "differs"),
        "sources": {s: sum(1 for r in rows if r["source"] == s) for s in SOURCES},
    }
    return rows, counts


def _identity(root, application_id):
    """company/role/url for this application, from the session or the history.

    Capture runs while the session still exists, but the CLI can be run after
    submission deleted it, so the history is the fallback rather than a guess.
    """
    session = _read_json(root / "sessions" / f"{application_id}.json", {})
    if session.get("company"):
        return (session.get("company"), session.get("role"),
                session.get("url"), session.get("ats"))
    roles = _read_json(root / "roles.json", {})
    role = roles.get(application_id)
    if isinstance(role, dict) and role.get("company"):
        return role.get("company"), role.get("role"), role.get("url"), role.get("ats")
    path = root / "applications.jsonl"
    latest = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("applicationId") == application_id and event.get("company"):
                latest = event
    if latest:
        return (latest.get("company"), latest.get("role"),
                latest.get("url"), latest.get("ats"))
    return None, None, None, None


def company_dir(company, root=None):
    """The folder file_docs.py files this company's documents into."""
    return _apps(root) / str(company)


def index(root=None):
    """{application id: path} for every record on disk, in one scan.

    Publishing asks for a hundred records in a row, and a per-record rglob over
    a hundred company folders is a scan each. This is the one scan.
    """
    apps = _apps(root)
    if not apps.exists():
        return {}
    found = {}
    for path in sorted(apps.rglob("answers-*.json")):
        stem = path.name[len("answers-"):-len(".json")]
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stem):
            found.setdefault(stem, path)
    return found


def record_path(application_id, company=None, root=None):
    """Where this application's record lives, whether or not it exists yet.

    Named by application id rather than by company, for the same reason
    jd-<id>.txt is: one company can have two open roles and they share a folder.
    """
    if company:
        return company_dir(company, root) / f"answers-{application_id}.json"
    return index(root).get(application_id)


def build(application_id, fields, *, root=None, answers=None):
    root = Path(root) if root else root_dir()
    answers = answers if answers is not None else _read_json(root / "answers.json", {})
    if not isinstance(answers, dict):
        answers = {}
    rows, counts = normalise_fields(fields, answers)
    company, role, url, ats = _identity(root, application_id)
    if not company:
        raise RecordError(
            f"no session, role index entry or history event names the company behind "
            f"{application_id}, so this record cannot be filed with its documents")
    record = {
        "schema": SCHEMA_VERSION,
        "applicationId": application_id,
        "company": company,
        "role": role or "",
        "capturedAt": now_stamp(),
        "counts": counts,
        "fields": rows,
    }
    if url:
        record["url"] = url
    if ats:
        record["ats"] = ats
    return record


def write(application_id, fields, *, root=None):
    record = build(application_id, fields, root=root)
    payload = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False)
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_RECORD_BYTES:
        raise RecordError(
            f"the record is {len(encoded)} bytes, over the {MAX_RECORD_BYTES} byte "
            f"ceiling. Trim long free-text answers rather than raising this.")
    path = company_dir(record["company"], root) / f"answers-{application_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    # The documents beside it carry a real address and phone number and the
    # folder is gitignored; this one carries the answers, so it is replaced
    # atomically and owner-only for the same reason.
    os.replace(tmp, path)
    return {"written": str(path), "bytes": len(encoded), **record["counts"]}


def load(application_id, path=None, root=None):
    path = path or record_path(application_id, root=root)
    if not path or not path.exists():
        return None
    record = _read_json(path, None)
    return record if isinstance(record, dict) else None


def captured_ids(root=None):
    """Every application that has a record, by id.

    The board asks this once per render to decide which Submitted rows offer
    Answers and which say "not captured", so it is a directory scan rather than
    a read of a hundred files.
    """
    return set(index(root))


def public_record(record):
    """The record as the board serves it: bounded, and with no stray keys.

    Trimming happens here rather than in the route so the local render and the
    deployed route cannot disagree about what a row contains.
    """
    if not isinstance(record, dict):
        return None
    rows = []
    for raw in (record.get("fields") or [])[:MAX_FIELDS]:
        if not isinstance(raw, dict):
            continue
        row = {
            "question": str(raw.get("question") or "")[:MAX_QUESTION_CHARS],
            "answer": str(raw.get("answer") or "")[:MAX_ANSWER_CHARS],
            "source": raw.get("source") if raw.get("source") in SOURCES else "generated",
            "readBack": raw.get("readBack") if raw.get("readBack") in READ_BACK
                        else "unverified",
            "sensitive": bool(raw.get("sensitive")),
        }
        if raw.get("withheld"):
            row["withheld"] = str(raw["withheld"])[:200]
            row["answer"] = ""
        if raw.get("readBackValue"):
            row["readBackValue"] = str(raw["readBackValue"])[:MAX_ANSWER_CHARS]
        if raw.get("note"):
            row["note"] = str(raw["note"])[:500]
        rows.append(row)
    return {
        "schema": SCHEMA_VERSION,
        "applicationId": str(record.get("applicationId") or "")[:64],
        "company": str(record.get("company") or "")[:200],
        "role": str(record.get("role") or "")[:300],
        "capturedAt": str(record.get("capturedAt") or "")[:32],
        "ats": str(record.get("ats") or "")[:80],
        "fields": rows,
    }


def _load_input(source):
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise RecordError(f"input is not valid JSON: {exc}") from exc
    # Either the bare list of fields or an object carrying one, so a caller can
    # hand over the manifest it already built without unwrapping it.
    if isinstance(payload, dict):
        payload = payload.get("fields")
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("write", help="file this application's answer record")
    sp.add_argument("--id", required=True)
    sp.add_argument("--input", required=True, help="manifest JSON path, or - for stdin")
    sp.add_argument("--root")

    sp = sub.add_parser("read", help="print a stored record")
    sp.add_argument("--id", required=True)

    sub.add_parser("list", help="print every application id that has a record")

    args = ap.parse_args()
    try:
        if args.command == "write":
            out = write(args.id, _load_input(args.input), root=args.root)
        elif args.command == "read":
            out = load(args.id)
            if out is None:
                raise SystemExit(f"answer_record: no record for {args.id}")
        else:
            out = sorted(captured_ids())
    except RecordError as exc:
        raise SystemExit(f"answer_record: {exc}")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
