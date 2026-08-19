#!/usr/bin/env python3
"""Print the earliest-start date to give on an application.

The rule is the Monday closest to fifteen days from today. It has to be
computed at fill time rather than stored, because "fifteen days from now" moves
every day and a stale date on an application is worse than no date.

  python3 scripts/start_date.py               # 'Monday 31 August 2026'
  python3 scripts/start_date.py --format iso  # '2026-08-31'
  python3 scripts/start_date.py --format us   # '08/31/2026'
"""

import argparse
from datetime import date, timedelta


def nearest_monday(reference=None, days_out=15):
    """The Monday nearest to `days_out` from the reference date.

    Ties go to the later Monday: promising a start sooner than the notice
    period allows is the more expensive direction to be wrong in.
    """
    today = reference or date.today()
    target = today + timedelta(days=days_out)
    before = target - timedelta(days=target.weekday())
    after = before + timedelta(days=7)
    return before if (target - before) < (after - target) else after


def main():
    ap = argparse.ArgumentParser(description="Earliest start date for applications.")
    ap.add_argument("--format", choices=["long", "iso", "us", "uk"], default="long")
    ap.add_argument("--days", type=int, default=15, help="notice period in days (default 15)")
    args = ap.parse_args()

    d = nearest_monday(days_out=args.days)
    out = {
        "long": d.strftime("%A %d %B %Y").replace(" 0", " "),
        "iso": d.isoformat(),
        "us": d.strftime("%m/%d/%Y"),
        "uk": d.strftime("%d/%m/%Y"),
    }[args.format]
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
