#!/usr/bin/env python3
"""Carry the laptop's HTTP validators into `http_validators`, once.

**So the first cloud sweep after the cache exists is warm rather than paying
the cold cost one more time.** The device has 814 ETag and Last-Modified
entries in `~/.job-application/source-cache.json`, earned over weeks of
sweeps; the container has none. Without this the first warm-capable run is
still cold, and the measurement that matters -- the steady-state sweep -- is
one run further away.

    python3 scripts/seed_validators.py --dry-run
    python3 scripts/seed_validators.py

Upsert, and the newer `seen_at` wins on conflict, so seeding never overwrites a
validator a cloud sweep has already refreshed.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import jobs_store  # noqa: E402


def rows(cache):
    """`(url, etag, last_modified)` for every usable entry."""
    out = []
    for url, value in (cache or {}).items():
        if not isinstance(value, dict) or not isinstance(url, str):
            continue
        etag = value.get("etag")
        modified = value.get("lastModified")
        if not etag and not modified:
            continue           # a validator with no validator in it
        out.append((url, etag, modified))
    return sorted(out)


def load(root=None):
    path = Path(jobs_store.root_dir(root)) / "source-cache.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Seed http_validators from the device cache.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root")
    args = ap.parse_args(argv)
    entries = rows(load(args.root))
    report = {"entries": len(entries)}
    if args.dry_run:
        print(json.dumps(report, indent=2))
        return 0
    import dual_write
    import jobs_db
    conn = jobs_db.connect()
    try:
        account = str(dual_write.account_id(conn))
        with conn.cursor() as cur:
            for start in range(0, len(entries), 500):
                chunk = entries[start:start + 500]
                cur.executemany(
                    "insert into http_validators "
                    "(account_id, url, etag, last_modified, seen_at) "
                    "values (%s, %s, %s, %s, now()) "
                    "on conflict (account_id, url) do update set "
                    "etag = excluded.etag, last_modified = excluded.last_modified, "
                    "seen_at = excluded.seen_at "
                    "where http_validators.seen_at < excluded.seen_at",
                    [(account, u, e, m) for u, e, m in chunk])
            cur.execute("select count(*) from http_validators where account_id = %s",
                        (account,))
            report["stored"] = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    report["account"] = account
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
