"""The employer resolver: propose, confirm, then move the edge.

FOUR COMMANDS, and the separation between them is the design.

    propose   read the pool, run `employer_extract`, write the queue
    review    print the queue, refusals first
    confirm   mark a batch confirmed, by a person, with their name on it
    apply     move the confirmed edges, writing `employer_decisions` first

**Nothing between `propose` and `confirm` touches an employer.** The rule
extracts a candidate and records the evidence; a person confirms. That is why
`employer_decisions.decided_by` reads `extracted+confirmed` rather than the
rule's name: a later resolver that decides on its own has to be
distinguishable from this one after the fact, and it will be, because it will
write a different value into the same column.

**Why the queue is a file rather than a table.** It is device state on the way
to a decision, like `answers.json`, and it is Class B. `employer_decisions` is
the durable record and lives in Postgres; the queue is the working set. A
proposal that is never confirmed should leave no trace in the mirror.

WHY THE APPLY STEP IS ITS OWN WRITE, measured 2026-09-06 against production
inside rolled-back transactions:

    repoint a posting, re-run upsert_postings on its record  -> survived
    correct the pool's company, re-run upsert_postings       -> did not move

`employer_id` leads the INSERT tuple and is absent from the `on conflict do
update` assignments, so a posting's employer is decided once, at insert. The
resolver therefore cannot ship as an ingest fix and cannot ship as a pool
correction. It writes `postings.employer_id` itself, exactly as it will have to
write `employers.domain` itself, and for the same reason: the upsert path is
deliberately not a correction path.

THE ORDER INSIDE `apply` IS NOT A PREFERENCE. Every edge's before-image goes
into `employer_decisions` in the same transaction and before the update, so a
crash between them leaves a record of an edge that did not move rather than a
moved edge with no record. Nothing else references the posting-to-employer
edge, so the before-image is the only way back.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "schema"))

import employer_extract          # noqa: E402
import store                     # noqa: E402

#: The rule that decided, written into `employer_decisions.decided_by`. The
#: `+confirmed` half is load-bearing: it is what tells a later reader that a
#: person saw this one, and a rule that decides alone must never write it.
DECIDED_BY = "extracted+confirmed"

QUEUE_NAME = "employer-proposals.json"


def queue_path(root=None):
    return Path(store.root_dir(root)) / QUEUE_NAME


def read_queue(root=None):
    path = queue_path(root)
    if not path.exists():
        return {"proposals": [], "refusals": [], "confirmed": []}
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # **Not an empty queue.** A queue that cannot be read and a queue with
        # nothing in it lead to opposite actions, and only one of them is
        # about the proposals.
        raise SystemExit(f"{path} could not be read: {exc}. It is not empty; "
                         f"it is unreadable, and applying anything now would "
                         f"be acting on a set nobody can see.")
    for key in ("proposals", "refusals", "confirmed"):
        held.setdefault(key, [])
    return held


def write_queue(payload, root=None):
    path = queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def pool_records(root=None, company_prefix=None, ids=None):
    """The pool, optionally narrowed to one employer's postings."""
    path = Path(store.root_dir(root)) / "jobs.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    jobs = raw.get("jobs") or raw
    records = [v for v in (jobs.values() if isinstance(jobs, dict) else jobs)
               if isinstance(v, dict) and v.get("id")]
    if ids:
        wanted = set(ids)
        records = [r for r in records if r["id"] in wanted]
    if company_prefix:
        low = company_prefix.lower()
        records = [r for r in records
                   if (r.get("company") or "").lower().startswith(low)]
    return records


def cmd_propose(args):
    records = pool_records(args.root, args.company, args.id)
    if not records:
        raise SystemExit("no records matched, so there is nothing to propose "
                         "about. That is not an empty queue either.")
    proposals, refusals = [], []
    for record in records:
        out = employer_extract.propose(record)
        entry = out.as_dict()
        entry["company"] = record.get("company")
        entry["title"] = record.get("title")
        entry["url"] = record.get("canonicalUrl")
        (refusals if out.refused else proposals).append(entry)
    payload = read_queue(args.root)
    payload["proposals"] = proposals
    payload["refusals"] = refusals
    path = write_queue(payload, args.root)
    print(json.dumps({"scanned": len(records), "proposals": len(proposals),
                      "refusals": len(refusals), "queue": str(path)}))
    return 0


def cmd_review(args):
    payload = read_queue(args.root)
    refusals, proposals = payload["refusals"], payload["proposals"]
    # **Refusals first, and that is the whole point of this command.** The
    # matches are spot-checked; the refusals are where the rule's edges are,
    # and a review that leads with 60 confident matches trains the reader to
    # scroll past the 162 that say the rule could not read the posting.
    by_reason = {}
    for r in refusals:
        by_reason.setdefault(r["reason"], []).append(r)
    print(f"{len(refusals)} refusals, {len(proposals)} proposals\n")
    for reason, rows in sorted(by_reason.items(),
                               key=lambda kv: -len(kv[1])):
        print(f"--- {reason}: {len(rows)}")
        for row in rows[:args.limit]:
            print(f"    {row['id']}  {row.get('title')}")
            print(f"      {row['detail']}")
            if row.get("candidates"):
                print(f"      candidates: {row['candidates']}")
        if len(rows) > args.limit:
            print(f"    ... and {len(rows) - args.limit} more")
        print()
    print(f"--- proposals: {len(proposals)}")
    for row in proposals[:args.limit]:
        print(f"    {row['confidence']:6} {row['name']}  <- {row['id']}")
        print(f"      {row['phrase']!r}  [{','.join(row['frames'])}]")
    if len(proposals) > args.limit:
        print(f"    ... and {len(proposals) - args.limit} more")
    return 0


def cmd_confirm(args):
    """Mark proposals confirmed. A person's name is required, not decorative.

    `employer_decisions.decided_by` records that a human confirmed; `because`
    records who and when. A confirmation with nobody's name on it is an audit
    trail that cannot answer the only question anybody asks of it later.
    """
    payload = read_queue(args.root)
    wanted = set(args.id or [])
    picked = [p for p in payload["proposals"]
              if not wanted or p["id"] in wanted]
    if wanted:
        missing = sorted(wanted - {p["id"] for p in picked})
        if missing:
            raise SystemExit(f"not in the queue as proposals: {missing}")
    if args.confidence:
        picked = [p for p in picked if p["confidence"] == args.confidence]
    if not picked:
        raise SystemExit("nothing matched, so nothing was confirmed")
    already = {c["id"] for c in payload["confirmed"]}
    added = [dict(p, confirmed_by=args.by) for p in picked
             if p["id"] not in already]
    payload["confirmed"].extend(added)
    write_queue(payload, args.root)
    print(json.dumps({"confirmed": len(added),
                      "already_confirmed": len(picked) - len(added),
                      "total_confirmed": len(payload["confirmed"])}))
    return 0


def apply_confirmed(conn, account_id, confirmed, dry_run=True):
    """Move every confirmed edge, recording both sides before it moves.

    Returns the list of moves. Nothing is committed here: the caller owns the
    transaction, so the before-image and the update land together or not at
    all.
    """
    import backfill
    moves = []
    with conn.cursor() as cur:
        for entry in confirmed:
            cur.execute(
                """select p.id, p.employer_id, e.norm_name
                     from postings p
                     left join employers e on e.id = p.employer_id
                    where p.legacy_id = %s""", (entry["id"],))
            row = cur.fetchone()
            if not row:
                # Not silence. A confirmed proposal for a posting Postgres does
                # not hold is a fact about the mirror, and dropping it here
                # would make the count of moves disagree with the count of
                # confirmations with nothing saying why.
                moves.append({"id": entry["id"], "skipped": "no posting row"})
                continue
            posting_id, from_id, from_norm = row
            to_norm = backfill.norm_company(entry["name"])
            if not to_norm:
                moves.append({"id": entry["id"],
                              "skipped": "the confirmed name normalises to "
                                         "nothing"})
                continue
            if to_norm == from_norm:
                moves.append({"id": entry["id"], "skipped": "already there"})
                continue

            cur.execute(
                """insert into employers (canonical_name, norm_name)
                   values (%s, %s)
                   on conflict (norm_name) do update
                     set canonical_name = employers.canonical_name
                   returning id""", (entry["name"], to_norm))
            to_id = cur.fetchone()[0]

            because = (f"{entry['confidence']} confidence from "
                       f"{'+'.join(entry['frames'])}: {entry['phrase'][:160]}; "
                       f"confirmed by {entry.get('confirmed_by')}")
            # **Before the update, in the same transaction.** Nothing else
            # references this edge, so this row is the only way back.
            cur.execute(
                """insert into employer_decisions
                     (account_id, posting_id, legacy_id, from_employer_id,
                      to_employer_id, from_norm_name, to_norm_name,
                      decided_by, because)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (account_id, posting_id, decided_by)
                     do update set to_employer_id = excluded.to_employer_id,
                                   to_norm_name = excluded.to_norm_name,
                                   because = excluded.because""",
                (account_id, posting_id, entry["id"], from_id, to_id,
                 from_norm, to_norm, DECIDED_BY, because))
            cur.execute(
                """update postings
                      set employer_id = %s, employer_decided_by = %s,
                          employer_decided_at = now()
                    where id = %s""", (to_id, DECIDED_BY, posting_id))
            moves.append({"id": entry["id"], "from": from_norm, "to": to_norm})
    if dry_run:
        conn.rollback()
    return moves


def cmd_apply(args):
    import dual_write
    import jobs_db
    payload = read_queue(args.root)
    confirmed = payload["confirmed"]
    if not confirmed:
        raise SystemExit("nothing is confirmed, so there is nothing to apply. "
                         "Run `confirm` first; this command never decides.")
    conn = jobs_db.connect()
    try:
        account = dual_write.account_id(conn)
        moves = apply_confirmed(conn, account, confirmed, dry_run=not args.apply)
        if args.apply:
            conn.commit()
    finally:
        conn.close()
    moved = [m for m in moves if "to" in m]
    print(json.dumps({"confirmed": len(confirmed), "moved": len(moved),
                      "skipped": len(moves) - len(moved),
                      "committed": bool(args.apply)}, indent=1))
    for m in moves:
        print("   ", json.dumps(m))
    if not args.apply:
        print("\ndry run: rolled back. Re-run with --apply to commit.")
    print("\nRead the moves, not this exit code.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", help="the store root; defaults to the device's")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("propose")
    p.add_argument("--company", help="only postings whose company starts with this")
    p.add_argument("--id", action="append", help="only these legacy ids")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("review")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("confirm")
    p.add_argument("--by", required=True, help="who is confirming")
    p.add_argument("--id", action="append", help="only these; default is all")
    p.add_argument("--confidence", choices=("high", "medium"))
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("apply")
    p.add_argument("--apply", action="store_true",
                   help="commit; without it the transaction is rolled back")
    p.set_defaults(func=cmd_apply)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
