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


@dataclass(frozen=True)
class ApplicationContext:
    application_id: str
    resume: Path
    cover: Path
    resume_html: Path | None = None
    root: Path | None = None

    @classmethod
    def create(
        cls,
        application_id: str,
        resume: str,
        cover: str,
        resume_html: str | None = None,
        root: str | None = None,
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
        return cls(
            _fixed_application_id(application_id),
            fixed_resume,
            fixed_cover,
            source,
            fixed_root,
        )


class ApplicationTools:
    def __init__(self, context: ApplicationContext):
        self.context = context

    def call(self, name: str, arguments: Any) -> dict[str, Any]:
        if name not in TOOL_NAMES:
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
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("the local application operation failed") from exc
        except Exception as exc:
            raise ToolExecutionError("the local application operation failed") from exc

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
        return {question: store.find_answer(answers, question) for question in cleaned}

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
        return finish.park(self.context.application_id, blockers, detail, unanswered)

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
                "Seven typed tools operate only on the application and artifacts "
                "fixed when this server started."
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
            {"tools": TOOLS}, modern=modern, cacheable=modern
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
        )
    except ToolInputError as exc:
        print(f"jobby application MCP: {exc}", file=sys.stderr)
        return 2
    return serve(ApplicationTools(context))


if __name__ == "__main__":
    sys.exit(main())
