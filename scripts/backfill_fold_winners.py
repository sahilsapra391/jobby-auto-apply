#!/usr/bin/env python3
"""Re-decide a named set of board-key collisions, once.

**A deployed decision rule only reaches records the next sweep touches.**
`dual_write.persist` mirrors a delta -- `added | changed` -- so
`upsert_postings` never sees a record that did not move. `BOARD_KEY_WINNER`
learned on 2026-09-05 to prefer a live posting over a closed one, and the three
folds it was written for did not flip on the next sweep, because none of the
six records changed in it: all six were last seen 2026-09-04.

So a rule change needs a backfill or it applies to new collisions only. This is
that step for one named set, and it is deliberately not a general re-mirror:
the delta path is correct and worth keeping, and a script that re-mirrors
everything to fix three rows is a bigger hammer than the problem and a standing
invitation to bypass it.

It runs `upsert_postings` alone. The rename branch keeps the displaced row's
uuid -- that is the whole reason the rename exists -- so `job_decisions` and
`job_scores`, which reference the uuid, stay pointed at the same row and need
no rewrite. Employer ids are read rather than upserted, so nothing outside
`postings` is written at all.

Verify by reading the postings back, not by this exiting 0. It prints the
before and after and the caller should look at them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "scripts"))

import backfill  # noqa: E402
import migrate  # noqa: E402

#: The three folds measured on 2026-09-05, each a Breezy posting under two
#: title slugs sharing one ATS id. `postings` holds the closed one and files
#: the live one under it, so v2 shows an open role as closed at the previous
#: title and pay.
PAIRS = [
    ("1dc73a213adfc263", "3c12eb512130a4bb"),   # bitdeer/eeab1edbc9db
    ("1afe6cbad39c16b2", "f4ca7580de7eacac"),   # surge/f8b44d25ba27
    ("4ba321d464098b3e", "d0710a7fb465bcff"),   # nationsbenefits/8afada1bbe99
]


def pool_records(ids):
    """The named records, straight out of the device's pool."""
    raw = json.loads((Path.home() / ".job-application" / "jobs.json")
                     .read_text(encoding="utf-8"))
    jobs = raw.get("jobs") or raw
    byid = {}
    for value in (jobs.values() if isinstance(jobs, dict) else jobs):
        if isinstance(value, dict) and value.get("id"):
            byid[value["id"]] = value
    missing = [i for i in ids if i not in byid]
    if missing:
        raise SystemExit(f"not in the pool, so there is nothing to re-decide: "
                         f"{missing}")
    return [byid[i] for i in ids]


def employers_for(conn, records):
    """Existing employer ids by normalised name. Reads only.

    `upsert_employers` would also work and would write. Nothing here needs an
    employer created: these three have been stored for days, and a backfill
    that quietly creates rows is a backfill nobody can bound.
    """
    names = sorted({backfill.norm_company(r.get("company"))
                    or backfill.UNKNOWN_EMPLOYER for r in records})
    with conn.cursor() as cur:
        cur.execute("select norm_name, id from employers where norm_name = any(%s)",
                    (names,))
        found = dict(cur.fetchall())
    missing = [n for n in names if n not in found]
    if missing:
        raise SystemExit(f"no employer row for {missing}; refusing to create one "
                         f"in a backfill")
    return found


def show(conn, label):
    with conn.cursor() as cur:
        for winner, loser in PAIRS:
            cur.execute("select legacy_id, closed_at is not null, title "
                        "from postings where legacy_id in (%s, %s)",
                        (winner, loser))
            rows = cur.fetchall()
            held = rows[0] if rows else None
            print(f"  {label:<7} {winner[:8]}../{loser[:8]}.. -> "
                  f"{(held[0][:8] + '..') if held else 'no row':<11}"
                  f"{'CLOSED' if held and held[1] else 'live  '} "
                  f"{(held[2] if held else '')[:34]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write; without it nothing is committed")
    args = ap.parse_args(argv)

    ids = [i for pair in PAIRS for i in pair]
    records = pool_records(ids)
    conn = migrate.connect(url=migrate.direct_url(migrate._device_url()),
                           announce=False)
    try:
        print("before:")
        show(conn, "before")
        employers = employers_for(conn, records)
        if not args.apply:
            print("\ndry run: nothing written. Re-run with --apply.")
            return 0
        with conn.transaction():
            _postings, merged = backfill.upsert_postings(conn, records, employers)
        print(f"\nupsert_postings folded: {merged}")
        print("after:")
        show(conn, "after")
        print("\nRead those, not this exit code.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
