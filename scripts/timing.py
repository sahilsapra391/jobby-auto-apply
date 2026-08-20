#!/usr/bin/env python3
"""How long applications actually take, and where the time goes.

Written because "the agents feel slow" is not something you can optimise
against. It reconstructs each run from three sources that are already on disk:
the spawn timestamp in the slot log, the document mtimes under applications/,
and the submitting/completed events in history.

That gives the split that matters:

  build   spawn -> both documents written      (tailoring, rendering, verifying)
  fill    documents written -> submitting      (opening the form and filling it)
  verify  submitting -> completed              (the confirmation read back)

  python3 scripts/timing.py                 # the split, with medians
  python3 scripts/timing.py --runs          # every run, slowest first
  python3 scripts/timing.py --since 2026-08-20T12:00:00Z
  python3 scripts/timing.py --json          # for comparing two points in time

Standard library only.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STORE = REPO / "scripts" / "store.py"
LOG_DIR = Path.home() / ".job-application" / "logs"
STAMP = "%Y-%m-%dT%H:%M:%SZ"

# A run longer than this was stuck, not slow. Including them makes the mean
# meaningless: two stalled runs of four hours moved it from 19 to 33 minutes.
STUCK_MINUTES = 90


def parse(ts):
    return dt.datetime.strptime(ts, STAMP)


def spawn_times():
    """applicationId -> when its runner started, from the slot logs."""
    out = {}
    for f in sorted(LOG_DIR.glob("apply*.log")):
        text = f.read_text(errors="replace")
        marks = [(m.start(), m.group(1)) for m in re.finditer(r"=== spawn \S+ (\S+) ===", text)]
        for i, (pos, ts) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            ran = re.search(r'"ran": "([^"]+)"', text[pos:end])
            if ran:
                out[ran.group(1)] = parse(ts)
    return out


def history():
    r = subprocess.run([sys.executable, str(STORE), "history-list"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"timing: cannot read history: {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "[]")


def document_times(events):
    """applicationId -> when the last of its documents was written."""
    wanted = {}
    for e in events:
        aid = e.get("applicationId")
        if not aid or e.get("event") != "completed":
            continue
        for key in ("resumeVariant", "coverLetter"):
            value = e.get(key) or ""
            if value.endswith(".pdf"):
                wanted.setdefault(aid, set()).add(Path(value).name)
    out = {}
    for aid, names in wanted.items():
        stamps = []
        for name in names:
            hits = list((REPO / "applications").rglob(name))
            if hits:
                stamps.append(dt.datetime.fromtimestamp(hits[0].stat().st_mtime, dt.timezone.utc)
                              .replace(tzinfo=None))
        if stamps:
            out[aid] = max(stamps)
    return out


def collect(since=None):
    events = history()
    spawns, docs = spawn_times(), document_times(events)
    submitting, completed = {}, {}
    for e in events:
        aid = e.get("applicationId")
        if not aid:
            continue
        if e.get("event") == "submitting":
            submitting.setdefault(aid, parse(e["at"]))
        elif e.get("event") == "completed":
            completed[aid] = parse(e["at"])

    runs, stuck = [], []
    for aid, start in spawns.items():
        end = completed.get(aid)
        if not end or end < start:
            continue
        if since and start < since:
            continue
        total = (end - start).total_seconds() / 60
        row = {"applicationId": aid, "startedAt": start.strftime(STAMP),
               "total": round(total, 1), "build": None, "fill": None, "verify": None}
        sub, built = submitting.get(aid), docs.get(aid)
        if sub and sub >= start:
            row["verify"] = round((end - sub).total_seconds() / 60, 1)
            if built and start <= built <= sub:
                row["build"] = round((built - start).total_seconds() / 60, 1)
                row["fill"] = round((sub - built).total_seconds() / 60, 1)
        (stuck if total > STUCK_MINUTES else runs).append(row)
    return runs, stuck


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return round(values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2, 1)


def summarise(runs, stuck):
    return {
        "runs": len(runs),
        "stuckExcluded": len(stuck),
        "medianTotal": median([r["total"] for r in runs]),
        "medianBuild": median([r["build"] for r in runs]),
        "medianFill": median([r["fill"] for r in runs]),
        "medianVerify": median([r["verify"] for r in runs]),
        "fastest": min([r["total"] for r in runs], default=None),
        "slowest": max([r["total"] for r in runs], default=None),
    }


def main():
    ap = argparse.ArgumentParser(description="Where application time goes.")
    ap.add_argument("--runs", action="store_true", help="every run, slowest first")
    ap.add_argument("--since", help="only runs started after this UTC stamp")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    since = parse(args.since) if args.since else None
    runs, stuck = collect(since)
    summary = summarise(runs, stuck)

    if args.json:
        print(json.dumps({"summary": summary, "runs": runs, "stuck": stuck}, indent=2))
        return 0

    s = summary
    if not s["runs"]:
        print("No completed runs in range.")
        return 1
    print(f"  runs measured   {s['runs']}"
          + (f"   ({s['stuckExcluded']} stuck runs over {STUCK_MINUTES}m excluded)"
             if s["stuckExcluded"] else ""))
    print(f"  median total    {s['medianTotal']} min")
    print(f"    build         {s['medianBuild']} min")
    print(f"    fill          {s['medianFill']} min")
    print(f"    verify        {s['medianVerify']} min")
    print(f"  fastest/slowest {s['fastest']} / {s['slowest']} min")
    if args.runs:
        print()
        print(f"  {'total':>6} {'build':>6} {'fill':>6} {'verify':>6}  application")
        for r in sorted(runs, key=lambda r: -r["total"]):
            def f(v):
                return f"{v:6.1f}" if v is not None else "     -"
            print(f"  {r['total']:6.1f} {f(r['build'])} {f(r['fill'])} {f(r['verify'])}"
                  f"  {r['applicationId'][:44]}")
        if stuck:
            print(f"\n  stuck, excluded from the medians:")
            for r in sorted(stuck, key=lambda r: -r["total"]):
                print(f"  {r['total']:6.1f}                        {r['applicationId'][:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
