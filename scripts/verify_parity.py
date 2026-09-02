#!/usr/bin/env python3
"""Do the two stores agree? Field by field, and non-zero when they do not.

    python3 scripts/verify_parity.py
    python3 scripts/verify_parity.py --json
    python3 scripts/verify_parity.py --limit 20     # how many to print

Exit codes, and they are the whole interface:

    0   the stores agree, and a real comparison happened
    1   they disagree, or the run could not conclude

Everything in here is arranged so that 0 cannot be returned for the wrong
reason. A parity job that can quietly decline to answer is a parity job that
reports green while checking nothing, which is this repository's oldest failure
shape and the one the seven-day clock would otherwise be measuring.

**It compares the file store against the new normalised tables**, never against
the legacy jsonb one. docs/parity.md carries that reasoning: the legacy table is
a snapshot taken four hours before the plan's baseline, so a job pointed at it
would open with 4,961 divergences and not one of them a fault, and a red build
nobody believes is worse than no build.

**A record present on one side only is a divergence, not a filter.** Comparing
the intersection is what makes a parity job green, and the intersection is
exactly where the 4,779 missing records hid.

**Mid-write is real and is not an excuse.** Three apply slots write
concurrently and a sweep runs every few hours, so parity reading between the
Postgres commit and the file write sees a momentary difference. On a divergence
it re-reads once. If the difference persists it is reported. If it clears, it is
still recorded, at a lower level and with both readings, because a path that is
always mid-write when parity looks is a path with a very slow write. What it
never does is drop a divergence on the grounds that it was probably a race: the
board has published an empty Submitted tab over a good one before, and that is
what that reasoning buys.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import backfill  # noqa: E402
import dual_write  # noqa: E402
import jobs_store  # noqa: E402

# The fields compared, and the file-store expression for each. Anything not
# here is either dropped by BACKFILL.md with a reason or is a container whose
# parts are compared individually.
FIELDS = {
    "title": lambda r: (r.get("title") or "").strip() or "(untitled)",
    "source": lambda r: (r.get("canonicalSource") or "").strip() or None,
    "apply_url": lambda r: (r.get("canonicalUrl") or "").strip() or None,
    "norm_title": lambda r: (r.get("normTitle") or "").strip() or None,
    "location_raw": lambda r: ((r.get("location") or {}).get("raw") or "").strip() or None,
    "city": lambda r: ((r.get("location") or {}).get("city") or "").strip() or None,
    "region": lambda r: ((r.get("location") or {}).get("region") or "").strip() or None,
    "workplace": lambda r: (r.get("workplace") or "").strip() or None,
    "desc_fingerprint": lambda r: (r.get("descFingerprint") or "").strip() or None,
    "lang": lambda r: (r.get("lang") or "").strip() or None,
    "salary_raw": lambda r: (r.get("salary") or "").strip() or None,
}


class ParityError(Exception):
    """The run could not conclude. Never raised for a genuine divergence."""


def file_state(root=None):
    """(records, mtime). Raises rather than reading an unreadable pool as empty."""
    path = Path(jobs_store.paths(jobs_store.root_dir(root))["jobs"])
    records = dual_write.read_pool(path)
    return records, path.stat().st_mtime


def postgres_state(conn, account_id):
    """legacy_id -> the comparable fields, from the normalised tables."""
    columns = ", ".join(f"p.{name}" for name in FIELDS)
    with conn.cursor() as cur:
        cur.execute(
            f"""select p.legacy_id, p.closed_at, d.state,
                       p.merged_legacy_ids, {columns}
                from postings p
                left join job_decisions d
                  on d.posting_id = p.id and d.account_id = %s""",
            (account_id,))
        rows = cur.fetchall()

    state, real, folded_into = {}, set(), {}
    for row in rows:
        legacy_id, closed_at, decision, folded = row[0], row[1], row[2], row[3]
        values = dict(zip(FIELDS, row[4:]))
        values["__state__"] = backfill.legacy_state(closed_at, decision)
        state[legacy_id] = values
        real.add(legacy_id)
        # A legacy record folded into this posting by the board key has no row
        # of its own, and it is still represented here. Resolving it through
        # the winner is a recorded fact; skipping it would be a filter, and a
        # filter is where the missing records hid.
        for other in (folded or []):
            folded_into.setdefault(other, legacy_id)

    # **A real row always wins over a fold reference, whatever the row order
    # was.** Both used to be written into one dict as the rows were read, so an
    # id with its own row that is *also* named in another row's
    # `merged_legacy_ids` took whichever came last. That id genuinely exists in
    # Postgres, and letting the fold entry win would hand it to the
    # pruned-loser exemption below.
    for other, winner in folded_into.items():
        if other not in real:
            state[other] = dict(state[winner], __merged_into__=winner)
    return state


def merge_groups(database):
    """{winner: {every legacy id that resolves to that posting}}.

    Read from the database side, because `__merged_into__` is what
    `postgres_state` stamps on a folded id and it is the only place the
    grouping exists.
    """
    groups = {}
    for legacy_id, row in database.items():
        winner = row.get("__merged_into__") or legacy_id
        groups.setdefault(winner, set()).add(legacy_id)
    return groups


def states_disagree(records, group):
    """Do the legacy records folded into one posting disagree about `state`?

    **This is the whole exemption and it is deliberately narrow.** Two legacy
    records sharing a board key collapse into one posting row, so when a sweep
    expires one and leaves the other new, the file holds two answers and
    Postgres can hold one. Neither side is wrong and no correct write resolves
    it -- parity was red for a day on exactly that.

    But only then. If every folded record agrees on a state and Postgres says
    something else, that is a real divergence and must still fail: exempting
    `state` on merged records generally would blind the check on five records
    and growing, which is the shape of a filter that hides what it was built
    to find.
    """
    seen = {records[i].get("state") for i in group if i in records}
    return len(seen) > 1


def compare(records, database):
    """Every difference, as a list of dicts. Never a boolean."""
    findings = []
    groups = merge_groups(database)
    #: legacy id -> the ids it shares a posting row with, when there are any.
    shared = {i: g for g in groups.values() if len(g) > 1 for i in g}

    for legacy_id in sorted(set(records) - set(database)):
        findings.append({"kind": "only_in_file", "id": legacy_id})
    for legacy_id in sorted(set(database) - set(records)):
        winner = database[legacy_id].get("__merged_into__")
        if winner:
            # **No record on either side.** This id has no posting row: it is a
            # string in the winner's `merged_legacy_ids`, recording that it once
            # folded there, and a sweep has since pruned it from the pool. The
            # question "does this exist on both sides" does not apply to it.
            #
            # Exactly that and no wider. An id with its own row never reaches
            # here, because `postgres_state` refuses to overwrite a real row
            # with a fold reference.
            findings.append({"kind": "pruned_loser", "id": legacy_id,
                             "merged_into": winner})
        else:
            findings.append({"kind": "only_in_postgres", "id": legacy_id})

    for legacy_id in sorted(set(records) & set(database)):
        record, row = records[legacy_id], database[legacy_id]
        winner = row.get("__merged_into__")

        # State is compared for every record, folded ones included, and is
        # exempt in exactly one case: the records sharing this posting row
        # disagree with each other, so the file holds two answers and the row
        # can hold one. See `states_disagree` for why it is not wider.
        expected_state = record.get("state")
        if row["__state__"] != expected_state:
            group = shared.get(legacy_id)
            if group and states_disagree(records, group):
                findings.append({"kind": "merged_field_dropped",
                                 "id": legacy_id, "field": "state",
                                 "file": expected_state,
                                 "postgres": row["__state__"],
                                 "merged_into": winner or legacy_id})
            else:
                findings.append({"kind": "field_mismatch", "id": legacy_id,
                                 "field": "state", "file": expected_state,
                                 "postgres": row["__state__"]})

        for name, read in FIELDS.items():
            want, got = read(record), row[name]
            if isinstance(got, str):
                got = got.strip() or None
            if want == got:
                continue
            if winner:
                # A folded record's own values were discarded by the merge,
                # which is the merge working: the two records differed, that is
                # why they were two. Reported as its own kind rather than
                # dropped, because a filter is where missing records hide, and
                # rather than as a divergence, because the loss is a recorded
                # design decision and not a fault. `merged_legacy_ids` is what
                # keeps it findable at all.
                findings.append({"kind": "merged_field_dropped",
                                 "id": legacy_id, "field": name,
                                 "file": want, "postgres": got,
                                 "merged_into": winner})
            else:
                findings.append({"kind": "field_mismatch", "id": legacy_id,
                                 "field": name, "file": want, "postgres": got})
    return findings


# --------------------------------------------------------------------------
# Class B: sessions, history, answers and profile
#
# Compared the same way and reported in the same list. The gate is "the file
# store is deleted", and a store that is the sole record for sessions, history,
# answers and profile cannot be deleted, so parity that only covers the pool is
# parity that cannot open that gate.
# --------------------------------------------------------------------------

def class_b_file_state(root=None):
    """What the file store holds for class B, keyed for comparison."""
    import store
    base = Path(store.root_dir(root))
    paths = store.paths(base)

    sessions = {}
    directory = Path(paths["sessions"])
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ParityError(f"{path.name} could not be read: {exc}")
            key = (payload.get("applicationId") or path.stem).strip()
            sessions[key] = (payload.get("status") or "unknown").strip()

    events = {}
    history = Path(paths["history"])
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise ParityError(f"applications.jsonl unreadable: {exc}")
            if event.get("eventId"):
                events[event["eventId"]] = event.get("event")

    answers = {}
    path = Path(paths["answers"])
    if path.exists():
        for key, entry in json.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(entry, dict):
                answers[key] = (entry.get("value"), bool(entry.get("sensitive")))

    profile = None
    path = Path(paths["profile"])
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        profile = {k: v for k, v in raw.items()
                   if k not in ("resumes", "writingSamples")}

    return {"sessions": sessions, "events": events, "answers": answers,
            "profile": profile}


def class_b_postgres_state(conn, account_id):
    with conn.cursor() as cur:
        cur.execute("select legacy_id, status from applications "
                    "where account_id = %s", (account_id,))
        sessions = dict(cur.fetchall())
        cur.execute("select payload->>'eventId', kind from application_events "
                    "where account_id = %s and payload ? 'eventId'",
                    (account_id,))
        events = dict(cur.fetchall())
        cur.execute("select key, value, sensitive from answer_bank "
                    "where account_id = %s", (account_id,))
        answers = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("select identity from profiles where account_id = %s",
                    (account_id,))
        row = cur.fetchone()
        profile = row[0] if row else None
    return {"sessions": sessions, "events": events, "answers": answers,
            "profile": profile}


def compare_class_b(files, database):
    """Divergences across sessions, history events, answers and profile."""
    findings = []

    # `roles.json` also creates applications rows, so Postgres legitimately
    # holds application ids the sessions directory does not: a session is
    # deleted once an application completes and the role index outlives it.
    # Only the other direction is a divergence.
    for group, key in (("sessions", "session"), ("events", "event"),
                       ("answers", "answer")):
        ours, theirs = files[group], database[group]
        for name in sorted(set(ours) - set(theirs)):
            findings.append({"kind": f"only_in_file", "id": name,
                             "field": key})
        for name in sorted(set(ours) & set(theirs)):
            if ours[name] != theirs[name]:
                findings.append({"kind": "field_mismatch", "id": name,
                                 "field": key, "file": ours[name],
                                 "postgres": theirs[name]})

    for name in sorted(set(database["events"]) - set(files["events"])):
        findings.append({"kind": "only_in_postgres", "id": name,
                         "field": "event"})
    for name in sorted(set(database["answers"]) - set(files["answers"])):
        findings.append({"kind": "only_in_postgres", "id": name,
                         "field": "answer"})

    if files["profile"] is not None:
        if database["profile"] is None:
            findings.append({"kind": "only_in_file", "id": "profile",
                             "field": "profile"})
        elif files["profile"] != database["profile"]:
            findings.append({"kind": "field_mismatch", "id": "profile",
                             "field": "profile",
                             "file": sorted(files["profile"]),
                             "postgres": sorted(database["profile"])})
    return findings


def run_once(conn, account_id, root=None):
    """One reading of both stores. Returns (findings, mtime)."""
    records, mtime = file_state(root)
    database = postgres_state(conn, account_id)
    findings = compare(records, database)
    findings.extend(compare_class_b(class_b_file_state(root),
                                    class_b_postgres_state(conn, account_id)))
    return findings, mtime


def run(conn, account_id, root=None, on_note=None):
    """The parity check, with the one re-read the concurrency rule allows.

    Returns a report. `divergences` is what makes the run red; `transient` is
    what cleared on the second reading and is recorded rather than dropped.
    """
    note = on_note or (lambda message: None)

    first, first_mtime = run_once(conn, account_id, root)
    report = {"checked": True, "divergences": [], "transient": [],
              "merged_field_dropped": [], "pruned_losers": [],
              "first_count": len(first),
              "reread": False, "first_mtime": first_mtime,
              "second_mtime": None}

    # A merge discarding the loser's own field values is a recorded design
    # decision, not a difference between the stores. Split out before the
    # re-read so a corpus with merges does not re-read on every clean run.
    #: Both are recorded facts rather than differences between the stores, and
    #: both accumulate: `mergedFieldDropped` went 2, 5, 7 and its pinned
    #: assertion had to become a derived one. The report carries the ids and
    #: nothing pins a length.
    ASIDES = ("merged_field_dropped", "pruned_loser")
    report["merged_field_dropped"] = [f for f in first
                                      if f["kind"] == "merged_field_dropped"]
    report["pruned_losers"] = [f for f in first if f["kind"] == "pruned_loser"]
    first = [f for f in first if f["kind"] not in ASIDES]

    if not first:
        return report

    # Something differs. Read both stores again before believing it.
    note(f"parity: {len(first)} differences on the first reading, re-reading")
    report["reread"] = True
    second, second_mtime = run_once(conn, account_id, root)
    second = [f for f in second if f["kind"] not in ASIDES]
    report["second_mtime"] = second_mtime

    def key(finding):
        return (finding["kind"], finding["id"], finding.get("field"))

    persisted = {key(f) for f in second}
    for finding in first:
        if key(finding) in persisted:
            report["divergences"].append(finding)
        else:
            # Cleared between the two readings: a write landed in between.
            # Recorded, with both readings, never dropped. A path that is
            # always mid-write when parity looks is a slow path and that is
            # worth knowing.
            report["transient"].append(
                dict(finding, cleared_on_reread=True,
                     first_mtime=first_mtime, second_mtime=second_mtime))
    # A difference that only the second reading saw is as real as one both saw.
    seen = {key(f) for f in first}
    for finding in second:
        if key(finding) not in seen:
            report["divergences"].append(dict(finding, seen_on="second"))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--root", help="override the store root")
    parser.add_argument("--url", help="override the connection URL")
    args = parser.parse_args(argv)

    def note(message):
        if not args.json:
            print(message, file=sys.stderr)

    # A run that checked nothing must never exit 0. Dual-write being off is
    # the way that happens without anything looking broken.
    if not dual_write.enabled():
        print("verify_parity: dual-write is not configured, so there is "
              "nothing to compare and nothing can be concluded.",
              file=sys.stderr)
        return 1

    try:
        conn = dual_write.connect() if not args.url else __import__(
            "migrate").connect(args.url, announce=False)
    except Exception as exc:
        print(f"verify_parity: no database: {exc}", file=sys.stderr)
        return 1

    try:
        account = dual_write.account_id(conn)
        report = run(conn, account, args.root, on_note=note)
    except (ParityError, dual_write.DualWriteError, backfill.BackfillError) as exc:
        print(f"verify_parity: inconclusive: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.json:
        print(json.dumps(report, indent=1, default=str))
    else:
        for finding in report["transient"][:args.limit]:
            print(f"transient  {finding['kind']:18} {finding['id']} "
                  f"{finding.get('field', '')} (cleared on re-read)")
        for finding in report["divergences"][:args.limit]:
            extra = ""
            if finding["kind"] == "field_mismatch":
                extra = (f" {finding['field']}: file={finding['file']!r} "
                         f"postgres={finding['postgres']!r}")
            print(f"DIVERGENCE {finding['kind']:18} {finding['id']}{extra}")
        hidden = len(report["divergences"]) - args.limit
        if hidden > 0:
            print(f"... and {hidden} more")
        print()
        print(f"divergences {len(report['divergences'])}, "
              f"transient {len(report['transient'])}, "
              f"merged-field drops {len(report['merged_field_dropped'])}, "
              f"pruned losers {len(report['pruned_losers'])}")

    return 1 if report["divergences"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
