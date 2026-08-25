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
import concurrent.futures
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import extract  # noqa: E402
import posting_body  # noqa: E402
import posting_sections  # noqa: E402
import runlock  # noqa: E402
import store  # noqa: E402

# Boards whose full posting `posting_body` knows how to read.
FETCHABLE = ("greenhouse.io", "lever.co", "ashbyhq.com")


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


def _has(sections):
    sections = sections or {}
    return bool(sections.get("responsibilities") or sections.get("requirements"))


def fetch_missing(jobs, workers=8, limit=None, progress=None):
    """Read the full posting for records the excerpt could not answer.

    The excerpt stops at 1,500 characters and a requirements block is usually
    below that, which is why a third of the board had nothing to show. Measured
    on 2026-08-25 over 60 sampled records: fetching recovered sections for 39 of
    them, 65%, and a year figure for 29.

    Only records that still have nothing are fetched, so a second run is cheap.
    """
    targets = [
        job for job in jobs.values()
        if not _has(job.get("sections"))
        and any(host in (job.get("canonicalUrl") or "") for host in FETCHABLE)
    ]
    if limit:
        targets = targets[:limit]
    counts = {"considered": len(targets), "fetched": 0, "failed": 0,
              "sectionsAdded": 0, "yearsAdded": 0}

    def work(job):
        try:
            return job, posting_body.full_body(job), None
        except Exception as err:                      # noqa: BLE001 - never fatal
            return job, None, err

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for job, body, err in pool.map(work, targets):
            done += 1
            if progress and done % 100 == 0:
                progress(done, len(targets))
            if err or not body:
                counts["failed"] += 1
                continue
            counts["fetched"] += 1
            sections = posting_sections.split_sections(body)
            if _has(sections):
                job["sections"] = sections
                counts["sectionsAdded"] += 1
            found = extract.years_from_sections(sections, body)
            current = job.get("extracted") or {}
            if found and current.get("yearsMin") is None:
                job["extracted"] = {**current, **found}
                counts["yearsAdded"] += 1
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", help="store directory")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--force", action="store_true", help="recompute existing sections")
    ap.add_argument("--fetch", action="store_true",
                    help="read the full posting for records the excerpt cannot answer")
    ap.add_argument("--limit", type=int, help="cap how many postings --fetch reads")
    ap.add_argument("--workers", type=int, default=8, help="parallel fetches (default 8)")
    args = ap.parse_args()

    root = store.root_dir(args.root)
    # The same lock ingest takes, for the same reason. This script reads the
    # whole store, works for minutes -- longer with --fetch -- and writes it
    # back, so it is a lost-update hazard of exactly the shape runlock exists
    # to close. On 2026-08-25 a run of this discarded a sweep's 222 closures
    # and 113 new jobs, which is not a hypothetical. A dry run writes nothing
    # and is left free.
    if args.dry_run:
        return _main(args, root)
    try:
        with runlock.held(root, "scan", note="backfill_sections"):
            return _main(args, root)
    except runlock.LockBusy as exc:
        print(f"backfill_sections: {exc}", file=sys.stderr)
        return 3


def _main(args, root):
    path = root / "jobs.json"
    if not path.exists():
        print(f"no job store at {path}", file=sys.stderr)
        return 2
    jobs = json.loads(path.read_text())
    counts = backfill(jobs, force=args.force)
    if args.fetch:
        def progress(done, total):
            print(f"  fetched {done}/{total}", file=sys.stderr, flush=True)
        counts["fetch"] = fetch_missing(jobs, workers=args.workers,
                                        limit=args.limit, progress=progress)
    counts["wrote"] = False
    changed = (counts["sectionsAdded"] or counts["yearsAdded"]
               or (counts.get("fetch") or {}).get("sectionsAdded")
               or (counts.get("fetch") or {}).get("yearsAdded"))
    if not args.dry_run and changed:
        handle, tmp = tempfile.mkstemp(dir=str(root), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(jobs, fh, separators=(",", ":"))
        os.replace(tmp, path)
        counts["wrote"] = True
    print(json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
