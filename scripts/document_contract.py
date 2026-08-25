#!/usr/bin/env python3
"""Encrypted job payload contract shared by the Mac and document worker.

The Mac owns the application store. The OCI worker receives only the material
needed to create one resume and cover letter: the posting, the selected base
resume, a reduced document identity, and confirmed writing evidence. Street
address data and form answers never leave the Mac.

Transport encryption is handled by deploy/document-jobs.mjs. This module defines and
validates the plaintext only at the two trusted endpoints.
"""

import base64
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
MAX_TEXT_BYTES = 512 * 1024
MAX_BASE_RESUME_BYTES = 12 * 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
POSTAL_IN_LOCATION = re.compile(
    r"\b(?:\d{4,6}(?:-\d{3,4})?|[A-Z]\d[A-Z][ -]?\d[A-Z]\d)\b",
    re.IGNORECASE,
)


class DocumentContractError(Exception):
    pass


class MissingJobDescription(DocumentContractError):
    """No posting body could be resolved for this application.

    Its own class because it is the one bundle failure the user can act on, and
    because `_submit` collapses every other cause into the fixed code
    `invalid_request`. Six applications in a row failed with that code and it
    named nothing; the reason underneath never reached the board.
    """


@contextmanager
def _store_root(root=None):
    previous = os.environ.get("JOB_APPLICATION_DIR")
    if root:
        os.environ["JOB_APPLICATION_DIR"] = str(Path(root).expanduser())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("JOB_APPLICATION_DIR", None)
        else:
            os.environ["JOB_APPLICATION_DIR"] = previous


def _read_text(path, label, limit=MAX_TEXT_BYTES):
    if not path:
        return ""
    source = Path(path).expanduser()
    if not source.exists():
        return ""
    data = source.read_bytes()
    if len(data) > limit:
        raise DocumentContractError(f"{label} is larger than {limit} bytes")
    return data.decode("utf-8", "replace")


def _slug(value, limit):
    words = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    joined = "_".join(words) or "Unknown"
    return joined[:limit].strip("_") or "Unknown"


def output_names(profile, company, role):
    person = _slug(" ".join(str(profile.get(k) or "")
                            for k in ("firstName", "lastName")), 45)
    company_part = _slug(company, 45)
    role_part = _slug(role, 55)
    prefix = f"{person}_{company_part}_{role_part}"
    return {
        "resumeHtml": f".{prefix}.resume.html",
        "resumePdf": f"{prefix}_Resume.pdf",
        "coverHtml": f".{prefix}.cover.html",
        "coverPdf": f"{prefix}_CoverLetter.pdf",
    }


def _reduced_profile(profile, selected):
    """Profile fields needed in documents, with form-only address data removed."""
    source_identity = (selected or {}).get("identity") or {}
    if not isinstance(source_identity, dict):
        source_identity = {}
    location = POSTAL_IN_LOCATION.sub("", str(source_identity.get("location") or ""))
    location = re.sub(r"\s+,", ",", re.sub(r"\s{2,}", " ", location)).strip(" ,")
    identity = {
        "phone": source_identity.get("phone"),
        "location": location,
    }
    full = source_identity.get("fullAddress") or {}
    full = dict(full) if isinstance(full, dict) else {}
    if full:
        identity["fullAddress"] = {
            key: full.get(key) for key in ("city", "state", "country") if full.get(key)
        }
    resume = {
        "key": (selected or {}).get("key"),
        "default": True,
        "appliesTo": list((selected or {}).get("appliesTo") or []),
        "path": "base-resume.pdf",
        "identity": identity,
    }
    return {
        "firstName": profile.get("firstName"),
        "lastName": profile.get("lastName"),
        "email": profile.get("email"),
        "links": {
            key: (profile.get("links") or {}).get(key)
            for key in ("linkedin", "website", "github")
            if isinstance(profile.get("links"), dict)
            and (profile.get("links") or {}).get(key)
        },
        "resumes": [resume],
    }


def build_bundle(application_id, provider, root=None):
    """Build one minimal document payload from the local application store."""
    if not SAFE_NAME.fullmatch(str(application_id or "")):
        raise DocumentContractError("applicationId contains unsupported characters")
    if provider not in ("openai", "anthropic", "gemini"):
        raise DocumentContractError("unknown provider")

    with _store_root(root):
        import brief
        import store

        summary = brief.build(application_id)
        profile_path = Path(store.root_dir()) / "profile.json"
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DocumentContractError("the local profile cannot be read") from exc

        base_path = Path(summary.get("baseResume") or "").expanduser()
        if not base_path.is_file():
            raise DocumentContractError("the selected base resume does not exist")
        base_bytes = base_path.read_bytes()
        if len(base_bytes) > MAX_BASE_RESUME_BYTES:
            raise DocumentContractError(
                f"the selected base resume is larger than {MAX_BASE_RESUME_BYTES} bytes")
        if not base_bytes.startswith(b"%PDF"):
            raise DocumentContractError("the selected base resume is not a PDF")

        selected = next(
            (entry for entry in profile.get("resumes") or []
             if Path(entry.get("path") or "").expanduser() == base_path),
            None,
        )
        if selected is None:
            raise DocumentContractError("the selected base resume is not in the profile")

        samples = []
        for item in profile.get("writingSamples") or []:
            path = item.get("path") if isinstance(item, dict) else item
            text = _read_text(path, "a writing sample")
            if text:
                samples.append(text)

        extraction = summary.get("extraction")
        job_text = extraction if isinstance(extraction, str) else \
            json.dumps(extraction, indent=2) if extraction else ""
        if not job_text.strip() and summary.get("jdPath"):
            job_text = _read_text(summary["jdPath"], "the job description")
        if not job_text.strip():
            raise MissingJobDescription(
                summary.get("jobDescriptionError")
                or "the application has no job description")
        if len(job_text.encode()) > MAX_TEXT_BYTES:
            raise DocumentContractError("the job description is too large")

        return {
            "schemaVersion": SCHEMA_VERSION,
            "applicationId": application_id,
            "provider": provider,
            "company": summary.get("company"),
            "role": summary.get("role"),
            "region": summary.get("region"),
            "jobDescription": job_text,
            "profile": _reduced_profile(profile, selected),
            "storyFacts": _read_text(summary.get("storyFacts"), "the story facts"),
            "writingSamples": samples,
            "baseResume": {
                "name": "base-resume.pdf",
                "sha256": hashlib.sha256(base_bytes).hexdigest(),
                "contentBase64": base64.b64encode(base_bytes).decode("ascii"),
            },
            "outputs": output_names(profile, summary.get("company"), summary.get("role")),
        }


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.get("schemaVersion") != SCHEMA_VERSION:
        raise DocumentContractError("unsupported document bundle")
    if not SAFE_NAME.fullmatch(str(bundle.get("applicationId") or "")):
        raise DocumentContractError("invalid applicationId")
    if bundle.get("provider") not in ("openai", "anthropic", "gemini"):
        raise DocumentContractError("invalid provider")
    if not str(bundle.get("jobDescription") or "").strip():
        raise DocumentContractError("job description is empty")
    outputs = bundle.get("outputs") or {}
    required = ("resumeHtml", "resumePdf", "coverHtml", "coverPdf")
    if set(outputs) != set(required):
        raise DocumentContractError("document output names are incomplete")
    if any(not SAFE_NAME.fullmatch(str(outputs[key])) for key in required):
        raise DocumentContractError("a document output name is unsafe")
    encoded = str((bundle.get("baseResume") or {}).get("contentBase64") or "")
    try:
        resume = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise DocumentContractError("the base resume encoding is invalid") from exc
    expected = (bundle.get("baseResume") or {}).get("sha256")
    if not resume.startswith(b"%PDF") or hashlib.sha256(resume).hexdigest() != expected:
        raise DocumentContractError("the base resume failed its integrity check")
    return resume


def materialize(bundle, directory):
    """Write decrypted input into a private, per-job worker directory."""
    resume = validate_bundle(bundle)
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    store_root = root / "store"
    output_root = root / "output"
    store_root.mkdir(mode=0o700, exist_ok=True)
    output_root.mkdir(mode=0o700, exist_ok=True)

    (root / "base-resume.pdf").write_bytes(resume)
    (root / "base-resume.pdf").chmod(0o600)
    (root / "job-description.txt").write_text(
        bundle["jobDescription"], encoding="utf-8")
    (root / "job-description.txt").chmod(0o600)
    (store_root / "profile.json").write_text(
        json.dumps(bundle.get("profile") or {}, indent=2), encoding="utf-8")
    (store_root / "profile.json").chmod(0o600)
    (store_root / "story-facts.txt").write_text(
        str(bundle.get("storyFacts") or ""), encoding="utf-8")
    (store_root / "story-facts.txt").chmod(0o600)
    (root / "writing-samples.txt").write_text(
        "\n\n--- SAMPLE ---\n\n".join(bundle.get("writingSamples") or []),
        encoding="utf-8")
    (root / "writing-samples.txt").chmod(0o600)

    runtime = {
        key: value for key, value in bundle.items()
        if key not in ("baseResume", "profile", "storyFacts", "writingSamples",
                       "jobDescription")
    }
    runtime.update({
        "taskRoot": str(root),
        "storeRoot": str(store_root),
        "outputRoot": str(output_root),
        "baseResumePath": str(root / "base-resume.pdf"),
        "jobDescriptionPath": str(root / "job-description.txt"),
        "storyFactsPath": str(store_root / "story-facts.txt"),
        "writingSamplesPath": str(root / "writing-samples.txt"),
    })
    task_path = root / "task.json"
    task_path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    task_path.chmod(0o600)
    return runtime


def pack_result(runtime):
    """Package verified worker outputs for encrypted return to the Mac."""
    output_root = Path(runtime["outputRoot"]).resolve()
    files = {}
    for key in ("resumeHtml", "resumePdf", "coverHtml", "coverPdf"):
        name = runtime["outputs"][key]
        path = (output_root / name).resolve()
        if path.parent != output_root or not path.is_file():
            raise DocumentContractError(f"verified output is missing: {key}")
        data = path.read_bytes()
        files[key] = {
            "name": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "contentBase64": base64.b64encode(data).decode("ascii"),
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "applicationId": runtime["applicationId"],
        "provider": runtime["provider"],
        "files": files,
        "checks": runtime.get("checks") or {},
    }


def install_result(result, folder, application_id):
    """Atomically install a decrypted result in the local application folder."""
    if not isinstance(result, dict) or result.get("schemaVersion") != SCHEMA_VERSION:
        raise DocumentContractError("unsupported document result")
    if result.get("applicationId") != application_id:
        raise DocumentContractError("the document result belongs to another application")
    files = result.get("files") or {}
    if set(files) != {"resumeHtml", "resumePdf", "coverHtml", "coverPdf"}:
        raise DocumentContractError("the document result is incomplete")

    prepared = {}
    seen_names = set()
    for key, item in files.items():
        if not isinstance(item, dict):
            raise DocumentContractError(f"the {key} record is invalid")
        name = str(item.get("name") or "")
        if not SAFE_NAME.fullmatch(name):
            raise DocumentContractError("the result contains an unsafe filename")
        expected_suffix = ".pdf" if key.endswith("Pdf") else ".html"
        if not name.lower().endswith(expected_suffix):
            raise DocumentContractError(f"the {key} has the wrong file type")
        if name in seen_names:
            raise DocumentContractError("the result contains duplicate filenames")
        seen_names.add(name)
        try:
            data = base64.b64decode(item.get("contentBase64") or "", validate=True)
        except (ValueError, TypeError) as exc:
            raise DocumentContractError(f"the {key} encoding is invalid") from exc
        if not data or len(data) > MAX_BASE_RESUME_BYTES:
            raise DocumentContractError(f"the {key} has an invalid size")
        if hashlib.sha256(data).hexdigest() != item.get("sha256"):
            raise DocumentContractError(f"the {key} failed its integrity check")
        if key.endswith("Pdf") and not data.startswith(b"%PDF"):
            raise DocumentContractError(f"the {key} is not a PDF")
        if key.endswith("Html"):
            start = data[:4096].lstrip().lower()
            if not (start.startswith(b"<!doctype html") or b"<html" in start):
                raise DocumentContractError(f"the {key} is not HTML")
        prepared[key] = (name, data)

    # Validate the whole result before creating any target. A corrupt fourth
    # file must not leave three documents from a rejected job in the folder.
    destination = Path(folder).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    written = {}
    for key, (name, data) in prepared.items():
        target = destination / name
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as temp:
            temp.write(data)
            temp_path = Path(temp.name)
        temp_path.chmod(0o600)
        os.replace(temp_path, target)
        target.chmod(0o600)
        written[key] = str(target)
    return written
