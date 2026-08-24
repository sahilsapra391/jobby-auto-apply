#!/usr/bin/env python3
"""Everything a run needs to start, in one call.

An apply run used to open with a dozen separate shell round trips: load the
session, read the profile, list the company folder, work out the base resume,
write the posting to a file, run the duplicate guard, and probe the toolchain to
find out which python has pymupdf. Measured across 86 real runs, the median run
made 102 tool calls in total, including session-load five times and ls five
times, for data that does not change while the run is going.

Every one of those is a round trip: the model waits, reads a small result, and
decides again. At roughly eleven seconds of wall clock per call, the discovery
phase alone was minutes of an agent doing nothing useful.

So this returns all of it as one JSON document, and has the side effects the
agent would otherwise perform by hand: it creates the company folder and writes
the posting text to jd.txt, ready for ats_match.

    python3 scripts/brief.py --id <application-id>

Standard library only.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import store  # noqa: E402
import document_contract  # noqa: E402

def applications_dir():
    """Where tailored documents live.

    Follows JOB_APPLICATION_DIR when it is set, so redirecting the store
    redirects everything a run touches. Without this a test pointed at a temp
    store still wrote real folders into the repo, which is the same class of
    mistake as a test store publishing over the real board.
    """
    redirect = os.environ.get("JOB_APPLICATION_DIR")
    if redirect:
        return Path(redirect).expanduser() / "applications"
    return REPO / "applications"


def run_json(args, default=None):
    """Call one of our own scripts and parse its JSON.

    Failures are surfaced rather than folded into an empty default. A previous
    version of this pattern elsewhere did `json.loads(out or "[]")`, which
    turned a crashed subprocess into an empty result and let a run continue on
    data that was never read.
    """
    r = subprocess.run([sys.executable] + [str(a) for a in args],
                       capture_output=True, text=True)
    if r.returncode != 0 and default is not None:
        return default
    text = (r.stdout or "").strip()
    if not text:
        if default is not None:
            return default
        raise SystemExit(f"brief: no output from {args[0]}: {(r.stderr or '').strip()[:200]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if default is not None:
            return default
        raise SystemExit(f"brief: unparseable output from {args[0]}: {text[:200]}")


def company_folder(company):
    """The folder name the documents live under.

    Matched case-insensitively against what already exists, so a retry writes
    into the folder the first attempt created rather than beside it.
    """
    name = (company or "Unknown").strip().rstrip(".")
    root = applications_dir()
    if root.is_dir():
        for existing in root.iterdir():
            if existing.is_dir() and existing.name.lower() == name.lower():
                return existing
    return root / name


def pick_base(profile, region):
    """The base resume for the posting's country, falling back to the default."""
    resumes = profile.get("resumes") or []
    region = (region or "").upper()
    for entry in resumes:
        if region and region in [str(a).upper() for a in (entry.get("appliesTo") or [])]:
            return entry
    for entry in resumes:
        if entry.get("default"):
            return entry
    return resumes[0] if resumes else None


def find_documents(folder, role, expected=None):
    """The resume and cover letter already sitting in the company folder.

    Only PDFs. The .resume.html render source is deliberately not offered as a
    document: uploading it would attach the wrong file type to an application.
    """
    out = {"resume": None, "coverLetter": None, "others": [], "ambiguous": False}
    if not folder.is_dir():
        return out
    words = [w for w in re.split(r"\W+", (role or "").lower()) if len(w) > 3]

    def score(p):
        stem = p.stem.lower()
        return sum(1 for w in words if w in stem)

    pdfs = sorted(folder.glob("*.pdf"))
    covers = [p for p in pdfs if "cover" in p.stem.lower()]
    resumes = [p for p in pdfs if p not in covers]

    def choose(candidates, expected_name=None):
        """The document belonging to THIS role, or nothing.

        Exactly one candidate means one role has been worked at this company, so
        it is this one. That is the retry case, and it matters because filenames
        often carry only the company: "Sahil_Sapra_Plaid_Resume.pdf" names no
        role at all.

        More than one and we decline, every time. Ranking by role words and
        taking the best looks reasonable and is not: with no floor it returned
        whichever sorted first when nothing matched, and with a floor it still
        matched "Android Maps Reach" to a file named AndroidAuto, because one
        role's words are a prefix of another's. There is no scoring rule here
        that is worth the failure it protects against, since brief reporting
        documentsReady tells the run not to rebuild, and the document it then
        uploads is another role's resume under the user's name. Rebuilding costs
        a few minutes; that costs the application.
        """
        if expected_name:
            exact = [p for p in candidates if p.name == expected_name]
            if len(exact) == 1:
                return exact[0], False
        if not candidates:
            return None, False
        if len(candidates) == 1:
            return candidates[0], False
        return None, True

    expected = expected or {}
    resume, r_ambiguous = choose(resumes, expected.get("resumePdf"))
    cover, c_ambiguous = choose(covers, expected.get("coverPdf"))
    out["resume"] = str(resume) if resume else None
    out["coverLetter"] = str(cover) if cover else None
    out["ambiguous"] = r_ambiguous or c_ambiguous
    out["others"] = [str(p) for p in pdfs
                     if str(p) not in (out["resume"], out["coverLetter"])]
    return out


def story_facts():
    """The story bank path, if it exists. Free-text answers draw on it."""
    path = Path(store.root_dir()) / "story-facts.txt"
    return str(path) if path.exists() else None


def toolchain():
    import importlib.util
    return {
        "python": "python3",
        "pymupdf": importlib.util.find_spec("pymupdf") is not None
                   or importlib.util.find_spec("fitz") is not None,
        "note": "python3 is already the right interpreter. Do not probe it, "
                "do not test imports, do not look for a different one.",
    }


def build(aid):
    entry = run_json([REPO / "scripts" / "store.py", "session-load", "--id", aid], default={})
    if not entry:
        raise SystemExit(f"brief: no watch entry for {aid}")

    profile = run_json([REPO / "scripts" / "store.py", "profile-get"], default={})
    # dupe_check exits 1 precisely when it finds a duplicate, so its verdict
    # has to be read from stdout rather than from the exit code. Defaulting on
    # a non-zero exit reported every real duplicate as clear.
    guard_run = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "dupe_check.py"), "--guard", aid],
        capture_output=True, text=True)
    try:
        guard = json.loads((guard_run.stdout or "").strip())
    except json.JSONDecodeError:
        guard = {"duplicate": None,
                 "unavailable": (guard_run.stderr or "").strip()[:200] or "no output"}

    company = entry.get("company") or "Unknown"
    folder = company_folder(company)
    folder.mkdir(parents=True, exist_ok=True)

    extraction = entry.get("extraction")
    jd_text = extraction if isinstance(extraction, str) else json.dumps(extraction, indent=2) \
        if extraction else ""
    # Named per application, not per company. Ten companies here already hold
    # more than one role in a single folder (Google holds three), and five
    # applications run at once. A shared jd.txt means two concurrent runs at one
    # company overwrite each other's posting, and the loser tailors a resume
    # against the wrong job and sends it.
    jd_path = folder / f"jd-{aid}.txt"
    # Never overwrite a posting body with nothing. On a retry the extraction can
    # be absent while the file written by the first attempt is still the only
    # copy of the posting anywhere.
    if jd_text.strip():
        jd_path.write_text(jd_text)
    elif not jd_path.exists():
        jd_path = None

    base = pick_base(profile, entry.get("region"))
    expected_documents = document_contract.output_names(
        profile, company, entry.get("role"))
    docs = find_documents(folder, entry.get("role"), expected_documents)
    pending = entry.get("pendingFields") or []

    return {
        "applicationId": aid,
        "company": company,
        "role": entry.get("role"),
        "url": entry.get("url"),
        "region": entry.get("region"),
        "ats": entry.get("ats"),
        "status": entry.get("status"),
        "isRetry": bool(pending),
        "pendingFields": pending,
        "note": entry.get("note"),
        "duplicate": guard,
        "folder": str(folder),
        "jdPath": str(jd_path) if jd_path else None,
        "extraction": extraction,
        "baseResume": (base or {}).get("path"),
        "identity": (base or {}).get("identity"),
        "baseKey": (base or {}).get("key"),
        "documents": docs,
        "documentsReady": bool(docs["resume"] and docs["coverLetter"]),
        "storyFacts": story_facts(),
        "toolchain": toolchain(),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True)
    args = ap.parse_args()
    print(json.dumps(build(args.id), indent=2))


if __name__ == "__main__":
    main()
