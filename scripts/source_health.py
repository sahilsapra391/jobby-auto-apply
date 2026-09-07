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

#: Sources held to a stricter standard, and why each is on the list.
#:
#: **48 of the 52 applications submitted through the board came from these
#: two.** "Detected within two sweeps" is a reasonable guarantee for a source
#: contributing a hundred postings and too slow for the two that carry the
#: pipeline: eight hours of a dead greenhouse is eight hours in which the only
#: thing the user would notice is that nothing new appeared, which is also what
#: a quiet day looks like.
#:
#: One sweep at 70%, which is the coverage gate's own floor. So for these two
#: the two thresholds coincide: the sweep whose absences stop being believed is
#: the sweep the user is told about. That is the intended coupling rather than
#: a coincidence -- refusing to close on a source and not mentioning it is the
#: quiet half of a fault this file has already been bitten by.
STRICT_SOURCES = {
    "greenhouse": {"floor": 0.70, "sweeps": 1,
                   "why": "37.8% of the last sweep's kept postings"},
    "ashby": {"floor": 0.70, "sweeps": 1,
              "why": "the second-largest ats source"},
}


def floor_for(source):
    return STRICT_SOURCES.get(source, {}).get("floor", DETECTION_FLOOR)


def sweeps_for(source):
    return STRICT_SOURCES.get(source, {}).get("sweeps", CONSECUTIVE_SWEEPS)


#: One board per strict source, fetched every sweep and checked on its own.
#:
#: **A source-level number can look fine while a board is broken**, which is
#: the same blindness the per-board circuit breaker has in reverse: greenhouse
#: returning 14,000 postings tells you nothing about whether it still parses
#: the one board you actually apply through. So one large, stable board per
#: strict source is asserted individually: it was fetched, it parsed, and its
#: yield is within `CANARY_TOLERANCE` of last time.
#:
#: Chosen by size, measured over the live pool 2026-09-07: spacex holds 583
#: greenhouse records, the largest of 353 boards (stripe 225 is the alternate);
#: snowflake holds 148 ashby records against airwallex's 181, and is preferred
#: for being the more durable institution rather than the larger board.
#: Largest matters because a partial parse break shows as a fraction, and 10%
#: of 583 is visible where 10% of 12 is not.
CANARIES = {"greenhouse": "spacex", "ashby": "snowflake"}

#: How far a canary's yield may move between sweeps before it is reported.
#:
#: **Not corpus-derived to the same standard as the pool brake, and worth
#: saying so.** Per-board history does not exist: `ingest-runs.log` records
#: per-channel totals and nothing per board, so there is no distribution to
#: take a percentile of. What is measurable is arrivals: over the last ten days
#: spacex first-saw a mean 38 postings a day against 583 held, snowflake 9.4
#: against 148 -- roughly 1.1% and 1.06% of the board per four-hour sweep, and
#: those are gross arrivals rather than net movement, so the net is smaller.
#: 10% is about nine times the largest observed movement.
#:
#: It is also a **lower bound**, because the pool is search-gated and the
#: canary compares the board's whole response. The first weeks of
#: `canaryYield` in the ledger are the data this should be re-derived from,
#: and until then this constant is a specification rather than a measurement.
CANARY_TOLERANCE = 0.10


def observe(entry, found, previous_anchor, source=None):
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
                        if ratio < floor_for(source) else 0)
    return entry


def canary_yield(source, postings):
    """How many postings the canary board returned, or None if unknowable.

    Reads the §3 record key off the postings rather than asking the connector,
    which is why the record contract had to land for a source before its canary
    could. `None` is "could not look" and is never `0`: a source that emits no
    key at all cannot be checked, and a canary that returned nothing is the
    loudest thing this function has to say.
    """
    token = CANARIES.get(source)
    if not token:
        return None
    count, saw_a_key = 0, False
    for posting in postings or ():
        key = _stored_key(posting)
        if not key:
            continue
        saw_a_key = True
        if key[0] == token:
            count += 1
    return count if saw_a_key else None


#: Fields a posting must carry for the board to be usable, and the share of the
#: canary's postings that must carry each.
#:
#: **This closes the one failure mode nothing else here can see.** Every other
#: way greenhouse or ashby can break moves a number: a URL scheme change 404s
#: every board, an auth requirement 401s them, a response-shape change empties
#: `jobs` so the count goes to zero, a new pagination default truncates it.
#: All of those trip the anchor, the canary count, or the channel status inside
#: one sweep.
#:
#: A **field** change moves nothing. `absolute_url` renamed, `location.name`
#: becoming `location.text`, `first_published` dropped: the response still
#: parses, the count is identical, the canary is happy, and the postings enter
#: the pool with no link, no location or no date -- where the search gate
#: quietly filters them or the board shows a card that goes nowhere. The count
#: is right and the thing the count summarises is not, which is the shape this
#: project has now paid for in six different places.
#:
#: Not 100%, because a real board legitimately has postings with no salary and
#: the occasional one with a blank location. The thresholds are what a healthy
#: board clears with room, and a field that genuinely becomes optional upstream
#: is a threshold to lower deliberately rather than a check to delete.
CANARY_FIELDS = {"canonicalUrl": 0.95, "title": 0.95, "boardKey": 0.90}


def canary_fields(source, postings):
    """Share of the canary's postings carrying each required field.

    Returns `{}` when the canary contributed nothing this sweep, which is
    "could not look" and is `canary_problem`'s business rather than this
    function's.
    """
    token = CANARIES.get(source)
    mine = [p for p in (postings or ())
            if token and (_stored_key(p) or ("",))[0] == token]
    if not mine:
        return {}
    out = {}
    for field in CANARY_FIELDS:
        have = sum(1 for p in mine if _present(p, field))
        out[field] = round(have / len(mine), 3)
    return out


def _present(posting, field):
    value = (posting or {}).get(field)
    if field == "boardKey":
        return _stored_key(posting) is not None
    return bool(str(value or "").strip())


def canary_field_problem(source, shares):
    """A canary that still counts right and has stopped carrying a field."""
    token = CANARIES.get(source)
    if not token or not shares:
        return None
    thin = sorted((f, shares[f]) for f in CANARY_FIELDS
                  if f in shares and shares[f] < CANARY_FIELDS[f])
    if not thin:
        return None
    field, share = thin[0]
    return {"kind": "canary_field_missing", "source": source, "board": token,
            "field": field, "share": share,
            "what": (f"{source} canary board {token} still returns the right "
                     f"number of postings, and only {share * 100:.0f}% of them "
                     f"carry {field}"),
            "takes": (f"the board's response shape changed without failing; "
                      f"diff one posting against scripts/sources/{source}.py "
                      f"and find what {field} is read from now")}


def canary_problem(source, count, previous, unchanged_slugs=()):
    """A problem about the canary board, or None.

    Three answers and they are not the same fact. A canary that was told
    "unchanged" returned nothing legitimately and is not checkable this sweep.
    A canary that returned nothing when it was asked is broken or gone. A
    canary whose yield moved more than the tolerance parsed, and parsed
    differently.
    """
    token = CANARIES.get(source)
    if not token:
        return None
    if token in set(unchanged_slugs or ()):
        return None
    if count is None:
        # The source stopped emitting record keys, which is a regression in the
        # contract rather than a fact about the board -- and it takes the
        # canary with it, so it is said rather than swallowed.
        return {"kind": "canary_unreadable", "source": source, "board": token,
                "what": (f"{source} no longer emits a record key, so its canary "
                         f"board {token} cannot be checked"),
                "takes": (f"scripts/sources/{source}.py should pass board_slug "
                          f"and external_id to base.posting")}
    if count == 0:
        return {"kind": "canary_silent", "source": source, "board": token,
                "count": 0,
                "what": (f"{source} canary board {token} was fetched and "
                         f"returned no postings"),
                "takes": (f"open the board's API response by hand; either the "
                          f"tenant moved or the parse broke")}
    if not isinstance(previous, int) or previous <= 0:
        return None
    drift = abs(count - previous) / previous
    if drift <= CANARY_TOLERANCE:
        return None
    return {"kind": "canary_moved", "source": source, "board": token,
            "count": count, "previous": previous, "drift": round(drift, 3),
            "what": (f"{source} canary board {token} returned {count} postings "
                     f"against {previous} last sweep, {drift * 100:.0f}% "
                     f"movement"),
            "takes": ("compare the board's response with the last one; a "
                      "partial parse break moves the count without failing")}


def _stored_key(posting):
    # Imported here rather than at module import, because `packages/schema` is
    # not on the path for every caller of this module and a canary check is not
    # worth making it a hard dependency of the alarm.
    try:
        import board_key
    except ImportError:                                  # pragma: no cover
        return None
    return board_key.stored(posting)


def under_anchor(ledger):
    """Sources at or past the consecutive-low threshold, worst ratio first."""
    out = []
    for source, entry in sorted((ledger or {}).items()):
        if int((entry or {}).get("lowRuns") or 0) < sweeps_for(source):
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
    return (channel_failures(by_channel) + canary_problems(ledger)
            + under_anchor(ledger) + disabled_boards(boards))


def canary_problems(ledger):
    """Canary problems recorded by the last sweep, read back off the ledger."""
    out = []
    for source in sorted(CANARIES):
        stored = ((ledger or {}).get(source) or {}).get("canaryProblem")
        if stored:
            out.append(stored)
    return out


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
