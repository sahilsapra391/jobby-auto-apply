#!/usr/bin/env python3
"""What the mirror writes, discovered from the mirror.

**Parity was comparing a subset nobody had written down.** Seven consecutive
green runs compared 11 of the 28 columns `upsert_postings` writes, and exactly
one of the four `_mirror_session` writes: `applications.status`, one string per
session. `phase` was never compared. Neither was `posted_at`, `summary`,
`years_min`, `sections`, `remote`, `tags`, or the whole of
`application_events.payload`, which is where the confirmation text and the
resume filename live.

Nothing was actually diverging. Measured 2026-09-02 over all 22,019 records the
pool and Postgres share, every one of the 28 columns agreed once str-vs-
timestamptz and str-vs-jsonb were compared as values rather than as reprs. So
this was not a latent divergence, it was a latent **blindness**: a whole class
of field could have gone wrong and the clock would have stayed green by
construction.

**So the field set is derived here rather than declared beside the mirror.**
A list written next to the thing it describes is a second declaration, and this
repository has now paid for that three times: the migration table list, the
`GATE_PATHS` list, and the generated-file list `sync_v2_styles --check` walked.
`tests/test_parity_compares_what_the_mirror_writes.py` is the control: it fails
when the mirror gains a column parity does not compare.

Two discovery routes, because the mirror uses two shapes:

  * `postings` builds its column list at runtime from `posting_row()`, so that
    function is called and its keys are the answer. This is the strongest form
    available: parity ends up comparing the mirror's own transform against the
    stored row, rather than a second implementation of it. The 11 lambdas that
    used to do that job were exactly such a second implementation, and being a
    subset is how they drifted.
  * every other table spells its columns out in the INSERT, so they are read
    out of the source.

A table whose columns cannot be discovered raises. It does not fall back to an
empty set: an empty set is a table parity would silently stop covering, which
is the failure this module exists to end.
"""

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

CLASS_B = REPO / "scripts" / "class_b.py"
BACKFILL = REPO / "packages" / "schema" / "backfill.py"

#: `insert into <table> ( <columns> )`, over a source file. Deliberately not a
#: SQL parser: these are literal column lists in f-strings, and anything a
#: regex cannot read is a shape this module must be taught rather than guess at.
_INSERT = re.compile(r"insert\s+into\s+(\w+)\s*\(([^)]*)\)", re.I | re.S)

#: A column list that is interpolated rather than literal. `postings` is the
#: only one, and it is discovered by calling `posting_row` instead.
_INTERPOLATED = re.compile(r"\{")


def _from_source(path):
    """{table: {columns}} for every literal INSERT in a file."""
    found = {}
    text = path.read_text(encoding="utf-8")
    for match in _INSERT.finditer(text):
        table, raw = match.group(1), match.group(2)
        if _INTERPOLATED.search(raw):
            continue
        columns = {c.strip() for c in raw.split(",") if c.strip()}
        found.setdefault(table, set()).update(columns)
    return found


def _posting_columns():
    """The columns `upsert_postings` writes, from the function that builds them.

    Called rather than parsed, because `_columns()` does exactly this at
    runtime and a second reading of it could disagree.
    """
    import backfill
    row = backfill.posting_row({
        "id": "x", "canonicalSource": "s", "canonicalUrl": "https://x/y"})
    # `employer_id` is prepended to the INSERT by hand and is not part of the
    # row dict, so it is added here to match what actually reaches the table.
    return set(row) | {"employer_id"}


def written():
    """{table: frozenset(columns)} the mirror writes. Never empty for a table."""
    found = {}
    for path in (CLASS_B, BACKFILL):
        for table, columns in _from_source(path).items():
            found.setdefault(table, set()).update(columns)
    found["postings"] = _posting_columns()

    empty = sorted(t for t, c in found.items() if not c)
    if empty:
        raise RuntimeError(
            f"no columns discovered for {empty}. A table that discovers as "
            f"empty is one parity would silently stop covering, which is the "
            f"failure this module exists to end.")
    return {t: frozenset(c) for t, c in found.items()}


#: Why a column the mirror writes is not compared. **A reason per entry, and
#: the reason is the point**: an exemption without one is a hole with a
#: comment. Every entry here is a column whose value cannot disagree, because
#: nothing on the file side produced it.
EXEMPT = {
    # -- keys, which are what the comparison is keyed on ------------------
    ("postings", "legacy_id"):
        "the join key. Both sides are keyed on it, so comparing it to itself "
        "is vacuous.",
    ("postings", "employer_id"):
        "a uuid Postgres assigns. The store has no such id, and the employer "
        "is compared through `employers.canonical_name` instead.",
    ("applications", "account_id"):
        "the tenant key, not content. One account today; it is the thing the "
        "comparison is scoped by rather than a field within it.",
    ("applications", "legacy_id"):
        "the join key. The comparison is keyed on it, so it cannot disagree "
        "with itself.",
    ("application_events", "account_id"):
        "the tenant key, not content. It scopes the comparison rather than "
        "being a field within it.",
    ("application_events", "application_id"):
        "a uuid join key. The event is reached through its application, so "
        "the link is exercised by the lookup rather than by a comparison.",
    ("answer_bank", "account_id"):
        "the tenant key, not content. It scopes the comparison rather than "
        "being a field within it.",
    ("answer_bank", "key"):
        "the join key. Both sides are keyed on it, so comparing it is vacuous.",
    ("profiles", "account_id"):
        "the tenant key. There is one profile row per account and it is what "
        "the lookup is scoped by.",
    ("job_scores", "account_id"):
        "the tenant key. A score is per reader, so this scopes the row rather "
        "than describing the job.",
    ("job_scores", "posting_id"):
        "a uuid join key Postgres assigns. The store has no such id; the score "
        "is reached through the posting's legacy id instead.",
    ("job_decisions", "account_id"):
        "the tenant key. A decision is one reader's opinion, so this scopes "
        "the row rather than describing it.",
    ("job_decisions", "posting_id"):
        "a uuid join key Postgres assigns. The decision is compared through "
        "the posting's own legacy id, as `__state__`.",

    # -- written as now(), so there is nothing on the file side ------------
    ("applications", "updated_at"):
        "the mirror sets it to now() on every write and never reads it from "
        "the store, so it cannot disagree with anything. **It is therefore "
        "not a timestamp any lane may display**: v2's Submitted column drew "
        "it and showed the backfill time for all 104 rows, against a "
        "completed event three days to two weeks earlier. The submitted date "
        "lives in application_events.at.",
    ("answer_bank", "updated_at"):
        "written as now(). The store's own entries carry no update time.",
    ("profiles", "updated_at"):
        "written as now() on every mirror pass, so there is nothing on the "
        "file side it could disagree with.",

    # -- provenance the mirror invents ------------------------------------
    ("job_decisions", "decided_from"):
        "a literal the writer stamps ('backfill' from the backfill, 'v2' from "
        "the board), naming which code path wrote the row. The store has no "
        "such field, so there is nothing to compare it against.",
}


def compared():
    """{table: frozenset(columns)} parity is required to compare."""
    out = {}
    for table, columns in written().items():
        keep = frozenset(c for c in columns if (table, c) not in EXEMPT)
        out[table] = keep
    return out


def uncovered(coverage):
    """Columns the mirror writes that `coverage` does not compare.

    `coverage` is {table: set(columns)}, what parity actually looks at. A table
    absent from it counts as covering nothing, which is the honest reading: a
    table nobody compares is a table nobody compares.
    """
    gaps = []
    for table, needed in sorted(compared().items()):
        have = set(coverage.get(table) or ())
        for column in sorted(needed - have):
            gaps.append((table, column))
    return gaps


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.json:
        import json
        print(json.dumps({t: sorted(c) for t, c in written().items()}, indent=2))
        return 0
    for table, columns in sorted(written().items()):
        skip = {c for c in columns if (table, c) in EXEMPT}
        print(f"{table} ({len(columns)} written, {len(skip)} exempt)")
        for column in sorted(columns):
            mark = "  exempt" if column in skip else "compared"
            print(f"    {mark}  {column}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
