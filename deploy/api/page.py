"""Render the deployed dashboard code against sanitized state from KV.

This is the board today. `docs/plans/jobby-v2-rebuild-plan.md` replaces it
in Phase 2 with a Next.js app under `apps/web`, which is a rewrite rather
than a port: §2 chooses "Next.js on Vercel" and justifies it with "already
works", but what already works is Vercel and git-deploy, not Next.js. See
docs/adr/0004-the-web-app-stays-on-vercel.md. Whatever replaces this file
inherits the check that keeps `deploy/renderer/` in step with `scripts/`.
"""

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
from datetime import datetime, timezone
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


# How old a board may be before it says so. The publish floor is 15 minutes and
# the watcher's own alarm fires at twice that, so this is deliberately looser:
# it is the reader's last line, for the case where the alarm has fired and
# nobody was looking.
STALE_AFTER_SECONDS = 3 * 60 * 60


def _freshness(value: str):
    """(label, is_stale) for the board's own timestamp, measured at render.

    This is computed here, per request, and not carried in the state, which is
    the whole point. On 2026-08-27 the board served a 23-hour-old snapshot and
    its status line said "Search v21 is live, every 4 hours, next run 19:31" --
    accurate for the moment that snapshot was built and useless afterwards,
    because it had frozen along with everything else around it.

    A freshness indicator that can freeze with the page is theatre. `now` here
    is request time, so this keeps counting up whatever has happened to
    publishing.
    """
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _display_time(value), False
    age = (datetime.now(timezone.utc) - when).total_seconds()
    if age < STALE_AFTER_SECONDS:
        return _display_time(value), False
    hours = age / 3600
    how_old = f"{hours:.0f} hours" if hours < 48 else f"{hours / 24:.0f} days"
    return (f"{_display_time(value)} \u2014 {how_old} old. "
            f"The board has stopped updating; new jobs are not reaching it."), True


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
        # Drift is measured before the render but only *acted on* after one,
        # because there is exactly one jobby:board-state and many deployments.
        # Every open PR gets a preview whose renderer hashes differently from
        # main's, and the laptop can only stamp the branch it is standing on,
        # so the two can never all agree. Refusing to draw on a hash mismatch
        # therefore 503s every preview permanently, which is the one thing
        # previews exist to prevent. The state itself is renderer-agnostic
        # data; rendererSchemaVersion above is what guards its shape.
        #
        # So: try the render. If it works, say who drew it in a header and
        # leave the board up. If it throws, that is the case the mismatch was
        # written for, and the answer says which half is behind instead of the
        # bare "could not be rendered" that cost a day.
        drift = _renderer_mismatch(envelope_fingerprint)
        try:
            label, is_stale = _freshness(generated_at)
            page = dashboard.build(state, label).encode("utf-8")
        except (OSError, ValueError, KeyError, TypeError) as error:
            failure = {"event": "board_render_failed", "errorType": type(error).__name__}
            print(json.dumps({**failure, **(drift or {})}))
            if drift:
                self._send(503,
                           b"The board was published by a different version of the "
                           b"renderer than this deployment. A deploy is catching up; "
                           b"reload shortly.\n",
                           "text/plain; charset=utf-8", {"Retry-After": "60"})
            else:
                self._send(502, b"The board state could not be rendered.\n",
                           "text/plain; charset=utf-8")
            return
        self._send(
            200,
            page,
            "text/html; charset=utf-8",
            {
                "Content-Security-Policy": CSP,
                "X-Jobby-Renderer-Commit": os.environ.get("VERCEL_GIT_COMMIT_SHA", "local"),
                "X-Jobby-State-Generated-At": generated_at or "unknown",
                # Both halves, always, so drift is visible on a working board
                # rather than only in the wreckage of a broken one.
                "X-Jobby-Renderer-Fingerprint": _renderer_fingerprint(),
                "X-Jobby-State-Fingerprint": envelope_fingerprint or "none",
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
