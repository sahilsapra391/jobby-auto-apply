"""Point every application at the posting it is for, where that can be known.

**Read-only by default.** `--apply` writes; without it this reports what it
would do and touches nothing.

`applications.posting_id` has been NULL for every row since §3 created the
column, so the application lanes draw a legacy slug where the board draws a
company and a role. This fills in what can honestly be filled, and reports the
rest by reason rather than as a single unresolved count, because the reasons
are what tell Phase 3 which part of the gap its connectors close for free.

## Where the URL comes from, and why one source is not enough

The session files are the live authority and cover 96 of 204 applications; the
rest are history-only, their sessions long cleaned up. `roles` covers 196 and
is a frozen snapshot that no migration creates and nothing maintains.

So both are read, session first, **and the distinction is kept**. A snapshot is
used here only to supply a *URL to look up*; the value stored is the uuid of a
row that is in `postings` right now, checked at resolution time. That is not
the same act as reading `roles.company` into a column, which would put a
snapshot's content on the board as though it were current. The report says
which source each URL came from, so a reader can tell.

Nothing here writes a company or a role. Those come from the posting once the
link exists, or the row says it has no linked posting.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import posting_link  # noqa: E402


def sessions(root):
    """`legacy_id -> (url, source)` from the device's own session files."""
    out = {}
    directory = Path(root).expanduser() / "sessions"
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        legacy = (record.get("applicationId") or "").strip()
        if not legacy:
            continue
        out[legacy] = ((record.get("url") or "").strip(),
                       (record.get("source") or record.get("ats") or "").strip())
    return out


def snapshot_urls(conn):
    """`legacy_id -> (url, source)` from `roles`, which is frozen.

    Used for the URL only. See the module docstring for why that is a different
    act from reading its company and role onto the board.
    """
    out = {}
    with conn.cursor() as cur:
        try:
            cur.execute("select application_id, url, record from roles")
        except Exception:
            conn.rollback()
            return out
        for legacy, url, record in cur.fetchall():
            source = ""
            if isinstance(record, dict):
                source = (record.get("source") or record.get("ats") or "").strip()
            out[(legacy or "").strip()] = ((url or "").strip(), source)
    return out


def plan(conn, account_id, root):
    """What every application would be linked to, and why not when it would not."""
    resolver = posting_link.Resolver(conn)
    live = sessions(root)
    frozen = snapshot_urls(conn)

    with conn.cursor() as cur:
        cur.execute("select id, legacy_id, posting_id, apply_url, source, ats "
                    "from applications where account_id = %s order by legacy_id",
                    (account_id,))
        rows = cur.fetchall()

    decisions = []
    for app_id, legacy, existing, mirrored_url, mirrored_source, mirrored_ats in rows:
        url, source = live.get(legacy, ("", ""))
        provenance = "session"
        if not url:
            # **The mirrored column before the frozen table.** `applications.
            # apply_url` is written from `roles.json` on every class B pass, so
            # it holds 205 entries where the `roles` table is frozen at the 196
            # a backfill left. Same field, one of them current.
            url = (mirrored_url or "").strip()
            source = (mirrored_source or mirrored_ats or "").strip()
            provenance = "mirrored-role" if url else "none"
        if not url:
            url, source = frozen.get(legacy, ("", ""))
            provenance = "roles-snapshot" if url else "none"
        posting_id, how = resolver.resolve(legacy_id=legacy, url=url, source=source)
        decisions.append({
            "applicationId": app_id, "legacyId": legacy,
            "postingId": posting_id, "how": how, "urlFrom": provenance,
            "alreadyLinked": existing is not None,
        })
    return decisions


def apply(conn, decisions):
    """Write the links. **Never clears one**: a link that exists was resolved
    against a posting that existed then, and a later prune must not silently
    unlink an application whose posting has aged out of the pool."""
    writable = [(d["postingId"], d["applicationId"]) for d in decisions
                if d["postingId"] and not d["alreadyLinked"]]
    with conn.cursor() as cur:
        for posting_id, app_id in writable:
            cur.execute("update applications set posting_id = %s "
                        "where id = %s and posting_id is null",
                        (posting_id, app_id))
    return len(writable)


def report(decisions):
    linked = [d for d in decisions if d["postingId"]]
    how = collections.Counter(d["how"] for d in linked)
    why = collections.Counter(d["how"] for d in decisions if not d["postingId"])
    origin = collections.Counter(d["urlFrom"] for d in linked)
    total = len(decisions)
    print(f"applications: {total}")
    print(f"  resolvable : {len(linked)}  ({100 * len(linked) // max(total, 1)}%)")
    for name, count in how.most_common():
        print(f"      via {name:<12} {count}")
    print(f"  url source :")
    for name, count in origin.most_common():
        print(f"      {name:<16} {count}")
    print(f"  unresolved : {total - len(linked)}")
    for name, count in why.most_common():
        print(f"      {name:<48} {count}")
    return len(linked)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the links; without it nothing is written")
    parser.add_argument("--root", default="~/.job-application")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO / "packages" / "schema"))
    import migrate
    url = migrate.resolved_url() if hasattr(migrate, "resolved_url") else None
    if not url:
        import dual_write
        url = dual_write.resolved_url()
    if not url:
        raise SystemExit("no database is configured")

    import psycopg
    with psycopg.connect(url, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute("select id from accounts order by created_at limit 1")
            row = cur.fetchone()
            if not row:
                raise SystemExit("no account exists")
            account_id = row[0]
        decisions = plan(conn, account_id, args.root)
        report(decisions)
        if args.apply:
            written = apply(conn, decisions)
            conn.commit()
            print(f"\nwrote {written} links")
        else:
            print("\ndry run; pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
