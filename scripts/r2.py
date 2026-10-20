#!/usr/bin/env python3
"""Cloudflare R2 access for Class 1 artifacts, signed with SigV4 from stdlib.

Class 1 is the architecture plan's name for the documents a user would want to
look at again: generated resumes and cover letters, the answer record for an
application, the job description as it read when we applied, a confirmation
capture. They are not secrets in the way a provider key is, so they live in a
private bucket in plaintext and are served through short-lived presigned GETs
after the board's own password check. See docs/plans/multi-tenant-architecture.md
section 6.

Two credentials exist and they are not interchangeable. The Workers KV token
already in the project cannot touch R2, and the R2 S3 token here cannot be used
from a Worker binding. Presigning needs the S3 one, and it never leaves the
device or the API function: the browser only ever sees a signed URL with an
expiry on it.

  python3 scripts/r2.py --check                 # is it configured, and does it answer
  python3 scripts/r2.py --head <key>
  python3 scripts/r2.py --presign <key>

Configuration lives in ~/.job-application/config.env, which `vercel env pull`
does not overwrite:

  R2_ACCESS_KEY_ID=<R2 API token, Object Read & Write, this bucket only>
  R2_SECRET_ACCESS_KEY=<the secret printed beside it, once>

R2_ACCOUNT_ID falls back to the CF_ACCOUNT_ID already set for the queue, and
R2_BUCKET defaults to jobby-artifacts, so only the two credentials need setting.
"""

import argparse
import datetime as _dt
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.sax import saxutils

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402

# The prefix every key starts with. One user today, and the segment exists so
# that a deletion request stays one prefix sweep and so the multi-tenant layout
# is this layout with a different id rather than a migration.
DEFAULT_USER_ID = "account-1"

R2_VARS = ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

REGION = "auto"
SERVICE = "s3"
ALGORITHM = "AWS4-HMAC-SHA256"

# A presigned URL is a bearer token for one object. Long enough to open a PDF on
# a phone over a bad connection, short enough that a URL in someone's history is
# not a standing grant.
DEFAULT_EXPIRY_SECONDS = 300

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _between(text, opening, closing):
    """Every value between two literal tags, XML-unescaped. Enough for a listing.

    The unescaping is the part that matters. A company folder called
    "Dewey & Cheatem" comes back from the listing as "Dewey &amp;
    Cheatem". Taking it literally reported three objects that are present as
    missing and three that do not exist as orphans, which is exactly the wrong
    answer from the tool whose job is to say what is really in the bucket.
    """
    out, position = [], 0
    while True:
        start = text.find(opening, position)
        if start < 0:
            return out
        end = text.find(closing, start)
        if end < 0:
            return out
        out.append(saxutils.unescape(text[start + len(opening):end],
                                     {"&quot;": '"', "&apos;": "'"}))
        position = end + len(closing)


class R2Error(Exception):
    """A real failure. Never raised for a key that simply does not exist."""


def _uri_encode(value, keep_slash):
    # AWS leaves A-Z a-z 0-9 - _ . ~ alone and percent-encodes everything else,
    # which is exactly what quote() does with an empty safe list.
    return urllib.parse.quote(value, safe="/" if keep_slash else "")


def _sign(key, message):
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret, datestamp):
    k = _sign(("AWS4" + secret).encode("utf-8"), datestamp)
    k = _sign(k, REGION)
    k = _sign(k, SERVICE)
    return _sign(k, "aws4_request")


class Client:
    """One bucket, one credential. Cheap to construct; holds no connection."""

    def __init__(self, account_id, bucket, access_key_id, secret_access_key,
                 user_id=DEFAULT_USER_ID, timeout=30):
        self.account_id = account_id
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.user_id = user_id
        self.timeout = timeout

    # -- addressing ---------------------------------------------------------

    @property
    def host(self):
        return f"{self.account_id}.r2.cloudflarestorage.com"

    def canonical_path(self, key):
        base = "/" + _uri_encode(self.bucket, keep_slash=False)
        return base + "/" + _uri_encode(key, keep_slash=True) if key else base

    def url(self, key):
        return f"https://{self.host}{self.canonical_path(key)}"

    # -- signing ------------------------------------------------------------

    def presign_get(self, key, expires=DEFAULT_EXPIRY_SECONDS, now=None,
                    response_headers=None):
        """A URL anyone holding it can GET, until it expires.

        Query-string signing rather than a header, because the point is to hand
        it to a browser as a redirect. The payload hash is the literal
        UNSIGNED-PAYLOAD that S3 requires for presigned requests.

        ``response_headers`` carries the ``response-content-*`` overrides S3
        honours on a GET. Without them a redirected PDF arrives named after its
        key, so a phone offers to save "resume.pdf" from an application id it
        has never heard of. They are signed like every other query parameter,
        so nobody can rewrite the filename on a URL we minted.
        """
        now = now or _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        credential = f"{self.access_key_id}/{datestamp}/{REGION}/{SERVICE}/aws4_request"

        query = [
            ("X-Amz-Algorithm", ALGORITHM),
            ("X-Amz-Credential", credential),
            ("X-Amz-Date", amz_date),
            ("X-Amz-Expires", str(int(expires))),
            ("X-Amz-SignedHeaders", "host"),
        ]
        query += [(k, str(v)) for k, v in (response_headers or {}).items()]
        # Canonical query strings are sorted by encoded name, then value.
        canonical_query = "&".join(
            f"{_uri_encode(k, False)}={_uri_encode(v, False)}"
            for k, v in sorted(query))
        canonical_request = "\n".join([
            "GET",
            self.canonical_path(key),
            canonical_query,
            f"host:{self.host}\n",
            "host",
            "UNSIGNED-PAYLOAD",
        ])
        scope = f"{datestamp}/{REGION}/{SERVICE}/aws4_request"
        to_sign = "\n".join([
            ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])
        signature = hmac.new(_signing_key(self.secret_access_key, datestamp),
                             to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{self.url(key)}?{canonical_query}&X-Amz-Signature={signature}"

    def _signed_headers(self, method, key, payload_sha256, extra=None, now=None):
        now = now or _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        headers = {
            "host": self.host,
            "x-amz-content-sha256": payload_sha256,
            "x-amz-date": amz_date,
        }
        for name, value in (extra or {}).items():
            headers[name.lower()] = str(value)

        names = sorted(headers)
        canonical_headers = "".join(f"{n}:{headers[n].strip()}\n" for n in names)
        signed_names = ";".join(names)
        canonical_request = "\n".join([
            method,
            self.canonical_path(key),
            "",
            canonical_headers,
            signed_names,
            payload_sha256,
        ])
        scope = f"{datestamp}/{REGION}/{SERVICE}/aws4_request"
        to_sign = "\n".join([
            ALGORITHM, amz_date, scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])
        signature = hmac.new(_signing_key(self.secret_access_key, datestamp),
                             to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"{ALGORITHM} Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_names}, Signature={signature}")
        return headers

    # -- operations ---------------------------------------------------------

    def _request(self, method, key, body=None, extra_headers=None,
                 payload_sha256=None):
        payload_sha256 = payload_sha256 or (
            hashlib.sha256(body).hexdigest() if body is not None else EMPTY_SHA256)
        headers = self._signed_headers(method, key, payload_sha256, extra_headers)
        request = urllib.request.Request(self.url(key), data=body, method=method)
        for name, value in headers.items():
            if name != "host":
                request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            # A key that was never written is emptiness, not a fault. Everything
            # else is a real failure and must not be mistaken for one, which is
            # the rule kv.mjs already follows for the same reason.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - the body is best-effort context
                pass
            if exc.code in (403, 404) and method in ("HEAD", "GET"):
                return exc.code, dict(exc.headers or {}), b""
            raise R2Error(f"R2 {method} {key} failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise R2Error(f"R2 {method} {key} could not be reached: {exc.reason}") from exc

    def _request_raw(self, method, key, canonical_query):
        """A signed request that carries a query string, which only list needs."""
        now = _dt.datetime.now(_dt.timezone.utc)
        headers = self._signed_headers_with_query(method, key, EMPTY_SHA256,
                                                  canonical_query, now)
        url = self.url(key) + ("?" + canonical_query if canonical_query else "")
        request = urllib.request.Request(url, method=method)
        for name, value in headers.items():
            if name != "host":
                request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - the body is best-effort context
                pass
            raise R2Error(f"R2 {method} {key or '/'} failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise R2Error(f"R2 {method} could not be reached: {exc.reason}") from exc

    def _signed_headers_with_query(self, method, key, payload_sha256,
                                   canonical_query, now):
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        headers = {"host": self.host, "x-amz-content-sha256": payload_sha256,
                   "x-amz-date": amz_date}
        names = sorted(headers)
        canonical_headers = "".join(f"{n}:{headers[n].strip()}\n" for n in names)
        signed_names = ";".join(names)
        canonical_request = "\n".join([
            method, self.canonical_path(key), canonical_query,
            canonical_headers, signed_names, payload_sha256])
        scope = f"{datestamp}/{REGION}/{SERVICE}/aws4_request"
        to_sign = "\n".join([ALGORITHM, amz_date, scope,
                             hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
        signature = hmac.new(_signing_key(self.secret_access_key, datestamp),
                             to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"{ALGORITHM} Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_names}, Signature={signature}")
        return headers

    def head(self, key):
        """Object metadata, or None when the key has never been written."""
        status, headers, _ = self._request("HEAD", key)
        if status in (403, 404):
            return None
        return {
            "bytes": int(headers.get("Content-Length") or 0),
            "sha256": headers.get("x-amz-meta-sha256") or "",
            "contentType": headers.get("Content-Type") or "",
            "etag": (headers.get("ETag") or "").strip('"'),
        }

    def get(self, key):
        status, _, body = self._request("GET", key)
        return None if status in (403, 404) else body

    def put(self, key, body, content_type="application/octet-stream", sha256=None):
        """Write one object, stamping its sha256 so a retry can recognise it."""
        digest = sha256 or hashlib.sha256(body).hexdigest()
        self._request("PUT", key, body=body, payload_sha256=digest, extra_headers={
            "content-type": content_type,
            "content-length": str(len(body)),
            "x-amz-meta-sha256": digest,
        })
        return {"key": key, "bytes": len(body), "sha256": digest}

    def delete(self, key):
        self._request("DELETE", key)
        return True

    def list_keys(self, prefix):
        """Every key under a prefix, paging until the bucket says it is done.

        ListObjectsV2 is a Class A operation, the expensive kind, with a million
        a month free. That is fine for a sweep run by hand and is exactly why
        nothing on the upload path lists: the manifest exists so the common
        case costs no operations at all.
        """
        keys, token = [], None
        while True:
            query = [("list-type", "2"), ("prefix", prefix), ("max-keys", "1000")]
            if token:
                query.append(("continuation-token", token))
            encoded = "&".join(f"{_uri_encode(k, False)}={_uri_encode(v, False)}"
                               for k, v in sorted(query))
            status, _headers, body = self._request_raw("GET", "", encoded)
            if status != 200:
                raise R2Error(f"R2 list under {prefix} failed: {status}")
            text = body.decode("utf-8", "replace")
            keys.extend(_between(text, "<Key>", "</Key>"))
            token = next(iter(_between(text, "<NextContinuationToken>",
                                       "</NextContinuationToken>")), None)
            if not token:
                return keys


# The bucket this project created, and the only one it writes. Named rather
# than required, because it is not a secret and a fourth thing to set up is a
# fourth thing to get wrong. Override it with R2_BUCKET.
DEFAULT_BUCKET = "jobby-artifacts"


def settings(env=None):
    """R2 configuration from config.env, with the environment winning.

    The account id falls back to CF_ACCOUNT_ID because R2 and Workers KV live
    in the same Cloudflare account and it is already configured for the queue.
    The two credentials have no fallback and never will: the KV token cannot
    see R2 and the R2 token cannot see KV.
    """
    merged = dict(toolpath.settings())
    source = env if env is not None else os.environ
    merged.update({k: v for k, v in source.items()
                   if k in R2_VARS + ("CF_ACCOUNT_ID",) and v})
    values = {k: merged.get(k, "") for k in R2_VARS}
    values["R2_ACCOUNT_ID"] = values["R2_ACCOUNT_ID"] or merged.get("CF_ACCOUNT_ID", "")
    values["R2_BUCKET"] = values["R2_BUCKET"] or DEFAULT_BUCKET
    return values


def configured(env=None):
    return all(settings(env).values())


def missing(env=None):
    return [k for k, v in settings(env).items() if not v]


def client(env=None, user_id=DEFAULT_USER_ID):
    """A Client, or a clear refusal naming what is not set.

    Refusing here rather than returning None is deliberate. Every caller that
    reaches this point has already decided R2 is in play, and a None that gets
    treated as "nothing to upload" is how an artifact silently fails to be
    stored anywhere.
    """
    values = settings(env)
    absent = [k for k, v in values.items() if not v]
    if absent:
        raise R2Error(
            "R2 is not configured. Missing " + ", ".join(absent)
            + " in ~/.job-application/config.env. See README.md, Setting it up.")
    return Client(values["R2_ACCOUNT_ID"], values["R2_BUCKET"],
                  values["R2_ACCESS_KEY_ID"], values["R2_SECRET_ACCESS_KEY"],
                  user_id=user_id)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cloudflare R2 access for Jobby artifacts.")
    ap.add_argument("--check", action="store_true", help="report configuration and reachability")
    ap.add_argument("--head", metavar="KEY", help="print one object's metadata")
    ap.add_argument("--get", metavar="KEY", help="write one object to stdout")
    ap.add_argument("--presign", metavar="KEY", help="print a short-lived GET URL")
    ap.add_argument("--expires", type=int, default=DEFAULT_EXPIRY_SECONDS)
    args = ap.parse_args(argv)

    if args.check:
        absent = missing()
        if absent:
            print(json.dumps({"configured": False, "missing": absent}, indent=2))
            return 1
        c = client()
        # HEAD of a key that cannot exist: proves the credential signs and the
        # bucket answers, without writing anything.
        try:
            c.head(f"u/{c.user_id}/.reachability-probe")
        except R2Error as exc:
            print(json.dumps({"configured": True, "reachable": False,
                              "error": str(exc)}, indent=2))
            return 1
        print(json.dumps({"configured": True, "reachable": True,
                          "bucket": c.bucket, "prefix": f"u/{c.user_id}/"}, indent=2))
        return 0

    try:
        c = client()
        if args.head:
            print(json.dumps(c.head(args.head), indent=2))
            return 0
        if args.get:
            body = c.get(args.get)
            if body is None:
                print("no such object", file=sys.stderr)
                return 1
            sys.stdout.buffer.write(body)
            return 0
        if args.presign:
            print(c.presign_get(args.presign, args.expires))
            return 0
    except R2Error as exc:
        print(f"r2: {exc}", file=sys.stderr)
        return 2

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
