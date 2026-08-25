#!/usr/bin/env python3
"""Clear legacy non-publication dates before connector backfill.

Dry by default. Production writes require an explicit `--apply` and always use
the same atomic private-store writer as ingest.
"""

import argparse
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store
import runlock  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", help="job store directory")
    parser.add_argument("--apply", action="store_true",
                        help="persist the repair; otherwise report only")
    args = parser.parse_args(argv)
    root = jobs_store.root_dir(args.store)
    guard = (contextlib.nullcontext() if not args.apply
             else runlock.held(root, "scan", note="repair_posted_dates"))
    try:
        with guard:
            jobs = jobs_store.load_jobs(root)
            changed = jobs_store.clear_untrusted_legacy_dates(jobs)
            if args.apply and changed:
                jobs_store.save_jobs(root, jobs)
    except runlock.LockBusy as exc:
        print(f"repair_posted_dates: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({"ok": True, "applied": args.apply,
                      "records": len(jobs), "datesCleared": len(changed)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
