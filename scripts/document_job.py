#!/usr/bin/env python3
"""Submit and collect encrypted document work from the local document worker.

The application queue stays on the Mac. This client only manages the resume
and cover-letter subtask. It writes an opaque job ID into the application
session before sending anything, which makes an interrupted enqueue safe to
repeat without creating a second job.

Plaintext request data is written to an owner-only temporary file for the
fixed Node bridge. It never appears in command arguments or Cloudflare KV
metadata. Provider credentials stay in Codex or owner-only provider key files.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import brief  # noqa: E402
import document_contract  # noqa: E402
import store  # noqa: E402
import toolpath  # noqa: E402

BRIDGE = REPO / "deploy" / "document-jobs.mjs"
SCHEMA_VERSION = 1
PROVIDERS = {"openai", "anthropic", "gemini"}
REMOTE_STATES = {"queued", "claimed", "completed", "retrieved", "failed"}
LOCAL_STATES = REMOTE_STATES | {"submitting", "ready"}
RESULT_KEYS = {"resumeHtml", "resumePdf", "coverHtml", "coverPdf"}
REMOTE_POLL_SECONDS = 15
MISSING_STATUS_GRACE_SECONDS = 2 * 60
MAX_ERROR_DETAIL = 140
_DETAIL_DISALLOWED = re.compile(r"[^A-Za-z0-9 ,.:%()'-]+")

# Only fixed codes may enter a session or CLI response. Vendor messages,
# network bodies, and subprocess stderr are never retained.
SAFE_ERROR_CODES = {
    "bundle_invalid",
    "cloudflare_not_configured",
    "generation_failed",
    "invalid_request",
    "job_description_missing",
    "job_expired",
    "job_id_invalid",
    "job_missing",
    "payload_auth_failed",
    "payload_invalid",
    "payload_too_large",
    "result_invalid",
    "session_missing",
    "state_invalid",
    "status_invalid",
    "transport_error",
    "transport_delete_failed",
    "transport_read_failed",
    "transport_unavailable",
    "transport_write_failed",
    "worker_failed",
    "worker_key_invalid",
    "worker_key_missing",
}


class DocumentJobError(Exception):
    """An expected client or transport failure with a fixed public code."""

    def __init__(self, code):
        self.code = _safe_error(code)
        super().__init__(self.code)


def _safe_error(code):
    return code if code in SAFE_ERROR_CODES else "transport_error"


def _safe_detail(value):
    """One short, fixed-vocabulary sentence naming why a request was refused.

    A fixed error code alone cost a day. Six applications parked on
    `invalid_request`, which `_submit` returns for every exception out of
    `build_bundle`, and the actual reason -- the application has no job
    description -- never left the process. The code stays fixed and public;
    this carries the sentence beside it.

    Only messages this repository writes reach here, and every one of them is a
    literal in `document_contract`. The scrub is belt and braces: a message that
    ever starts interpolating a path, a posting, or a provider body is cut to
    the characters a plain sentence needs, folded to one line, and bounded.
    """
    if not isinstance(value, str):
        return ""
    cleaned = _DETAIL_DISALLOWED.sub(" ", value)
    return " ".join(cleaned.split())[:MAX_ERROR_DETAIL]


def _utc_now():
    return datetime.now(timezone.utc)


def _now():
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp_datetime(value):
    valid = _valid_timestamp(value)
    if not valid:
        return None
    parsed = datetime.fromisoformat(valid.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _valid_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _valid_job_id(value):
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise DocumentJobError("state_invalid")
    return str(parsed)


def _valid_provider(value):
    if value not in PROVIDERS:
        raise DocumentJobError("state_invalid")
    return value


def _clean_files(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != RESULT_KEYS:
        raise DocumentJobError("state_invalid")
    clean = {}
    for key, name in value.items():
        name = str(name or "")
        if Path(name).name != name or not document_contract.SAFE_NAME.fullmatch(name):
            raise DocumentJobError("state_invalid")
        clean[key] = name
    return clean


def _clean_state(value):
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise DocumentJobError("state_invalid")
    state = value.get("state")
    if state not in LOCAL_STATES:
        raise DocumentJobError("state_invalid")
    clean = {
        "schemaVersion": SCHEMA_VERSION,
        "jobId": _valid_job_id(value.get("jobId")),
        "provider": _valid_provider(value.get("provider")),
        "state": state,
        "createdAt": _valid_timestamp(value.get("createdAt")) or _now(),
        "updatedAt": _valid_timestamp(value.get("updatedAt")) or _now(),
    }
    for key in ("expiresAt", "installedAt", "lastErrorAt", "lastRemotePollAt"):
        timestamp = _valid_timestamp(value.get(key))
        if timestamp:
            clean[key] = timestamp
    for key in ("errorCode", "lastErrorCode"):
        if value.get(key):
            clean[key] = _safe_error(value[key])
    detail = _safe_detail(value.get("errorDetail"))
    if detail:
        clean["errorDetail"] = detail
    files = _clean_files(value.get("files"))
    if files is not None:
        clean["files"] = files
    return clean


def _validate_application_id(application_id):
    try:
        return store.safe_id(application_id)
    except store.StoreError as exc:
        raise DocumentJobError("state_invalid") from exc


def _session_path(application_id, root=None):
    aid = _validate_application_id(application_id)
    sessions = Path(store.paths(store.root_dir(root))["sessions"])
    return sessions / (aid + ".json")


@contextmanager
def _application_lock(application_id, root=None):
    """Serialize document state changes across watcher processes."""
    session_path = _session_path(application_id, root)
    if not session_path.is_file():
        raise DocumentJobError("session_missing")
    lock_path = session_path.parent / ("." + session_path.stem + ".document-job.lock")
    descriptor = None
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise DocumentJobError("state_invalid") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _load_session(application_id, root=None):
    path = _session_path(application_id, root)
    if not path.is_file():
        raise DocumentJobError("session_missing")
    try:
        session = store.read_json(path, {})
    except (OSError, store.StoreError) as exc:
        raise DocumentJobError("state_invalid") from exc
    if not isinstance(session, dict):
        raise DocumentJobError("state_invalid")
    return session


def get_state(application_id, root=None):
    """Return the saved job state, or None when no remote job exists."""
    session = _load_session(application_id, root)
    value = session.get("documentJob")
    return _clean_state(value) if value is not None else None


def _save_state(application_id, value, root=None):
    clean = _clean_state(value)
    session = _load_session(application_id, root)
    session["documentJob"] = clean
    session["updatedAt"] = store.now()
    try:
        store.write_private(_session_path(application_id, root), session)
    except OSError as exc:
        raise DocumentJobError("state_invalid") from exc
    return clean


def _new_state(provider):
    timestamp = _now()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "jobId": str(uuid.uuid4()),
        "provider": _valid_provider(provider),
        "state": "submitting",
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def _changed(current, **updates):
    out = dict(current)
    out.update(updates)
    out["updatedAt"] = _now()
    return out


def _clear_errors(state):
    out = dict(state)
    for key in ("errorCode", "lastErrorCode", "lastErrorAt", "errorDetail"):
        out.pop(key, None)
    return out


def _record_last_error(application_id, state, code, root=None):
    updated = _changed(
        state,
        lastErrorCode=_safe_error(code),
        lastErrorAt=_now(),
    )
    return _save_state(application_id, updated, root)


def _record_failure(application_id, state, code, root=None, detail=""):
    updated = _changed(state, state="failed", errorCode=_safe_error(code))
    for key in ("lastErrorCode", "lastErrorAt"):
        updated.pop(key, None)
    clean = _safe_detail(detail)
    if clean:
        updated["errorDetail"] = clean
    else:
        updated.pop("errorDetail", None)
    return _save_state(application_id, updated, root)


@contextmanager
def _store_context(root=None):
    previous = os.environ.get("JOB_APPLICATION_DIR")
    if root is not None:
        os.environ["JOB_APPLICATION_DIR"] = str(Path(root).expanduser())
    try:
        yield
    finally:
        if root is not None:
            if previous is None:
                os.environ.pop("JOB_APPLICATION_DIR", None)
            else:
                os.environ["JOB_APPLICATION_DIR"] = previous


def _brief(application_id, root=None):
    with _store_context(root):
        return brief.build(application_id)


def _bridge_error(stderr):
    try:
        parsed = json.loads((stderr or "").strip())
    except (json.JSONDecodeError, TypeError):
        return "transport_error"
    return _safe_error(parsed.get("error")) if isinstance(parsed, dict) else "transport_error"


def transport_call(action, job_id, bundle=None, env=None, timeout=120):
    """Call the fixed Node bridge without putting plaintext in argv."""
    if action not in {"enqueue", "status", "retrieve", "delete"}:
        raise DocumentJobError("transport_error")
    job_id = _valid_job_id(job_id)
    temp_path = None
    try:
        command = [toolpath.node(), str(BRIDGE), action]
        if action == "enqueue":
            if not isinstance(bundle, dict):
                raise DocumentJobError("bundle_invalid")
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="jobby-doc-",
                suffix=".json",
                delete=False,
            )
            temp_path = Path(handle.name)
            try:
                os.fchmod(handle.fileno(), 0o600)
                try:
                    json.dump(bundle, handle, separators=(",", ":"))
                except (TypeError, ValueError) as exc:
                    raise DocumentJobError("bundle_invalid") from exc
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            command.extend([str(temp_path), job_id])
        else:
            command.append(job_id)

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(REPO),
            env=toolpath.queue_env(env),
            timeout=timeout,
        )
    except DocumentJobError:
        raise
    except (OSError, subprocess.SubprocessError, SystemExit) as exc:
        raise DocumentJobError("transport_unavailable") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass

    if completed.returncode != 0:
        raise DocumentJobError(_bridge_error(completed.stderr))
    try:
        return json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise DocumentJobError("transport_error") from exc


def _remote_update(application_id, state, status, root=None):
    if not isinstance(status, dict):
        raise DocumentJobError("status_invalid")
    if _valid_job_id(status.get("jobId")) != state["jobId"]:
        raise DocumentJobError("status_invalid")
    remote_state = status.get("state")
    if remote_state not in REMOTE_STATES:
        raise DocumentJobError("status_invalid")
    if remote_state == "failed":
        return _record_failure(
            application_id,
            state,
            status.get("errorCode") or "worker_failed",
            root,
        )
    updated = _clear_errors(_changed(state, state=remote_state))
    expires_at = _valid_timestamp(status.get("expiresAt"))
    if expires_at:
        updated["expiresAt"] = expires_at
    return _save_state(application_id, updated, root)


def _submit(application_id, state, root=None, transport=None):
    transport = transport or transport_call
    try:
        bundle = document_contract.build_bundle(
            application_id,
            state["provider"],
            root=root,
        )
    except document_contract.MissingJobDescription as exc:
        # Its own code, because it is the one refusal the user can act on. A
        # posting that was withdrawn, or a board this cannot read, is not the
        # same kind of thing as "invalid_request", which reads like a bug in
        # Jobby and sends them nowhere.
        return _record_failure(
            application_id, state, "job_description_missing", root, str(exc))
    except document_contract.DocumentContractError as exc:
        # The contract refuses on facts about the request, not on its content,
        # so its own sentence is safe to keep and is the only thing that tells
        # the user which fact is missing.
        return _record_failure(
            application_id, state, "invalid_request", root, str(exc))
    except (Exception, SystemExit):
        return _record_failure(application_id, state, "invalid_request", root)

    try:
        response = transport("enqueue", state["jobId"], bundle=bundle)
        if not isinstance(response, dict) or response.get("jobId") != state["jobId"]:
            raise DocumentJobError("status_invalid")
        remote_state = response.get("state")
        if remote_state not in REMOTE_STATES - {"failed"}:
            raise DocumentJobError("status_invalid")
        return _save_state(
            application_id,
            _clear_errors(_changed(state, state=remote_state)),
            root,
        )
    except DocumentJobError as exc:
        return _record_last_error(application_id, state, exc.code, root)


def _ensure_unlocked(application_id, provider, root=None, transport=None):
    application_id = _validate_application_id(application_id)
    _valid_provider(provider)
    state = get_state(application_id, root)
    if state is not None:
        # The first selected provider is sticky. A settings change applies to
        # later jobs and cannot create a second job for this application.
        if state["state"] == "submitting":
            return _submit(application_id, state, root, transport)
        return state

    try:
        summary = _brief(application_id, root)
    except (Exception, SystemExit):
        summary = {}
    if summary.get("documentsReady"):
        return {
            "schemaVersion": SCHEMA_VERSION,
            "provider": provider,
            "state": "ready",
            "existing": True,
        }

    # Save before enqueue. If the process stops after Cloudflare accepts the
    # request, the next pass reuses this same ID and the transport deduplicates.
    state = _save_state(application_id, _new_state(provider), root)
    return _submit(application_id, state, root, transport)


def ensure(application_id, provider, root=None, transport=None):
    """Ensure one document job exists, without polling or claiming it."""
    with _application_lock(application_id, root):
        return _ensure_unlocked(application_id, provider, root, transport)


def _archive_failure(application_id, state, root=None):
    session = _load_session(application_id, root)
    history = session.get("documentJobFailures")
    history = history if isinstance(history, list) else []
    clean_history = []
    for entry in history[-9:]:
        if not isinstance(entry, dict):
            continue
        try:
            clean_history.append({
                "jobId": _valid_job_id(entry.get("jobId")),
                "provider": _valid_provider(entry.get("provider")),
                "errorCode": _safe_error(entry.get("errorCode")),
                "failedAt": _valid_timestamp(entry.get("failedAt")) or _now(),
            })
        except DocumentJobError:
            continue
    archived = {
        "jobId": state["jobId"],
        "provider": state["provider"],
        "errorCode": _safe_error(state.get("errorCode")),
        "failedAt": state.get("updatedAt") or _now(),
    }
    if not clean_history or clean_history[-1]["jobId"] != state["jobId"]:
        clean_history.append(archived)
    session["documentJobFailures"] = clean_history
    session["updatedAt"] = store.now()
    try:
        store.write_private(_session_path(application_id, root), session)
    except OSError as exc:
        raise DocumentJobError("state_invalid") from exc


def retry_failed(application_id, provider, root=None, transport=None):
    """Create a new job only after an explicit retry of a saved failure."""
    with _application_lock(application_id, root):
        state = get_state(application_id, root)
        if state is None:
            return _ensure_unlocked(application_id, provider, root, transport)
        if state["state"] != "failed":
            return state
        _valid_provider(provider)
        _archive_failure(application_id, state, root)
        replacement = _save_state(application_id, _new_state(provider), root)
        return _submit(application_id, replacement, root, transport)


def _install(application_id, state, payload, root=None):
    if not isinstance(payload, dict) or payload.get("jobId") != state["jobId"]:
        return _record_failure(application_id, state, "result_invalid", root)
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("provider") != state["provider"]:
        return _record_failure(application_id, state, "result_invalid", root)
    try:
        summary = _brief(application_id, root)
        written = document_contract.install_result(
            result,
            summary["folder"],
            application_id,
        )
        files = {key: Path(written[key]).name for key in RESULT_KEYS}
        _clean_files(files)
    except (Exception, SystemExit):
        return _record_failure(application_id, state, "result_invalid", root)

    updated = _clear_errors(_changed(
        state,
        state="ready",
        installedAt=_now(),
        files=files,
    ))
    return _save_state(application_id, updated, root)


def _poll_unlocked(application_id, root=None, transport=None):
    application_id = _validate_application_id(application_id)
    transport = transport or transport_call
    state = get_state(application_id, root)
    if state is None:
        return {"schemaVersion": SCHEMA_VERSION, "state": "not_requested"}
    if state["state"] in {"ready", "failed"}:
        return state
    if state["state"] == "submitting":
        return _submit(application_id, state, root, transport)

    now = _utc_now()
    expires_at = _timestamp_datetime(state.get("expiresAt"))
    if expires_at is not None and expires_at <= now:
        return _record_failure(application_id, state, "job_expired", root)
    last_poll = _timestamp_datetime(state.get("lastRemotePollAt"))
    if last_poll is not None and (now - last_poll).total_seconds() < REMOTE_POLL_SECONDS:
        return state

    state = _save_state(
        application_id,
        _changed(state, lastRemotePollAt=_now()),
        root,
    )

    try:
        status = transport("status", state["jobId"])
        if status is None:
            missing_at = _timestamp_datetime(state.get("lastErrorAt")) \
                if state.get("lastErrorCode") == "job_missing" else None
            if missing_at is not None and (
                    now - missing_at).total_seconds() >= MISSING_STATUS_GRACE_SECONDS:
                return _record_failure(application_id, state, "job_missing", root)
            if missing_at is not None:
                return state
            return _record_last_error(application_id, state, "job_missing", root)
        state = _remote_update(application_id, state, status, root)
        if state["state"] in {"completed", "retrieved"}:
            payload = transport("retrieve", state["jobId"])
            if payload is not None:
                state = _install(application_id, state, payload, root)
                if state.get("state") == "ready":
                    try:
                        transport("delete", state["jobId"])
                    except DocumentJobError:
                        # The encrypted records retain their TTL. A cleanup
                        # outage must not hide documents already verified and
                        # installed on the Mac.
                        pass
        return state
    except DocumentJobError as exc:
        return _record_last_error(application_id, state, exc.code, root)


def poll(application_id, root=None, transport=None):
    """Refresh one job and install its verified result when available."""
    with _application_lock(application_id, root):
        return _poll_unlocked(application_id, root, transport)


def sync(application_id, provider, root=None, transport=None):
    """Submit when absent, otherwise poll. Intended for one watcher cycle."""
    before = get_state(application_id, root)
    state = ensure(application_id, provider, root, transport)
    if before is None or state.get("state") in {"ready", "failed", "submitting"}:
        return state
    return poll(application_id, root, transport)


def is_ready(state):
    return isinstance(state, dict) and state.get("state") == "ready"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="application store directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("ensure", "sync", "retry"):
        command = subparsers.add_parser(name)
        command.add_argument("--id", required=True)
        command.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    for name in ("poll", "show"):
        command = subparsers.add_parser(name)
        command.add_argument("--id", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "ensure":
            result = ensure(args.id, args.provider, args.root)
        elif args.command == "sync":
            result = sync(args.id, args.provider, args.root)
        elif args.command == "retry":
            result = retry_failed(args.id, args.provider, args.root)
        elif args.command == "poll":
            result = poll(args.id, args.root)
        else:
            result = get_state(args.id, args.root) or {
                "schemaVersion": SCHEMA_VERSION,
                "state": "not_requested",
            }
    except DocumentJobError as exc:
        result = {"schemaVersion": SCHEMA_VERSION, "state": "error", "errorCode": exc.code}

    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result.get("state") in {"error", "failed"} else 0


if __name__ == "__main__":
    sys.exit(main())
