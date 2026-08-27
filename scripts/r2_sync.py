#!/usr/bin/env python3
"""Push Class 1 artifacts to R2, and prove afterwards that they arrived.

The backfill and the steady state are the same code on purpose. A migration
that maps a file to one key and a publish that maps it to another is a
migration that never finishes, and the second one wins silently.

  python3 scripts/r2_sync.py                  # dry run: what would move, and how much
  python3 scripts/r2_sync.py --apply          # upload what has changed
  python3 scripts/r2_sync.py --verify         # re-download every object and re-hash it
  python3 scripts/r2_sync.py --verify --quick # size, stamped hash and ETag, no bodies

Idempotency has three layers, cheapest first, because R2 prices operations and
a publish runs dozens of times a day:

  1. a local manifest of what this machine has already uploaded, so an unchanged
     tree costs zero operations
  2. a HEAD when the manifest does not know, so a re-run after a crash matches
     what is already there instead of writing it again
  3. the object's own x-amz-meta-sha256, so "same content" is a comparison and
     not an assumption

An upload that fails raises. The local file stays authoritative and the caller
is expected to stop rather than carry on with a board that links to nothing.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r2  # noqa: E402
import r2_artifacts  # noqa: E402

MANIFEST_NAME = "r2-manifest.json"
MANIFEST_VERSION = 1


def manifest_path(root=None):
    base = Path(root).expanduser() if root else Path(
        os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()
    return base / MANIFEST_NAME


def load_manifest(root=None):
    path = manifest_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"schemaVersion": MANIFEST_VERSION, "objects": {}}
    if data.get("schemaVersion") != MANIFEST_VERSION or not isinstance(data.get("objects"), dict):
        return {"schemaVersion": MANIFEST_VERSION, "objects": {}}
    return data


def save_manifest(data, root=None):
    """Owner-only, and written through a temp file in the same directory.

    Sharing one temp filename across writers is how a half-written store once
    got moved into place and published a board with an empty Submitted tab, so
    the name is unique per writer here.
    """
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False,
                                     encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        temp = Path(handle.name)
    temp.chmod(0o600)
    os.replace(temp, path)
    return path


def doc_index(artifacts):
    """filename -> {key, sha256, bytes, contentType}, for the serving route.

    The board links to documents by filename and always has, so the index is
    what lets an R2 layout keyed by application id answer those links without
    republishing every board anyone has open.

    A filename that occurs twice in the tree is left out rather than resolved
    arbitrarily. ``posting.txt`` exists in a dozen company folders and means a
    different posting in each; picking one would serve one company's document
    under another company's link. The board's own links carry company and role
    in the filename and do not collide.
    """
    counts = {}
    for artifact in artifacts:
        counts[artifact["name"]] = counts.get(artifact["name"], 0) + 1
    index, ambiguous = {}, sorted(n for n, c in counts.items() if c > 1)
    for artifact in artifacts:
        if counts[artifact["name"]] == 1:
            index[artifact["name"]] = {
                "key": artifact["key"],
                "sha256": artifact["sha256"],
                "bytes": artifact["bytes"],
                "contentType": artifact["contentType"],
            }
    return index, ambiguous


def sync(root=None, user_id=r2.DEFAULT_USER_ID, apps_dir=None, apply=False,
         client=None, plan=None, progress=None):
    """Upload every artifact whose content R2 does not already hold.

    Returns counts and the doc index, whether or not anything was uploaded, so
    a dry run reports exactly what an apply would do.
    """
    plan = plan or r2_artifacts.collect(root, user_id, apps_dir)
    artifacts = plan["artifacts"]
    index, ambiguous = doc_index(artifacts)
    result = {
        "objects": len(artifacts),
        "bytes": sum(a["bytes"] for a in artifacts),
        "uploaded": 0, "uploadedBytes": 0,
        "skippedByManifest": 0, "matchedRemote": 0,
        "ambiguousNames": ambiguous,
        "applied": bool(apply),
        "index": index,
    }
    if not apply:
        manifest = load_manifest(root)
        known = manifest["objects"]
        for artifact in artifacts:
            entry = known.get(artifact["key"])
            if entry and entry.get("sha256") == artifact["sha256"]:
                result["skippedByManifest"] += 1
            else:
                result["uploaded"] += 1
                result["uploadedBytes"] += artifact["bytes"]
        result["wouldUpload"] = result.pop("uploaded")
        result["wouldUploadBytes"] = result.pop("uploadedBytes")
        return result

    client = client or r2.client(user_id=user_id)
    manifest = load_manifest(root)
    known = manifest["objects"]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        for artifact in artifacts:
            key, digest = artifact["key"], artifact["sha256"]
            entry = known.get(key)
            if entry and entry.get("sha256") == digest:
                result["skippedByManifest"] += 1
                continue
            # The manifest does not know this one. Ask R2 before writing: a run
            # that died halfway through, or a second machine, may already have
            # put exactly these bytes there, and a PUT is the expensive class.
            head = client.head(key)
            if head and head.get("sha256") == digest:
                result["matchedRemote"] += 1
            else:
                body = Path(artifact["path"]).read_bytes()
                if hashlib.sha256(body).hexdigest() != digest:
                    # The file changed between the plan and the read. Hash what
                    # is actually going up rather than what we expected.
                    digest = hashlib.sha256(body).hexdigest()
                client.put(key, body, artifact["contentType"], digest)
                result["uploaded"] += 1
                result["uploadedBytes"] += len(body)
                if progress:
                    progress(artifact, result)
            known[key] = {"sha256": digest, "bytes": artifact["bytes"],
                          "name": artifact["name"], "uploadedAt": stamp}
    finally:
        # Persist whatever did land, even on the way out of a failure, so a
        # retry resumes instead of re-uploading everything that already worked.
        manifest["bucket"] = client.bucket
        manifest["userId"] = user_id
        manifest["objects"] = known
        save_manifest(manifest, root)
    return result


def verify(root=None, user_id=r2.DEFAULT_USER_ID, apps_dir=None, deep=True,
           client=None, plan=None):
    """Compare every object in R2 against the local file it came from.

    Deep re-downloads the body and re-hashes it, which is the only check that
    catches a corrupted object: the stamped metadata survives a bad body, so a
    HEAD-only pass would report a corruption as healthy. Quick compares size,
    the stamped hash and the ETag against the local MD5, which R2 sets to the
    body's MD5 for a single-part upload, so it catches corruption too and costs
    no download.
    """
    plan = plan or r2_artifacts.collect(root, user_id, apps_dir)
    client = client or r2.client(user_id=user_id)
    checked = 0
    problems = []
    for artifact in plan["artifacts"]:
        key = artifact["key"]
        head = client.head(key)
        if head is None:
            problems.append({"key": key, "problem": "missing from R2",
                             "path": artifact["path"]})
            continue
        checked += 1
        if head["bytes"] != artifact["bytes"]:
            problems.append({"key": key, "problem": "size differs",
                             "remote": head["bytes"], "local": artifact["bytes"]})
            continue
        if head.get("sha256") and head["sha256"] != artifact["sha256"]:
            problems.append({"key": key, "problem": "stamped sha256 differs",
                             "remote": head["sha256"], "local": artifact["sha256"]})
            continue
        if deep:
            body = client.get(key)
            if body is None:
                problems.append({"key": key, "problem": "vanished between HEAD and GET"})
                continue
            actual = hashlib.sha256(body).hexdigest()
            if actual != artifact["sha256"]:
                problems.append({"key": key, "problem": "body sha256 differs",
                                 "remote": actual, "local": artifact["sha256"]})
        else:
            etag = (head.get("etag") or "").lower()
            # Multipart uploads carry a -N suffix and are not a plain MD5.
            if etag and "-" not in etag:
                local_md5 = hashlib.md5(Path(artifact["path"]).read_bytes()).hexdigest()
                if etag != local_md5:
                    problems.append({"key": key, "problem": "ETag differs from local MD5",
                                     "remote": etag, "local": local_md5})
    return {
        "objects": len(plan["artifacts"]),
        "present": checked,
        "missing": sum(1 for p in problems if p["problem"] == "missing from R2"),
        "mismatched": sum(1 for p in problems if p["problem"] != "missing from R2"),
        "deep": bool(deep),
        "ok": not problems,
        "problems": problems[:50],
    }


def prune(root=None, user_id=r2.DEFAULT_USER_ID, apps_dir=None, apply=False,
          client=None, plan=None):
    """Delete objects under the user prefix that no local artifact maps to.

    Needed because a mapping rule can change: broadening the sidecar pattern to
    accept slug application ids moved thirteen job descriptions out of unfiled/
    and into their application, and without this the old keys would sit there
    forever, still served by any index that remembered them.

    One list call, then deletes, which R2 charges nothing for. Never runs from
    the publish path: it is the only operation here that could remove
    something, so it is a decision rather than a side effect.
    """
    plan = plan or r2_artifacts.collect(root, user_id, apps_dir)
    client = client or r2.client(user_id=user_id)
    wanted = {a["key"] for a in plan["artifacts"]}
    prefix = f"u/{user_id}/"
    present = client.list_keys(prefix)
    orphans = sorted(k for k in present if k not in wanted)
    if apply:
        manifest = load_manifest(root)
        for key in orphans:
            client.delete(key)
            manifest["objects"].pop(key, None)
        save_manifest(manifest, root)
    return {"present": len(present), "wanted": len(wanted),
            "orphans": len(orphans), "deleted": len(orphans) if apply else 0,
            "applied": bool(apply), "keys": orphans[:50]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync Class 1 artifacts to Cloudflare R2.")
    ap.add_argument("--apply", action="store_true", help="actually upload (default is a dry run)")
    ap.add_argument("--verify", action="store_true", help="check what is in R2 against local files")
    ap.add_argument("--quick", action="store_true", help="verify by size, stamped hash and ETag only")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    ap.add_argument("--apps", help="applications directory (default the one beside this checkout)")
    ap.add_argument("--user", default=r2.DEFAULT_USER_ID)
    ap.add_argument("--index", action="store_true", help="print the filename index and stop")
    ap.add_argument("--prune", action="store_true",
                    help="delete objects under the prefix no local artifact maps to")
    args = ap.parse_args(argv)

    try:
        plan = r2_artifacts.collect(args.root, args.user, args.apps)
        if args.index:
            index, ambiguous = doc_index(plan["artifacts"])
            print(json.dumps({"entries": len(index), "ambiguous": ambiguous,
                              "index": index}, indent=2, sort_keys=True))
            return 0
        if args.prune:
            out = prune(args.root, args.user, args.apps, apply=args.apply, plan=plan)
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0
        if args.verify:
            out = verify(args.root, args.user, args.apps, deep=not args.quick, plan=plan)
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0 if out["ok"] else 1
        out = sync(args.root, args.user, args.apps, apply=args.apply, plan=plan)
        out.pop("index", None)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except (r2.R2Error, RuntimeError) as exc:
        print(f"r2-sync: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
