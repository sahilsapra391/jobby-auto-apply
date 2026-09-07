#!/usr/bin/env python3
"""When a board stops being asked, and when a whole source stops being retired.

**A dead board and a bad afternoon are different facts, and counting them
together retires the registry during an outage.** A 404 from
`boards-api.greenhouse.io/v1/boards/<token>/jobs` means the tenant does not
exist: the company renamed its board, moved ATS, or the token was never right.
A 500, a timeout, or a connection reset means Greenhouse is unwell for a few
minutes and every one of its 353 boards will say the same thing at the same
time. One is evidence about the board. The other is evidence about the day.

So only a definitive answer counts toward the breaker. A transient failure is
recorded -- `last_failed_at` and `last_failure`, because the occurrence able to
name a cause is the one guaranteed not to be reproducible later -- and does not
move the counter. The cost of that choice is a wasted request per sweep against
a board that is genuinely gone but only ever times out. The cost of the other
choice is one bad afternoon at an ATS taking every board it serves out of the
registry, and a registry does not recover from that on its own.

**The second rule is the one this codebase has learned twice.** A per-item brake
cannot see a failure correlated across items: `MAX_CLOSED_SHARE` is per board
and 2026-08-30 closed 1,860 records without tripping it once. The same shape is
available here and it is worse, because a retired board stops being *asked*
rather than merely having its absences believed. If an ATS changes its URL
scheme, every board under it returns 404 -- a definitive answer, arrived at
honestly, from a healthy server -- and a per-board breaker retires the whole
source in `FAILURE_CEILING` sweeps. `trip_plan` therefore refuses to disable
more than `MAX_SOURCE_TRIP_SHARE` of one source's live boards in a single pass
and says so, on the same reasoning as brake 5: a correlated anomaly is evidence
about the source rather than about any of its boards.

Nothing here talks to a database. It takes rows and returns decisions, so the
rules can be tested without one and the storage can move from the file store to
Postgres to whatever comes next without the rules moving with it.
"""

#: Consecutive definitive failures before a board stops being enqueued.
#:
#: Three rather than one, because a tenant token can 404 for a few minutes
#: while an employer republishes its board, and because the sweep runs every
#: four hours: three is half a day of the same definitive answer.
FAILURE_CEILING = 3

#: The most of one source's live boards that a single pass may retire.
#:
#: Measured 2026-09-07 across the registry: greenhouse 353 boards with 6 dead,
#: ashby 194 with 2, lever 91 with 1. So an ordinary pass retires under 2% of
#: any source and 10% is far above anything healthy while being far below the
#: scheme-change case, which is all of them at once.
MAX_SOURCE_TRIP_SHARE = 0.10

#: Below this a share is noise. Four live boards losing one is 25% and means
#: nothing.
MIN_SOURCE_FOR_SHARE_CHECK = 20

#: HTTP statuses that are an answer about the board rather than about the day.
#: 404 is the tenant not existing. 410 is it having existed and being gone.
DEFINITIVE_STATUSES = frozenset({404, 410})


def classify(status=None, error=""):
    """`definitive`, `transient`, or `ok` for one board's fetch outcome.

    `status` is the HTTP status when there was a response at all. `error` is
    set when there was not -- a timeout, a DNS failure, a reset -- and those are
    always transient, because no server said anything about this board.
    """
    if status is None:
        return "transient" if error else "ok"
    if status in DEFINITIVE_STATUSES:
        return "definitive"
    if 200 <= status < 300:
        return "ok"
    return "transient"


def apply_outcome(board, status=None, error="", at=None):
    """The board row after one fetch. Pure: returns a new dict.

    A success clears the counter and the disable, because a board that answers
    is a board that works and leaving it retired would need a person to notice.
    """
    board = dict(board or {})
    # Normalised on every path, not only the ones that increment it. A board
    # that has only ever failed transiently otherwise carries no counter at
    # all, and a caller reading the field directly gets a KeyError rather than
    # the zero it means. Found by the test for the transient rule, which read
    # the counter after ten 503s.
    board["consecutive_failures"] = int(board.get("consecutive_failures") or 0)
    kind = classify(status, error)
    if kind == "ok":
        board["consecutive_failures"] = 0
        board["last_ok_at"] = at
        board["disabled_at"] = None
        board["disabled_reason"] = None
        return board
    board["last_failed_at"] = at
    board["last_failure"] = (f"http {status}" if status is not None
                             else str(error)[:200]) or "unknown"
    if kind == "definitive":
        board["consecutive_failures"] = int(board.get("consecutive_failures") or 0) + 1
    return board


def should_trip(board):
    """Whether this board has earned being taken out of the rotation."""
    if board.get("disabled_at"):
        return False
    return int(board.get("consecutive_failures") or 0) >= FAILURE_CEILING


def trip_plan(boards, at=None):
    """Which boards to disable this pass, and which to refuse to.

    `boards` is every board for **one source**, live and disabled. Returns
    `(to_disable, refusal)` where `refusal` is None or a dict naming why the
    whole set was left alone.
    """
    live = [b for b in boards if not b.get("disabled_at")]
    candidates = [b for b in live if should_trip(b)]
    if not candidates:
        return [], None
    share = len(candidates) / len(live) if live else 0.0
    if (len(live) >= MIN_SOURCE_FOR_SHARE_CHECK
            and share > MAX_SOURCE_TRIP_SHARE):
        return [], {
            "source": (candidates[0].get("source") if candidates else None),
            "live": len(live),
            "wouldDisable": len(candidates),
            "share": round(share, 4),
            "why": "more than %d%% of one source's live boards at once; "
                   "treating this as a change at the ATS rather than %d dead "
                   "boards" % (int(MAX_SOURCE_TRIP_SHARE * 100), len(candidates)),
        }
    return [dict(b, disabled_at=at,
                 disabled_reason=(f"{b.get('consecutive_failures')} consecutive "
                                  f"definitive failures, last: "
                                  f"{b.get('last_failure') or 'unknown'}"))
            for b in candidates], None


def enqueueable(boards):
    """The boards a sweep should ask. Disabled ones are skipped, not deleted."""
    return [b for b in boards if not b.get("disabled_at")]
