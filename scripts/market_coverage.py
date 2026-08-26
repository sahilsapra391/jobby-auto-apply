#!/usr/bin/env python3
"""Direct-listing share per market: the number tenant discovery has to move.

Registry row count is not the measure. A platform can gain fifty rows and leave
the board unchanged; what matters is whether a market's jobs come from the
employer's own board or from an aggregator's copy of it. Direct listings carry
a real apply URL, a real posting date and a full description, and they predict
the board's remainder award almost exactly.

So this is the before-and-after instrument, and it is deliberately its own
script rather than a note in a sweep report, because the two measurements have
to be taken the same way weeks apart.

  python3 scripts/market_coverage.py
  python3 scripts/market_coverage.py --json --out before.json
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store  # noqa: E402

# A posting the employer published, as against somebody's copy of it. The
# sighting classes come from the source registry, not from guessing at URLs.
DIRECT_CLASSES = frozenset(("ats", "careers"))

# The markets worth naming separately. Everything else is counted in `other`,
# which is reported rather than dropped so the totals still add up.
NAMED = ("IN", "US", "AE", "SG", "GB", "CA", "AU", "DE")


def classify(job):
    sources = job.get("sources") or []
    if not sources:
        return "other"
    return min((s.get("sourceClass") for s in sources if isinstance(s, dict)),
               key=lambda c: jobs_store.canonical_rank(c), default="other")


def measure(jobs):
    by_market = defaultdict(lambda: {"total": 0, "direct": 0})
    platforms = Counter()
    for job in jobs.values():
        if job.get("state") in ("passed", "expired"):
            continue
        country = ((job.get("location") or {}).get("country") or "").upper()
        market = country if country in NAMED else ("other" if country else "unknown")
        row = by_market[market]
        row["total"] += 1
        if classify(job) in DIRECT_CLASSES:
            row["direct"] += 1
            platforms[job.get("canonicalSource") or "?"] += 1
    out = {}
    for market, row in by_market.items():
        out[market] = dict(row, share=(row["direct"] / row["total"] if row["total"] else 0.0))
    return {"markets": out, "directByPlatform": dict(platforms.most_common())}


def tenants(companies):
    counts = Counter()
    for company in (companies or {}).values():
        if company.get("ats") and company.get("token"):
            counts[company["ats"]] += 1
    return dict(counts.most_common())


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", help="store directory")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--out", help="also write the JSON here")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.store)
    report = measure(jobs_store.load_jobs(root))
    report["tenantsByPlatform"] = tenants(jobs_store.load_companies(root))

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print(f"{'market':<9} {'jobs':>7} {'direct':>7} {'share':>7}")
    order = sorted(report["markets"].items(), key=lambda kv: -kv[1]["total"])
    for market, row in order:
        print(f"{market:<9} {row['total']:>7} {row['direct']:>7} {row['share']:>6.1%}")
    print(f"\ntenants per platform ({sum(report['tenantsByPlatform'].values())} total)")
    for name, n in report["tenantsByPlatform"].items():
        print(f"  {name:<18} {n:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
