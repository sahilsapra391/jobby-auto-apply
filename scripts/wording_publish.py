#!/usr/bin/env python3
"""Publish the resume wording to the owner-only editor's KV key.

**What it is for.** v2's resume wording editor is owner-only and must never
read the wording from Postgres, because `class_b._PROFILE_EXCLUDED` keeps it out
of there and every public page reads Postgres. The retired Python board carried
the wording inside its published board state; nothing publishes that any more.
So the device writes one dedicated KV key, `jobby:owner:resume-wording`
(`deploy/device_keys.mjs`), which exactly one route reads, behind `isOwner()`.

The payload is a projection, never a second home for the wording. The device's
`resume-content.json` (or the shipped template when that is absent) always
wins; edits from the editor come back through the `resumeEdits` queue and
`store.py resume-content-put`, and this then republishes what the store holds.

**When it runs.** `watch.refresh_owner_wording` spawns it on the watcher's
first tick after a start, whenever the modification stamp of any input moves
(the device's wording, the shipped wording, the HTML template, the profile
that supplies the contact block), and immediately after a wording edit is
accepted. `owner-wording.mjs` reads the key first and skips the write when the
digest matches, so a restart costs one KV read and no write.

    python3 scripts/wording_publish.py             publish if changed
    python3 scripts/wording_publish.py --dry-run   print the digest and sizes
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parent
DEPLOY = REPO / "deploy"
TEMPLATE_HTML = REPO / "templates" / "resume.html"
sys.path.insert(0, str(SCRIPT_DIR))

import store  # noqa: E402


def inputs(root):
    """The files whose content decides what this publishes, in a fixed order.

    `watch` stats these to decide when to run; `digest` hashes what they lead
    to. Listed once here so the two cannot disagree about what counts.
    """
    p = store.paths(root)
    return [Path(p["resumeContent"]), store.RESUME_TEMPLATE_CONTENT,
            TEMPLATE_HTML, Path(p["profile"])]


def render(root, content):
    """The base resume as HTML, so the editor shows the document itself.

    Rendered here, on the machine that owns the template, because no deployed
    bundle carries `templates/`. Returns "" when anything is missing, and the
    editor says so rather than drawing an empty page.
    """
    try:
        import resume_build
        if not TEMPLATE_HTML.exists() or not content:
            return ""
        name, contact = resume_build.identity_block(root=root)
        rendered = resume_build.render(TEMPLATE_HTML.read_text(encoding="utf-8"), content)
        return rendered.replace("{{name}}", name).replace("{{contact}}", contact)
    except (OSError, ValueError, KeyError, SystemExit, ImportError) as exc:
        # A resume that will not render is a broken preview, not a broken
        # wording. The reason goes to stderr rather than nowhere.
        sys.stderr.write(f"wording_publish: render failed: {exc!r}\n")
        return ""


def snapshot(root):
    """`{digest, content, render}` for what the store holds right now."""
    content = store.resume_content(root)
    html = render(root, content)
    digest = hashlib.sha256(
        json.dumps({"content": content, "render": html},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"digest": digest, "content": content, "render": html}


def publish(root=None, runner=subprocess.run, node=None, env=None):
    """Hand the snapshot to `owner-wording.mjs`. Returns its verdict, or raises.

    Raises `RuntimeError` naming the failure: the caller decides whether to
    retry, and a failure that became a quiet `False` would leave the editor
    showing an old wording with nothing saying why.
    """
    import toolpath
    root = store.root_dir(root)
    snap = snapshot(root)
    if not snap["content"]:
        raise RuntimeError("there is no resume wording to publish")
    fd, temporary = tempfile.mkstemp(prefix="jobby-wording-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(snap, stream)
        result = runner(
            [node or toolpath.node(), "owner-wording.mjs", "write", temporary],
            cwd=DEPLOY, env=env if env is not None else toolpath.queue_env(),
            capture_output=True, text=True, timeout=60)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("owner-wording.mjs failed: "
                           + ((result.stderr or "").strip()[:300] or f"exit {result.returncode}"))
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("owner-wording.mjs printed something that is not JSON") from exc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the digest and sizes; write nothing")
    args = ap.parse_args(argv)
    if args.dry_run:
        snap = snapshot(store.root_dir(args.root))
        print(json.dumps({"digest": snap["digest"],
                          "contentBytes": len(json.dumps(snap["content"])),
                          "renderBytes": len(snap["render"])}))
        return 0
    try:
        print(json.dumps(publish(args.root)))
    except RuntimeError as exc:
        sys.stderr.write(f"wording_publish: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
