#!/usr/bin/env python3
"""File tailored documents into applications/<Company>/, one folder per company.

Two jobs, both of which exist because of a real failure mode:

  - A company with two open roles produces two resumes. Named by company alone
    they collide, and the second silently overwrites the first. When a company
    has more than one application, the role goes in the filename.
  - The documents carry a real street address and phone number, so the folder
    is gitignored and written owner-only.

  python3 scripts/file_docs.py --from ~/.job-application/resumes   # migrate
  python3 scripts/file_docs.py --check                             # audit only
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APPS = REPO / "applications"
STORE = REPO / "scripts" / "store.py"


def slug(value):
    return re.sub(r"[^A-Za-z0-9]+", "", value or "")


def short_role(role):
    """A role token short enough to keep the filename readable in an inbox."""
    dropped = {"product", "manager", "senior", "sr", "the", "and", "of", "for", "a"}
    words = [w for w in re.split(r"[^A-Za-z0-9]+", role or "") if w and w.lower() not in dropped]
    token = "".join(w[:1].upper() + w[1:] for w in words)[:40]
    if token:
        return token
    # Stripping the generic words emptied it ("Product Manager"). Fall back to
    # the whole role rather than a meaningless placeholder.
    return re.sub(r"[^A-Za-z0-9]+", "", role or "")[:40] or "Role"


def load_entries():
    """company/role per applicationId, from watch entries and history."""
    out = {}
    listing = subprocess.run([sys.executable, str(STORE), "session-list"],
                             capture_output=True, text=True)
    for row in json.loads(listing.stdout or "[]"):
        aid = row["applicationId"]
        detail = subprocess.run([sys.executable, str(STORE), "session-load", "--id", aid],
                                capture_output=True, text=True)
        if detail.returncode == 0:
            d = json.loads(detail.stdout)
            out[aid] = (d.get("company"), d.get("role"))
    hist = subprocess.run([sys.executable, str(STORE), "history-list"],
                          capture_output=True, text=True)
    for e in json.loads(hist.stdout or "[]"):
        aid = e.get("applicationId")
        if aid and aid not in out:
            out[aid] = (e.get("company"), e.get("role"))
    return out


def person_stem(profile):
    """The name part of a legacy filename, `First_Last`, from the profile.

    Read from the profile rather than written here, so the rule names nobody.
    An empty profile gives an empty stem, which matches no real file: the
    caller learns nothing was attributed rather than being handed a guess.
    """
    profile = profile or {}
    return "_".join(part for part in (slug(profile.get("firstName")),
                                      slug(profile.get("lastName"))) if part)


def load_profile():
    root = Path(os.environ.get("JOB_APPLICATION_DIR")
                or "~/.job-application").expanduser()
    try:
        return json.loads((root / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def plan(entries, person):
    """Decide each application's filename stem, disambiguating shared companies."""
    per_company = Counter(slug(c) for c, _ in entries.values() if c)
    stems = {}
    if not person:
        return stems
    for aid, (company, role) in entries.items():
        if not company:
            continue
        stem = f"{person}_{slug(company)}"
        if per_company[slug(company)] > 1:
            stem += f"_{short_role(role)}"
        stems[aid] = (company, stem)
    return stems


def cmd_migrate(args, stems, person):
    moved, collisions = 0, []
    by_slug = {}
    for aid, (company, stem) in stems.items():
        by_slug.setdefault(slug(company), []).append((aid, company, stem))

    for src_dir in args.sources:
        src_dir = Path(src_dir).expanduser()
        if not src_dir.exists():
            continue
        for f in sorted(src_dir.glob("*.pdf")):
            m = re.match(re.escape(person) + r"_(.+?)_(Resume|CoverLetter)\.pdf$",
                         f.name)
            if not m:
                collisions.append(f"unparsed filename, left in place: {f.name}")
                continue
            company_slug, kind = m.group(1), m.group(2)
            candidates = by_slug.get(company_slug, [])
            if len(candidates) != 1:
                # Ambiguous: the file name alone cannot say which role it is.
                collisions.append(
                    f"{f.name}: {len(candidates)} applications share company "
                    f"'{company_slug}'; rebuild with a role-qualified name")
                continue
            _, company, stem = candidates[0]
            dest_dir = APPS / company
            dest_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(dest_dir, 0o700)
            dest = dest_dir / f"{stem}_{kind}.pdf"
            shutil.move(str(f), str(dest))
            os.chmod(dest, 0o600)
            moved += 1
    return {"moved": moved, "problems": collisions}


def cmd_check(_args, stems):
    report = {}
    for aid, (company, stem) in sorted(stems.items()):
        d = APPS / company
        have = sorted(p.name for p in d.glob("*.pdf")) if d.exists() else []
        report[aid] = {
            "folder": str(d.relative_to(REPO)),
            "expected": [f"{stem}_Resume.pdf", f"{stem}_CoverLetter.pdf"],
            "present": have,
            "complete": f"{stem}_Resume.pdf" in have and f"{stem}_CoverLetter.pdf" in have,
        }
    missing = [a for a, r in report.items() if not r["complete"]]
    return {"applications": len(report), "incomplete": missing, "detail": report}


def main():
    ap = argparse.ArgumentParser(description="File tailored documents per company.")
    ap.add_argument("--from", dest="sources", nargs="*", default=[
        "~/.job-application/resumes", "~/.job-application/cover-letters"],
        help="directories to migrate PDFs out of")
    ap.add_argument("--check", action="store_true", help="audit only, move nothing")
    args = ap.parse_args()

    APPS.mkdir(parents=True, exist_ok=True)
    os.chmod(APPS, 0o700)
    person = person_stem(load_profile())
    if not person:
        print("file_docs: the profile has no name, so no filename can be planned",
              file=sys.stderr)
        return 1
    stems = plan(load_entries(), person)
    result = cmd_check(args, stems) if args.check else cmd_migrate(args, stems, person)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
