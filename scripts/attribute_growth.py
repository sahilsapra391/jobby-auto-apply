#!/usr/bin/env python3
"""Split a sweep's growth by where the jobs came from.

A sweep that manifests one change rarely manifests only that change. The sweep
measured here carried both the 34 newly seeded tenants and the SmartRecruiters
paging fix, which lifted Canva from 100 postings to 253 and Coolblue from 100
to 474 on boards we already had. Reported as one number, the seeding experiment
takes credit for the paging fix and neither gets an honest result.

So every job added since the snapshot is attributed to exactly one of:

  seeded    its board is one of the tokens this discovery block added
  paging    its board is a pre-existing SmartRecruiters board, which could
            only return its first hundred postings before
  other     everything else: ordinary churn, new postings on old boards

  python3 scripts/attribute_growth.py --before before-boards.json --seeds seeds.json
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", required=True, help="pre-sweep snapshot")
    ap.add_argument("--seeds", required=True, help="JSON list of {ats, token, name}")
    ap.add_argument("--store")
    args = ap.parse_args()

    before = json.loads(Path(args.before).read_text())
    seen_ids = set(before["ids"])
    seeds = json.loads(Path(args.seeds).read_text())
    seed_names = {(s["ats"], (s["name"] or "").casefold()) for s in seeds}
    seed_tokens = {s["token"].casefold() for s in seeds}

    jobs = jobs_store.load_jobs(jobs_store.root_dir(args.store))
    # Pre-existing SmartRecruiters boards: present in the snapshot, so any
    # growth on them is the paging fix rather than a new tenant.
    old_sr = {key.split("|", 1)[1].casefold()
              for key in before["byBoard"] if key.startswith("smartrecruiters|")}

    buckets = defaultdict(lambda: defaultdict(int))
    totals = defaultdict(int)
    for job_id, job in jobs.items():
        if job_id in seen_ids or job.get("state") in ("passed", "expired"):
            continue
        source = job.get("canonicalSource") or "?"
        company = (job.get("company") or "").casefold()
        market = ((job.get("location") or {}).get("country") or "").upper() or "unknown"
        if (source, company) in seed_names or company in seed_tokens:
            bucket = "seeded"
        elif source == "smartrecruiters" and company in old_sr:
            bucket = "paging"
        else:
            bucket = "other"
        buckets[bucket][market] += 1
        totals[bucket] += 1

    markets = sorted({m for b in buckets.values() for m in b},
                     key=lambda m: -sum(b.get(m, 0) for b in buckets.values()))
    print(f"new jobs since the snapshot: {sum(totals.values())}\n")
    print(f"{'market':<9}{'seeded':>8}{'paging':>8}{'other':>8}")
    for market in markets[:12]:
        print(f"{market:<9}{buckets['seeded'].get(market,0):>8}"
              f"{buckets['paging'].get(market,0):>8}{buckets['other'].get(market,0):>8}")
    print(f"{'TOTAL':<9}{totals['seeded']:>8}{totals['paging']:>8}{totals['other']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
