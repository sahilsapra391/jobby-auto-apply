#!/usr/bin/env python3
"""Re-mirror `logo_domain` and `logo_alt` for records a rule change re-decided.

**§11b, on the change that added `reed.co.uk` to `INFRA_HOST_PARTS`.** A
deployed decision rule reaches only what the next sweep touches:
`dual_write.persist` mirrors `added | changed`, and a record that did not move
is never re-decided. So the derivation changed for 2,530 reed postings and the
stored columns kept the answer computed under the old rule --
`logo_domain = www.reed.co.uk`, which is the job board's own favicon.

Parity caught it, which is the guard working: 5,060 `field_mismatch` findings,
2,530 on `logo_domain` and 2,530 on `logo_alt`, within minutes of the deploy.

**This is the backfill the rule should have shipped with**, and it is written
as its own script for the reason `backfill_fold_winners` gives: a general
re-mirror is a standing invitation to bypass the delta path, which is correct.
This one is bounded to records whose stored logo columns disagree with what
the current rule computes, so a second run is a no-op.

Unlike `employer_id`, these columns *are* in the upsert's `on conflict`
assignments -- they come from `posting_row` -- so re-running `upsert_postings`
over the affected records is enough. Nothing else is written: employers are
read rather than upserted, the way the fold backfill does it.

Defaults to a dry run. Read the counts, not the exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import backfill      # noqa: E402
import dual_write    # noqa: E402
import jobs_store    # noqa: E402
import migrate       # noqa: E402


def stale(conn, records):
    """Records whose stored logo columns disagree with the current rule.

    The comparison is against `posting_row`, which is the mirror's own
    transform, so this cannot drift from what a re-mirror would write. A list
    of hosts here would be a second declaration of the rule that changed.
    """
    by_id = {r["id"]: r for r in records if r.get("id")}
    if not by_id:
        return []
    with conn.cursor() as cur:
        cur.execute("select legacy_id, logo_domain, logo_alt from postings "
                    "where legacy_id = any(%s)", (list(by_id),))
        held = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    out = []
    for legacy, record in by_id.items():
        if legacy not in held:
            continue
        try:
            row = backfill.posting_row(record)
        except backfill.BackfillError:
            continue
        want = (row.get("logo_domain"), row.get("logo_alt"))
        if want != held[legacy]:
            out.append(record)
    return out


def employers_for(conn, records):
    """The employer ids these records already point at, read not written.

    `upsert_postings` needs the map and must not be handed a writer: the
    employer half of this corpus was corrected by hand and re-upserting it
    would run `coalesce(existing, excluded)` over 2,500 rows for no reason.
    """
    names = sorted({backfill.norm_company(r.get("company")) or
                    backfill.UNKNOWN_EMPLOYER for r in records})
    with conn.cursor() as cur:
        cur.execute("select norm_name, id from employers where norm_name = any(%s)",
                    (names,))
        found = dict(cur.fetchall())
    missing = [n for n in names if n not in found]
    if missing:
        raise SystemExit(
            f"{len(missing)} employers are not stored yet, so this would have "
            f"to create them and that is not this script's job: {missing[:5]}")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="commit; without it the transaction is rolled back")
    ap.add_argument("--root", help="the store root; defaults to the device's")
    args = ap.parse_args(argv)

    records = list(jobs_store.load_jobs(jobs_store.root_dir(args.root)).values())
    conn = migrate.connect(url=migrate.direct_url(migrate._device_url()),
                           announce=False)
    try:
        targets = stale(conn, records)
        print(json.dumps({"pool": len(records), "stale": len(targets)}))
        if not targets:
            print("nothing to do; the stored columns already agree with the rule.")
            return 0
        employers = employers_for(conn, targets)
        with conn.cursor() as cur:
            cur.execute("select logo_domain, count(*) from postings "
                        "where legacy_id = any(%s) group by 1 order by 2 desc limit 5",
                        ([r["id"] for r in targets],))
            print("before:", cur.fetchall())
        backfill.upsert_postings(conn, targets, employers)
        with conn.cursor() as cur:
            cur.execute("select logo_domain, count(*) from postings "
                        "where legacy_id = any(%s) group by 1 order by 2 desc limit 5",
                        ([r["id"] for r in targets],))
            print("after :", cur.fetchall())
        left = stale(conn, records)
        print(json.dumps({"still_stale": len(left), "committed": bool(args.apply)}))
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
            print("\ndry run: rolled back. Re-run with --apply to commit.")
    finally:
        conn.close()
    print("\nRead the counts, not this exit code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
