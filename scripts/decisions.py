#!/usr/bin/env python3
"""The device's own decisions, read back from Postgres.

**`jobs_store` contains zero SELECTs, and that is the whole reason the file
store cannot be deleted.** The pool file carries a `state` on every record and
`dual_write` mirrors the three account-scoped values of it into
`job_decisions`, but nothing has ever read them back. Measured 2026-09-03: an
absent pool reads as empty, the next sweep repopulates from the connectors, and
the device silently loses 16 watched, 100 applied and 11 passed. Those
decisions were in Postgres the whole time with no reader.

So this is the first Phase 4 module and it is deliberately small: a reader, and
nothing that changes who decides. The file stays authoritative until a consumer
is switched over one at a time, and each switch is its own change with its own
evidence.

**What is a decision and what is a fact about the posting.** `STATE_MAP` has
five values and only three are decisions:

    new       not a decision; the absence of one
    expired   a fact about the posting, mirrored as `postings.closed_at`
    watched   -> watching
    passed    -> dropped
    applied   -> submitted

That split is the multi-tenant design rather than an accident of this schema.
Two accounts looking at one posting share `closed_at` and disagree about
`state`, which is why `job_decisions` is account-scoped and `postings` is not.

**The mapping is inverted from `backfill.STATE_MAP` rather than written out.**
A second copy of it is the shape this repository has paid for five times, most
recently `verify_parity.FIELDS` reimplementing `posting_row` and comparing 11
of 28 columns. A value added to the writer arrives here on its own, and
`tests/test_decisions_read.py` fails if the two ever disagree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "scripts"))


def legacy_state():
    """`{decision state: pool state}`, inverted from the mirror's own map."""
    import backfill
    return {decision: legacy
            for legacy, (_closed, decision) in backfill.STATE_MAP.items()
            if decision}


def decision_state():
    """`{pool state: decision state}`, the direction the mirror writes."""
    return {legacy: decision for decision, legacy in legacy_state().items()}


def for_account(conn, account_id):
    """`{legacy_id: pool state}` for every decision this account has recorded.

    Keyed on the pool's own id rather than on `postings.id`, because that is
    what every consumer on the device holds and translating at the boundary
    beats translating at each call site.

    A decision whose posting has been deleted is dropped rather than raised on:
    the join is what makes the key resolvable at all, and a decision about a
    posting that no longer exists is not a decision the device can act on. It
    is counted, so "none matched" and "none exist" stay different facts.
    """
    back = legacy_state()
    with conn.cursor() as cur:
        cur.execute(
            """select p.legacy_id, d.state
                 from job_decisions d
                 join postings p on p.id = d.posting_id
                where d.account_id = %s""",
            (account_id,))
        rows = cur.fetchall()
    out, unknown = {}, {}
    for legacy_id, state in rows:
        mapped = back.get(state)
        if mapped is None:
            # A state the mirror can write and this map does not know is a
            # schema change nobody carried across. Never silently skipped: the
            # caller is about to treat its absence as "no decision", which is
            # the one reading that is always wrong.
            unknown[state] = unknown.get(state, 0) + 1
            continue
        out[legacy_id] = mapped
    if unknown:
        raise ValueError(
            f"job_decisions holds states this reader cannot map: {unknown}. "
            f"backfill.STATE_MAP is the source of the mapping; a value added "
            f"to the constraint in migration 0002 and not to that map arrives "
            f"here")
    return out


def orphans(conn, account_id):
    """Decisions whose posting row is gone. Counted, never inferred as zero."""
    with conn.cursor() as cur:
        cur.execute(
            """select count(*) from job_decisions d
                left join postings p on p.id = d.posting_id
               where d.account_id = %s and p.id is null""",
            (account_id,))
        return cur.fetchone()[0]


def from_pool(jobs):
    """`{legacy_id: pool state}` for the same three states, off the pool."""
    keep = set(legacy_state().values())
    return {jid: record["state"] for jid, record in (jobs or {}).items()
            if (record or {}).get("state") in keep}


def compare(pool, stored):
    """The three sets, never their sizes.

    `counts agreeing` is the trap this repository has met twice: six Found
    filters each moved the row count and one moved the rows, and 205
    applications produced 205 filenames from a rule that collides three ways.
    So this returns what is only on each side and what disagrees, and the
    caller reports those rather than a total.
    """
    only_pool = sorted(set(pool) - set(stored))
    only_stored = sorted(set(stored) - set(pool))
    disagree = sorted(k for k in set(pool) & set(stored) if pool[k] != stored[k])
    return {"onlyOnDevice": only_pool, "onlyInPostgres": only_stored,
            "disagree": [{"id": k, "device": pool[k], "postgres": stored[k]}
                         for k in disagree],
            "agreed": len(set(pool) & set(stored)) - len(disagree)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--compare", action="store_true",
                    help="read both sides and report where they differ")
    args = ap.parse_args(argv)

    import jobs_db
    import jobs_store
    conn = jobs_db.connect()
    try:
        import dual_write
        account = dual_write.account_id(conn)
        stored = for_account(conn, account)
        missing = orphans(conn, account)
    finally:
        conn.close()

    report = {"account": str(account), "decisions": len(stored),
              "byState": {}, "orphanedDecisions": missing}
    for state in sorted(set(stored.values())):
        report["byState"][state] = sum(1 for v in stored.values() if v == state)

    if args.compare:
        pool = from_pool(jobs_store.load_jobs(jobs_store.root_dir(None)))
        report["pool"] = len(pool)
        report["comparison"] = compare(pool, stored)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.compare:
        c = report["comparison"]
        return 0 if not (c["onlyOnDevice"] or c["onlyInPostgres"]
                         or c["disagree"]) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
