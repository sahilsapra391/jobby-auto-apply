#!/usr/bin/env python3
"""How much of each column is actually populated, held against its own best.

**Parity retires with the file store, and it takes two checks with it.**
`mirror_fields` describes the mirror and `parity_coverage` measures parity, so
neither has a subject once there is nothing to compare against. What survives
checks shape: `schema_drift` answers "does this column exist", which was true on
both sides of every divergence parity has ever caught. Measured against the real
one -- 469 postings inserted with `logo_domain` NULL because production ran a
`posting_row` without `_logo_columns` -- `schema_drift` saw 3 of 3 logo columns
present throughout and would have said nothing.

So this is `parity_coverage`'s ratchet pointed at content instead of at
coverage. Per column, the share of rows that hold a value, against the highest
share this database has ever shown. Local, needs no second store, and fails in
the loud direction: a writer that stops writing reports a smaller number rather
than going quiet.

    python3 scripts/population.py report
    python3 scripts/population.py check          # non-zero if a column fell
    python3 scripts/population.py lower --column postings.logo_domain \\
        --reason "..." --allow-drop

**What it cannot see, stated here rather than left to be discovered.** A value
written *wrong* keeps its column just as populated: a status written `active`
when it should be `submitted` is invisible to this. So is a row that should
exist and does not, since the share is taken over the rows that are there. Both
were parity's, both go unwatched, and `docs/what-parity-alone-catches.md` says
so at more length. This is a floor under the deletion, not a replacement for
what is being deleted.
"""

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "packages" / "schema"))

#: How far a share may fall before it is a finding.
#:
#: **Not any drop.** A corpus turns over -- postings close, new ones arrive from
#: feeds with different fields -- and a share wanders by tenths of a point on
#: its own. An alarm that fires on that is an alarm nobody keeps, which is this
#: repository's other recurring failure. One point is well inside the real case:
#: the logo erasure took `logo_domain` from 97.6% toward 84%.
TOLERANCE = 0.01

#: Columns whose emptiness is the normal state, so a share of them says nothing.
#:
#: Each carries its reason, the way `mirror_fields.EXEMPT` does. A column listed
#: here is one where "mostly null" is what the data means, not a fault.
EXEMPT = {
    ("postings", "closed_at"):
        "set only when a posting closes, so a low share means the pool is "
        "mostly open, which is the healthy state.",
    ("postings", "body_r2_key"):
        "written only for postings whose body was archived to R2.",
    ("applications", "dropped_at"):
        "set only on a dropped application.",
    ("applications", "dropped_reason"):
        "2 of 58 dropped applications carry one; a reason is optional by "
        "design.",
    ("applications", "session_deleted_at"):
        "set only when a session file is pruned.",
    ("employers", "merged_into"):
        "set only on an employer row folded into another.",
}


def share(populated, total):
    """The share of rows holding a value, or None when there are no rows.

    **Zero of zero is not zero per cent.** A fresh table would otherwise read as
    a total regression, and the first thing anybody would do is raise the
    threshold until it stopped complaining.
    """
    if not total:
        return None
    return populated / total


def marks_path(root=None):
    """On the device, for the reason `parity_coverage.marks_path` gives: a
    floor checked into git is deployed like everything else, so a checkout that
    is behind carries a floor that is behind."""
    base = pathlib.Path(root) if root else pathlib.Path("~/.job-application")
    return base.expanduser() / "population-marks.json"


def read_marks(root=None):
    try:
        data = json.loads(marks_path(root).read_text(encoding="utf-8"))
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


def measure(conn, tables=None):
    """`{table: {column: (populated, total)}}` for the mirrored tables.

    One query per table rather than one per column: a `count(*) filter (where
    ... is not null)` per column in a single pass over the table, which is one
    sequential scan instead of thirty.
    """
    import mirror_fields
    wanted = tables or sorted(mirror_fields.compared())
    out = {}
    with conn.cursor() as cur:
        for table in wanted:
            cur.execute(
                """select column_name from information_schema.columns
                    where table_schema = current_schema() and table_name = %s
                    order by ordinal_position""", (table,))
            columns = [r[0] for r in cur.fetchall()
                       if (table, r[0]) not in EXEMPT]
            if not columns:
                continue
            counts = ", ".join(
                f'count(*) filter (where "{c}" is not null) as "{c}"'
                for c in columns)
            # **The row count's alias has to be one no column can have.**
            # `job_scores` has a column called `total`, so aliasing the count
            # as `total` silently overwrote it and the reading lost a column
            # while looking complete.
            cur.execute(f'select count(*) as "#rows", {counts} '
                        f'from "{table}"')
            row = dict(zip([d.name for d in cur.description], cur.fetchone()))
            total = row.pop("#rows")
            out[table] = {c: (row[c], total) for c in columns}
    return out


def below(reading, marks, tolerance=TOLERANCE):
    """Columns whose share has fallen further than the tolerance below its mark."""
    out = []
    for table, columns in sorted(reading.items()):
        for column, (populated, total) in sorted(columns.items()):
            now = share(populated, total)
            mark = (marks.get(table) or {}).get(column)
            if now is None or mark is None:
                continue
            if now < mark - tolerance:
                out.append({"table": table, "column": column,
                            "mark": round(mark, 4), "now": round(now, 4),
                            "rows": total,
                            "missing": int(round((mark - now) * total))})
    return out


def raise_marks(reading, data):
    """Observation only ever raises. Returns what moved."""
    lifted = []
    for table, columns in reading.items():
        held = data["marks"].setdefault(table, {})
        for column, (populated, total) in columns.items():
            now = share(populated, total)
            if now is None:
                continue
            was = held.get(column)
            if was is None or now > was:
                held[column] = round(now, 4)
                lifted.append({"table": table, "column": column,
                               "from": was, "to": held[column]})
    return lifted


class PopulationError(RuntimeError):
    pass


def lower(reading, reason, allow=False, root=None, column=None, now=None):
    """The deliberate way down, needing both halves.

    The same shape as `parity_coverage.lower` and `migrate.check_expand_only`:
    a reason that travels with the change, plus a flag on the command. A column
    can legitimately stop being populated -- a feed drops a field, an extractor
    is retired -- and without a way down the first honest reduction wedges the
    check and somebody edits the marks file.

    **Unlike the coverage ratchet there is no world to check the reason
    against.** A column that is merely emptier is still a column, so nothing
    here can confirm the claim the way "gone from the schema, or newly exempt"
    can. The reason is recorded with the numbers instead, so the next reader can
    check it rather than trust it.
    """
    data = read_marks(root)
    drops = below(reading, data["marks"])
    if column:
        drops = [d for d in drops if f"{d['table']}.{d['column']}" == column]
    if not drops:
        raise PopulationError(
            "nothing to lower: no column is below its mark by more than the "
            f"{TOLERANCE:.0%} tolerance.")
    if not (reason or "").strip():
        raise PopulationError(
            "a reduction needs a reason. Name what stopped populating the "
            "column and why, so the next person can check it rather than "
            "trust it.")
    if not allow:
        raise PopulationError(
            "refusing to lower a population mark without --allow-drop. "
            + ", ".join(f"{d['table']}.{d['column']} "
                        f"{d['mark']:.1%}->{d['now']:.1%}" for d in drops))
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for drop in drops:
        data["marks"][drop["table"]][drop["column"]] = drop["now"]
        data["reductions"].append({
            "at": stamp, "table": drop["table"], "column": drop["column"],
            "from": drop["mark"], "to": drop["now"], "rows": drop["rows"],
            "reason": reason.strip(),
        })
    write_marks(data, root)
    return {"lowered": drops}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    for name in ("report", "check"):
        s = sub.add_parser(name)
        s.add_argument("--url")
        s.add_argument("--root")
        s.add_argument("--json", action="store_true")
    low = sub.add_parser("lower")
    low.add_argument("--column", help="table.column, or every drop if omitted")
    low.add_argument("--reason", required=True)
    low.add_argument("--allow-drop", action="store_true")
    low.add_argument("--url")
    low.add_argument("--root")
    args = ap.parse_args(argv)

    import migrate
    conn = migrate.connect(getattr(args, "url", None), announce=False)
    reading = measure(conn)
    data = read_marks(args.root)

    if args.cmd == "lower":
        try:
            out = lower(reading, args.reason, args.allow_drop, args.root,
                        args.column)
        except PopulationError as exc:
            sys.stderr.write(f"population: {exc}\n")
            return 1
        for d in out["lowered"]:
            print(f"lowered {d['table']}.{d['column']} "
                  f"{d['mark']:.1%} -> {d['now']:.1%}")
        return 0

    short = below(reading, data["marks"])
    if args.cmd == "check":
        # Observation raises the marks; a fall is reported and never absorbed.
        if raise_marks(reading, data):
            write_marks(data, args.root)
    if getattr(args, "json", False):
        print(json.dumps({"below": short, "reading": {
            t: {c: list(v) for c, v in cols.items()}
            for t, cols in reading.items()}}, indent=2))
    else:
        for table, columns in sorted(reading.items()):
            for column, (populated, total) in sorted(columns.items()):
                pct = share(populated, total)
                mark = (data["marks"].get(table) or {}).get(column)
                flag = ""
                if pct is not None and mark is not None and pct < mark - TOLERANCE:
                    flag = f"   FELL from {mark:.1%}"
                print(f"  {table}.{column:<24s} "
                      f"{'--' if pct is None else format(pct, '.1%'):>7s}"
                      f" of {total}{flag}")
        if short:
            print(f"\n{len(short)} column(s) below their mark")
    return 1 if short else 0


if __name__ == "__main__":
    raise SystemExit(main())
