#!/usr/bin/env python3
"""Record a click-to-visible run against the gate, and say what is missing.

    python3 scripts/gate_latency.py record samples.json --note "wifi, 5GHz"
    python3 scripts/gate_latency.py show

The gate is one sentence and every word of it is load-bearing:

    Measured p95 click-to-visible under 250 ms across all lanes, on a real
    device over a real network, recorded.

**"Across all lanes" is a requirement on the worst lane, not on their
average.** A Found lane at 400 ms hidden behind five empty ones at 20 ms pools
to about 90 ms and passes while the lane the reader actually uses is two and a
half times over. So every row is kept and the verdict is the maximum.

**A run that has not measured the write path has not measured the gate.** The
board's own controls are dark while `live_board` is off, so a run taken before
the flag is turned on can only exercise the read half. That is recorded as
`incomplete` with the missing kinds named, never as a pass. It is the same rule
`answer_record.py` applies to a salvaged question: what was not observed gets a
level of its own rather than being quietly absent.
"""

import argparse
import datetime as _dt
import json
import pathlib
import sys

#: The gate's number, in milliseconds, from docs/plans/phase2-live-board.md.
BUDGET_MS = 250

#: Where a recorded run lands. In the repository, because "recorded" means a
#: reader can find it later without anybody's console still being open.
RECORD = pathlib.Path(__file__).resolve().parent.parent / "docs" / "gate-latency.md"

#: What a complete run has to contain.
#:
#: `write-optimistic` is the gate's own subject: the reader clicks and the card
#: moves. `write-ack` is the round trip behind it, which the reader does not
#: wait for but which decides whether the card stays moved. `apply-event` is
#: the same journey inbound, an event arriving and a row repainting.
#:
#: `load-more` and `reload` are here because they are the two other things a
#: click can start, and a rig that measures only what is easy measures the
#: wrong thing.
#:
#: **`apply-event-cold` is recorded and not required, and that is deliberate.**
#: A board's first clicks compete with hydration, image loads and a cold route
#: handler: measured at 40 iterations on Found, the series ran 735, 44, 154,
#: 248, 115, 228, 238, 243, 244, 250, 141, 131, 133 and then a flat 33 for the
#: remaining twenty-seven. Pooled that is a p95 of 244 which describes neither
#: state. The gate is about whether the board feels live in use, so it is read
#: against the warm number, with the cold one in the table beside it because a
#: reader really does experience it once per page.
REQUIRED_KINDS = (
    "write-optimistic", "write-ack", "apply-event", "load-more", "reload",
)

#: Recorded, shown, and not gated on. Never move one of these into the list
#: above without saying what changed about the gate.
INFORMATIONAL_KINDS = ("apply-event-cold",)

#: The lanes the board draws. Taken from the generator rather than typed here:
#: a lane added there and missing here would be a lane nobody measured, and the
#: verdict would read complete.
def lanes():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import v2_lanes  # noqa: E402
    return tuple(v2_lanes.LANES)


def percentile(values, p):
    """Nearest rank, matching `measure.ts` so the two agree on one number."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = -(-p * len(ordered) // 100)          # ceil, in integers
    return ordered[min(len(ordered) - 1, max(0, rank - 1))]


def summarise(samples):
    """One row per (kind, lane), worst p95 first."""
    groups = {}
    for s in samples:
        groups.setdefault((s["kind"], s["lane"]), []).append(float(s["ms"]))
    rows = [{"kind": k, "lane": l, "n": len(v),
             "p50": percentile(v, 50), "p95": percentile(v, 95), "max": max(v)}
            for (k, l), v in groups.items()]
    return sorted(rows, key=lambda r: -r["p95"])


def verdict(rows, required=REQUIRED_KINDS, all_lanes=None):
    """Pass, fail, or incomplete, and why.

    **Incomplete is not a soft fail and must never be reported as a pass.** A
    run missing the write kinds has measured the half that was available, and
    saying so is the whole point of having a level for it.
    """
    all_lanes = tuple(all_lanes or lanes())
    seen_kinds = {r["kind"] for r in rows}
    missing_kinds = [k for k in required if k not in seen_kinds]
    # A lane with no sample of any kind was not measured.
    seen_lanes = {r["lane"] for r in rows}
    missing_lanes = [l for l in all_lanes if l not in seen_lanes]
    over = [r for r in rows
            if r["p95"] > BUDGET_MS and r["kind"] not in INFORMATIONAL_KINDS]
    if missing_kinds or missing_lanes:
        return "incomplete", {
            "missingKinds": missing_kinds, "missingLanes": missing_lanes,
            "over": over,
        }
    return ("fail" if over else "pass"), {"over": over}


def table(rows):
    out = ["| kind | lane | n | p50 ms | p95 ms | max ms |",
           "| --- | --- | ---: | ---: | ---: | ---: |"]
    for r in rows:
        flag = "" if r["p95"] <= BUDGET_MS else " over"
        out.append(f"| {r['kind']} | {r['lane']} | {r['n']} | "
                   f"{r['p50']:.0f} | {r['p95']:.0f}{flag} | {r['max']:.0f} |")
    return "\n".join(out)


def render(rows, state, why, note, when):
    lines = [
        "# Click to visible, measured",
        "",
        "Generated by `scripts/gate_latency.py record`. Do not edit by hand.",
        "",
        f"The gate is p95 under {BUDGET_MS} ms across all lanes, on a real "
        "device over a real network. The clock starts in the click handler and",
        "stops at the frame that paints the change, which over-reports by up to",
        "one frame (about 16 ms at 60 Hz). Server-side execution is a different",
        "quantity: 0.6 ms for a Found page by date, 39.6 ms by score, against",
        "hundreds of milliseconds of round trip from this Mac.",
        "",
        f"**{state.upper()}** as of {when}." + (f" {note}" if note else ""),
        "",
    ]
    if state == "incomplete":
        lines += [
            "Incomplete, which is not a soft pass. These were not measured at",
            "all, so the run says nothing about them:",
            "",
        ]
        if why["missingKinds"]:
            lines.append("- kinds: " + ", ".join(why["missingKinds"]))
        if why["missingLanes"]:
            lines.append("- lanes: " + ", ".join(why["missingLanes"]))
        lines += [
            "",
            "The write kinds cannot be measured while `live_board` is off: the",
            "board's own controls are dark, so there is no click to time.",
            "",
        ]
    cold = [r for r in rows if r["kind"] in INFORMATIONAL_KINDS]
    if cold:
        lines += [
            "The `-cold` rows are the first clicks after a page load, when the "
            "board is still",
            "hydrating and loading images against a cold route handler. They "
            "are recorded",
            "because a reader meets them once per page, and they are not "
            "gated on, because",
            "the gate asks whether the board feels live in use. A row of "
            "theirs marked over",
            "is information, not a failure.",
            "",
        ]
    if why.get("over"):
        lines += ["Over budget:", ""]
        lines += [f"- {r['kind']} on {r['lane']}: p95 {r['p95']:.0f} ms"
                  for r in why["over"]]
        lines.append("")
    lines += [table(rows), ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="write docs/gate-latency.md from a run")
    rec.add_argument("samples", help="JSON from window.__jobbyMeasure.samples()")
    rec.add_argument("--note", default="", help="the network and device")
    rec.add_argument("--out", default=str(RECORD))
    sub.add_parser("show", help="print the recorded run")
    args = ap.parse_args(argv)

    if args.command == "show":
        path = pathlib.Path(RECORD)
        print(path.read_text() if path.exists() else "no run recorded yet")
        return 0

    samples = json.loads(pathlib.Path(args.samples).read_text())
    if not isinstance(samples, list) or not samples:
        print("no samples in that file", file=sys.stderr)
        return 2
    rows = summarise(samples)
    state, why = verdict(rows)
    when = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    pathlib.Path(args.out).write_text(render(rows, state, why, args.note, when))
    print(f"{state}: {len(samples)} samples, {len(rows)} rows -> {args.out}")
    return 0 if state == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
