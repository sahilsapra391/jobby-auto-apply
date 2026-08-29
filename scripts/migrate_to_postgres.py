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
import store  # noqa: E402


class MigrationError(Exception):
    """A read failed. Never a silently empty table -- see gather()."""


def _read_json_file(path, default):
    """One file, or a named failure. Never a silent default.

    `jobs_store._read` makes the same choice for the same reason: an
    unreadable pool must never look like an empty one. The first version of
    this returned {} on any error, so a truncated answers.json migrated as
    zero answers and the count-only verify then certified it correct.
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"{path.name} could not be read: {exc}") from exc


def _pool_size_mb(root):
    """The pool file's size, or 0 when there is none. The dry run is the mode
    advertised as safe and must not crash on an empty store."""
    try:
        return (Path(root) / "jobs.json").stat().st_size / 1024 / 1024
    except OSError:
        return 0.0


def _paths(root):
    """Where the store keeps things, asked rather than guessed.

    Hardcoding names here got history wrong on the first attempt: the file is
    `applications.jsonl`, not `history.jsonl`, and the migration reported zero
    events without complaining. store.paths() is the one place that knows.
    """
    return store.paths(root)


def _read_sessions(root):
    """The session files themselves, not `store.py session-list`.

    session-list returns a seven-field projection -- applicationId, company,
    role, status, step, pending, updatedAt -- and `pending` is a count rather
    than the list. Migrating that put a status line into the table this schema
    calls the half that must never be lost. Measured over the 88 real
    sessions: 53 distinct fields dropped, one session losing 28 of them,
    including applyUrl, coverLetter, documentJob, pendingFields, bankCapture
    and browserSlot.
    """
    directory = Path(_paths(root)["sessions"])
    if not directory.is_dir():
        return {}
    out = {}
    for path in sorted(directory.glob("*.json")):
        record = _read_json_file(path, None)
        if not isinstance(record, dict):
            raise MigrationError(f"{path.name} is not a session object")
        out[record.get("applicationId") or path.stem] = record
    return out


def _read_history(root):
    """The application event log, one JSON object per line."""
    path = Path(_paths(root)["history"])
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MigrationError(f"{path.name} could not be read: {exc}") from exc
    events = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError as exc:
            raise MigrationError(
                f"{path.name} line {number} is not JSON: {exc}") from exc
    return events


def gather(root):
    """Everything on disk that belongs off device, with its size."""
    return {
        "jobs": jobs_store.load_jobs(root),
        "companies": jobs_store.load_companies(root),
        "passed": jobs_store.load_passed(root),
        "sessions": _read_sessions(root),
        "history": _read_history(root),
        "roles": _read_json_file(Path(_paths(root)["roles"]), {}),
        "answers": _read_json_file(Path(_paths(root)["answers"]), {}),
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


def _verify_content(conn, data, sample=300):
    """Compare stored records against the files, not counts against counts.

    The first version compared `len()` of the migration's own input against
    `count(*)` of what that input produced, so both sides came from one read
    and no fault in reading could ever show up. A truncated read matched
    itself and printed "Every table matches."
    """
    import random
    jobs = data["jobs"]
    ids = sorted(jobs)
    chosen = random.Random(0).sample(ids, min(sample, len(ids)))
    if not chosen:
        return {"sampled": 0, "identical": 0, "differing": [], "missing": []}
    with conn.cursor() as cur:
        cur.execute("select id, record from jobs where id = any(%s)", (chosen,))
        stored = dict(cur.fetchall())
    identical, differing, missing = 0, [], []
    for job_id in chosen:
        if job_id not in stored:
            missing.append(job_id)
        elif json.dumps(stored[job_id], sort_keys=True) == json.dumps(jobs[job_id], sort_keys=True):
            identical += 1
        else:
            differing.append(job_id)
    return {"sampled": len(chosen), "identical": identical,
            "differing": differing, "missing": missing}


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
        print("DRY RUN, nothing written\n")
        for name, n in on_disk.items():
            print(f"  {name:<12} {n:>7,} records")
        print(f"\n  jobs.json on disk: "
              f"{_pool_size_mb(root):.0f} MB")
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
        content = _verify_content(conn, data)

    print("\nVERIFY, database against files\n")
    ok = True
    for name in ("jobs", "sessions", "roles", "companies", "answers", "passed", "history"):
        want, got = on_disk.get(name, 0), rows.get(name, 0)
        mark = "ok " if want == got else "OFF"
        ok = ok and want == got
        print(f"  {mark} {name:<12} files {want:>7,}   database {got:>7,}")

    print(f"\n  states in the database: {census}")
    print(f"  sampled {content['sampled']} job records: "
          f"{content['identical']} byte-identical, {len(content['differing'])} differing, "
          f"{len(content['missing'])} missing")
    for job_id in (content["differing"] + content["missing"])[:5]:
        print(f"    mismatch: {job_id}")

    ok = ok and not content["differing"] and not content["missing"]
    if not ok:
        print("\nThe database does not match the files. The local files are "
              "untouched and still authoritative.", file=sys.stderr)
        return 1
    print("\nEvery table matches and the sampled records are identical. "
          "The local files are untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
