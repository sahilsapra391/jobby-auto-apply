#!/usr/bin/env python3
"""What a channel returned last time it was believed, and whether to believe it now.

**This is not guarding a hypothesis. It is guarding 2026-08-30.**

    2026-08-30T01:02Z     found before   found after   status
    greenhouse                   5,909         3,209   ok
    smartrecruiters                996             0   ok
    oracle_orc                   9,357         9,356   ok

Greenhouse returned 54% of its previous yield and reported success. Every
Greenhouse board that came back short had its missing postings evaluated for
closure, and 435 were closed. Nothing in the report could say whether those were
real closures or a truncated fetch, because the sweep never recorded what a
complete fetch of that channel looks like. That is the entire gap, and it went
unnoticed for four days.

SmartRecruiters returned **zero**, reported success, and closed nothing, which is
worth stating because it is the case people expect this to be about. It is
already safe: a channel that returns nothing contributes no keys to `seen`, and
brake 1 in `jobs_store.close_missing` never lets a key it has not seen be
touched. The empty response was never the danger. The half-complete one was.

**So this replaces an inference with a declaration.** `status: ok` is the
connector's opinion of its own HTTP calls. `found` against the same channel's
last believed run is evidence about its coverage, and the two are different
claims that were sharing a field.

The ledger is the device's `sweep_runs`, keyed by channel:

    {"greenhouse": {"found": 14254, "at": "2026-09-07T15:50:47Z"}}

and Phase 3's Postgres table is this with a `board_slug` column and a row per
run rather than a row per channel. Deliberately the same contract so the port
is a change of storage rather than of meaning.

**Written after the postings are committed, never before.** A yield recorded
first is a lie the moment the write fails, and the next sweep would then ratchet
against a run that did not happen. Same ordering as `writes.publish`: commit,
then record. `ingest.run` calls `remember` inside its own `if not dry_run`
block, beside `save_jobs`.

**A refused channel is not a failed one.** It keeps its postings, which are real
and arrived; what it loses is the right to have its *absences* read as closures.
That is the narrowest possible response to "we are not sure this fetch was
complete", and it is exactly brake 1 applied on purpose rather than by accident.
"""

#: A believed run returning less than this share of the channel's own last
#: believed run is a truncation wearing a success.
#:
#: **Calibrated against the incident and against ordinary movement.** Greenhouse
#: returned 54.3% on 2026-08-30. Over this device's 72 recorded sweeps the
#: ordinary channel-to-channel movement is far smaller: boards are added and
#: removed between runs, and a channel routinely moves a few per cent. 70% is
#: below anything a healthy sweep does and comfortably above the event, and a
#: channel that has genuinely halved will re-anchor after one refused sweep
#: rather than being refused for ever (see `remember`).
YIELD_FLOOR = 0.70

#: Below this the ratio is noise rather than evidence. A channel that returned
#: eleven postings and now returns seven has not been truncated, it has three
#: fewer boards. Measured on the 2026-09-07 sweep, 12 of 27 channels returned
#: under 50, and every one of them is a handful of boards rather than a crawl.
MIN_FOUND_FOR_RATCHET = 50

#: The meta key the ledger lives under.
STORE_KEY = "sweepRuns"


def gate(channel, found, previous):
    """Whether this channel's absences may be read as closures.

    Returns `(believed, reason)`. `reason` is empty when believed, and names the
    numbers when not, because a refusal nobody can read is the `closureRefused`
    problem one file over.
    """
    if not isinstance(found, int) or found < 0:
        return False, "the channel reported no usable count for this run"
    last = (previous or {}).get("found")
    if not isinstance(last, int) or last < MIN_FOUND_FOR_RATCHET:
        # No anchor, or an anchor too small to mean anything. Not a refusal:
        # this is the first believed run for the channel and there is nothing
        # to compare it against, which is different from a comparison that
        # failed. The pool-level brake still covers the sweep as a whole.
        return True, ""
    if found >= last * YIELD_FLOOR:
        return True, ""
    return False, (
        f"returned {found} against {last} last time ("
        f"{found / last:.0%}), below the {int(YIELD_FLOOR * 100)}% floor, so "
        f"its absences are not being read as closures this sweep")


def remember(ledger, channel, found, at):
    """Record a believed run, returning the new ledger.

    **Only believed runs are recorded, and that is what stops a refusal
    becoming permanent.** If a refused run were written, the next sweep would
    ratchet against the truncated number and a genuinely halved channel would
    be refused once and believed for ever afterwards -- the ratchet would have
    followed the fault down. If instead nothing is written, the anchor stays at
    the last number anybody believed, and a channel that has really shrunk is
    refused for one sweep and then... also refused, for ever.

    Neither is right on its own, so the anchor decays: a run that is refused
    twice running against the same anchor re-anchors on the second, because two
    independent fetches agreeing is evidence and one is not. `refusals` counts
    consecutive refusals against the current anchor and is cleared by any
    believed run.
    """
    ledger = dict(ledger or {})
    ledger[channel] = {"found": int(found), "at": at, "refusals": 0}
    return ledger


def note_refusal(ledger, channel, found, at):
    """Record that a channel was refused, and re-anchor on the second in a row.

    Returns `(ledger, re_anchored)`.
    """
    ledger = dict(ledger or {})
    entry = dict(ledger.get(channel) or {})
    if not entry:
        return ledger, False
    count = int(entry.get("refusals") or 0) + 1
    if count >= 2:
        # Two independent fetches agree that the channel is smaller than the
        # anchor. That is evidence; one was not.
        ledger[channel] = {"found": int(found), "at": at, "refusals": 0}
        return ledger, True
    entry["refusals"] = count
    ledger[channel] = entry
    return ledger, False
