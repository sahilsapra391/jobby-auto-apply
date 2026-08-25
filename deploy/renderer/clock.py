#!/usr/bin/env python3
"""One timezone for everything anyone reads.

The store keeps UTC and always will: it is unambiguous, it sorts, and it does
not move twice a year. But nothing a person reads should be in UTC, because
"last scan 20:00" is a small subtraction every single time.

Everything displayed is Eastern. Not a fixed offset — `America/New_York`, so it
is EDT for eight months and EST for four and neither is ever an hour wrong. A
hardcoded -5 would have been correct in February and wrong today.

The zone matters for dates too, not just clocks. 2026-08-21T01:30Z is the 20th
in Eastern, so slicing the first ten characters off a UTC stamp shows tomorrow.
`date_label` converts first and then slices.
"""

from datetime import datetime, timezone

ZONE_NAME = "America/New_York"

try:
    from zoneinfo import ZoneInfo

    ZONE = ZoneInfo(ZONE_NAME)
except Exception:  # pragma: no cover - only on a build with no tz database
    # Falling back to UTC rather than to a guessed offset. A board that says
    # UTC and means it is honest; a board that says EST and is an hour out is
    # worse than one that never claimed Eastern at all.
    ZONE = timezone.utc

STAMP_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S")


def parse(stamp):
    """An aware UTC datetime from a stored stamp, or None."""
    text = str(stamp or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local(stamp):
    """The same instant in Eastern, or None."""
    parsed = parse(stamp)
    return parsed.astimezone(ZONE) if parsed else None


def zone_label(stamp=None):
    """EDT or EST, whichever applied at that instant."""
    when = local(stamp) or datetime.now(ZONE)
    return when.strftime("%Z") or "ET"


def time_label(stamp):
    """`16:00 EDT`, or an empty string when there is nothing to show."""
    when = local(stamp)
    return f"{when:%H:%M} {when:%Z}" if when else ""


def clock_label(stamp):
    """`16:00:07 EDT`, for the places that show seconds."""
    when = local(stamp)
    return f"{when:%H:%M:%S} {when:%Z}" if when else ""


def date_label(stamp):
    """The Eastern calendar date, `2026-08-20`, not the UTC one."""
    when = local(stamp)
    return f"{when:%Y-%m-%d}" if when else ""


def stamp_label(stamp):
    """`20 Aug 2026, 16:00 EDT`."""
    when = local(stamp)
    return f"{when:%d %b %Y, %H:%M} {when:%Z}" if when else ""


def now_label():
    """The same, for right now."""
    when = datetime.now(ZONE)
    return f"{when:%d %b %Y, %H:%M} {when:%Z}"
