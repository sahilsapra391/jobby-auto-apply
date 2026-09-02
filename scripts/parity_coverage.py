#!/usr/bin/env python3
"""How much of the database parity is actually looking at.

**34 green parity runs on 2026-09-02 were honest about 12 fields and counted as
evidence about 30.** That sentence is the whole reason this file exists. The
timer runs the deployed copy of `verify_parity`, production was 17 hours behind
main, and its checker compared twelve hand-written posting fields while the
mirror wrote thirty. Every run said `divergences: 0`. Every one was true. None
of them measured what the clock counted them for.

Nothing in the log could have shown it. A run that compares less does not fail,
it succeeds faster, and the only difference between a thorough green and a
shallow green is a number nobody was recording.

So each run records what it looked at, and the clock refuses to count a run
that looked at less than the most this device has ever seen.

**Not a version stamp.** The obvious fix is to stamp the checker's commit and
compare it against the repository, and it is worse in three ways: it needs to
know what the repository says, which is a network call from a machine that
should not need one; it changes on every commit whether or not coverage moved;
and it answers "is this the same code" when the question is "is this looking at
everything". A column count is local, moves only when coverage moves, and
survives the day merge-to-main deploys to the device, because it never asks the
repository anything.

**And it fails in the loud direction.** A checker that has gone blind reports a
*smaller* number, not a missing one. Compare the shapes this repository keeps
hitting, where the broken instrument goes quiet and silence reads as health.

Three states, and only one of them is a defect:

    compared   the comparison holds this column
    exempt     it does not, and `mirror_fields.EXEMPT` says why
    unseen     it does not, and nothing says why

`exempt` and `unseen` are reported apart because merging them is how 16 real
gaps hide behind 22 good reasons.
"""

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "packages" / "schema"))

import mirror_fields  # noqa: E402


def marks_path(root=None):
    """Where the high-water marks live.

    **On the device, not in the repository, and that is the load-bearing
    choice.** A floor checked into git is deployed like everything else, so a
    checkout that is behind carries a floor that is behind, and a stale checker
    clears a stale bar. This file only ever rises from what this machine has
    actually observed, so a checker that regresses is measured against the best
    reading this database has ever given, whatever code produced it.
    """
    base = pathlib.Path(root) if root else pathlib.Path("~/.job-application")
    return base.expanduser() / "parity-coverage.json"


def live_columns(conn, tables):
    """{table: {column, ...}} for the tables the mirror writes."""
    out = {}
    with conn.cursor() as cur:
        cur.execute(
            """select table_name, column_name
                 from information_schema.columns
                where table_schema = current_schema()
                  and table_name = any(%s)""", (list(tables),))
        for table, column in cur.fetchall():
            out.setdefault(table, set()).add(column)
    return out


def measure(conn):
    """What the comparison covers right now, per table.

    Intersected with the live schema on purpose: a field the mirror writes into
    a column that does not exist is a different fault, and one the migration
    check owns. Counting it here would inflate coverage with columns nobody
    could compare.
    """
    compared = mirror_fields.compared()
    exempt = {}
    for (table, column), why in mirror_fields.EXEMPT.items():
        exempt.setdefault(table, {})[column] = why

    live = live_columns(conn, sorted(compared))
    out = {}
    for table in sorted(compared):
        here = live.get(table, set())
        seen = set(compared[table]) & here
        excused = here & set(exempt.get(table, {}))
        out[table] = {
            "compared": sorted(seen),
            "exempt": sorted(excused),
            "unseen": sorted(here - seen - excused),
        }
    return out


def totals(measurement):
    """The three counts, which is what a log line carries."""
    return {key: sum(len(t[key]) for t in measurement.values())
            for key in ("compared", "exempt", "unseen")}


def read_marks(root=None):
    path = marks_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"marks": {}, "reductions": []}
    if not isinstance(data, dict):
        return {"marks": {}, "reductions": []}
    data.setdefault("marks", {})
    data.setdefault("reductions", [])
    return data


def write_marks(data, root=None):
    path = marks_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def below(measurement, marks):
    """Tables covering less than their high-water mark, and which columns left.

    The columns are named rather than counted, because "postings dropped from
    30 to 12" sends someone looking and "postings lost `logo_domain`,
    `logo_src`, `years_text` ..." tells them what happened.
    """
    out = []
    for table, cover in sorted(measurement.items()):
        mark = marks.get(table) or {}
        was = set(mark.get("compared") or [])
        now = set(cover["compared"])
        missing = sorted(was - now)
        if missing:
            out.append({"table": table, "mark": len(was), "now": len(now),
                        "missing": missing})
    return out


def raise_marks(measurement, data):
    """Observation only ever raises. Returns the tables that moved up.

    A mark rises to the union of what it held and what this run saw, not to
    this run's set: a checker that gained one column and lost another has not
    earned a lower bar on the one it lost.
    """
    lifted = []
    for table, cover in measurement.items():
        mark = data["marks"].setdefault(table, {"compared": []})
        was = set(mark.get("compared") or [])
        union = was | set(cover["compared"])
        if union != was:
            mark["compared"] = sorted(union)
            lifted.append({"table": table, "from": len(was), "to": len(union)})
    return lifted


class CoverageError(RuntimeError):
    pass


def exempt_pairs():
    """`{(table, column)}` carrying a reason, from `mirror_fields`.

    Read here rather than reached into elsewhere, and injectable, because
    `mirror_fields` is a parity gate path: a test that needed to add an entry
    would otherwise have to edit it and restart the three-day clock.
    """
    return set(mirror_fields.EXEMPT)


def account_for(conn, measurement, data, exempt=None):
    """Why each column left the comparison: gone, exempted, or unaccounted.

    **This is the half that makes the reason mean something.** A bare
    `--reason` on a command line is a sentence anybody can type; what stops the
    ratchet being unstuck by typing one is that every departed column has to be
    explained by the world rather than by the operator. A column that no longer
    exists in the schema, or that now carries an `EXEMPT` entry with a reason,
    is accounted for. A column that simply stopped being compared is not, and
    that is exactly the stale-checker case this whole file is about.
    """
    live = live_columns(conn, sorted(measurement))
    exempt_cols = exempt_pairs() if exempt is None else exempt
    verdicts = []
    for entry in below(measurement, data["marks"]):
        table = entry["table"]
        for column in entry["missing"]:
            if column not in live.get(table, set()):
                why = "gone"
            elif (table, column) in exempt_cols:
                why = "exempted"
            else:
                why = "unaccounted"
            verdicts.append({"table": table, "column": column, "why": why})
    return verdicts


def lower(conn, reason, allow=False, root=None, now=None,
          measurement=None, exempt=None):
    """The legitimate way down, and it needs both halves.

    `migrate.check_expand_only` is the precedent and the shape is deliberately
    the same: a declaration that travels with the change, plus a flag on the
    command, and either alone is not enough. Here the declaration is the world
    -- the column is gone from the schema, or it carries an `EXEMPT` reason --
    and the flag is `--allow-contract` with a reason of its own, recorded.

    Without a way down, the first honest reduction wedges the clock and the
    next person edits the marks file to unstick it, which is the ratchet
    deleting itself.
    """
    data = read_marks(root)
    if measurement is None:
        measurement = measure(conn)
    drops = below(measurement, data["marks"])
    if not drops:
        raise CoverageError(
            "nothing to lower: no table covers less than its high-water mark.")
    if not (reason or "").strip():
        raise CoverageError(
            "a reduction needs a reason. Name what stopped being compared and "
            "why, so the next person can check it rather than trust it.")
    if not allow:
        raise CoverageError(
            "refusing to lower a coverage mark without --allow-contract. "
            f"{len(drops)} table(s) cover less than they have: "
            + ", ".join(f"{d['table']} {d['mark']}->{d['now']}" for d in drops))

    verdicts = account_for(conn, measurement, data, exempt)
    unaccounted = [v for v in verdicts if v["why"] == "unaccounted"]
    if unaccounted:
        raise CoverageError(
            "these columns still exist and carry no EXEMPT reason, so the "
            "reduction is not accounted for: "
            + ", ".join(f"{v['table']}.{v['column']}" for v in unaccounted)
            + ". Either the checker has gone blind, or they need an EXEMPT "
              "entry with a reason.")

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for drop in drops:
        data["marks"][drop["table"]]["compared"] = \
            sorted(measurement[drop["table"]]["compared"])
        data["reductions"].append({
            "at": stamp, "table": drop["table"], "reason": reason.strip(),
            "from": drop["mark"], "to": drop["now"],
            "columns": [{"column": v["column"], "why": v["why"]}
                        for v in verdicts if v["table"] == drop["table"]],
        })
    write_marks(data, root)
    return {"lowered": drops, "columns": verdicts}


def summary(conn, root=None, record=True):
    """One reading, for a log line. Raises nothing the caller cannot log.

    `record` is what a scheduled run does: observe, and let the mark rise. A
    report reads without moving the bar.
    """
    measurement = measure(conn)
    data = read_marks(root)
    short = below(measurement, data["marks"])
    if record:
        if raise_marks(measurement, data):
            write_marks(data, root)
    counts = totals(measurement)
    counts["belowMark"] = short
    return counts, measurement


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    rep = sub.add_parser("report", help="what the comparison covers")
    rep.add_argument("--json", action="store_true")
    rep.add_argument("--url")
    rep.add_argument("--root")
    low = sub.add_parser("lower", help="deliberately lower a high-water mark")
    low.add_argument("--reason", required=True)
    low.add_argument("--allow-contract", action="store_true")
    low.add_argument("--url")
    low.add_argument("--root")
    args = ap.parse_args(argv)

    import migrate
    conn = migrate.connect(getattr(args, "url", None), announce=False)

    if args.cmd == "lower":
        try:
            out = lower(conn, args.reason, args.allow_contract, args.root)
        except CoverageError as exc:
            sys.stderr.write(f"parity_coverage: {exc}\n")
            return 1
        for drop in out["lowered"]:
            print(f"lowered {drop['table']} {drop['mark']} -> {drop['now']}")
        return 0

    counts, measurement = summary(conn, args.root, record=(args.cmd != "report"))
    if getattr(args, "json", False):
        print(json.dumps({"totals": counts, "tables": measurement}, indent=2))
        return 0
    print(f"{'table':20s} {'compared':>8s} {'exempt':>7s} {'unseen':>7s}")
    for table, cover in sorted(measurement.items()):
        print(f"{table:20s} {len(cover['compared']):8d} "
              f"{len(cover['exempt']):7d} {len(cover['unseen']):7d}"
              + (f"   unseen: {', '.join(cover['unseen'])}"
                 if cover["unseen"] else ""))
    print(f"\ncompared {counts['compared']}, "
          f"exempt with a reason {counts['exempt']}, "
          f"unseen {counts['unseen']}")
    for short in counts["belowMark"]:
        print(f"  BELOW MARK {short['table']} {short['mark']} -> {short['now']}"
              f"   lost: {', '.join(short['missing'])}")
    return 1 if counts["belowMark"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
