#!/usr/bin/env python3
"""A narrow stdio firewall in front of one Playwright MCP process.

The form agent needs page snapshots and ordinary form controls. It does not
need JavaScript execution, browser storage, network inspection, filesystem
writes, or arbitrary file uploads. This proxy launches the configured MCP
child without a shell, publishes a fixed tool surface, validates each call,
and forwards only requests inside that surface.

Each protocol message is one UTF-8 JSON line. Child output that is not a
bounded JSON-RPC line is never copied to stdout.
"""

from __future__ import annotations

import argparse
from collections import deque
import copy
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Sequence


SERVER_NAME = "jobby-browser-firewall"
SERVER_VERSION = "1.0.0"
# The stateless MCP revision. A client that discovers this version never
# sends initialize, so every result it accepts has to carry the completion
# markers below. Answering server/discover without them left the firewall
# "connected" with an empty tool list, and the form agent had no way to
# touch the page. application_mcp.py already does this; the two must agree.
MODERN_PROTOCOL_VERSION = "2026-07-28"
MAX_CLIENT_LINE_BYTES = 1024 * 1024
MAX_CHILD_LINE_BYTES = 8 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 64_000


class PolicyError(ValueError):
    """A client request falls outside the fixed browser policy."""


class ProtocolError(ValueError):
    """A child message is not a usable JSON-RPC response."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{label} must be an object")
    return value


def _closed(value: dict[str, Any], allowed: set[str]) -> None:
    if set(value) - allowed:
        raise PolicyError("unsupported browser tool argument")


def _string(
    value: Any,
    label: str,
    *,
    maximum: int = 4_000,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise PolicyError(f"{label} must be a string")
    if "\x00" in value:
        raise PolicyError(f"{label} contains unsupported characters")
    if not allow_empty and not value:
        raise PolicyError(f"{label} must not be empty")
    if len(value) > maximum:
        raise PolicyError(f"{label} is too long")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyError(f"{label} must be a boolean")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise PolicyError(f"{label} is outside the allowed range")
    return value


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{label} must be a number")
    number = float(value)
    if not minimum <= number <= maximum:
        raise PolicyError(f"{label} is outside the allowed range")
    return number


def _element(arguments: dict[str, Any], extra: set[str] | None = None) -> None:
    allowed = {"element", "target"} | (extra or set())
    _closed(arguments, allowed)
    _string(arguments.get("target"), "target")
    if "element" in arguments:
        _string(arguments["element"], "element")


def _read_pdf(path: Path) -> tuple[int, str, int, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PolicyError("bound upload cannot be opened") from exc
    digest = hashlib.sha256()
    total = 0
    signature = b""
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PolicyError("bound upload is not a regular file")
        if before.st_size < 6 or before.st_size > MAX_PDF_BYTES:
            raise PolicyError("bound upload has an invalid size")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if not signature:
                signature = chunk[:5]
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                raise PolicyError("bound upload is too large")
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise PolicyError("bound upload cannot be read") from exc
    finally:
        os.close(descriptor)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
        or total != before.st_size
    ):
        raise PolicyError("bound upload changed while it was checked")
    if signature != b"%PDF-":
        raise PolicyError("bound upload is not a PDF")
    return total, digest.hexdigest(), before.st_dev, before.st_ino


@dataclass(frozen=True)
class UploadBinding:
    path: Path
    size: int
    sha256: str
    device: int
    inode: int

    @classmethod
    def create(cls, raw: str) -> "UploadBinding":
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise PolicyError("upload path is invalid")
        supplied = Path(raw)
        if not supplied.is_absolute() or supplied.suffix.lower() != ".pdf":
            raise PolicyError("upload must be an absolute PDF path")
        try:
            resolved = supplied.resolve(strict=True)
        except OSError as exc:
            raise PolicyError("upload cannot be resolved") from exc
        if resolved != supplied:
            raise PolicyError("upload path must not contain symlinks")
        size, digest, device, inode = _read_pdf(resolved)
        return cls(resolved, size, digest, device, inode)

    def verify(self) -> None:
        size, digest, device, inode = _read_pdf(self.path)
        if (
            size != self.size
            or digest != self.sha256
            or device != self.device
            or inode != self.inode
        ):
            raise PolicyError("bound upload changed after startup")


def _schema(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = list(required)
    return result


ELEMENT = {"type": "string", "minLength": 1, "maxLength": 4_000}
TARGET = {"type": "string", "minLength": 1, "maxLength": 4_000}


def _tool_definitions(upload_paths: Sequence[str]) -> tuple[dict[str, Any], ...]:
    return (
        {
            "name": "browser_snapshot",
            "description": "Return an accessibility snapshot without writing a file.",
            "inputSchema": _schema({
                "target": TARGET,
                "depth": {"type": "integer", "minimum": 0, "maximum": 50},
                "boxes": {"type": "boolean"},
            }),
        },
        {
            "name": "browser_find",
            "description": "Find text in the current accessibility snapshot.",
            # A bare top-level oneOf is not an object schema, and a client that
            # requires "type": "object" drops the tool. Exactly one of the two is
            # still enforced, in _validate_browser_find, which is the boundary
            # that actually matters.
            "inputSchema": _schema({
                "text": {"type": "string", "minLength": 1, "maxLength": 1_000},
                "regex": {"type": "string", "minLength": 1, "maxLength": 1_000},
            }),
        },
        {
            "name": "browser_click",
            "description": "Click a referenced page element.",
            "inputSchema": _schema({
                "element": ELEMENT,
                "target": TARGET,
                "doubleClick": {"type": "boolean"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "modifiers": {
                    "type": "array", "maxItems": 5, "uniqueItems": True,
                    "items": {"type": "string", "enum": [
                        "Alt", "Control", "ControlOrMeta", "Meta", "Shift",
                    ]},
                },
            }, ["target"]),
        },
        {
            "name": "browser_hover",
            "description": "Hover over a referenced page element.",
            "inputSchema": _schema({"element": ELEMENT, "target": TARGET}, ["target"]),
        },
        {
            "name": "browser_fill_form",
            "description": "Fill referenced text, choice, and slider fields.",
            "inputSchema": _schema({
                "fields": {
                    "type": "array", "minItems": 1, "maxItems": 100,
                    "items": _schema({
                        "name": {"type": "string", "minLength": 1,
                                 "maxLength": 1_000},
                        "element": ELEMENT,
                        "target": TARGET,
                        "type": {"type": "string", "enum": [
                            "textbox", "checkbox", "radio", "combobox", "slider",
                        ]},
                        "value": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                    }, ["name", "target", "type", "value"]),
                },
            }, ["fields"]),
        },
        {
            "name": "browser_type",
            "description": "Type into a referenced field without submitting it.",
            "inputSchema": _schema({
                "element": ELEMENT,
                "target": TARGET,
                "text": {"type": "string", "maxLength": MAX_TEXT_CHARS},
                "slowly": {"type": "boolean"},
            }, ["target", "text"]),
        },
        {
            "name": "browser_press_key",
            "description": "Press a non-submit form key such as Tab, Space, or an arrow key.",
            "inputSchema": _schema({
                "key": {"type": "string", "enum": [
                    "Tab", "Shift+Tab", "Space", "ArrowUp", "ArrowDown",
                    "ArrowLeft", "ArrowRight", "Escape", "Backspace", "Delete",
                    "Home", "End", "PageUp", "PageDown",
                ]},
            }, ["key"]),
        },
        {
            "name": "browser_select_option",
            "description": "Choose one or more values in a referenced dropdown.",
            "inputSchema": _schema({
                "element": ELEMENT,
                "target": TARGET,
                "values": {
                    "type": "array", "minItems": 1, "maxItems": 100,
                    "items": {"type": "string", "maxLength": 4_000},
                },
            }, ["target", "values"]),
        },
        {
            "name": "browser_handle_dialog",
            "description": "Accept or dismiss the current browser dialog.",
            "inputSchema": _schema({
                "accept": {"type": "boolean"},
                "promptText": {"type": "string", "maxLength": 4_000},
            }, ["accept"]),
        },
        {
            "name": "browser_file_upload",
            "description": "Upload only the fixed resume or cover letter PDF.",
            "inputSchema": _schema({
                "paths": {
                    "type": "array", "minItems": 1, "maxItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": list(upload_paths)},
                },
            }, ["paths"]),
        },
        {
            "name": "browser_attach_file",
            # Playwright MCP's own browser_file_upload only answers while a
            # file chooser is open, and the click that opens one is exactly the
            # click that hangs: a file input is usually zero-sized and covered,
            # so Playwright waits for actionability that never arrives and the
            # call times out. Measured on GitLab on 2026-08-27, where every
            # upload returned "can only be used when there is related modal
            # state present" and the application parked with both PDFs
            # unattached. `setInputFiles` needs no chooser and no visibility,
            # which is why this exists.
            #
            # It reaches the page through browser_run_code_unsafe, the one tool
            # the firewall most deliberately removes, so the code string is
            # written here and never assembled from anything the agent sent.
            # The agent chooses a target and which of its own two bound PDFs,
            # and both cross into JavaScript as JSON literals.
            "description": (
                "Attach the fixed resume or cover letter PDF to a file input "
                "directly, without opening a file chooser. Prefer this over "
                "browser_file_upload, which needs an open chooser dialog."
            ),
            "inputSchema": _schema({
                "target": TARGET,
                "element": ELEMENT,
                "paths": {
                    "type": "array", "minItems": 1, "maxItems": 2,
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": list(upload_paths)},
                },
            }, ["paths"]),
        },
        {
            "name": "browser_tabs",
            "description": "List tabs or select one existing tab.",
            # Object schema for the same reason as browser_find. The pairing of
            # select with index is enforced in _validate_browser_tabs.
            "inputSchema": _schema({
                "action": {"type": "string", "enum": ["list", "select"]},
                "index": {"type": "integer", "minimum": 0, "maximum": 100},
            }, ["action"]),
        },
        {
            "name": "browser_wait_for",
            "description": "Wait briefly for page text or a page transition.",
            "inputSchema": _schema({
                "time": {"type": "number", "minimum": 0, "maximum": 30},
                "text": {"type": "string", "minLength": 1, "maxLength": 4_000},
                "textGone": {"type": "string", "minLength": 1, "maxLength": 4_000},
            }),
        },
    )


@dataclass(frozen=True)
class ClientDecision:
    forward: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    pending_method: str | None = None
    modern: bool = False


@dataclass(frozen=True)
class ChildDecision:
    to_client: dict[str, Any] | None = None
    to_child: dict[str, Any] | None = None


def _response(
    request_id: Any,
    result: Any | None = None,
    *,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


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
        # The tool surface is fixed for the life of this proxy, and the upload
        # enum names one application's two files, so the cache stays private.
        result["ttlMs"] = 60_000
        result["cacheScope"] = "private"
    return result


# Written here, never assembled from client text. The two values the agent
# chooses arrive as `paths` and `ref`, both injected as JSON literals by
# _attach_file_code, so no client string is ever concatenated into source.
#
# A ref resolves through Playwright's aria-ref selector, the same handle the
# accessibility snapshot hands the agent. What the ref points at is usually the
# visible button rather than the input, so the input is looked for at the ref,
# under it, and beside it before the search widens to the page. Order matters:
# a page with several file inputs would otherwise take whichever came first.
#
# The input is not a sufficient observable and never was. `setInputFiles`
# resolving proves only that Playwright wrote files onto the DOM element;
# everything that decides the outcome happens afterwards in the page's own
# change handler. Three shapes were measured on 2026-08-28, and the input
# answers the same in all three: SmartRecruiters takes the file, sends it and
# clears `input.value` so re-choosing the same file still fires change, which
# empties `node.files` on success; Greenhouse swaps the field out for its own
# "file.pdf / Remove" view, which detaches the node the read would ask; and a
# page that rejects on type or size also leaves `node.files` empty. Reading
# success or failure off the input alone therefore has to be wrong somewhere,
# and across every recorded run it was wrong in both directions at once.
#
# So the page's own rendering of the filename is the second observable, and it
# is scoped rather than searched for. Both bound filenames are generic, so a
# document-wide `text.includes(name)` would confirm a resume from help text or
# from the cover letter beside it. The scopes are the input's own ancestors,
# innermost first, captured as a live handle *before* the attach because the
# container that confirms the upload is often the one the page rewrites. A
# match has to be a text node's own text, has to be a change against a baseline
# taken before the attach, and must not sit inside an alert: a form that
# rejects a file names that file in its error, and counting that as
# confirmation is the one mistake with a submission behind it.
ATTACH_FILE_CODE = """async (page) => {
  const paths = %(paths)s;
  const ref = %(ref)s;
  const names = paths.map((path) => path.split('/').pop());
  const brief = (error) =>
    // fromCharCode rather than an escape: this string is a Python literal
    // holding JavaScript, so a backslash here has two layers to survive.
    String((error && error.message) || error)
      .split(String.fromCharCode(10))[0].slice(0, 200);
  // Both file inputs on a SmartRecruiters form carry id=file-input, one per web
  // component shadow root, where the id really is unique. So the id cannot say
  // which of the two a call touched, every error on that form read identically,
  // and two sessions inferred from those logs that nth=0 and nth=1 had resolved
  // to the same element. The way out of the element can tell them apart: three
  // ancestors up, one is the apply-with-resume helper and the other the resume
  // field. Measured on the live Versant posting on 2026-08-28.
  const describe = (node) => {
    const bits = ['input'];
    for (const name of ['id', 'name']) {
      const value = node.getAttribute(name);
      if (value) bits.push(name + '=' + value);
    }
    const accept = node.getAttribute('accept');
    if (accept) {
      // SmartRecruiters accepts 60 extensions and printing all of them buried
      // everything that identifies the field.
      bits.push('accept=' + (accept.length > 80 ? accept.slice(0, 80) + '...' : accept));
    }
    if (node.multiple) bits.push('multiple');
    const where = [];
    let at = node.parentNode;
    while (at && at.nodeType !== 9 && where.length < 4) {
      if (at.nodeType === 11) {
        where.push('shadow of ' + (at.host ? at.host.tagName.toLowerCase() : '?'));
      } else {
        let step = at.tagName.toLowerCase();
        for (const name of ['data-test', 'data-testid', 'id', 'name', 'aria-label']) {
          const value = at.getAttribute(name);
          if (value) { step += '[' + name + '=' + value.slice(0, 40) + ']'; break; }
        }
        where.push(step);
      }
      at = at.parentNode || at.host || null;
    }
    if (where.length) bits.push('in ' + where.join(' < '));
    return bits.join(' ');
  };
  // Serialized into the page on its own, so it closes over nothing and takes
  // the filenames as an argument. Run once before the attach and once after,
  // against the same held scopes, so what it reports is a change and not a
  // standing string.
  const scan = (scopes, wanted) => {
    const alarming = (node) => {
      const role = node.getAttribute('role');
      if (role === 'alert' || role === 'alertdialog' || role === 'status') return true;
      if (node.getAttribute('aria-live')) return true;
      const named = typeof node.className === 'string' ? node.className : '';
      return /(^|[^a-z])(error|invalid|warning|failed|reject)([^a-z]|$)/i.test(named);
    };
    return scopes.map((scope) => {
      let seen = 0;
      let shown = 0;
      const complained = [];
      const visit = (root, inAlert) => {
        if (!root || seen > 4000) return;
        for (const node of root.childNodes) {
          if (++seen > 4000) return;
          if (node.nodeType === 3) {
            const text = node.data.trim();
            if (text && text.length <= 200 && wanted.some((name) => text.includes(name))) {
              if (inAlert) { if (complained.length < 3) complained.push(text.slice(0, 200)); }
              else shown++;
            }
          } else if (node.nodeType === 1) {
            const under = inAlert || alarming(node);
            if (node.shadowRoot) visit(node.shadowRoot, under);
            visit(node, under);
          }
        }
      };
      visit(scope, false);
      return { shown: shown, complained: complained };
    });
  };
  let inputs = [];
  if (ref) {
    const at = /^e[0-9]+$/.test(ref) ? page.locator('aria-ref=' + ref) : page.locator(ref);
    for (const scope of [at, at.locator('input[type=file]'),
                         at.locator('xpath=./ancestor::*[position()<=3]//input[@type=\"file\"]')]) {
      let total = 0;
      try { total = await scope.count(); } catch (error) { continue; }
      for (let index = 0; index < total; index++) {
        const candidate = scope.nth(index);
        let shape = '';
        try {
          shape = await candidate.evaluate((node) =>
            node.tagName.toLowerCase() + ':' + (node.getAttribute('type') || ''));
        } catch (error) { continue; }
        if (shape === 'input:file') inputs.push(candidate);
      }
      if (inputs.length) break;
    }
    if (!inputs.length) throw new Error('the target resolved to no file input');
  } else {
    const all = page.locator('input[type=file]');
    const total = await all.count();
    if (total === 0) throw new Error('the page has no file input');
    // Choosing the first of several is how a cover letter lands in the resume
    // field, silently, on a form nobody reads back. Name them and let the
    // caller say which; the hidden ones carry no accessibility ref, so a CSS
    // selector in `target` is the handle that always exists.
    if (total > 1) {
      const seen = [];
      for (let index = 0; index < total; index++) {
        seen.push((index + 1) + ' of ' + total + ': ' + await all.nth(index).evaluate(describe));
      }
      throw new Error('the page has ' + total + ' file inputs; pass target to choose one: '
                      + seen.join(' ; '));
    }
    inputs = [all.first()];
  }
  const input = inputs[0];
  // Described before the attach, not after. A form that accepts a file may
  // replace the field with its own view of it in the same tick, and the handle
  // held here is detached by the time anything asks it a question.
  const described = await input.evaluate(describe);
  const multiple = await input.evaluate((node) => Boolean(node.multiple));
  if (paths.length > 1 && !multiple) {
    throw new Error('this input takes one file; call once per input (' + described + ')');
  }
  // Held before the attach for the same reason, and it matters more here: the
  // scope that ends up showing the filename is frequently the one the page
  // rewrote, so a chain walked afterwards would start from an orphan and see
  // nothing. `document` is excluded deliberately; a page-wide scope is the
  // false positive this is built to avoid.
  const scopes = await input.evaluateHandle((node) => {
    const out = [];
    let at = node.parentNode;
    while (at && at.nodeType !== 9 && out.length < 5) {
      out.push(at);
      at = at.parentNode || at.host || null;
    }
    return out;
  });
  const before = await scopes.evaluate(scan, names);

  await input.setInputFiles(paths);

  let landed = '';
  let readFault = '';
  try {
    landed = await input.evaluate((node) =>
      Array.from(node.files || []).map((file) => file.name + ' (' + file.size + ' bytes)').join(', '),
      undefined, { timeout: 3000 });
  } catch (error) {
    // Kept, not swallowed. This branch used to return a sentence asserting the
    // page had taken the file, inferred from an unread `error` that could
    // equally have been a navigation, a destroyed context or this timeout
    // simply expiring. Every success the tool had ever reported came out of
    // here, and none of them was independently confirmed.
    readFault = brief(error);
  }

  let confirmed = -1;
  let complaint = '';
  let scanFault = '';
  try {
    const after = await scopes.evaluate(scan, names);
    for (let index = 0; index < after.length; index++) {
      if (confirmed < 0 && after[index].shown > before[index].shown) confirmed = index;
      if (!complaint && after[index].complained.length > before[index].complained.length) {
        complaint = after[index].complained[after[index].complained.length - 1];
      }
    }
  } catch (error) {
    scanFault = brief(error);
  }

  const beside = 'the page now shows ' + names.join(', ') + ' beside the field';
  if (landed) {
    return 'attached to ' + described + ': ' + landed + (confirmed >= 0 ? '; ' + beside : '');
  }
  if (confirmed >= 0) {
    return 'attached ' + names.join(', ') + ' to ' + described + '; '
           + (readFault ? 'the field could not be read back (' + readFault + ')'
                        : 'the field itself reports no file')
           + ', and ' + beside + ', which is what a form that takes the file and'
           + ' clears the input looks like';
  }
  throw new Error(
    'the attachment to ' + described + ' is unconfirmed, which is not the same as failed. '
    + 'setInputFiles resolved, so the file may well be on the form: '
    + (readFault ? 'the field could not be read back (' + readFault + ')'
                 : 'the field reports no file, which a form that takes the file and'
                   + ' clears the input also does')
    + ', and nothing beside it shows ' + names.join(', ') + '. '
    + (complaint ? 'The page says: "' + complaint + '". ' : '')
    + (scanFault ? 'Reading the surroundings failed: ' + scanFault + '. ' : '')
    + 'Read the field on the page before attaching again.');
}"""


def _js_literal(value: Any) -> str:
    """A JSON value as JavaScript source, safe for the two separators JSON allows raw."""
    return (json.dumps(value)
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _attach_file_code(paths: Sequence[str], ref: str | None) -> str:
    return ATTACH_FILE_CODE % {
        "paths": _js_literal(list(paths)),
        "ref": _js_literal(ref) if ref else "null",
    }


def _tool_error(request_id: Any, message: str) -> dict[str, Any]:
    return _response(request_id, {
        "content": [{"type": "text", "text": message}],
        "structuredContent": {"error": message},
        "isError": True,
    })


def _request_id(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PolicyError("request id must be a string or integer")
    if isinstance(value, str) and len(value) > 256:
        raise PolicyError("request id is too long")


class BrowserPolicy:
    """Validate client calls and sanitize the child's advertised surface."""

    def __init__(self, upload_paths: Sequence[str]):
        if len(upload_paths) != 2:
            raise PolicyError("exactly two upload PDFs are required")
        bindings = tuple(UploadBinding.create(path) for path in upload_paths)
        if bindings[0].path == bindings[1].path:
            raise PolicyError("upload PDFs must be distinct")
        self.bindings = bindings
        self.binding_by_path = {str(binding.path): binding for binding in bindings}
        self.tool_definitions = _tool_definitions(tuple(self.binding_by_path))
        self.tool_names = frozenset(tool["name"] for tool in self.tool_definitions)
        # What the agent actually reached for. An agent that touched the form
        # and an agent that never had a browser tool both end as "parked", and
        # only this tells them apart. It counts accepted calls, which is the
        # question being asked: whether the surface was there to use.
        self.calls: dict[str, int] = {}

    def validate_tool_call(self, name: Any, raw_arguments: Any) -> dict[str, Any]:
        if not isinstance(name, str) or name not in self.tool_names:
            raise PolicyError("browser tool is not allowed")
        arguments = _object(raw_arguments, "arguments")
        validator = getattr(self, f"_validate_{name}")
        validator(arguments)
        return arguments

    def _validate_browser_snapshot(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"target", "depth", "boxes"})
        if "target" in arguments:
            _string(arguments["target"], "target")
        if "depth" in arguments:
            _integer(arguments["depth"], "depth", 0, 50)
        if "boxes" in arguments:
            _boolean(arguments["boxes"], "boxes")

    def _validate_browser_find(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"text", "regex"})
        present = [name for name in ("text", "regex") if name in arguments]
        if len(present) != 1:
            raise PolicyError("find requires exactly one search value")
        _string(arguments[present[0]], present[0], maximum=1_000)

    def _validate_browser_click(self, arguments: dict[str, Any]) -> None:
        _element(arguments, {"doubleClick", "button", "modifiers"})
        if "doubleClick" in arguments:
            _boolean(arguments["doubleClick"], "doubleClick")
        if "button" in arguments and arguments["button"] not in {"left", "right", "middle"}:
            raise PolicyError("mouse button is not allowed")
        if "modifiers" in arguments:
            modifiers = arguments["modifiers"]
            allowed = {"Alt", "Control", "ControlOrMeta", "Meta", "Shift"}
            if (
                not isinstance(modifiers, list)
                or len(modifiers) > 5
                or len(set(modifiers)) != len(modifiers)
                or any(not isinstance(value, str) or value not in allowed for value in modifiers)
            ):
                raise PolicyError("click modifiers are invalid")

    def _validate_browser_hover(self, arguments: dict[str, Any]) -> None:
        _element(arguments)

    def _validate_browser_fill_form(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"fields"})
        fields = arguments.get("fields")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 100:
            raise PolicyError("fields must be a nonempty bounded array")
        for raw in fields:
            field = _object(raw, "field")
            _closed(field, {"name", "element", "target", "type", "value"})
            _string(field.get("name"), "field name", maximum=1_000)
            _string(field.get("target"), "field target")
            if "element" in field:
                _string(field["element"], "field element")
            if field.get("type") not in {
                "textbox", "checkbox", "radio", "combobox", "slider",
            }:
                raise PolicyError("field type is not allowed")
            _string(field.get("value"), "field value", maximum=MAX_TEXT_CHARS,
                    allow_empty=True)

    def _validate_browser_type(self, arguments: dict[str, Any]) -> None:
        _element(arguments, {"text", "slowly"})
        _string(arguments.get("text"), "text", maximum=MAX_TEXT_CHARS,
                allow_empty=True)
        if "slowly" in arguments:
            _boolean(arguments["slowly"], "slowly")

    def _validate_browser_press_key(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"key"})
        allowed = {
            "Tab", "Shift+Tab", "Space", "ArrowUp", "ArrowDown", "ArrowLeft",
            "ArrowRight", "Escape", "Backspace", "Delete", "Home", "End",
            "PageUp", "PageDown",
        }
        if arguments.get("key") not in allowed:
            raise PolicyError("key is not allowed")

    def _validate_browser_select_option(self, arguments: dict[str, Any]) -> None:
        _element(arguments, {"values"})
        values = arguments.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise PolicyError("option values must be a nonempty bounded array")
        for value in values:
            _string(value, "option value", allow_empty=True)

    def _validate_browser_handle_dialog(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"accept", "promptText"})
        _boolean(arguments.get("accept"), "accept")
        if "promptText" in arguments:
            _string(arguments["promptText"], "promptText", allow_empty=True)

    def _validate_browser_file_upload(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"paths"})
        self._validate_bound_paths(arguments.get("paths"))

    def _validate_browser_attach_file(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"target", "element", "paths"})
        if "target" in arguments:
            _string(arguments["target"], "target")
        if "element" in arguments:
            _string(arguments["element"], "element")
        self._validate_bound_paths(arguments.get("paths"))

    def _validate_bound_paths(self, paths: Any) -> None:
        if not isinstance(paths, list) or not 1 <= len(paths) <= 2:
            raise PolicyError("upload paths must be a nonempty bounded array")
        if any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths):
            raise PolicyError("upload paths are invalid")
        for path in paths:
            binding = self.binding_by_path.get(path)
            if binding is None:
                raise PolicyError("upload path is not bound to this application")
            binding.verify()

    def _validate_browser_tabs(self, arguments: dict[str, Any]) -> None:
        action = arguments.get("action")
        if action == "list":
            _closed(arguments, {"action"})
        elif action == "select":
            _closed(arguments, {"action", "index"})
            _integer(arguments.get("index"), "index", 0, 100)
        else:
            raise PolicyError("tab action is not allowed")

    def _validate_browser_wait_for(self, arguments: dict[str, Any]) -> None:
        _closed(arguments, {"time", "text", "textGone"})
        if not arguments:
            raise PolicyError("wait needs a bounded condition")
        if "time" in arguments:
            _number(arguments["time"], "time", 0, 30)
        if "text" in arguments:
            _string(arguments["text"], "text")
        if "textGone" in arguments:
            _string(arguments["textGone"], "textGone")

    def prepare_client_message(self, message: Any) -> ClientDecision:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            request_id = message.get("id") if isinstance(message, dict) else None
            return ClientDecision(response=_response(
                request_id, error={"code": -32600, "message": "Invalid Request"}
            ))
        if set(message) - {"jsonrpc", "id", "method", "params"}:
            return ClientDecision(response=_response(
                message.get("id"), error={"code": -32600, "message": "Invalid Request"}
            ))
        request_id = message.get("id")
        if "id" in message:
            try:
                _request_id(request_id)
            except PolicyError as exc:
                return ClientDecision(response=_response(
                    None, error={"code": -32600, "message": str(exc)}
                ))
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return ClientDecision(response=_response(
                request_id, error={"code": -32600, "message": "Invalid Request"}
            ))
        if method == "server/discover" and "id" in message:
            return ClientDecision(response=_response(request_id, _complete_result({
                "supportedVersions": [MODERN_PROTOCOL_VERSION],
                "capabilities": {"tools": {"listChanged": False}},
                "instructions": "Only fixed form and page tools are available.",
            }, modern=True, cacheable=True)))
        if "id" not in message:
            if method in {"notifications/initialized", "notifications/cancelled"}:
                return ClientDecision(forward=message)
            return ClientDecision()
        if method in {"initialize", "ping"}:
            return ClientDecision(forward=message, pending_method=method,
                                  modern=_modern_request(params))
        if method == "tools/list":
            if set(params) - {"cursor", "_meta"}:
                return ClientDecision(response=_response(
                    request_id, error={"code": -32602, "message": "Invalid params"}
                ))
            if "cursor" in params:
                try:
                    _string(params["cursor"], "cursor", maximum=1_000)
                except PolicyError:
                    return ClientDecision(response=_response(
                        request_id, error={"code": -32602, "message": "Invalid params"}
                    ))
            return ClientDecision(forward=message, pending_method=method,
                                  modern=_modern_request(params))
        if method == "tools/call":
            if set(params) - {"name", "arguments", "_meta"}:
                return ClientDecision(response=_tool_error(
                    request_id, "Browser tool arguments were rejected by the firewall."
                ))
            try:
                arguments = self.validate_tool_call(
                    params.get("name"), params.get("arguments", {})
                )
            except PolicyError:
                return ClientDecision(response=_tool_error(
                    request_id, "Browser tool call was rejected by the firewall."
                ))
            self.calls[str(params.get("name"))] = (
                self.calls.get(str(params.get("name")), 0) + 1
            )
            if params.get("name") == "browser_attach_file":
                message = self._as_attach_file_call(message, arguments)
            return ClientDecision(forward=message, pending_method=method,
                                  modern=_modern_request(params))
        return ClientDecision(response=_response(
            request_id, error={"code": -32601, "message": "Method not found"}
        ))

    def _as_attach_file_call(
        self, message: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Rewrite one validated attach into the upstream call that performs it.

        The arguments read here have already been through the validator, so the
        paths are the bound ones and nothing else survives _closed. Only the
        rewritten message reaches the child; the agent's own is discarded.
        """
        code = _attach_file_code(arguments["paths"], arguments.get("target"))
        rewritten = copy.deepcopy(message)
        rewritten["params"] = {
            "name": "browser_run_code_unsafe",
            "arguments": {"code": code},
        }
        return rewritten

    def filter_tools_result(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise ProtocolError("browser backend returned an invalid tool list")
        supported = {
            tool.get("name") for tool in result["tools"]
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        # browser_attach_file has no upstream of its own. It is carried by
        # browser_run_code_unsafe, so that is what its presence is judged on;
        # advertising it against a child that cannot perform it would hand the
        # agent a tool that fails at the form.
        if "browser_run_code_unsafe" in supported:
            supported.add("browser_attach_file")
        return {
            "tools": [
                copy.deepcopy(tool) for tool in self.tool_definitions
                if tool["name"] in supported
            ]
        }

    def filter_child_message(
        self,
        message: Any,
        pending_method: str | None,
        modern: bool = False,
    ) -> ChildDecision:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ProtocolError("browser backend returned invalid JSON-RPC")
        if isinstance(message.get("method"), str):
            if "id" not in message:
                return ChildDecision()
            return ChildDecision(to_child=_response(
                message.get("id"),
                error={"code": -32601, "message": "Client requests are disabled"},
            ))
        if pending_method is None or "id" not in message:
            return ChildDecision()
        has_result = "result" in message
        has_error = "error" in message
        if has_result == has_error:
            raise ProtocolError("browser backend returned an invalid response")
        if has_error:
            return ChildDecision(to_client=message)
        if pending_method == "initialize":
            result = message["result"]
            if not isinstance(result, dict):
                raise ProtocolError("browser backend returned an invalid initialize result")
            version = result.get("protocolVersion", "2025-11-25")
            if not isinstance(version, str) or len(version) > 64:
                version = "2025-11-25"
            return ChildDecision(to_client=_response(message["id"], {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": "Only fixed form and page tools are available.",
            }))
        if pending_method == "tools/list":
            return ChildDecision(to_client=_response(
                message["id"], _complete_result(
                    self.filter_tools_result(message["result"]),
                    modern=modern, cacheable=modern,
                )
            ))
        if modern and pending_method in {"tools/call", "ping"}:
            result = message["result"]
            if isinstance(result, dict):
                return ChildDecision(to_client=_response(
                    message["id"], _complete_result(result, modern=True)
                ))
        return ChildDecision(to_client=message)


def _json_loads(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError("invalid JSON line") from exc


def read_bounded_line(source: BinaryIO, maximum: int) -> tuple[bytes | None, bool]:
    """Read one bounded binary line and drain the remainder if it is oversized."""
    raw = source.readline(maximum + 1)
    if raw == b"":
        return None, False
    if len(raw) <= maximum:
        return raw, False
    while raw and not raw.endswith(b"\n"):
        raw = source.readline(maximum + 1)
    return b"", True


def _id_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            return


class StdioProxy:
    """Relay approved JSON-RPC requests between one client and one child."""

    def __init__(
        self,
        policy: BrowserPolicy,
        process: subprocess.Popen[bytes],
        source: BinaryIO,
        sink: BinaryIO,
    ):
        self.policy = policy
        self.process = process
        self.source = source
        self.sink = sink
        self.pending: dict[str, tuple[Any, str, bool]] = {}
        self.pending_lock = threading.Lock()
        self.client_lock = threading.Lock()
        self.child_lock = threading.Lock()
        self.stop = threading.Event()
        self.client_done = threading.Event()
        self.child_done = threading.Event()
        self.backend_failed = threading.Event()

    def _write_line(self, sink: BinaryIO, lock: threading.Lock, message: Any) -> bool:
        try:
            raw = json.dumps(
                message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            if len(raw) > MAX_CHILD_LINE_BYTES:
                return False
            with lock:
                sink.write(raw + b"\n")
                sink.flush()
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _write_client(self, message: dict[str, Any]) -> bool:
        return self._write_line(self.sink, self.client_lock, message)

    def _write_child(self, message: dict[str, Any]) -> bool:
        if self.process.stdin is None:
            return False
        return self._write_line(self.process.stdin, self.child_lock, message)

    def _fail_pending(self) -> None:
        with self.pending_lock:
            pending = list(self.pending.values())
            self.pending.clear()
        for request_id, _method, _modern in pending:
            self._write_client(_response(
                request_id,
                error={"code": -32000, "message": "Browser backend unavailable"},
            ))

    def _client_loop(self) -> None:
        try:
            while not self.stop.is_set():
                raw, oversized = read_bounded_line(self.source, MAX_CLIENT_LINE_BYTES)
                if raw is None:
                    break
                if oversized:
                    self._write_client(_response(
                        None, error={"code": -32700, "message": "Protocol line too large"}
                    ))
                    continue
                if not raw.strip():
                    continue
                try:
                    message = _json_loads(raw)
                except ProtocolError:
                    self._write_client(_response(
                        None, error={"code": -32700, "message": "Parse error"}
                    ))
                    continue
                decision = self.policy.prepare_client_message(message)
                if decision.response is not None:
                    self._write_client(decision.response)
                if decision.forward is None:
                    continue
                pending_key = None
                if decision.pending_method is not None:
                    request_id = decision.forward["id"]
                    pending_key = _id_key(request_id)
                    with self.pending_lock:
                        if pending_key in self.pending:
                            duplicate = True
                        else:
                            duplicate = False
                            self.pending[pending_key] = (
                                request_id, decision.pending_method, decision.modern
                            )
                    if duplicate:
                        self._write_client(_response(
                            request_id,
                            error={"code": -32600, "message": "Duplicate request id"},
                        ))
                        continue
                if not self._write_child(decision.forward):
                    if pending_key is not None:
                        with self.pending_lock:
                            self.pending.pop(pending_key, None)
                    self._write_client(_response(
                        decision.forward.get("id"),
                        error={"code": -32000, "message": "Browser backend unavailable"},
                    ))
                    self.backend_failed.set()
                    self.stop.set()
                    break
        finally:
            self.client_done.set()

    def _child_loop(self) -> None:
        try:
            if self.process.stdout is None:
                self.backend_failed.set()
                return
            while not self.stop.is_set():
                raw, oversized = read_bounded_line(
                    self.process.stdout, MAX_CHILD_LINE_BYTES
                )
                if raw is None:
                    break
                if oversized or not raw.strip():
                    self.backend_failed.set()
                    break
                try:
                    message = _json_loads(raw)
                except ProtocolError:
                    self.backend_failed.set()
                    break
                pending_method = None
                pending_modern = False
                pending_key = None
                if isinstance(message, dict) and "id" in message and "method" not in message:
                    try:
                        pending_key = _id_key(message["id"])
                    except (TypeError, ValueError):
                        pending_key = None
                    if pending_key is not None:
                        with self.pending_lock:
                            entry = self.pending.pop(pending_key, None)
                        if entry is not None:
                            pending_method = entry[1]
                            pending_modern = entry[2]
                try:
                    decision = self.policy.filter_child_message(
                        message, pending_method, pending_modern
                    )
                except ProtocolError:
                    self.backend_failed.set()
                    break
                if decision.to_child is not None and not self._write_child(decision.to_child):
                    self.backend_failed.set()
                    break
                if decision.to_client is not None and not self._write_client(decision.to_client):
                    self.stop.set()
                    break
        finally:
            if not self.client_done.is_set() and not self.stop.is_set():
                self.backend_failed.set()
            self.child_done.set()

    def run(self) -> int:
        client = threading.Thread(
            target=self._client_loop, name="jobby-browser-client", daemon=True
        )
        child = threading.Thread(
            target=self._child_loop, name="jobby-browser-child", daemon=True
        )
        client.start()
        child.start()
        child_input_closed = False
        while not self.child_done.is_set():
            if self.client_done.is_set() and not child_input_closed:
                if self.process.stdin is not None:
                    try:
                        with self.child_lock:
                            self.process.stdin.close()
                    except OSError:
                        pass
                child_input_closed = True
            time.sleep(0.02)
        self.stop.set()
        if self.backend_failed.is_set():
            self._fail_pending()
        child.join(timeout=2)
        if not self.client_done.is_set():
            client.join(timeout=0.1)
        try:
            returncode = self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _terminate(self.process)
            returncode = self.process.returncode if self.process.returncode is not None else 1
        if self.backend_failed.is_set() or returncode != 0:
            return 1
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a restricted firewall in front of Playwright MCP"
    )
    parser.add_argument(
        "--upload", action="append", required=True,
        help="One fixed PDF path. Supply this option exactly twice.",
    )
    parser.add_argument(
        "child", nargs=argparse.REMAINDER,
        help="Playwright MCP command and arguments after --",
    )
    parser.add_argument(
        "--activity",
        default="",
        help="write a per-tool call count here when the run ends",
    )
    return parser


BACKEND_STDERR_LINES = 40
BACKEND_STDERR_LINE_CHARS = 400


def _drain_backend_stderr(stream, sink: "deque[str]") -> None:
    try:
        for line in stream:
            sink.append(line.decode("utf-8", "replace").rstrip()[:BACKEND_STDERR_LINE_CHARS])
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _report_backend_stderr(lines: "deque[str]", exit_code: int | None) -> None:
    """Say what the backend said, on the one stream a failing run keeps."""
    kept = [line for line in lines if line]
    if not kept:
        print(
            "jobby browser firewall: the browser backend ended "
            f"(exit {exit_code}) and wrote nothing to stderr",
            file=sys.stderr,
        )
        return
    print(
        f"jobby browser firewall: the browser backend ended (exit {exit_code}); "
        f"its last {len(kept)} stderr line(s) follow",
        file=sys.stderr,
    )
    for line in kept:
        print(f"  backend: {line}", file=sys.stderr)


def _write_activity(path: str, policy: "BrowserPolicy", errors: "deque[str]",
                    exit_code: int | None) -> None:
    """Record what the agent reached for, for the runner that outlives it.

    A parked application looks identical whether the agent worked the form and
    hit a real blocker or never had a browser tool at all. The firewall is the
    only part of the run that sees every call, so it is what says which.
    Best-effort: a run must not fail because its own bookkeeping could not.
    """
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "calls": dict(sorted(policy.calls.items())),
            "totalCalls": sum(policy.calls.values()),
            "backendExit": exit_code,
            "backendStderr": [line for line in errors if line][-5:],
        }, separators=(",", ":"))
        descriptor = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except (OSError, TypeError, ValueError):
        pass


def main() -> int:
    args = build_parser().parse_args()
    child_argv = list(args.child)
    if child_argv and child_argv[0] == "--":
        child_argv = child_argv[1:]
    try:
        policy = BrowserPolicy(args.upload)
    except PolicyError as exc:
        print(f"jobby browser firewall: {exc}", file=sys.stderr)
        return 2
    if not child_argv or not child_argv[0]:
        print("jobby browser firewall: a child command is required", file=sys.stderr)
        return 2
    # The child's stderr used to go to DEVNULL. When the backend died the
    # client saw only "connection closed: initialize response" and the reason
    # was destroyed at the moment it was produced, which is why a browser
    # server that failed a real run on 2026-08-27 answered every hand probe
    # afterwards and was never reproduced. It is drained on a thread so a full
    # pipe can never block the backend, kept to the last few kilobytes, and
    # printed only if the run ends badly.
    try:
        process = subprocess.Popen(
            child_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        print("jobby browser firewall: browser backend could not start", file=sys.stderr)
        return 2
    backend_errors: deque[str] = deque(maxlen=BACKEND_STDERR_LINES)
    drain = threading.Thread(
        target=_drain_backend_stderr, args=(process.stderr, backend_errors), daemon=True
    )
    drain.start()
    try:
        code = StdioProxy(policy, process, sys.stdin.buffer, sys.stdout.buffer).run()
    finally:
        _terminate(process)
    drain.join(timeout=1)
    if code != 0 or process.poll() not in (0, None):
        _report_backend_stderr(backend_errors, process.poll())
    _write_activity(args.activity, policy, backend_errors, process.poll())
    return code


if __name__ == "__main__":
    sys.exit(main())
