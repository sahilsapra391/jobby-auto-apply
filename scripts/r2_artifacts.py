#!/usr/bin/env python3
"""Decide which local files are Class 1 artifacts and what their R2 key is.

One collector, used by both the backfill and by every publish, because a
migration that maps a file to one key and the steady state that maps it to
another is a migration that never finishes.

The layout is the one docs/plans/multi-tenant-architecture.md section 8 fixes:

    u/{userId}/app/{applicationId}/{kind}.{ext}

so that deleting a user is one prefix sweep and multi-tenant lands on this
layout with a different id rather than a migration. Anything that cannot be
tied to an application id keeps its company folder and its filename under

    u/{userId}/unfiled/{company}/{filename}

which is deliberately not a guess. Measured on 2026-08-25 against the real
tree: 216 of 253 PDFs, 26 of 39 job descriptions and 7 answer records carry an
application id. The other 346 files are working drafts and hand-made documents
from before document_contract owned the naming, and filing them under an
invented id would be worse than filing them under the company they belong to.
A stranger's application id on someone's resume is the same mistake as a
stranger's logo on their card.

Attribution comes from four sources, most trustworthy first:

  1. the filename the store itself recorded on a session or a history event
  2. document_contract.output_names for the application's company and role,
     which is what every document generated since that contract landed is called
  3. file_docs.plan stems, which is what the generation before it was called
  4. the application id embedded in jd-<id>.txt, questions-<id>.json,
     answers-<id>.json and confirm-<id>.png

Nothing here reads the network. Hashing 31MB of local files takes about a
tenth of a second, which is why the publish path can afford to do it every time.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import document_contract  # noqa: E402
import file_docs  # noqa: E402
import r2  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


# Suffix -> what to serve it as. A file with any other suffix is not an
# artifact; it is something else that happens to be in the folder.
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# The four generated documents, plus the three records that ride with them.
KIND_BY_CONTRACT_KEY = {
    "resumePdf": ("resume", ".pdf"),
    "resumeHtml": ("resume", ".html"),
    "coverPdf": ("cover", ".pdf"),
    "coverHtml": ("cover", ".html"),
}

# The id in the filename is matched loosely and then checked against the store,
# rather than matched against an id shape. Only 37 of 187 application ids on
# this machine are the 16-hex kind; the rest are slugs like
# "acme-ai-agent-product-manager", and a hex-shaped pattern filed 13 of the
# 39 job descriptions in the tree as unattributable. Membership in the store is
# the guard, so a loose pattern cannot invent an application.
SIDECARS = (
    (re.compile(r"^jd-(.+)\.txt$"), "jd", ".txt"),
    (re.compile(r"^questions-(.+)\.json$"), "answers", ".json"),
    (re.compile(r"^answers-(.+)\.json$"), "answers", ".json"),
    (re.compile(r"^confirm(?:ation)?-(.+)\.(png|jpg|jpeg)$"), "confirmation", None),
)

SKIP_NAMES = {".DS_Store", ".gitignore", ".gitkeep"}


def applications_dir(root=None):
    """Where tailored documents live, following a redirected store."""
    redirect = root or os.environ.get("JOB_APPLICATION_DIR")
    if redirect:
        return Path(redirect).expanduser() / "applications"
    return REPO / "applications"


def _store(root, *args):
    cmd = [sys.executable, str(REPO / "scripts" / "store.py")]
    if root:
        cmd += ["--root", str(root)]
    proc = subprocess.run(cmd + list(args), capture_output=True, text=True)
    if proc.returncode != 0:
        # An empty default here is how a crashed subprocess becomes a
        # convincing "there are no applications", which is item 8 in the
        # system reference's list of failures already paid for.
        raise RuntimeError(
            f"store.py {args[0]} failed: {(proc.stderr or '').strip()[:200]}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"store.py {args[0]} returned unparseable output") from exc


# Which store field names which document. Kept as a mapping rather than read
# off the filename, because "C.pdf" says nothing and the store already knows.
RECORDED_FIELDS = {"resumeVariant": "resume", "coverLetter": "cover"}


def load_applications(root=None):
    """applicationId -> {company, role, files}. Sessions first, then history.

    ``files`` is {kind: [filename]}, not a flat list. An earlier version kept a
    flat list and recovered the kind by looking for "CoverLetter" in the name,
    which files every document an employer received under whichever name sorts
    first when the naming is anything else.
    """
    out = {}

    def record(entry, source):
        for field, kind in RECORDED_FIELDS.items():
            value = source.get(field)
            if value and str(value).endswith(".pdf"):
                entry["files"].setdefault(kind, []).append(PurePosixPath(str(value)).name)

    for row in _store(root, "session-list") or []:
        aid = row.get("applicationId")
        if not aid:
            continue
        detail = _store(root, "session-load", "--id", aid) or {}
        entry = out.setdefault(aid, {"company": detail.get("company"),
                                     "role": detail.get("role"), "files": {}})
        record(entry, detail)
    for event in _store(root, "history-list") or []:
        aid = event.get("applicationId")
        if not aid:
            continue
        entry = out.setdefault(aid, {"company": event.get("company"),
                                     "role": event.get("role"), "files": {}})
        record(entry, event)
        entry["company"] = entry.get("company") or event.get("company")
        entry["role"] = entry.get("role") or event.get("role")
    return out


def name_map(applications, profile):
    """filename -> (applicationId, kind, ext), by the four rules in the docstring."""
    mapping = {}

    def claim(name, aid, kind, ext):
        if name and name not in mapping:
            mapping[name] = (aid, kind, ext)

    # 1. What the store recorded. A fact rather than a derivation, so it wins,
    #    and the kind comes from the field it was recorded under.
    for aid, entry in sorted(applications.items()):
        for kind, names in sorted((entry.get("files") or {}).items()):
            for name in names:
                claim(name, aid, kind, ".pdf")

    # 2. The naming contract in force today, for all four documents.
    for aid, entry in sorted(applications.items()):
        if not entry.get("company"):
            continue
        # **Both generations, and the older one is not dead weight.** The
        # contract gained six characters of the application id on 2026-09-03
        # because company and role alone collided: three applications to one
        # employer, for three different roles, all claimed one filename. Everything written
        # before that day is still on disk and in KV under the untagged name,
        # so dropping it here would unattribute documents that resolve today.
        for tagged in (aid, None):
            names = document_contract.output_names(
                profile, entry["company"], entry.get("role"), tagged)
            for contract_key, (kind, ext) in KIND_BY_CONTRACT_KEY.items():
                claim(names[contract_key], aid, kind, ext)

    # 3. The naming the generation before it used, for the two PDFs.
    stems = file_docs.plan({aid: (e.get("company"), e.get("role"))
                            for aid, e in applications.items()},
                           file_docs.person_stem(profile))
    for aid, (_company, stem) in sorted(stems.items()):
        claim(f"{stem}_Resume.pdf", aid, "resume", ".pdf")
        claim(f"{stem}_CoverLetter.pdf", aid, "cover", ".pdf")

    return mapping


def _company_segment(value):
    """A company folder name, safe to be one key segment.

    R2 keys are UTF-8 and tolerate almost anything, but a slash would invent a
    directory level and a control character would make the key unspeakable.
    """
    cleaned = "".join(ch for ch in str(value or "") if ch.isprintable() and ch != "/")
    return cleaned.strip().strip(".") or "Unknown"


# Which filename wins when two claim one key, lowest first. A captured record
# beats the bare question list it was salvaged from, whatever they sort like.
FILENAME_PREFERENCE = ("answers-", "questions-", "confirmation-", "confirm-")


def _rank(name):
    for position, prefix in enumerate(FILENAME_PREFERENCE):
        if name.startswith(prefix):
            return position
    return len(FILENAME_PREFERENCE)


def _sha256_and_size(path):
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def collect(root=None, user_id=r2.DEFAULT_USER_ID, apps_dir=None):
    """Every Class 1 artifact under applications/, with its key and its hash.

    Files are visited in sorted path order so that two runs over an unchanged
    tree produce byte-identical plans, which is what makes the backfill's count
    and the publish's manifest comparable at all.
    """
    apps = Path(apps_dir) if apps_dir else applications_dir(root)
    applications = load_applications(root)
    profile = _store(root, "profile-get") or {}
    names = name_map(applications, profile)

    artifacts, skipped, taken = [], [], {}
    if not apps.is_dir():
        return {"artifacts": [], "skipped": [], "root": str(apps)}

    for path in sorted(apps.rglob("*")):
        if not path.is_file() or path.name in SKIP_NAMES:
            continue
        suffix = path.suffix.lower()
        if suffix not in CONTENT_TYPES:
            skipped.append({"path": str(path), "reason": "not an artifact type"})
            continue

        aid = kind = ext = None
        if path.name in names:
            aid, kind, ext = names[path.name]
        else:
            for pattern, sidecar_kind, fixed_ext in SIDECARS:
                match = pattern.match(path.name)
                if match and match.group(1) in applications:
                    aid, kind, ext = match.group(1), sidecar_kind, fixed_ext or suffix
                    break

        company = _company_segment(path.parent.name)
        if aid:
            key = f"u/{user_id}/app/{aid}/{kind}{ext}"
            # Two differently named files can both claim one application's key:
            # a contract-named resume and a stem-named one from the generation
            # before, or answers-<id>.json beside the questions-<id>.json it was
            # salvaged from. The loser keeps its own name under unfiled/ rather
            # than overwriting the winner.
            #
            # Which one wins is chosen, not alphabetical. It used to fall out of
            # sorted path order, which happened to give the right answer for
            # answers/questions and gave it for no reason: re-syncing while a
            # record was briefly absent would hand the key back to the bare
            # list, and the card's modal would then fetch an array where it
            # expects an object. It fails closed, and silently, which is the
            # worst way to find out. Found by a peer session measuring the live
            # bucket rather than by anything here.
            incumbent = taken.get(key)
            if incumbent and _rank(path.name) >= _rank(Path(incumbent).name):
                key = f"u/{user_id}/unfiled/{company}/{path.name}"
                aid = kind = None
            else:
                if incumbent:
                    # The newcomer is better. Demote the file already holding
                    # the key instead of the one arriving.
                    for earlier in artifacts:
                        if earlier["path"] == incumbent:
                            earlier["key"] = (f"u/{user_id}/unfiled/"
                                              f"{earlier['company']}/{earlier['name']}")
                            earlier["applicationId"] = earlier["kind"] = None
                            break
                taken[key] = str(path)
        else:
            key = f"u/{user_id}/unfiled/{company}/{path.name}"

        digest, size = _sha256_and_size(path)
        artifacts.append({
            "path": str(path),
            "name": path.name,
            "company": company,
            "applicationId": aid,
            "kind": kind,
            "key": key,
            "sha256": digest,
            "bytes": size,
            "contentType": CONTENT_TYPES[suffix],
        })

    return {"artifacts": artifacts, "skipped": skipped, "root": str(apps)}


def summarise(plan):
    artifacts = plan["artifacts"]
    attributed = [a for a in artifacts if a["applicationId"]]
    by_kind = {}
    for a in artifacts:
        by_kind[a["kind"] or "unfiled"] = by_kind.get(a["kind"] or "unfiled", 0) + 1
    return {
        "objects": len(artifacts),
        "bytes": sum(a["bytes"] for a in artifacts),
        "attributed": len(attributed),
        "unfiled": len(artifacts) - len(attributed),
        "applications": len({a["applicationId"] for a in attributed}),
        "byKind": dict(sorted(by_kind.items())),
        "skipped": len(plan["skipped"]),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Plan the R2 key for every local artifact.")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    ap.add_argument("--user", default=r2.DEFAULT_USER_ID)
    ap.add_argument("--apps", help="applications directory (default the one beside this checkout)")
    ap.add_argument("--full", action="store_true", help="print every artifact, not a summary")
    args = ap.parse_args(argv)

    plan = collect(args.root, args.user, args.apps)
    print(json.dumps(plan if args.full else summarise(plan), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
