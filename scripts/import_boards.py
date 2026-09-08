#!/usr/bin/env python3
"""Put `companies.json` into the `boards` table, once.

**The container has no `companies.json` and must not be given one.** An image
carrying a registry sweeps whatever was true on build day for as long as that
image lives, and the registry changes every sweep as `seed_companies` discovers
tenants. So the boards move to a table both sides can read, and this is the
one-time carry.

    python3 scripts/import_boards.py --dry-run     count and show, write nothing
    python3 scripts/import_boards.py

**It is additive and idempotent.** `on conflict do nothing` on
`(account_id, source, board_slug)`, so re-running adds what is new and touches
nothing else -- and in particular it never re-enables a board the circuit
breaker has disabled. A registry import that silently un-retires every dead
board would undo the one thing `board_health` exists to do.

Only entries with both an `ats` and a `token` come across. Measured 2026-09-07:
895 entries, 877 with both, across 18 platforms. The 18 without are companies
whose board was never resolved, and a row with no slug is a row nothing can
fetch.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import jobs_store  # noqa: E402


def rows(companies):
    """`(source, slug, discovered_via)` for every fetchable board."""
    out = []
    for entry in (companies or {}).values():
        source = (entry.get("ats") or "").strip().lower()
        slug = (entry.get("token") or "").strip()
        if not source or not slug or source == "unknown":
            continue
        out.append((source, slug, (entry.get("addedFrom") or "registry")[:60]))
    # **Deduped on the constraint's own key, not on the whole tuple.** The
    # unique key is `(account_id, source, board_slug)` and `discovered_via` is
    # not in it, so deduping on all three reported 866 boards where the
    # database stored 795: 71 slugs appear twice with different `addedFrom`
    # values ("pack" and "roles"), and `on conflict do nothing` correctly kept
    # one each. Nothing was lost; the count was wrong, which is the
    # declared-twice shape with a dedup key standing in for a constraint.
    first = {}
    for source, slug, via in sorted(out):
        first.setdefault((source, slug), (source, slug, via))
    return sorted(first.values())


def load(root=None):
    path = jobs_store.paths(jobs_store.root_dir(root))["companies"]
    return json.loads(Path(path).read_text(encoding="utf-8"))


def insert(conn, account, entries, batch=500):
    added = 0
    with conn.cursor() as cur:
        for start in range(0, len(entries), batch):
            chunk = entries[start:start + batch]
            cur.executemany(
                "insert into boards (account_id, source, board_slug, "
                "discovered_via) values (%s, %s, %s, %s) "
                "on conflict (account_id, source, board_slug) do nothing",
                [(account, s, slug, via) for s, slug, via in chunk])
            added += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.commit()
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description="Carry companies.json into boards.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root")
    args = ap.parse_args(argv)

    companies = load(args.root)
    entries = rows(companies)
    counts = {}
    for source, _slug, _via in entries:
        counts[source] = counts.get(source, 0) + 1
    report = {"companies": len(companies), "fetchable": len(entries),
              "bySource": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}

    if args.dry_run:
        print(json.dumps(report, indent=2))
        return 0

    import dual_write
    import jobs_db
    conn = jobs_db.connect()
    try:
        account = dual_write.account_id(conn)
        # `str`, because psycopg returns a uuid.UUID and json.dumps does
        # not serialise one. The insert had already succeeded when this
        # raised, which is the worst place for a report to fail.
        report["account"] = str(account)
        with conn.cursor() as cur:
            cur.execute("select count(*) from boards where account_id = %s",
                        (account,))
            report["before"] = cur.fetchone()[0]
        insert(conn, account, entries)
        with conn.cursor() as cur:
            cur.execute("select count(*) from boards where account_id = %s",
                        (account,))
            report["after"] = cur.fetchone()[0]
            cur.execute("select count(*) from boards where account_id = %s "
                        "and disabled_at is not null", (account,))
            report["disabled"] = cur.fetchone()[0]
    finally:
        conn.close()
    report["added"] = report["after"] - report["before"]
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
