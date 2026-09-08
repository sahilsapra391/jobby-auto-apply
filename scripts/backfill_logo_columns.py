#!/usr/bin/env python3
"""Re-decide `logo_src`, `logo_domain` and `logo_alt` for records the rule moved.

**A deployed decision rule only reaches records the next sweep touches.**
`dual_write.persist` mirrors a delta -- `added | changed` -- so
`upsert_postings` never sees a record that did not move. #204 added
`usajobs.gov` and `themuse.com` to `company_logos`' aggregator hosts on
2026-09-06, which changed what `backfill._logo_columns` derives: those hosts
stop qualifying as an employer domain, so a record that used to store
`logo_domain='www.usajobs.gov'` with `logo_alt=False` now derives a
company-name domain with `logo_alt=True`.

Measured 2026-09-08 by a read-only parity run: **708 divergences across 354
postings**, 337 `usajobs` and 17 `themuse`. Both channels return `kept: 0` on
every sweep and the newest of those records was last seen 2026-09-06T04:01:17Z,
so **nothing will ever touch them again** and the mirror is wrong permanently.
That is the same shape as `backfill_fold_winners.py` and this is deliberately
written in its idiom.

Scope. It re-decides only the three logo columns, only for records whose
freshly derived value disagrees with what Postgres holds, and it creates
nothing: no employer rows, no postings. A record in Postgres and not in the
pool is reported and skipped, because re-deriving from a record you do not have
is inventing one.

Verify by reading the postings back, or by re-running parity. Not by this
exiting 0.
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

LOGO_COLUMNS = ("logo_src", "logo_domain", "logo_alt")


def pool_by_id():
    raw = json.loads((Path.home() / ".job-application" / "jobs.json")
                     .read_text(encoding="utf-8"))
    jobs = raw.get("jobs") or raw
    out = {}
    for value in (jobs.values() if isinstance(jobs, dict) else jobs):
        if isinstance(value, dict) and value.get("id"):
            out[value["id"]] = value
    if not out:
        # A read that failed and a store that is empty produce the same value,
        # and this one would then report "nothing to fix" about 354 wrong rows.
        raise SystemExit("the pool parsed to zero records; refusing to treat "
                         "that as an empty backfill")
    return out


def drifted(conn, records, sources=None):
    """Rows whose stored logo columns disagree with today's derivation.

    Derived from `backfill._logo_columns`, the same function the mirror writes
    through, rather than from a second copy of the rule. A backfill that
    re-implements the derivation it is correcting can only ever be a third
    opinion.
    """
    # `postings` is not account-scoped: it has no `account_id` column, unlike
    # `applications` and `job_decisions`. The corpus is shared and multi-tenant
    # separation lives in `job_decisions`, per the plan's section 6.
    where, args = "", []
    if sources:
        where = "where source = any(%s)"
        args.append(list(sources))
    with conn.cursor() as cur:
        cur.execute(
            f"select legacy_id, source, logo_src, logo_domain, logo_alt "
            f"from postings {where}", args)
        rows = cur.fetchall()

    out, absent = [], []
    for legacy_id, source, src, domain, alt in rows:
        record = records.get(legacy_id)
        if record is None:
            absent.append(legacy_id)
            continue
        want = backfill._logo_columns(record)
        have = {"logo_src": src, "logo_domain": domain, "logo_alt": alt}
        if want != have:
            out.append((legacy_id, source, have, want))
    return out, absent


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write; without it nothing is committed")
    ap.add_argument("--source", action="append",
                    help="limit to a source; repeatable. Omit to scan all.")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N rows, for a rehearsal")
    args = ap.parse_args(argv)

    records = pool_by_id()
    conn = migrate.connect(url=migrate.direct_url(migrate._device_url()),
                           announce=False)
    try:
        rows, absent = drifted(conn, records, args.source)
        if args.limit:
            rows = rows[:args.limit]

        by_source = {}
        for _, source, _, _ in rows:
            by_source[source] = by_source.get(source, 0) + 1
        print(f"{len(rows)} posting(s) whose logo columns the current rule "
              f"decides differently")
        for source, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
            print(f"  {source:<20} {count}")
        if absent:
            print(f"{len(absent)} row(s) in Postgres and not in the pool, "
                  f"skipped rather than re-derived from nothing")
        for legacy_id, source, have, want in rows[:5]:
            print(f"  e.g. {legacy_id[:12]} {source}")
            print(f"       stored  {have}")
            print(f"       derives {want}")

        if not rows:
            return 0
        if not args.apply:
            print("\ndry run: nothing written. Re-run with --apply.")
            return 0

        with conn.transaction():
            with conn.cursor() as cur:
                for legacy_id, _, _, want in rows:
                    cur.execute(
                        "update postings set logo_src = %s, logo_domain = %s, "
                        "logo_alt = %s where legacy_id = %s",
                        (want["logo_src"], want["logo_domain"],
                         want["logo_alt"], legacy_id))
        print(f"\nwrote {len(rows)} row(s)")

        # Read back, because the write returning is not the write landing.
        left, _ = drifted(conn, records, args.source)
        print(f"{len(left)} still drifted after the write")
        return 0 if not left else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
