#!/usr/bin/env python3
"""A narrow stdio MCP server for one local application.

The form-filling model needs a browser and seven pieces of Jobby state
machinery. It does not need a shell or general file access. This server binds
the application id and the two upload artifacts when it starts, then exposes
only typed operations for that application.

Each JSON-RPC message is one UTF-8 JSON line, as required by MCP stdio. Nothing
except protocol messages is written to stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import answer_record  # noqa: E402
import experience  # noqa: E402
import finish  # noqa: E402
import salary  # noqa: E402
import store  # noqa: E402


SERVER_NAME = "jobby-application"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-11-25"
MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSIONS = {
    "2024-11-05", "2025-03-26", "2025-06-18", PROTOCOL_VERSION,
}
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_HTML_BYTES = 20 * 1024 * 1024
MAX_QUESTIONS = 100
MAX_QUESTION_CHARS = 4_000
MAX_TOTAL_QUESTION_CHARS = 64_000
MAX_TEXT_CHARS = 1_000
MAX_STORY_FACT_BYTES = 200_000
APPLICATION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
ALLOWED_PHASES = ("Filling the form",)


class ToolInputError(ValueError):
    """The model supplied arguments outside a tool's closed schema."""


class ToolExecutionError(RuntimeError):
    """A fixed local operation failed without exposing private output."""


def _object(value: Any, label: str = "arguments") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolInputError(f"{label} must be an object")
    return value


def _closed(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolInputError("unsupported argument: " + unknown[0])


def _text(
    value: Any,
    label: str,
    *,
    maximum: int = MAX_TEXT_CHARS,
    allow_empty: bool = False,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ToolInputError(f"{label} must be a string")
    if "\x00" in value or (not allow_newlines and ("\r" in value or "\n" in value)):
        raise ToolInputError(f"{label} must be one line")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise ToolInputError(f"{label} must not be empty")
    if len(cleaned) > maximum:
        raise ToolInputError(f"{label} is too long")
    return cleaned


def _fixed_application_id(value: str) -> str:
    if not isinstance(value, str) or not APPLICATION_ID.fullmatch(value) or ".." in value:
        raise ToolInputError("application id contains unsupported characters")
    # Keep this server's check in lockstep with the store's path guard.
    try:
        return store.safe_id(value)
    except store.StoreError as exc:
        raise ToolInputError("application id contains unsupported characters") from exc


def _regular_file(raw: str, label: str, suffix: str, maximum: int) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ToolInputError(f"{label} path is required")
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise ToolInputError(f"{label} path contains unsupported characters")
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ToolInputError(f"{label} path must be absolute")
        if path.is_symlink() or not path.is_file():
            raise ToolInputError(f"{label} must be a regular file")
        size = path.stat().st_size
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ToolInputError(f"{label} cannot be read") from exc
    if resolved.suffix.lower() != suffix or size < 6 or size > maximum:
        raise ToolInputError(f"{label} has the wrong file type or size")
    return resolved


def _pdf(raw: str, label: str) -> Path:
    path = _regular_file(raw, label, ".pdf", MAX_PDF_BYTES)
    try:
        with path.open("rb") as stream:
            signature = stream.read(5)
    except OSError as exc:
        raise ToolInputError(f"{label} cannot be read") from exc
    if signature != b"%PDF-":
        raise ToolInputError(f"{label} is not a PDF")
    return path


def _html(raw: str, label: str = "resume HTML") -> Path:
    path = _regular_file(raw, label, ".html", MAX_HTML_BYTES)
    try:
        with path.open("rb") as stream:
            start = stream.read(4096).lstrip().lower()
    except OSError as exc:
        raise ToolInputError(f"{label} cannot be read") from exc
    if not (start.startswith(b"<!doctype html") or b"<html" in start):
        raise ToolInputError(f"{label} is not HTML")
    return path


def _derived_resume_html(resume: Path) -> Path | None:
    """Map a verified output PDF to its hidden render source, without searching.

    Worker results use ``<prefix>_Resume.pdf`` and
    ``.<prefix>.resume.html``. An exact name transform avoids choosing another
    role's resume when one company folder contains several applications.
    """
    marker = "_resume.pdf"
    lower = resume.name.lower()
    if not lower.endswith(marker):
        return None
    prefix = resume.name[: -len(marker)]
    candidate = resume.parent / f".{prefix}.resume.html"
    if not candidate.is_file() or candidate.is_symlink():
        return None
    try:
        return _html(str(candidate))
    except ToolInputError:
        return None


@contextmanager
def _store_root(root: Path | None):
    previous = os.environ.get("JOB_APPLICATION_DIR")
    if root is not None:
        os.environ["JOB_APPLICATION_DIR"] = str(root)
    try:
        yield
    finally:
        if root is not None:
            if previous is None:
                os.environ.pop("JOB_APPLICATION_DIR", None)
            else:
                os.environ["JOB_APPLICATION_DIR"] = previous


TOOLS = [
    {
        "name": "set_phase",
        "description": "Mark this application as filling its form, with a short progress detail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "enum": list(ALLOWED_PHASES)},
                "detail": {"type": "string", "maxLength": 500},
            },
            "required": ["phase"],
            "additionalProperties": False,
        },
    },
    {
        "name": "resolve_answers",
        "description": "Resolve a page of application questions against confirmed local answers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_QUESTIONS,
                    "items": {"type": "string", "minLength": 1,
                              "maxLength": MAX_QUESTION_CHARS},
                }
            },
            "required": ["questions"],
            "additionalProperties": False,
        },
    },
    {
        "name": "story_facts",
        "description": "Return the user's confirmed story bank, for free-text questions only.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "experience_plan",
        "description": "Return every position from this application's fixed tailored resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "style": {"type": "string", "enum": ["text", "prose"]},
                "maxChars": {"type": "integer", "minimum": 0, "maximum": 20_000},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "salary_midpoint",
        "description": "Calculate the midpoint of a salary band stated in the posting.",
        "inputSchema": {
            "type": "object",
            "properties": {"band": {"type": "string", "minLength": 1,
                                      "maxLength": 500}},
            "required": ["band"],
            "additionalProperties": False,
        },
    },
    {
        "name": "park_application",
        "description": (
            "Park this application for the user with one or more named blockers. "
            "List in `unanswered` any blocker that is a form question with no "
            "stored answer, exactly as it appeared, so it is recorded for next "
            "time. Leave out blockers that are actions rather than questions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pending": {
                    "type": "array", "minItems": 1, "maxItems": 20,
                    "items": {"type": "string", "minLength": 1, "maxLength": 300},
                },
                # A second flat array rather than objects inside `pending`: the
                # advertised surface stays object-at-the-top with plain string
                # items, and a client on an older revision that omits it still
                # parks correctly.
                "unanswered": {
                    "type": "array", "minItems": 0, "maxItems": 20,
                    "items": {"type": "string", "minLength": 1, "maxLength": 300},
                },
                "detail": {"type": "string", "minLength": 1, "maxLength": 1_000},
            },
            "required": ["pending", "detail"],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_answers",
        "description": (
            "File this application's question-and-answer manifest: every field the "
            "form asked, the value entered, where it came from, and whether it was "
            "read back. Call it once, after the read-back pass and before pre_submit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": answer_record.MAX_FIELDS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string", "minLength": 1,
                                "maxLength": answer_record.MAX_QUESTION_CHARS,
                            },
                            "answer": {
                                "type": "string",
                                "maxLength": answer_record.MAX_ANSWER_CHARS,
                            },
                            "source": {"type": "string",
                                       "enum": list(answer_record.SOURCES)},
                            "sourceKey": {"type": "string", "maxLength": 200},
                            "readBack": {"type": "string",
                                         "enum": list(answer_record.READ_BACK)},
                            "readBackValue": {
                                "type": "string",
                                "maxLength": answer_record.MAX_ANSWER_CHARS,
                            },
                            "note": {"type": "string", "maxLength": 500},
                        },
                        "required": ["question", "source"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["fields"],
            "additionalProperties": False,
        },
    },
    {
        "name": "pre_submit",
        "description": "Run cancellation and duplicate guards, then write the pre-submit event.",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "record_completed",
        "description": "Record a visible success confirmation using the server's fixed artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirmation": {"type": "string", "minLength": 1,
                                 "maxLength": 1_000},
                "submissionNonce": {"type": "string", "minLength": 20,
                                    "maxLength": 200},
            },
            "required": ["confirmation", "submissionNonce"],
            "additionalProperties": False,
        },
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}

#: The two tools that can put an application in front of an employer.
#:
#: **A fill-only run is one this server refuses to advertise them to.** The
#: mode exists because an application that reached `submitting` and parked on
#: something only a person can do -- an emailed code, an account, a CAPTCHA --
#: cannot be re-filled by a normal run: `do_not_start` refuses it, and that
#: refusal is right, because a second run of a form that already reached
#: Submit is how an application goes in twice under a real name.
#:
#: So the way to re-fill it safely is to make submitting impossible rather than
#: forbidden. **Not a flag in the prompt.** A prompt is advice to one model; a
#: tool that is not in the set cannot be called by any of them, which is the
#: same argument `cover_letter.validate_content` makes about greetings and the
#: reason the browser firewall removes navigation rather than asking for it not
#: to be used. The mode is set from the trusted runner's argv and reaches this
#: process on its command line, where nothing the agent can say touches it.
SUBMISSION_TOOLS = frozenset({"pre_submit", "record_completed"})


def tools_for(fill_only: bool) -> list[dict[str, Any]]:
    """The advertised tool set for this run's mode."""
    if not fill_only:
        return TOOLS
    return [tool for tool in TOOLS if tool["name"] not in SUBMISSION_TOOLS]


def tool_names_for(fill_only: bool) -> frozenset[str]:
    """The callable tool set for this run's mode.

    Separate from `tools_for` on purpose, and both are enforced. A client that
    read `tools/list` once and kept the answer -- or one that was told a tool
    name by anything other than this server -- reaches `call` with a name that
    was never advertised, and an advertised-only guard would run it. Two
    surfaces, the same rule, in the shape `FenceSurfaceContractTests` already
    pins for the browser firewall.
    """
    return frozenset(TOOL_NAMES - SUBMISSION_TOOLS) if fill_only else frozenset(TOOL_NAMES)


@dataclass(frozen=True)
class ApplicationContext:
    application_id: str
    resume: Path
    cover: Path
    resume_html: Path | None = None
    root: Path | None = None
    #: Whether this run may reach Submit. See `SUBMISSION_TOOLS`.
    fill_only: bool = False

    @classmethod
    def create(
        cls,
        application_id: str,
        resume: str,
        cover: str,
        resume_html: str | None = None,
        root: str | None = None,
        fill_only: bool = False,
    ) -> "ApplicationContext":
        fixed_resume = _pdf(resume, "resume")
        fixed_cover = _pdf(cover, "cover letter")
        source = _html(resume_html) if resume_html else _derived_resume_html(fixed_resume)
        fixed_root = None
        if root is not None:
            if (not isinstance(root, str) or not root.strip() or "\x00" in root
                    or "\r" in root or "\n" in root):
                raise ToolInputError("store root must be an absolute regular directory")
            try:
                candidate = Path(root).expanduser()
                if (not candidate.is_absolute() or candidate.is_symlink()
                        or not candidate.is_dir()):
                    raise ToolInputError(
                        "store root must be an absolute regular directory"
                    )
                fixed_root = candidate.resolve(strict=True)
            except OSError as exc:
                raise ToolInputError(
                    "store root must be an absolute regular directory"
                ) from exc
        # `is True`, not truthiness. The value arrives from argv and from a
        # JSON context, and a mode that turns itself on for the string "false"
        # would disarm the submission gate; a mode that fails to turn on merely
        # refuses a re-fill, which is the safe direction of a wrong answer.
        return cls(
            _fixed_application_id(application_id),
            fixed_resume,
            fixed_cover,
            source,
            fixed_root,
            fill_only is True,
        )


class ApplicationTools:
    def __init__(self, context: ApplicationContext):
        self.context = context

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        if name not in tool_names_for(self.context.fill_only):
            # A withheld tool is named rather than called unknown, because the
            # two need different behaviour from the reader: an unknown name
            # invites a retry under a spelling variation, and this one has to
            # stop the run and park. Saying so costs nothing -- the tool is
            # gone either way -- and it is the difference between an agent that
            # parks with the handoff and one that spends its turns guessing.
            if name in SUBMISSION_TOOLS:
                raise ToolInputError(
                    f"{name} is not available on this run. This is a fill-only "
                    "run: it exists to put the form back in front of the user, "
                    "who submits it themselves. Fill and read back every field, "
                    "call record_answers, then park with what is left for them "
                    "to do."
                )
            raise ToolInputError("unknown tool")
        args = _object(arguments)
        method = getattr(self, "_" + name)
        try:
            with _store_root(self.context.root):
                return method(args)
        except (ToolInputError, ToolExecutionError):
            raise
        except store.StoreError as exc:
            raise ToolExecutionError("the local application store rejected the operation") from exc
        except SystemExit as exc:
            message = str(exc).strip()
            raise ToolExecutionError(message[:300] or "the local operation failed") from exc
        except (OSError, subprocess.SubprocessError, ValueError,
                json.JSONDecodeError) as exc:
            raise ToolExecutionError(self._named(name, exc)) from exc
        except Exception as exc:                                 # noqa: BLE001
            raise ToolExecutionError(self._named(name, exc)) from exc

    @staticmethod
    def _named(tool, exc):
        """A failure the reader can act on, without leaking what the store holds.

        **These two branches returned "the local application operation failed"
        and discarded the exception.** `raise ... from exc` keeps the chain for
        a traceback nobody prints, so the cause ceased to exist at the moment
        it was produced -- which is the rule this project has already paid for
        three times with `DEVNULL`, `ping_health` and `pool_staleness`.

        Observed on a real run, 2026-09-07: `set_phase` failed once against the
        live Chainguard application and the whole record of it was that
        sentence. The phase reached "Filling the form" a moment later, so
        whatever it was did not stop the run, and it is not knowable now.

        **The class name and nothing else from the exception body.** The
        message travels into a provider's context, and an `OSError` carries a
        path while a `ValueError` from the store can carry a value. The type
        says which of the four kinds of thing went wrong, which is what
        separates "the store rejected it" from "the disk did"; the detail goes
        to stderr, which `browser_mcp_proxy` drains and the run log keeps.
        """
        kind = type(exc).__name__
        # `str`, not `repr`: `repr(OSError(2, "no such file", path))` drops the
        # path entirely and only `str` renders it as "[Errno 2] ...: '/path'".
        # The filename is the single most useful thing about an OSError, and a
        # line claiming the detail is recoverable while dropping it is worse
        # than no line. Caught by the test asserting recoverability rather than
        # by reading this.
        sys.stderr.write(
            f"application_mcp: {tool} failed with {kind}: {exc}\n")
        sys.stderr.flush()
        return (f"the local application operation failed with {kind}; "
                f"the detail is in this run's log")

    def _set_phase(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, {"phase", "detail"})
        phase = _text(args.get("phase"), "phase", maximum=100)
        if phase not in ALLOWED_PHASES:
            raise ToolInputError("unsupported phase")
        detail = _text(args.get("detail", ""), "detail", maximum=500, allow_empty=True)
        command = [
            sys.executable,
            str(SCRIPTS / "phase.py"),
            "--id",
            self.context.application_id,
            "--phase",
            phase,
            "--detail",
            detail,
        ]
        if self.context.root is not None:
            command.extend(["--root", str(self.context.root)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if result.returncode != 0:
            raise ToolExecutionError("the application phase could not be updated")
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ToolExecutionError("the application phase returned no result") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise ToolExecutionError("the application phase could not be updated")
        return payload

    def _resolve_answers(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, {"questions"})
        questions = args.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
            raise ToolInputError("questions must be a non-empty bounded list")
        cleaned = [
            _text(item, f"questions[{index}]", maximum=MAX_QUESTION_CHARS,
                  allow_newlines=True)
            for index, item in enumerate(questions)
        ]
        if len(set(cleaned)) != len(cleaned):
            raise ToolInputError("questions must not contain duplicates")
        if sum(len(item) for item in cleaned) > MAX_TOTAL_QUESTION_CHARS:
            raise ToolInputError("questions are too large")
        answers_path = Path(store.paths(store.root_dir(self.context.root))["answers"])
        answers = store.read_json(answers_path, {})
        # The work-authorisation table is read from the store root too, so it
        # has to be the same root the answers came from.
        with _store_root(self.context.root):
            return {question: store.find_answer(answers, question)
                    for question in cleaned}

    def _story_facts(self, args: dict[str, Any]) -> dict[str, Any]:
        """The confirmed story bank, which the prompt names and nothing served.

        The form prompt has always listed story facts as one of the sources a
        free-text answer may draw on, and the brief has always carried the
        story bank. It carried the PATH, and a form agent has no file access,
        so the source named in its own instructions was unreachable. Measured
        on 2026-08-25: the Workato application parked on "why do you think you
        will be successful in this role" with a ten-story bank on disk.

        Read-only, one fixed file, no arguments. The document worker reads the
        same file for the cover letter.
        """
        _closed(args, set())
        root = Path(store.root_dir(self.context.root))
        text = ""
        path = root / "story-facts.txt"
        if path.is_file() and path.stat().st_size <= MAX_STORY_FACT_BYTES:
            text = path.read_text(encoding="utf-8", errors="replace").strip()

        # The answers already given on other applications, in the user's own
        # words. The file and the store overlap but neither contains the other:
        # measured on 2026-08-25 the store held two competency stories the file
        # did not, and six direct answers ("what motivates you", "why are you
        # looking") that exist nowhere else.
        answers = store.read_json(Path(store.paths(root)["answers"]), {})
        previous = []
        for key in sorted(answers):
            entry = answers[key]
            if not isinstance(entry, dict):
                continue
            if "free-text" not in (entry.get("tags") or []):
                continue
            value = str(entry.get("value") or "").strip()
            if not value:
                continue
            previous.append({"question": str(entry.get("question") or key),
                             "answer": value})
        if not text and not previous:
            raise ToolExecutionError("no story facts are recorded for this user")
        return {
            "storyFacts": text,
            "previousAnswers": previous,
            "note": ("Confirmed facts and the answers already given on other applications, "
                     "in the user's own words. Source material, not paste-ready text: write "
                     "the answer out in the first person to the length the field asks for, "
                     "and never paste a prefix addressed to you such as 'HIS OWN WORDS' or "
                     "'Competencies:'. Invent no number, date, employer or outcome that is "
                     "not here or in the resume."),
        }

    def _experience_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, {"style", "maxChars"})
        source = self.context.resume_html
        if source is None:
            raise ToolExecutionError("the fixed resume HTML is unavailable")
        style = args.get("style", "text")
        if style not in {"text", "prose"}:
            raise ToolInputError("style must be text or prose")
        maximum = args.get("maxChars", 0)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 <= maximum <= 20_000:
            raise ToolInputError("maxChars must be an integer from 0 to 20000")
        html = source.read_text(encoding="utf-8")
        positions = experience.plan(html, self.context.root, maximum, style)
        if not positions:
            raise ToolExecutionError("the fixed resume has no experience entries")
        return {
            "positions": positions,
            "count": len(positions),
            "note": "Fill every position, newest first.",
        }

    def _salary_midpoint(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, {"band"})
        band = _text(args.get("band"), "band", maximum=500)
        value, detail = salary.midpoint(band)
        if value is None:
            return {"answer": None, "band": band, "why": detail}
        return {"answer": detail["formatted"], "value": value,
                "band": band, "why": detail["note"]}

    def _park_application(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, {"pending", "detail", "unanswered"})
        pending = args.get("pending")
        if not isinstance(pending, list) or not 1 <= len(pending) <= 20:
            raise ToolInputError("pending must be a non-empty bounded list")
        blockers = [
            _text(item, f"pending[{index}]", maximum=300)
            for index, item in enumerate(pending)
        ]
        if len(set(blockers)) != len(blockers):
            raise ToolInputError("pending must not contain duplicates")
        raw = args.get("unanswered")
        if raw is not None and (not isinstance(raw, list) or len(raw) > 20):
            raise ToolInputError("unanswered must be a bounded list")
        unanswered = [
            _text(item, f"unanswered[{index}]", maximum=300)
            for index, item in enumerate(raw or [])
        ]
        # Tagging a blocker that was never parked would put a question in the
        # bank attributed to an application that never asked it.
        stray = [q for q in unanswered if q not in set(blockers)]
        if stray:
            raise ToolInputError("every unanswered question must also be in pending")
        detail = _text(args.get("detail"), "detail", maximum=1_000)
        refused = self._blockers_the_store_answers(blockers)
        if refused:
            raise ToolInputError(
                "refusing to park questions the store already answers: "
                + "; ".join(refused[:5])
                + ". Fill these via resolve_answers (source stored or profile), then "
                "park only what remains. If a stored answer does not fit the field, "
                "park a row naming the actual blocker instead of the question."
            )
        return finish.park(self.context.application_id, blockers, detail, unanswered)

    # A park row that is a question the store can answer is the one park that
    # must never happen: it sends the user a question they already answered,
    # which is the exact job this product exists to remove. The prompt has
    # said so since the Robinhood run parked on seven stored answers
    # (2026-08-28), and the TELUS run parked "Where did you hear about us?"
    # hours later with the answer confirmed in the bank since 2026-08-19.
    # Saying it is not a guard; this is the guard.
    #
    # Only question-shaped rows are gated. A blocker is usually an action
    # ("solve the CAPTCHA", "attach the resume manually") and measured on
    # 2026-08-27, 0 of 29 real blockers were question-shaped; resolving prose
    # against the bank would refuse legitimate parks on fuzzy accidents.
    QUESTION_SHAPED = re.compile(
        r"\?|^\s*(are|do|does|did|have|has|what|when|where|which|who|why|how"
        r"|is|was|will|would|can|could|may|should)\b", re.I)

    def _blockers_the_store_answers(self, blockers: list[str]) -> list[str]:
        root = store.root_dir(self.context.root)
        answers = store.read_json(Path(store.paths(root)["answers"]), {})
        profile = store.read_json(Path(store.paths(root)["profile"]), {})
        refused = []
        for question in blockers:
            if not self.QUESTION_SHAPED.search(question):
                continue
            found = store.resolve_answer(answers, profile, question)
            if not isinstance(found, dict) or found.get("match") == "none":
                continue
            value = found.get("value") or found.get("answer")
            if not value:
                continue
            if found.get("state") == "sensitive" or found.get("sensitive") is True:
                # The value stays out of an error string; the agent reads it
                # back through resolve_answers like any other sensitive hit.
                refused.append(f"{question[:80]!r} (answered; read it via resolve_answers)")
            else:
                refused.append(f"{question[:80]!r} -> {str(value)[:60]!r}")
        return refused

    def _record_answers(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, {"fields"})
        fields = args.get("fields")
        if not isinstance(fields, list):
            raise ToolInputError("fields must be an array")
        try:
            return answer_record.write(
                self.context.application_id, fields, root=self.context.root
            )
        except answer_record.RecordError as exc:
            # The refusal names what was wrong, because the caller is the only
            # thing that can fix the manifest and a blank "operation failed"
            # leaves it guessing. The message is built from fixed strings and
            # the caller's own field labels, never from stored answers.
            raise ToolInputError(str(exc)[:400]) from exc

    def _pre_submit(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, set())
        return finish.pre_submit(self.context.application_id)

    def _record_completed(self, args: dict[str, Any]) -> dict[str, Any]:
        _closed(args, {"confirmation", "submissionNonce"})
        confirmation = _text(
            args.get("confirmation"), "confirmation", maximum=1_000,
            allow_newlines=True,
        )
        nonce = _text(args.get("submissionNonce"), "submissionNonce", maximum=200)
        return finish.completed(
            self.context.application_id,
            confirmation,
            str(self.context.resume),
            str(self.context.cover),
            nonce,
        )


def _response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None):
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    return message


def _tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
    }
    if is_error:
        result["isError"] = True
    return result


def _modern_request(params: dict[str, Any]) -> bool:
    meta = params.get("_meta")
    return (
        isinstance(meta, dict)
        and meta.get("io.modelcontextprotocol/protocolVersion")
        == MODERN_PROTOCOL_VERSION
    )


def _complete_result(
    payload: dict[str, Any], *, modern: bool, cacheable: bool = False
) -> dict[str, Any]:
    if not modern:
        return payload
    result = dict(payload)
    result["resultType"] = "complete"
    meta = dict(result.get("_meta") or {})
    meta["io.modelcontextprotocol/serverInfo"] = {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
    }
    result["_meta"] = meta
    if cacheable:
        # The tool surface is fixed for the life of this short-lived server,
        # but private scope keeps it from being reused across applications.
        result["ttlMs"] = 60_000
        result["cacheScope"] = "private"
    return result


def handle_message(message: Any, tools: ApplicationTools) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _response(message.get("id") if isinstance(message, dict) else None,
                         error={"code": -32600, "message": "Invalid Request"})
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})
    if not isinstance(method, str) or not isinstance(params, dict):
        return _response(request_id, error={"code": -32600, "message": "Invalid Request"})

    # Notifications have no response. The initialized notification is the only
    # one needed by this server, but unknown notifications are also ignored per
    # JSON-RPC rather than turned into stdout noise.
    if "id" not in message:
        return None
    if method == "server/discover":
        return _response(request_id, _complete_result({
            "supportedVersions": [MODERN_PROTOCOL_VERSION],
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": (
                f"{len(tools_for(tools.context.fill_only))} typed tools operate "
                "only on the application and artifacts fixed when this server "
                "started."
                + (" This run cannot submit: the submission tools are absent."
                   if tools.context.fill_only else "")
            ),
        }, modern=True, cacheable=True))
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return _response(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    modern = _modern_request(params)
    if method == "ping":
        return _response(request_id, _complete_result({}, modern=modern))
    if method == "tools/list":
        return _response(request_id, _complete_result(
            {"tools": tools_for(tools.context.fill_only)},
            modern=modern, cacheable=modern
        ))
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        try:
            if not isinstance(name, str):
                raise ToolInputError("tool name must be a string")
            payload = tools.call(name, arguments)
            return _response(request_id, _complete_result(
                _tool_result(payload), modern=modern
            ))
        except ToolInputError as exc:
            return _response(request_id, _complete_result(
                _tool_result({"error": str(exc)}, True), modern=modern
            ))
        except ToolExecutionError as exc:
            return _response(request_id, _complete_result(
                _tool_result({"error": str(exc)}, True), modern=modern
            ))
    return _response(request_id, error={"code": -32601, "message": "Method not found"})


def serve(tools: ApplicationTools, source=None, sink=None) -> int:
    source = source or sys.stdin
    sink = sink or sys.stdout
    for raw in source:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            response = _response(None, error={"code": -32700, "message": "Parse error"})
        else:
            response = handle_message(message, tools)
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sink.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCP tools for one fixed application")
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--resume", required=True)
    parser.add_argument("--cover", required=True)
    parser.add_argument("--resume-html")
    parser.add_argument(
        "--fill-only", action="store_true",
        help="withhold the submission tools; this run fills and hands back")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        context = ApplicationContext.create(
            args.application_id,
            args.resume,
            args.cover,
            args.resume_html,
            args.root,
            args.fill_only,
        )
    except ToolInputError as exc:
        print(f"jobby application MCP: {exc}", file=sys.stderr)
        return 2
    return serve(ApplicationTools(context))


if __name__ == "__main__":
    sys.exit(main())
