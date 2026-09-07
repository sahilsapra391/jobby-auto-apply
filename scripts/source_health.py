#!/usr/bin/env python3
"""Which sources are not working, in words a person can act on.

**The board said "27 problems" and had one.** `ScanBanner` built its list with
`c.status === "error" || c.why`, and every channel carries a `why` -- "353
boards", "1 of 1 countries x 1 terms" -- so the list was all 27 channels.
Measured 2026-09-07: 27 listed, 1 actually errored, 26 there for having a note.
A problems list that includes everything is a list nobody reads, and it is the
same fault as the error list that was 80% 304s: the signal existed and said the
wrong thing.

So a problem here has to pass one test: **a reader can do something about it.**
Each entry names what it is, what it would take, and the numbers behind it.

Three kinds today, and the shape is open for a fourth:

    channel_failed     the connector errored. The reason is the connector's.
    source_under_anchor  yield below DETECTION_FLOOR of its own believed
                         anchor for CONSECUTIVE_SWEEPS sweeps running.
    board_disabled     the circuit breaker retired a board. Names the board.

**Detection is per source and the anchor is believed yield**, which is the same
anchor `sweep_runs` uses for the coverage gate rather than a second number that
can disagree with it. Per board is the circuit breaker's job
(`packages/schema/board_health.py`) and a per-board signal cannot see a source
whose boards all came back a little short.

**Two sweeps, not one.** A single bad fetch is a bad afternoon and should not
page anybody; the coverage gate already refuses that sweep's closures, which is
the cheap protective action. Two consecutive sweeps at under half is a source
that has changed, and at the tier-1 interval that is eight hours to detection.
Nothing here claims to fix a source that changed its URL scheme -- that needs a
person -- but being told within two sweeps, with the source and the ratio
named, is the guarantee that can actually be built.
"""

#: Yield below this share of the source's own anchor counts as a low sweep.
#:
#: Lower than `sweep_runs.YIELD_FLOOR` (0.70) on purpose, and they are asking
#: different questions. 70% is "do not believe this sweep's absences", which is
#: cheap, reversible and should fire readily. 50% is "tell the user something
#: is wrong", which costs their attention and should not.
DETECTION_FLOOR = 0.50

#: Consecutive low sweeps before it is a problem rather than a bad fetch.
CONSECUTIVE_SWEEPS = 2


def observe(entry, found, previous_anchor):
    """The ledger entry after one sweep, with the low-run counter moved.

    Pure. `entry` is this source's `sweep_runs` ledger row and the counter
    lives beside the anchor because they are read together and a second store
    would be a second thing to keep in step.

    **Counted whether the sweep was believed or refused**, and computed against
    the anchor that was in force when the sweep ran, because `note_refusal`
    re-anchors after two refusals and a counter reading the new anchor would
    clear itself at the exact moment it was supposed to fire.
    """
    entry = dict(entry or {})
    anchor = previous_anchor if isinstance(previous_anchor, int) else None
    if not anchor or anchor <= 0 or not isinstance(found, int) or found < 0:
        # No anchor is not a low sweep. It is the first believed run for this
        # source, and there is nothing to be under.
        entry["lowRuns"] = 0
        entry.pop("lastRatio", None)
        return entry
    ratio = found / anchor
    entry["lastRatio"] = round(ratio, 3)
    entry["lowRuns"] = (int(entry.get("lowRuns") or 0) + 1
                        if ratio < DETECTION_FLOOR else 0)
    return entry


def under_anchor(ledger):
    """Sources at or past the consecutive-low threshold, worst ratio first."""
    out = []
    for source, entry in sorted((ledger or {}).items()):
        if int((entry or {}).get("lowRuns") or 0) < CONSECUTIVE_SWEEPS:
            continue
        out.append({
            "kind": "source_under_anchor",
            "source": source,
            "ratio": entry.get("lastRatio"),
            "anchor": entry.get("found"),
            "sweeps": int(entry.get("lowRuns") or 0),
            "what": (f"{source} returned {_pct(entry.get('lastRatio'))} of its "
                     f"usual {_count(entry.get('found'))} for "
                     f"{int(entry.get('lowRuns') or 0)} sweeps running"),
            "takes": (f"check {source}'s API for a changed endpoint, response "
                      f"shape or auth; the connector is "
                      f"scripts/sources/{source}.py"),
        })
    return sorted(out, key=lambda p: (p["ratio"] is None, p["ratio"]))


def channel_failures(by_channel):
    """Channels the sweep could not use, with the connector's own reason."""
    out = []
    for source, row in sorted((by_channel or {}).items()):
        if (row or {}).get("status") != "error":
            continue
        why = str(row.get("why") or "").strip() or "no reason recorded"
        takes = (f"set the variable named above, then re-run the sweep"
                 if "no key" in why.lower()
                 else f"read the failure and fix scripts/sources/{source}.py")
        out.append({"kind": "channel_failed", "source": source,
                    "what": f"{source} failed: {why}", "takes": takes})
    return out


def disabled_boards(boards):
    """Boards the circuit breaker retired, each named."""
    out = []
    for board in sorted(boards or [],
                        key=lambda b: (b.get("source") or "",
                                       b.get("board_slug") or "")):
        if not board.get("disabled_at"):
            continue
        out.append({
            "kind": "board_disabled",
            "source": board.get("source"),
            "board": board.get("board_slug"),
            "what": (f"{board.get('source')} board "
                     f"{board.get('board_slug')} stopped answering: "
                     f"{board.get('last_failure') or 'unknown'}"),
            "takes": ("re-resolve the tenant token or remove the company from "
                      "the registry"),
        })
    return out


def problems(by_channel=None, ledger=None, boards=None):
    """Everything wrong with sourcing right now, worst kind first.

    Order is failure, then coverage, then boards: a channel that did not run at
    all is worse than one that ran short, and both are worse than one board of
    several hundred.
    """
    return (channel_failures(by_channel) + under_anchor(ledger)
            + disabled_boards(boards))


def warning(report=None, ledger=None, boards=None):
    """One line for the dead-man ping, or "" when sourcing is healthy.

    Joins `watch.PING_REASONS`, so this is a reason on the alarm the user
    already has rather than a new alarm nobody has seen fire. It names counts
    and the worst example; the board's problems list carries the rest, and an
    alarm that tries to be a report is one nobody finishes reading.
    """
    found = problems((report or {}).get("byChannel"), ledger, boards)
    if not found:
        return ""
    worst = found[0]
    more = len(found) - 1
    return ("SOURCING: " + worst["what"]
            + (f"; and {more} more" if more else "")
            + f". {worst['takes']}.")


def _pct(ratio):
    return "an unknown share" if ratio is None else f"{ratio * 100:.0f}%"


def _count(value):
    return "unknown" if not isinstance(value, int) else f"{value:,}"
