#!/usr/bin/env python3
"""Render and verify a repository-owned cover letter.

The document agent supplies a small JSON object containing prose backed by
evidence it has already selected. This module does not invent, expand, or
rewrite that prose. It validates the input, adds contact identity from the
candidate profile, renders HTML through the fixed template, and uses the same
headless Chrome PDF path as tailored resumes.

Content JSON:

    {
      "employer": "Northwind Analytics",
      "role": "Senior Product Manager",
      "date": "2026-08-23",
      "salutation": "Hiring Team",
      "opening": "Why this role is a specific fit...",
      "evidence": ["A supported example...", "Another supported example..."],
      "closing": "What the candidate would bring..."
    }

The profile is the object returned by ``scripts/store.py profile-get``. A
region selects the matching resume identity, with the default resume as the
fallback. The street address is never copied into the document header.

Standard library only, apart from the repository's existing PDF verifier.
"""

import argparse
import html
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = REPO / "templates" / "cover-letter.html"

sys.path.insert(0, str(SCRIPT_DIR))
import resume_pdf  # noqa: E402


CONTENT_KEYS = {
    "employer", "role", "date", "salutation", "opening", "evidence", "closing"
}
REQUIRED_CONTENT_KEYS = {"employer", "role", "opening", "evidence", "closing"}
MAX_BODY_CHARACTERS = 2800
MIN_BOTTOM_MARGIN_POINTS = 28.0

# These are editing artifacts, not ordinary words such as "role" or "company".
# Refusing them at the input boundary keeps an unfinished letter from becoming
# a polished-looking PDF.
PLACEHOLDER = re.compile(
    r"\{\{.*?\}\}|"
    r"\[(?:insert[^\]]*|company(?: name)?|role(?: title)?|job title|candidate name|date)\]|"
    r"\b(?:lorem ipsum|tbd|todo|placeholder|your name|company name|job title here|"
    r"insert (?:company|role|name|date)(?: here)?|x{3,})\b",
    re.IGNORECASE | re.DOTALL,
)
TOKEN = re.compile(r"\{\{[A-Za-z][A-Za-z0-9]*\}\}")
WHITESPACE = re.compile(r"\s+")


class CoverLetterError(Exception):
    """An input or document failure safe to show to the caller."""


@dataclass(frozen=True)
class Candidate:
    name: str
    email: str
    location: str
    phone: str
    links: tuple
    street: str


def load_json(spec):
    """Read a JSON object from a path, or stdin when the path is ``-``."""
    try:
        if spec == "-":
            value = json.load(sys.stdin)
        else:
            with open(Path(spec).expanduser(), encoding="utf-8") as stream:
                value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverLetterError(f"cannot read JSON input: {exc}") from exc
    if not isinstance(value, dict):
        raise CoverLetterError("JSON input must be an object")
    return value


def private_write(path, text):
    """Write a generated document atomically with owner-only permissions."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def clean_line(value, field, minimum=1, maximum=180):
    if not isinstance(value, str):
        raise CoverLetterError(f"{field} must be text")
    cleaned = WHITESPACE.sub(" ", value).strip()
    if len(cleaned) < minimum:
        raise CoverLetterError(f"{field} is too short")
    if len(cleaned) > maximum:
        raise CoverLetterError(f"{field} exceeds {maximum} characters")
    if PLACEHOLDER.search(cleaned):
        raise CoverLetterError(f"{field} contains placeholder text")
    return cleaned


def clean_paragraph(value, field):
    return clean_line(value, field, minimum=20, maximum=900)


def validate_content(raw):
    """Return the only content shape the fixed template accepts."""
    if not isinstance(raw, dict):
        raise CoverLetterError("content must be a JSON object")
    extra = sorted(set(raw) - CONTENT_KEYS)
    if extra:
        raise CoverLetterError(f"unsupported content keys: {', '.join(extra)}")
    missing = sorted(REQUIRED_CONTENT_KEYS - set(raw))
    if missing:
        raise CoverLetterError(f"missing content keys: {', '.join(missing)}")

    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
        raise CoverLetterError("evidence must contain one to three paragraphs")

    cleaned = {
        "employer": clean_line(raw["employer"], "employer", 2, 160),
        "role": clean_line(raw["role"], "role", 2, 180),
        "salutation": clean_line(raw.get("salutation", "Hiring Team"),
                                  "salutation", 2, 100),
        "opening": clean_paragraph(raw["opening"], "opening"),
        "evidence": [clean_paragraph(item, f"evidence[{index}]")
                     for index, item in enumerate(evidence)],
        "closing": clean_paragraph(raw["closing"], "closing"),
    }
    raw_date = raw.get("date")
    if raw_date is None:
        chosen_date = date.today()
    elif not isinstance(raw_date, str):
        raise CoverLetterError("date must use YYYY-MM-DD")
    else:
        try:
            chosen_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise CoverLetterError("date must use YYYY-MM-DD") from exc
    cleaned["date"] = chosen_date.isoformat()

    body_size = sum(len(cleaned[key]) for key in ("opening", "closing"))
    body_size += sum(len(item) for item in cleaned["evidence"])
    if body_size > MAX_BODY_CHARACTERS:
        raise CoverLetterError(
            f"letter body exceeds {MAX_BODY_CHARACTERS} characters and may not fit one page"
        )
    return cleaned


def identity_for_region(profile, region=None):
    """Select contact identity without copying a street address into output."""
    if not isinstance(profile, dict):
        raise CoverLetterError("profile must be a JSON object")
    first = clean_line(profile.get("firstName", ""), "candidate firstName", 1, 80)
    last = clean_line(profile.get("lastName", ""), "candidate lastName", 1, 80)
    email = clean_line(profile.get("email", ""), "candidate email", 3, 180)
    if "@" not in email or any(char.isspace() for char in email):
        raise CoverLetterError("candidate email is not valid")

    resumes = profile.get("resumes") or []
    if not isinstance(resumes, list):
        raise CoverLetterError("profile resumes must be a list")
    wanted = str(region or "").upper()
    selected = None
    for entry in resumes:
        if not isinstance(entry, dict):
            continue
        applies_to = [str(item).upper() for item in (entry.get("appliesTo") or [])]
        if wanted and wanted in applies_to:
            selected = entry
            break
    if selected is None:
        selected = next(
            (entry for entry in resumes
             if isinstance(entry, dict) and entry.get("default")),
            resumes[0] if resumes and isinstance(resumes[0], dict) else {},
        )
    identity = selected.get("identity") or {}
    if not isinstance(identity, dict):
        raise CoverLetterError("selected resume identity must be an object")
    location = clean_line(identity.get("location", ""), "candidate location", 2, 160)
    phone = clean_line(identity.get("phone", ""), "candidate phone", 5, 40)

    full_address = identity.get("fullAddress") or {}
    street = ""
    if isinstance(full_address, dict):
        street = str(full_address.get("street") or "").strip()

    links = []
    profile_links = profile.get("links") or {}
    if not isinstance(profile_links, dict):
        raise CoverLetterError("profile links must be an object")
    for key, label in (("linkedin", "LinkedIn"), ("website", "Portfolio"),
                       ("github", "GitHub")):
        value = str(profile_links.get(key) or "").strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise CoverLetterError(f"candidate {key} link must use http or https")
        links.append((label, value))

    return Candidate(
        name=f"{first} {last}", email=email, location=location,
        phone=format_phone(phone), links=tuple(links), street=street,
    )


def format_phone(value):
    digits = "".join(char for char in str(value) if char.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return str(value).strip()


def display_date(iso_date):
    parsed = date.fromisoformat(iso_date)
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def anchor(href, label):
    return '<a href="%s">%s</a>' % (
        html.escape(href, quote=True), html.escape(label, quote=False)
    )


def render_html(template, content, candidate):
    """Fill the fixed template with escaped, validated text."""
    contact = "<span class=\"contact-item\">%s</span>" % html.escape(candidate.location)
    contact += "<span class=\"divider\">|</span>"
    contact += "<span class=\"contact-item\">%s</span>" % html.escape(candidate.phone)
    contact += "<span class=\"divider\">|</span>"
    contact += anchor("mailto:" + candidate.email, candidate.email)

    link_html = ""
    if candidate.links:
        link_html = '<div class="links">%s</div>' % (
            '<span class="divider">|</span>'.join(
                anchor(url, label) for label, url in candidate.links
            )
        )

    paragraphs = [content["opening"], *content["evidence"], content["closing"]]
    body = "\n".join("<p>%s</p>" % html.escape(item) for item in paragraphs)
    salutation = content["salutation"].strip().rstrip(",")
    replacements = {
        "candidateName": html.escape(candidate.name),
        "contactLine": contact,
        "profileLinks": link_html,
        "documentTitle": html.escape(
            f"{candidate.name} | {content['employer']} Cover Letter"
        ),
        "date": html.escape(display_date(content["date"])),
        "employer": html.escape(content["employer"]),
        "role": html.escape(content["role"]),
        "salutation": html.escape(salutation),
        "body": body,
    }
    output = template
    for key, value in replacements.items():
        output = output.replace("{{" + key + "}}", value)
    unresolved = sorted(set(TOKEN.findall(output)))
    if unresolved:
        raise CoverLetterError(
            "template contains unresolved tokens: " + ", ".join(unresolved)
        )
    return output


# A hyphenated word broken across two rendered lines comes back out of the PDF
# as "feature- parity", and the authored text says "feature-parity", so the
# substring check missed a paragraph that was on the page in full and rejected
# the whole job. Applied to both sides, so a letter that really was written
# with a space after the hyphen still matches itself.
WRAPPED_HYPHEN = re.compile(r"(?<=\w)-\s+(?=\w)")


def normalized(value):
    collapsed = WHITESPACE.sub(" ", html.unescape(str(value))).strip().casefold()
    return WRAPPED_HYPHEN.sub("-", collapsed)


def expected_text(content, candidate):
    return [
        candidate.name, candidate.email, candidate.location, candidate.phone,
        display_date(content["date"]), content["employer"], content["role"],
        f"Dear {content['salutation'].strip().rstrip(',')},",
        content["opening"], *content["evidence"], content["closing"],
    ]


def verify_pdf(pdf_path, content, candidate):
    """Check text, identity, page count, and measurable clipping signals."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        raise CoverLetterError(f"no PDF at {path}")
    try:
        pymupdf = resume_pdf.require_pymupdf()
        document = pymupdf.open(str(path))
    except resume_pdf.ToolError as exc:
        raise CoverLetterError(str(exc)) from exc
    except Exception as exc:
        raise CoverLetterError(f"cannot open PDF: {exc}") from exc

    problems = []
    page_count = len(document)
    if page_count != 1:
        problems.append(f"expected one page, found {page_count}")

    extracted = "\n".join(page.get_text("text") for page in document)
    normalized_text = normalized(extracted)
    letters = sum(char.isalpha() for char in extracted)
    readable = len(normalized_text) >= 200 and letters >= 140
    if not readable:
        problems.append("extracted text is too short to be a readable cover letter")

    required = {
        "candidateName": normalized(candidate.name) in normalized_text,
        "candidateEmail": normalized(candidate.email) in normalized_text,
        "employer": normalized(content["employer"]) in normalized_text,
        "role": normalized(content["role"]) in normalized_text,
    }
    for field, present in required.items():
        if not present:
            problems.append(f"required field is absent: {field}")

    missing_content = []
    for index, value in enumerate(expected_text(content, candidate)):
        if normalized(value) not in normalized_text:
            missing_content.append(index)
    if missing_content:
        problems.append(
            "authored content is missing from extracted text at positions "
            + ", ".join(str(index) for index in missing_content)
        )

    street_omitted = not candidate.street or normalized(candidate.street) not in normalized_text
    if not street_omitted:
        problems.append("street address appears in the document header")

    outside_page = 0
    bottom_margin = None
    for page in document:
        spans = [
            span
            for block in page.get_text("dict").get("blocks", [])
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if span.get("text", "").strip()
        ]
        for span in spans:
            x0, y0, x1, y1 = span["bbox"]
            if x0 < -0.5 or y0 < -0.5 or x1 > page.rect.width + 0.5 \
                    or y1 > page.rect.height + 0.5:
                outside_page += 1
        if spans:
            remaining = page.rect.height - max(span["bbox"][3] for span in spans)
            bottom_margin = remaining if bottom_margin is None else min(bottom_margin, remaining)
    document.close()

    if outside_page:
        problems.append(f"{outside_page} text spans extend outside the page")
    if bottom_margin is None:
        problems.append("PDF contains no measurable text spans")
    elif bottom_margin < MIN_BOTTOM_MARGIN_POINTS:
        problems.append(
            f"bottom text margin is {bottom_margin:.1f}pt, below the "
            f"{MIN_BOTTOM_MARGIN_POINTS:.1f}pt clipping guard"
        )

    overflow = page_count != 1 or bool(missing_content) or outside_page > 0 \
        or bottom_margin is None or bottom_margin < MIN_BOTTOM_MARGIN_POINTS
    return {
        "ok": not problems,
        "pageCount": page_count,
        "readable": readable,
        "textCharacters": len(normalized_text),
        "requiredFields": required,
        "streetAddressOmitted": street_omitted,
        "contentComplete": not missing_content,
        "overflowSignals": {
            "detected": overflow,
            "textSpansOutsidePage": outside_page,
            "bottomMarginPoints": None if bottom_margin is None else round(bottom_margin, 1),
        },
        "problems": problems,
    }


def render_pdf(html_path, pdf_path):
    args = type("RenderArgs", (), {"html": str(html_path), "out": str(pdf_path)})
    try:
        return resume_pdf.cmd_render(args)
    except resume_pdf.ToolError as exc:
        raise CoverLetterError(str(exc)) from exc


def read_inputs(args):
    if args.content == "-" and args.profile == "-":
        raise CoverLetterError("content and profile cannot both read from stdin")
    content = validate_content(load_json(args.content))
    candidate = identity_for_region(load_json(args.profile), args.region)
    return content, candidate


def command_render(args):
    content, candidate = read_inputs(args)
    template_path = Path(args.template).expanduser().resolve()
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CoverLetterError(f"cannot read template: {exc}") from exc

    rendered = render_html(template, content, candidate)
    html_path = private_write(args.out_html, rendered)
    pdf_path = Path(args.out_pdf).expanduser().resolve()
    render_result = render_pdf(html_path, pdf_path)
    verification = verify_pdf(pdf_path, content, candidate)
    return {
        "ok": verification["ok"],
        "html": str(html_path),
        "pdf": str(pdf_path),
        "pdfBytes": render_result["bytes"],
        "verification": verification,
    }


def command_verify(args):
    content, candidate = read_inputs(args)
    return verify_pdf(args.pdf, content, candidate)


def parser():
    root = argparse.ArgumentParser(
        description="Render and verify a one-page cover letter from selected evidence."
    )
    sub = root.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render private HTML and PDF, then verify")
    render.add_argument("--content", required=True, help="content JSON path, or - for stdin")
    render.add_argument("--profile", required=True, help="candidate profile JSON path")
    render.add_argument("--region", help="posting country code used to select identity")
    render.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    render.add_argument("--out-html", required=True)
    render.add_argument("--out-pdf", required=True)

    verify = sub.add_parser("verify", help="verify a rendered PDF against its inputs")
    verify.add_argument("--pdf", required=True)
    verify.add_argument("--content", required=True, help="content JSON path, or - for stdin")
    verify.add_argument("--profile", required=True, help="candidate profile JSON path")
    verify.add_argument("--region", help="posting country code used to select identity")
    return root


COMMANDS = {"render": command_render, "verify": command_verify}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = COMMANDS[args.command](args)
    except (CoverLetterError, TimeoutError) as exc:
        result = {"ok": False, "error": str(exc)}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    except Exception as exc:
        if exc.__class__.__name__ == "TimeoutExpired":
            result = {"ok": False, "error": "Chrome timed out rendering the PDF"}
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2
        raise
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
