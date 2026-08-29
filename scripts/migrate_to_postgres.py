#!/usr/bin/env python3
"""Move the store off the device: files to Postgres, once.

Defaults to a dry run and reports what it would move. `--apply` writes.

It does not delete anything. The local files stay exactly as they are until a
later change stops reading them, because the sequence that loses data is
"migrate, then discover the reader was still on the old path". Verification is
its own step and runs after the write: row counts and state census from the
database, compared against the files they came from.

  python3 scripts/migrate_to_postgres.py
  python3 scripts/migrate_to_postgres.py --apply
  python3 scripts/migrate_to_postgres.py --verify
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import jobs_db  # noqa: E402
import jobs_store  # noqa: E402


def _store(root, *args):
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "store.py"),
                          "--root", str(root), *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    try:
        return json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return []


def gather(root):
    """Everything on disk that belongs off device, with its size."""
    jobs = jobs_store.load_jobs(root)
    companies = jobs_store.load_companies(root)
    passed = jobs_store.load_passed(root)
    sessions = _store(root, "session-list")
    history = _store(root, "history-list")

    def read(name, default):
        path = Path(root) / name
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    return {
        "jobs": jobs,
        "companies": companies,
        "passed": passed,
        "sessions": {s.get("applicationId"): s for s in sessions if s.get("applicationId")},
        "history": history,
        "roles": read("roles.json", {}),
        "answers": read("answers.json", {}),
    }


def migrate(conn, data):
    written = {}
    started = time.time()
    written["jobs"] = jobs_db.replace_jobs(conn, data["jobs"])
    written["sessions"] = jobs_db._replace_simple(
        conn, "sessions", "application_id", data["sessions"].items(),
        extra=(("status", lambda r: r.get("status")),
               ("company", lambda r: r.get("company")),
               ("role", lambda r: r.get("role")),
               ("updated_at", lambda r: jobs_store.parse_stamp(r.get("updatedAt")))))
    written["roles"] = jobs_db._replace_simple(
        conn, "roles", "application_id", data["roles"].items(),
        extra=(("company", lambda r: r.get("company")),
               ("role", lambda r: r.get("role")),
               ("url", lambda r: r.get("url"))))
    written["companies"] = jobs_db._replace_simple(
        conn, "companies", "key", data["companies"].items(),
        extra=(("name", lambda r: r.get("name")),
               ("ats", lambda r: r.get("ats")),
               ("token", lambda r: r.get("token"))))
    written["answers"] = jobs_db._replace_simple(
        conn, "answers", "slug", data["answers"].items())
    written["passed"] = jobs_db._replace_simple(
        conn, "passed", "id", data["passed"].items())

    # History has no natural key on the row, so it is appended rather than
    # upserted, and the table is cleared first to keep a re-run idempotent.
    with conn.cursor() as cur:
        cur.execute("delete from history")
        rows = [(e.get("applicationId"), e.get("event"),
                 jobs_store.parse_stamp(e.get("at")), json.dumps(e))
                for e in data["history"]]
        if rows:
            cur.executemany(
                "insert into history (application_id, event, at, record) "
                "values (%s, %s, %s, %s)", rows)
    conn.commit()
    written["history"] = len(data["history"])
    written["seconds"] = round(time.time() - started, 1)
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", help="store directory")
    ap.add_argument("--apply", action="store_true", help="write to Postgres")
    ap.add_argument("--verify", action="store_true",
                    help="compare the database against the files")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.root)
    data = gather(root)
    on_disk = {name: len(value) for name, value in data.items()}

    if not args.apply and not args.verify:
        print("DRY RUN — nothing written\n")
        for name, n in on_disk.items():
            print(f"  {name:<12} {n:>7,} records")
        print(f"\n  jobs.json on disk: "
              f"{(Path(root) / 'jobs.json').stat().st_size / 1024 / 1024:.0f} MB")
        print("\nRun again with --apply to move it.")
        return 0

    with jobs_db.connect() as conn:
        jobs_db.ensure_schema(conn)
        if args.apply:
            written = migrate(conn, data)
            print("MIGRATED\n")
            for name, n in written.items():
                if name != "seconds":
                    print(f"  {name:<12} {n:>7,} rows")
            print(f"\n  took {written['seconds']}s")

        rows = jobs_db.counts(conn)
        census = jobs_db.state_census(conn)

    print("\nVERIFY — database against files\n")
    ok = True
    for name in ("jobs", "sessions", "roles", "companies", "answers", "passed", "history"):
        want, got = on_disk.get(name, 0), rows.get(name, 0)
        mark = "ok " if want == got else "OFF"
        ok = ok and want == got
        print(f"  {mark} {name:<12} files {want:>7,}   database {got:>7,}")
    print(f"\n  states in the database: {census}")

    if not ok:
        print("\nCounts differ. The local files are untouched and still authoritative.",
              file=sys.stderr)
        return 1
    print("\nEvery table matches. The local files are untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
