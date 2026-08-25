"""Render the deployed dashboard code against sanitized state from KV."""

from __future__ import annotations

import base64
import binascii
import hmac
import hashlib
import pathlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path


DEPLOY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY_ROOT / "renderer"))
import dashboard  # noqa: E402


BOARD_STATE_KEY = "jobby:board-state"
RENDERER_SCHEMA_VERSION = 1
MAX_ENVELOPE_BYTES = 16 * 1024 * 1024
MAX_STATE_BYTES = 20 * 1024 * 1024

BASE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

CSP = "; ".join((
    "default-src 'none'",
    "style-src 'unsafe-inline'",
    "script-src 'unsafe-inline' 'self'",
    "img-src 'self' data:",
    "manifest-src 'self'",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
))


def _authorized(header: str | None) -> bool:
    expected = os.environ.get("DASHBOARD_PASSWORD")
    if not expected or not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return False
    supplied = decoded.partition(":")[2] if ":" in decoded else ""
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _kv_value(key: str) -> bytes:
    account = os.environ.get("CF_ACCOUNT_ID")
    namespace = os.environ.get("CF_KV_NAMESPACE_ID")
    token = os.environ.get("CF_API_TOKEN")
    if not account or not namespace or not token:
        raise RuntimeError("board state is not configured")
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{urllib.parse.quote(account, safe='')}/storage/kv/namespaces/"
        f"{urllib.parse.quote(namespace, safe='')}/values/"
        f"{urllib.parse.quote(key, safe='')}"
    )
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read(MAX_ENVELOPE_BYTES + 1)
    if len(body) > MAX_ENVELOPE_BYTES:
        raise ValueError("board state envelope is too large")
    return body


def _renderer_fingerprint() -> str:
    """Hash this deployment's own copy of the renderer modules."""
    digest = hashlib.sha256()
    here = pathlib.Path(__file__).resolve().parent.parent / "renderer"
    names = sorted(p.name for p in here.glob("*.py") if p.name != "__init__.py")
    for name in names:
        try:
            digest.update((here / name).read_bytes())
        except OSError:
            digest.update(b"missing:" + name.encode())
    return digest.hexdigest()[:16]


def _renderer_mismatch(published: str) -> dict | None:
    """Whether the state was published by a different renderer than this one.

    Absent on the state means an older publisher wrote it, so render anyway
    rather than blocking the board on a field that is still rolling out.
    """
    if not published:
        return None
    mine = _renderer_fingerprint()
    if mine == published:
        return None
    return {"publishedBy": published, "deployed": mine}


def _state() -> tuple[dict, str]:
    envelope = json.loads(_kv_value(BOARD_STATE_KEY))
    if (
        not isinstance(envelope, dict)
        or envelope.get("schemaVersion") != 1
        or envelope.get("rendererSchemaVersion") != RENDERER_SCHEMA_VERSION
        or envelope.get("encoding") != "gzip+base64"
    ):
        raise ValueError("board state schema is unsupported")
    compressed = base64.b64decode(envelope.get("payload", ""), validate=True)
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    state_bytes = inflater.decompress(compressed, MAX_STATE_BYTES + 1)
    if len(state_bytes) > MAX_STATE_BYTES or inflater.unconsumed_tail:
        raise ValueError("board state is too large")
    state_bytes += inflater.flush()
    if len(state_bytes) > MAX_STATE_BYTES or not inflater.eof:
        raise ValueError("board state is invalid")
    state = json.loads(state_bytes)
    if not isinstance(state, dict):
        raise ValueError("board state must be an object")
    return (state, str(envelope.get("generatedAt") or ""),
            str(envelope.get("rendererFingerprint") or ""))


def _display_time(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime(
            "%d %b %Y, %H:%M"
        )
    except ValueError:
        return "just now"


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str, extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in BASE_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _page(self) -> None:
        if not os.environ.get("DASHBOARD_PASSWORD"):
            self._send(503, b"Dashboard password is not configured.\n", "text/plain; charset=utf-8")
            return
        if not _authorized(self.headers.get("Authorization")):
            self._send(
                401,
                b"Authentication required.\n",
                "text/plain; charset=utf-8",
                {"WWW-Authenticate": 'Basic realm="Application board", charset="UTF-8"'},
            )
            return
        try:
            state, generated_at, envelope_fingerprint = _state()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                message = b"No board state has been published yet.\n"
                self._send(503, message, "text/plain; charset=utf-8")
                return
            print(json.dumps({"event": "board_state_read_failed", "errorType": type(error).__name__}))
            self._send(502, b"The board state could not be read.\n", "text/plain; charset=utf-8")
            return
        except (OSError, ValueError, json.JSONDecodeError, binascii.Error, zlib.error) as error:
            print(json.dumps({"event": "board_state_read_failed", "errorType": type(error).__name__}))
            self._send(502, b"The board state could not be read.\n", "text/plain; charset=utf-8")
            return
        mismatch = _renderer_mismatch(envelope_fingerprint)
        if mismatch:
            # Say which half is behind instead of throwing. The board and the
            # code that renders it are deployed by different routes: state is
            # published from the user's machine every few minutes, code only on
            # a merge. Between the two there is a window where new state meets
            # an older renderer, and that used to surface as a bare 502 reading
            # "The board state could not be rendered", which says nothing about
            # what is wrong or that it will clear on its own.
            print(json.dumps({"event": "renderer_state_mismatch", **mismatch}))
            self._send(503,
                       b"The board was published by a different version of the "
                       b"renderer than this deployment. A deploy is catching up; "
                       b"reload shortly.\n",
                       "text/plain; charset=utf-8", {"Retry-After": "60"})
            return
        try:
            page = dashboard.build(state, _display_time(generated_at)).encode("utf-8")
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(json.dumps({"event": "board_render_failed", "errorType": type(error).__name__}))
            self._send(502, b"The board state could not be rendered.\n", "text/plain; charset=utf-8")
            return
        self._send(
            200,
            page,
            "text/html; charset=utf-8",
            {
                "Content-Security-Policy": CSP,
                "X-Jobby-Renderer-Commit": os.environ.get("VERCEL_GIT_COMMIT_SHA", "local"),
                "X-Jobby-State-Generated-At": generated_at or "unknown",
            },
        )

    def do_GET(self) -> None:
        self._page()

    def do_HEAD(self) -> None:
        self._page()

    def do_POST(self) -> None:
        self._send(
            405,
            b"Method not allowed.\n",
            "text/plain; charset=utf-8",
            {"Allow": "GET, HEAD"},
        )
