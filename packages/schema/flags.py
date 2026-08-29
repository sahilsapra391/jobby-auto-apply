#!/usr/bin/env python3
"""Per-account feature flags.

Every cutover in the rebuild is flipped for one account before all of them, so
a flag is the unit of "this account reads Postgres and that one still reads the
file store". They live in `accounts.flags`, a jsonb object, absent meaning off.

Three rules, and each of them exists because the alternative fails silently.

**A name has to be registered.** `enabled(flags, "postgres_read")` against a
flag actually called `postgres_reads` returns False from any dict lookup, and a
cutover switched on that never happens looks exactly like a cutover that is off.
An unregistered name raises here instead, and the error names the nearest
registered flag, because a refusal that does not say what was meant is one
somebody works around by adding the misspelling to the registry.

**A value has to be a real boolean.** `"false"` is truthy in JavaScript and in
Python, and these are read from a Python runner and a TypeScript route handler.
The column refuses a non-boolean, so no reader has to remember; this module
refuses one too, for the case where a value arrives from somewhere other than
the column.

**Absent is off.** That is the only default under which an unfinished cutover
is safe: a new flag added by a deploy is off for every account that has not
been switched, including every account created before the flag existed.

The registry is deliberately small and deliberately here rather than in the
database. A flag is a thing the code branches on, so the set of them is a
property of a release, not of a row; a flag in the database that no code reads
is indistinguishable from one that is off.
"""

import difflib

REGISTRY = {
    "postgres_reads": {
        "description": "Read the job pool and decisions from Postgres rather "
                       "than the file store. Writes go to both regardless "
                       "while this is being rolled out.",
        "phase": "1",
    },
    "live_board": {
        "description": "Subscribe the board to the tenant channel over WSS "
                       "and apply events, instead of re-fetching a snapshot.",
        "phase": "2",
    },
    "cloud_sourcing": {
        "description": "Take the job pool from the cloud sweep rather than "
                       "the device scan daemon.",
        "phase": "3",
    },
}


class UnknownFlag(KeyError):
    """A name that is not in the registry. Never raised for an unset flag."""

    def __str__(self):                                   # KeyError repr()s
        return self.args[0] if self.args else ""


class BadFlagValue(ValueError):
    """A stored value that is not a JSON boolean."""


class NoSuchAccount(LookupError):
    """Asked for the flags of an account that does not exist."""


def enabled(flags, name):
    """Whether `name` is on for the account whose flags these are.

    `flags` may be None, which is a row read before the column was backfilled
    or a caller that had nothing. That is off, not a traceback.
    """
    if name not in REGISTRY:
        near = difflib.get_close_matches(name, sorted(REGISTRY), n=1, cutoff=0.5)
        hint = f" Did you mean {near[0]}?" if near else ""
        raise UnknownFlag(
            f"{name!r} is not a registered flag.{hint} Register it in "
            f"packages/schema/flags.py so a misspelling cannot read as off "
            f"forever. Known: {', '.join(sorted(REGISTRY))}.")

    if not flags:
        return False

    if name not in flags:
        return False

    value = flags[name]
    if value is not True and value is not False:
        raise BadFlagValue(
            f"flag {name!r} holds {value!r}, which is not a JSON boolean. "
            f"A string is truthy in JavaScript and in Python, so a flag that "
            f"can hold one is on in the browser and off on the device.")
    return value


def for_account(conn, account_id):
    """The stored flags for one account.

    Raises rather than returning `{}` when the account does not exist. "No such
    account" and "an account with nothing switched on" are different facts, and
    a caller that cannot tell them apart cannot notice a deleted account.
    """
    with conn.cursor() as cur:
        cur.execute("select flags from accounts where id = %s", (str(account_id),))
        row = cur.fetchone()
    if row is None:
        raise NoSuchAccount(f"no account {account_id}")
    return row[0] or {}


def describe():
    """The registry as text, for `--help` and for docs/adr."""
    lines = []
    for name in sorted(REGISTRY):
        entry = REGISTRY[name]
        lines.append(f"{name}  (phase {entry['phase']})")
        lines.append(f"    {entry['description']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
