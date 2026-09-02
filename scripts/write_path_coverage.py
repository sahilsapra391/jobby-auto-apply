#!/usr/bin/env python3
"""Which write paths have actually fired since dual-write went live.

    python3 scripts/write_path_coverage.py            the checklist
    python3 scripts/write_path_coverage.py --json
    python3 scripts/write_path_coverage.py --unticked  just what is missing

**The gate is three consecutive green days AND every write path in
`docs/write-paths.md` observed firing at least once since dual-write went live,
minus the uncoverable list.** Duration alone never proved the rare ones ran: a
week of green with a drop-from-In-flight never exercised is a week of green
about the paths that fire anyway. Class A moves on every four-hour sweep; class
B moves only when the user acts, so the rare paths are the risk and they are
exactly the ones a calendar cannot vouch for.

**Counted over the whole log, not per clock window, and that is a correction.**
It used to count from the clock start, which zeroed on every gate-path change.
Eight restarts in two days each reset the count to nought while the actual
coverage was complete, and each reset asked the user for a fresh set of real
actions to prove again what had already been proved: another job watched,
another application dropped. A counter that costs a product decision every time
it zeroes is a defect in the requirement rather than in the coverage. A path
that has fired since dual-write went live has been observed, and observing it
again tells nobody anything.

`--since` still narrows the window when the question is "what fired lately",
which is a different question from the gate.

**A sidecar, not the parity job.** `verify_parity.py` is a gate path: changing
it restarts the clock, and a counter that resets the thing it is counting is
not a counter. Nothing here is imported by `verify_parity`, `class_b`,
`dual_write` or `jobs_store._persist_pool`.

**One hook, in `store.write_private`, and the call site comes from the stack.**
Effects are not enough: a session file reaching `dropped` says a drop happened
and cannot say whether `promote.passed` or `jobs_store.main` did it, and the
inventory is call sites. `write_private` is the only function that puts bytes
in the store, and `_persist_pool` reaches disk through it, so hooking there
observes the pool without modifying the pool -- which matters because
`_persist_pool` is a gate path and this must not touch it.

`sys._getframe` rather than `inspect.stack()`, which builds a full FrameInfo
with source context for every frame and would be paid on every write in the
system. Depth is capped, and the walk stops at the first frame that matches the
inventory.

**It never affects the caller.** Same contract as the class B mirror standing
beside it: the file is already on disk and the caller has already succeeded, so
a failure here is swallowed and reported and nothing else. A coverage counter
that can fail a real application would be a worse bug than the one it is
guarding against.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "write-paths.md"
MAX_DEPTH = 40


def log_path():
    """Resolved per call, through the store root. Never a Path.home() constant
    computed at import: see the 2026-08-30 incident in CLAUDE.md."""
    root = Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application"))
    return root.expanduser() / "logs" / "write-paths.jsonl"


def documented(doc=None):
    """{(module, function, primitive): class} from the three tables.

    Deliberately the same parse `tests/test_write_paths.py::documented` makes,
    and `test_write_path_coverage.py` compares the two outputs rather than
    restating the rule. One contract in two places is how the logo derivation
    went wrong; comparing outputs is how that was caught.
    """
    path = Path(doc) if doc else DOC
    rows, current = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        match = re.match(r"^##\s+Class\s+([ABC])\b", stripped)
        if match:
            current = match.group(1)
            continue
        if stripped.startswith("##"):
            current = None
            continue
        if current is None or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("Module", "---"):
            continue
        key = tuple(c.strip("`") for c in cells[:3])
        if all(key):
            rows[key] = current
    return rows


def required(doc=None):
    """The paths the gate requires. Class C is excluded by construction: those
    rows are the ones the document says are deliberately not mirrored, so
    waiting for them to fire would wait forever for a thing that must not
    happen."""
    return {k for k, cls in documented(doc).items() if cls in ("A", "B")}


#: Frames belonging to this counter rather than to the system it counts.
#:
#: Only `store._observe_write_path` is actually load-bearing, and the reason is
#: worth writing down because a longer list reads as more careful and is not.
#: `_callers` does `sys._getframe(2)`, so its own frame and `observe`'s are
#: already past the start of the walk; adding them here would be decoration.
#: The hook's frame sits exactly at the start, so it is the one that has to go.
_OWN_FRAMES = {("store", "_observe_write_path"),
               ("dual_write", "_observe_write_path")}


def _callers(depth=MAX_DEPTH):
    """(module, function) for each frame above this one, innermost first,
    with the hook's own frames removed."""
    out = []
    try:
        frame = sys._getframe(2)
    except ValueError:                                   # pragma: no cover
        return out
    seen = 0
    while frame is not None and seen < depth:
        code = frame.f_code
        entry = (Path(code.co_filename).stem, code.co_name)
        if entry not in _OWN_FRAMES:
            out.append(entry)
        frame = frame.f_back
        seen += 1
    return out


def observe(primitive="write_private", inventory=None, now=None):
    """Record every documented call site on the stack that led here.

    **One write ticks the whole chain, not just the innermost link.** The
    inventory keys are (module, function, primitive), which is "in module M,
    function F is a write path via P". Pressing Watch puts `promote.watch`,
    `mark_watched`, `edit`, `_persist_pool` and `write_private` on one stack,
    and four of those pairs are documented rows. Ticking them together is the
    difference between the gate being reachable and it waiting forever for
    events that only ever happen at once.

    **Containment, not adjacency, and that distinction is not academic.** The
    first version paired neighbouring frames and `jobs_store.mark_watched ->
    edit` never ticked, because `edit` is a context manager and
    `contextlib.__exit__` sits between the two. Any decorator, context manager
    or callback would do the same. So F must appear on the stack and P must
    appear strictly inside it, which is the honest reading of "F's write path
    fired" and survives a wrapper nobody thought about.

    Never raises, and returns the keys it recorded.
    """
    recorded = []
    try:
        inv = inventory if inventory is not None else required()
        frames = _callers()
        if not frames:
            return recorded
        # Innermost index of each function name on the stack. The hook lives
        # inside the primitive, so the primitive's own frame is frames[0].
        depth = {}
        for i, (_, fn) in enumerate(frames):
            depth.setdefault(fn, i)
        modules = {}
        for i, (mod, fn) in enumerate(frames):
            modules.setdefault((mod, fn), i)

        stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        driven = bool(os.environ.get("JOBBY_DRIVEN_WRITE"))

        for key in inv:
            module, function, called = key
            outer = modules.get((module, function))
            inner = depth.get(called)
            if outer is None or inner is None or inner >= outer:
                continue
            _append({"at": stamp, "module": module, "function": function,
                     "primitive": called, "driven": driven})
            recorded.append(key)
    except Exception as exc:                             # never fail a write
        try:
            sys.stderr.write(f"write_path_coverage: {type(exc).__name__}: {exc}\n")
        except Exception:
            pass
    return recorded


def _append(record):
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def observations(path=None, since=None):
    path = Path(path) if path else log_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.append({"unparseable": line[:120]})
            continue
        if since and rec.get("at", "") < since:
            continue
        out.append(rec)
    return out


#: Where the paths nobody can honestly drive are listed, with their reasons.
UNCOVERABLE_DOC = Path(__file__).resolve().parents[1] / "docs" / "write-path-coverage.md"


def uncoverable(doc=None):
    """The inventory rows the uncoverable list names, read from that document.

    **Derived, not restated.** A second copy of this list in code is a place
    for the two to disagree, and the disagreement would be silent: the gate
    would either demand a path nobody can drive or excuse one somebody can.

    Matched loosely on purpose. Some entries name a full row
    (`jobs_store._persist_pool -> write_private`) and some name only the caller
    (`repair_watch_drift.main`), because that script has one write and spelling
    its callee out adds nothing. A row is excused when the document names
    either form in a backtick.
    """
    path = Path(doc) if doc else UNCOVERABLE_DOC
    if not path.exists():
        return set()
    quoted = set(re.findall(r"`([^`]+)`", path.read_text(encoding="utf-8")))
    out = set()
    for module, function, primitive in required():
        row = f"{module}.{function} -> {primitive}"
        caller = f"{module}.{function}"
        if row in quoted or caller in quoted:
            out.add((module, function, primitive))
    return out


def coverage(since=None, path=None, doc=None):
    need = required(doc)
    seen = {}
    for rec in observations(path, since):
        if rec.get("unparseable"):
            continue
        key = (rec.get("module"), rec.get("function"), rec.get("primitive"))
        if key not in need:
            continue
        entry = seen.setdefault(key, {"count": 0, "driven": True, "firstAt": rec["at"]})
        entry["count"] += 1
        entry["lastAt"] = rec["at"]
        # Organic wins: one un-driven firing makes the path organically covered.
        if not rec.get("driven"):
            entry["driven"] = False
    classes = documented(doc)
    excused = uncoverable(doc) & need
    return {
        "since": since,
        "required": len(need),
        "uncoverable": sorted(f"{m}.{f} -> {p}" for (m, f, p) in excused),
        "ticked": sorted(f"{m}.{f} -> {p}" for (m, f, p) in seen),
        "unticked": sorted(f"{m}.{f} -> {p}" for (m, f, p) in need - set(seen)),
        "driven": sorted(f"{m}.{f} -> {p}" for (m, f, p), v in seen.items()
                         if v["driven"]),
        "byClass": {c: sorted(f"{m}.{f} -> {p}" for (m, f, p) in need - set(seen)
                              if classes.get((m, f, p)) == c) for c in ("A", "B")},
        # **The gate is every coverable path, not every path.** Driving the
        # excused ones would assert something untrue or repair damage that does
        # not exist, and a requirement nobody can meet honestly is met
        # dishonestly or not at all.
        "complete": not (need - set(seen) - excused),
    }


def clock_start():
    """When the current clock started, from parity_clock. Imported lazily so
    this module stays usable without it."""
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import parity_clock
        result = parity_clock.assess(parity_clock.read(),
                                     gate_change=parity_clock.gate_path_touched())
        return result.get("startedAt")
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--unticked", action="store_true")
    ap.add_argument("--since", help="ISO stamp; the gate counts the whole log")
    ap.add_argument("--since-clock", action="store_true",
                    help="only what has fired since the clock last started")
    ap.add_argument("--log", type=Path)
    args = ap.parse_args(argv)

    # The gate reads the whole log. `--since` is opt-in, and `--since-clock`
    # is the old behaviour kept for the "what has fired lately" question.
    since = args.since
    if args.since_clock:
        since = clock_start()
    result = coverage(since, args.log)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["complete"] else 1
    if args.unticked:
        for row in result["unticked"]:
            print(row)
        return 0 if result["complete"] else 1

    print(f"write paths, since "
          f"{since or 'dual-write went live'}")
    print(f"  ticked   {len(result['ticked'])} of {result['required']}")
    if result["driven"]:
        print(f"  driven   {len(result['driven'])} of those fired only because "
              f"they were driven deliberately")
    if result["uncoverable"]:
        print(f"  excused  {len(result['uncoverable'])} on the uncoverable "
              f"list, each with a reason in docs/write-path-coverage.md")
    excused = set(result["uncoverable"])
    for cls in ("A", "B"):
        missing = result["byClass"][cls]
        # **Excused and unticked are different facts and the report used to
        # print them as one.** It said "class A, still unticked (7)" directly
        # above "every documented write path has fired", which contradict each
        # other on the face of it; every one of those seven was on the
        # uncoverable list. A reader has to be able to tell "nobody has driven
        # this yet" from "nobody honestly can".
        waiting = [row for row in missing if row not in excused]
        if waiting:
            print(f"\n  class {cls}, still unticked ({len(waiting)}):")
            for row in waiting:
                print(f"    {row}")
        held = [row for row in missing if row in excused]
        if held:
            print(f"\n  class {cls}, excused ({len(held)}), reasons in "
                  f"docs/write-path-coverage.md:")
            for row in held:
                print(f"    {row}")
    if result["complete"]:
        print(f"\n  every coverable write path has fired"
              + (f"; {len(excused)} excused." if excused else "."))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
