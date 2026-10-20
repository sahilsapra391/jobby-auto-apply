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
import signal
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


def doc_index_from_manifest(root=None, artifacts=()):
    """filename -> key, from what R2 holds rather than from what disk holds.

    This is what makes local eviction safe. The index used to be derived from
    the tree the collector walks, so deleting a local file after uploading it
    would have dropped its entry on the next publish and turned every board
    link to that document into a 404 -- the artifact safely in R2 and
    unreachable, which is the worst of both.

    The manifest records every object this machine has put in the bucket and
    survives the local file being removed, so it is the honest source. Anything
    on disk that has not been uploaded yet is merged in on top, because it will
    be uploaded moments later in the same publish and the index has to describe
    the state after that, not before.
    """
    manifest = load_manifest(root)["objects"]
    entries = {}
    for key, record in manifest.items():
        name = record.get("name")
        if not name:
            continue
        entries.setdefault(name, []).append({
            "key": key, "sha256": record.get("sha256", ""),
            "bytes": record.get("bytes", 0),
            "contentType": record.get("contentType") or "application/pdf",
        })
    for artifact in artifacts:
        entries.setdefault(artifact["name"], [])
        if not any(e["key"] == artifact["key"] for e in entries[artifact["name"]]):
            entries[artifact["name"]].append({
                "key": artifact["key"], "sha256": artifact["sha256"],
                "bytes": artifact["bytes"], "contentType": artifact["contentType"]})

    # Same rule as before: a name claimed by two objects is left out rather
    # than resolved arbitrarily. posting.txt exists in a dozen company folders.
    index = {name: values[0] for name, values in entries.items() if len(values) == 1}
    ambiguous = sorted(name for name, values in entries.items() if len(values) > 1)
    return index, ambiguous


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
    # From the manifest, not from the tree. A file evicted after upload is gone
    # from `artifacts` but still in R2, and its board link has to keep working.
    index, ambiguous = doc_index_from_manifest(root, artifacts)
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

            # The preference has to outlive the local tree, or eviction undoes
            # it. answers-<id>.json and questions-<id>.json both claim
            # app/<id>/answers.json, and r2_artifacts picks the record. Evict
            # the record -- correctly, it is safely in R2 -- and the question
            # list is the only claimant left, so the next sync sees a changed
            # sha for that key and writes a bare array over the record.
            #
            # Both operations were individually correct and their composition
            # destroyed content in R2. It happened: two answer records were
            # overwritten, 2,655 bytes to 838 and 1,118 to 136, found by
            # jobby-auto-apply-a7 sampling the live bucket rather than by
            # anything here. #96 made the preference deliberate within one
            # scan; this makes it durable across scans, because the manifest is
            # the only memory of the winner once the file is gone.
            incumbent = (entry or {}).get("name")
            if incumbent and incumbent != artifact["name"] \
                    and r2_artifacts._rank(artifact["name"]) \
                        > r2_artifacts._rank(incumbent):
                result["refusedDowngrade"] = result.get("refusedDowngrade", 0) + 1
                result.setdefault("downgrades", []).append(
                    {"key": key, "stored": incumbent, "refused": artifact["name"]})
                continue

            if entry and entry.get("sha256") == digest:
                result["skippedByManifest"] += 1
                # The record is the index's source now, so a field added after
                # it was written has to be filled in on the way past. Without
                # this, every object uploaded before contentType existed serves
                # as application/pdf forever, including the JSON and the HTML.
                if entry.get("contentType") != artifact["contentType"]:
                    entry["contentType"] = artifact["contentType"]
                    entry["name"] = artifact["name"]
                    result["refreshed"] = result.get("refreshed", 0) + 1
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
                          "name": artifact["name"], "uploadedAt": stamp,
                          "contentType": artifact["contentType"]}
    finally:
        # Persist whatever did land, even on the way out of a failure, so a
        # retry resumes instead of re-uploading everything that already worked.
        manifest["bucket"] = client.bucket
        manifest["userId"] = user_id
        manifest["objects"] = known
        save_manifest(manifest, root)
    return result


def terminal_applications(root=None):
    """Application ids whose story is over: submitted, abandoned, or dropped.

    Only these may have their local files removed. A live application still
    needs its PDFs on disk, because the form agent uploads them to the
    employer by path and the browser firewall pins that path by device, inode,
    size and sha256. R2 cannot be the source for a file being handed to a
    file-chooser, so eviction waits for the run to finish.
    """
    latest, dropped = {}, set()
    for event in r2_artifacts._store(root, "history-list") or []:
        aid = event.get("applicationId")
        if aid and event.get("at"):
            latest[aid] = max(latest.get(aid, ("", "")), (event["at"], event.get("event") or ""))
    for row in r2_artifacts._store(root, "session-list") or []:
        aid = row.get("applicationId")
        if aid and (row.get("status") or "") == "dropped":
            dropped.add(aid)
    done = {aid for aid, (_at, event) in latest.items()
            if event in ("completed", "abandoned")}
    return done | dropped


def evict(root=None, user_id=r2.DEFAULT_USER_ID, apps_dir=None, apply=False,
          client=None, plan=None, deep=True):
    """Delete local artifacts that R2 verifiably holds, for finished applications.

    Three conditions before a byte is removed, because the whole point is that
    the artifact survives:

      1. the application has reached a terminal outcome
      2. the object exists in R2 and its content matches the local file, checked
         by re-downloading and hashing rather than by trusting the manifest
      3. the filename resolves through the index, so the board can still serve it

    Anything that fails one of the three is kept and reported. There is no force
    flag: an artifact nobody can prove is in R2 stays on the disk that has it.
    """
    plan = plan or r2_artifacts.collect(root, user_id, apps_dir)
    client = client or r2.client(user_id=user_id)
    finished = terminal_applications(root)
    index, _ambiguous = doc_index_from_manifest(root, plan["artifacts"])

    # A company folder is settled when every application recorded against it has
    # finished. The unattributable files in it -- the working drafts and hand-made
    # documents from before document_contract owned the naming -- belong to those
    # applications even though nothing says which, so a settled folder is the
    # honest test for them. A folder with live work in it keeps everything,
    # because a draft there might still be an input to something running.
    live_companies = set()
    for aid, entry in r2_artifacts.load_applications(root).items():
        if aid not in finished and entry.get("company"):
            live_companies.add(r2_artifacts._company_segment(entry["company"]).lower())

    removable, kept, freed = [], [], 0
    for artifact in plan["artifacts"]:
        aid = artifact["applicationId"]
        reason = None
        if not aid and artifact["company"].lower() in live_companies:
            reason = "unattributed, and its company still has live work"
        elif not aid and artifact["company"].lower() not in live_companies:
            reason = None            # settled folder: safe, falls through to R2 checks
        elif aid not in finished:
            reason = "the application has not finished"
        # The index check guards against turning a board link into a dead link,
        # so it only applies to names the board can actually resolve. A name the
        # index does not carry is one it never served -- the ambiguous ones,
        # posting.txt in a dozen company folders -- and those are reachable in
        # R2 by key whether or not a copy sits here.
        listed = index.get(artifact["name"])
        if reason is None and listed and listed.get("key") != artifact["key"]:
            reason = "the board resolves this name to a different object"
        elif reason is None:
            head = client.head(artifact["key"])
            if head is None:
                reason = "no object in R2"
            elif head.get("bytes") != artifact["bytes"]:
                reason = "R2 holds a different size"
            elif deep:
                body = client.get(artifact["key"])
                if body is None:
                    reason = "vanished between HEAD and GET"
                elif hashlib.sha256(body).hexdigest() != artifact["sha256"]:
                    reason = "R2 content does not match the local file"
        if reason:
            kept.append({"path": artifact["path"], "why": reason})
        else:
            removable.append(artifact)
            freed += artifact["bytes"]

    removed = 0
    if apply:
        for artifact in removable:
            try:
                Path(artifact["path"]).unlink()
                removed += 1
            except OSError as exc:
                kept.append({"path": artifact["path"], "why": f"could not remove: {exc}"})
    return {
        "onDisk": len(plan["artifacts"]),
        "terminalApplications": len(finished),
        "removable": len(removable), "removed": removed,
        "bytesFreed": freed if apply else 0,
        "bytesRemovable": freed,
        "kept": len(kept),
        "keptReasons": _reason_counts(kept),
        "applied": bool(apply),
    }


def _reason_counts(kept):
    counts = {}
    for item in kept:
        counts[item["why"]] = counts.get(item["why"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _restore_object(client, key, expected_sha, name, destination):
    """Fetch one stored object, verify its stamped hash, and write it whole.

    Verifies the hash it was stored with before writing, and writes through a
    temporary file in the destination directory so a partial download can never
    be handed to a file chooser as a complete one.
    """
    body = client.get(key)
    if body is None:
        raise r2.R2Error(f"{name!r} is indexed at {key} but R2 has no object there")
    digest = hashlib.sha256(body).hexdigest()
    if expected_sha and digest != expected_sha:
        raise r2.R2Error(f"{name!r} came back with a different hash than it was stored with")
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(body)
        temp = Path(handle.name)
    temp.chmod(0o600)
    os.replace(temp, destination)
    return {"name": name, "key": key, "bytes": len(body),
            "sha256": digest, "path": str(destination)}


def materialise(name, destination, root=None, user_id=r2.DEFAULT_USER_ID,
                client=None):
    """Bring an evicted artifact back to disk, by the filename it had.

    Eviction is only safe if the file can come back, and one thing genuinely
    needs it back: the form agent uploads a PDF to the employer by path, and the
    browser firewall pins that path by device, inode, size and sha256. A retry
    on an application whose documents were evicted has to stage them again.
    """
    index, _ambiguous = doc_index_from_manifest(root)
    entry = index.get(name)
    if not entry:
        raise r2.R2Error(f"no stored artifact is named {name!r}")
    client = client or r2.client(user_id=user_id)
    return _restore_object(client, entry["key"], entry.get("sha256"), name,
                           destination)


def materialise_application(application_id, folder, root=None,
                            user_id=r2.DEFAULT_USER_ID, client=None):
    """Bring back every artifact R2 attributes to one application.

    The retry path is why this exists as more than --get. Eviction removes
    local artifacts once an application reaches a terminal outcome, and
    drop.py --restore makes that outcome reversible, so a retried application
    can reach the runner with its documents safely in R2 and nothing on disk.
    The runner used to refuse with "the tailored resume and cover letter are
    not both ready" and consume the click, observed on 6c3824319c68c547 on
    2026-08-28. Restoring is bounded by the manifest's attribution for this
    application id, keyed under u/{user}/app/{id}/, verifies each stored hash,
    and leaves files already on disk alone.
    """
    manifest = load_manifest(root)["objects"]
    prefix = f"u/{user_id}/app/{application_id}/"
    wanted = [(key, record) for key, record in sorted(manifest.items())
              if key.startswith(prefix) and (record or {}).get("name")]
    if not wanted:
        return []
    client = client or r2.client(user_id=user_id)
    folder = Path(folder).expanduser()
    restored = []
    for key, record in wanted:
        name = record["name"]
        destination = folder / name
        if destination.is_file():
            continue
        restored.append(_restore_object(client, key, record.get("sha256"),
                                        name, destination))
    return restored


def push_application(application_id, root=None, user_id=r2.DEFAULT_USER_ID,
                     apps_dir=None, client=None):
    """Put one application's artifacts in R2 now, rather than at the next publish.

    Publishing is the batch path and it runs minutes to hours after a document
    is rendered. In that window the only copy is the local one, which is exactly
    the state this work exists to end. Called when documents are installed and
    again when an application finishes, so the gap is seconds rather than a
    publish cycle.
    """
    plan = r2_artifacts.collect(root, user_id, apps_dir)
    mine = [a for a in plan["artifacts"] if a["applicationId"] == application_id]
    if not mine:
        return {"applicationId": application_id, "objects": 0, "uploaded": 0}
    result = sync(root, user_id, apps_dir, apply=True, client=client,
                  plan={"artifacts": mine, "skipped": [], "root": plan["root"]})
    return {"applicationId": application_id, "objects": len(mine),
            "uploaded": result["uploaded"], "matchedRemote": result["matchedRemote"],
            "skippedByManifest": result["skippedByManifest"]}


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


class DeadlineExceeded(Exception):
    """`--deadline` ran out. Not a RuntimeError, so it is reported as itself."""


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
    ap.add_argument("--evict", action="store_true",
                    help="remove local artifacts R2 verifiably holds, for finished "
                         "applications only. Dry run without --apply")
    ap.add_argument("--push", metavar="APPLICATION_ID",
                    help="upload one application's artifacts now")
    ap.add_argument("--get", nargs=2, metavar=("NAME", "DEST"),
                    help="bring one evicted artifact back to disk by filename")
    ap.add_argument("--deadline", type=int, metavar="SECONDS",
                    help="give up after this long and exit 3 saying so. The "
                         "watcher's periodic sweep sets it, because it never "
                         "waits on the child and a hung upload would otherwise "
                         "hold the sweep's lock forever")
    args = ap.parse_args(argv)

    if args.deadline:
        def _out_of_time(signum, frame):
            raise DeadlineExceeded(f"gave up after {args.deadline}s")
        signal.signal(signal.SIGALRM, _out_of_time)
        signal.alarm(args.deadline)

    try:
        plan = r2_artifacts.collect(args.root, args.user, args.apps)
        if args.index:
            index, ambiguous = doc_index(plan["artifacts"])
            print(json.dumps({"entries": len(index), "ambiguous": ambiguous,
                              "index": index}, indent=2, sort_keys=True))
            return 0
        if args.get:
            print(json.dumps(materialise(args.get[0], args.get[1], args.root,
                                         args.user), indent=2, sort_keys=True))
            return 0
        if args.push:
            print(json.dumps(push_application(args.push, args.root, args.user,
                                              args.apps), indent=2, sort_keys=True))
            return 0
        if args.evict:
            out = evict(args.root, args.user, args.apps, apply=args.apply, plan=plan)
            print(json.dumps(out, indent=2, sort_keys=True))
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
    except DeadlineExceeded as exc:
        # Uploads already made are kept: `sync` saves the manifest in a
        # `finally`, so the next sweep starts where this one stopped.
        print(f"r2-sync: {exc}", file=sys.stderr)
        return 3
    except (r2.R2Error, RuntimeError) as exc:
        print(f"r2-sync: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.deadline:
            signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
