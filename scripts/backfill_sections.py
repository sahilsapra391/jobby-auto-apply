#!/usr/bin/env python3
"""Fill `sections` and re-run the year figure on records already in the store.

New records get both at ingest, from the full posting body. Records already in
the store were written before this existed, so without a backfill an expanded
card says the posting has no headings until that job is seen again.

The excerpt is what is on hand, and it is capped at 1,500 characters, so this
recovers less than ingest does: a requirements block below the cap is not here
to be read. It is still the difference between something and nothing, and a
later sighting of the same job replaces it with the full-body version.

Writes atomically and only when something changed. `--dry-run` reports without
touching the file, which is the right default for a store a daemon is using.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import extract  # noqa: E402
import posting_sections  # noqa: E402
import store  # noqa: E402


def backfill(jobs, force=False):
    """Mutates `jobs` in place. Returns counts for the report."""
    counts = {"records": len(jobs), "sectionsAdded": 0, "yearsAdded": 0, "yearsChanged": 0}
    for job in jobs.values():
        body = job.get("descriptionExcerpt") or ""
        if not body:
            continue
        existing = job.get("sections") or {}
        has = bool((existing.get("responsibilities") or existing.get("requirements")))
        if has and not force:
            sections = existing
        else:
            sections = posting_sections.split_sections(body)
            if sections.get("responsibilities") or sections.get("requirements"):
                job["sections"] = sections
                counts["sectionsAdded"] += 1
        found = extract.years_from_sections(sections, body)
        if not found:
            continue
        current = job.get("extracted") or {}
        # Never overwrite a figure ingest read off the full body with one read
        # off a truncated excerpt. Only fill a gap.
        if current.get("yearsMin") is None:
            job["extracted"] = {**current, **found}
            counts["yearsAdded"] += 1
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="store directory")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--force", action="store_true", help="recompute existing sections")
    args = ap.parse_args()

    root = store.root_dir(args.root)
    path = root / "jobs.json"
    if not path.exists():
        print(f"no job store at {path}", file=sys.stderr)
        return 2
    jobs = json.loads(path.read_text())
    counts = backfill(jobs, force=args.force)
    counts["wrote"] = False
    if not args.dry_run and (counts["sectionsAdded"] or counts["yearsAdded"]):
        handle, tmp = tempfile.mkstemp(dir=str(root), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(jobs, fh, separators=(",", ":"))
        os.replace(tmp, path)
        counts["wrote"] = True
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
