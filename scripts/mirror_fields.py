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
    """({table: {columns}}, {tables whose column list is interpolated}).

    **The skipped set is returned, not swallowed.** The first version dropped
    an interpolated INSERT silently, and the first table added after that
    -- `provider_links`, built from `provider_row`'s keys -- was invisible to
    discovery, uncovered by parity, and reported as fully covered. That is the
    exact failure this module exists to end, reproduced inside it within an
    hour. A table this cannot read has to be named, so `written` can refuse.
    """
    found, interpolated = {}, set()
    text = path.read_text(encoding="utf-8")
    for match in _INSERT.finditer(text):
        table, raw = match.group(1), match.group(2)
        if _INTERPOLATED.search(raw):
            interpolated.add(table)
            continue
        columns = {c.strip() for c in raw.split(",") if c.strip()}
        found.setdefault(table, set()).update(columns)
    return found, interpolated


def _posting_columns():
    """The columns `upsert_postings` writes, from the function that builds them.

    Called rather than parsed, because `_columns()` does exactly this at
    runtime and a second reading of it could disagree.
    """
    import backfill
    row = backfill.posting_row({
        "id": "x", "canonicalSource": "s", "canonicalUrl": "https://x/y"})
    # The INSERT prepends columns of its own, outside the row dict. Read from
    # `backfill` rather than listed here: this was `{"employer_id"}` written
    # out, and when the INSERT grew `employer_decided_by` and
    # `employer_decided_at` the list went one short without failing -- a
    # smaller discovered set is a smaller comparison, not an error, which is
    # the exact silence this module was built to end.
    return set(row) | set(backfill.PREPENDED_COLUMNS)


def _provider_columns():
    """The columns `_mirror_providers` writes, from `provider_row`."""
    import class_b
    # `account_id` and `updated_at` are added around the row in the statement.
    return set(class_b.provider_row("openai", {})) | {"account_id", "updated_at"}


def _application_columns():
    """The columns the class B mirror writes to `applications`.

    Two writers, disjoint sets: `_application_id` writes everything a session
    is authoritative for, and `_mirror_roles` writes the six the role index
    owns. Plus the keys and the write time, which the statements add around
    the rows.

    This entry exists because widening `session_row` turned that INSERT into an
    interpolated one, and the refusal above fired on the very next run. That is
    the guard working: before it, the table would have gone quietly
    undiscovered and parity would have reported full coverage of nothing.
    """
    import class_b
    return (set(class_b.session_row({})) | set(class_b.role_row({}))
            | {"account_id", "legacy_id", "updated_at"})


def _board_meta_columns():
    """The columns written to `board_meta`, from both its writers.

    `_mirror_meta` owns the scan columns and `_mirror_search` owns
    `search_config`. Disjoint by construction: a column in both would be nulled
    by whichever ran last.
    """
    import class_b
    scan, saved = set(class_b.meta_row({})), set(class_b.search_row({}))
    overlap = scan & saved
    if overlap:
        raise RuntimeError(
            f"two writers claim {sorted(overlap)} on board_meta. Whichever "
            f"runs last wins and the other's value is lost every pass.")
    return scan | saved | {"account_id", "updated_at"}


#: Tables whose INSERT interpolates its column list, and the function that
#: answers for each. **Every interpolated table must appear here**; `written`
#: refuses otherwise rather than quietly covering none of it.
_EXPLICIT = {
    "postings": _posting_columns,
    "provider_links": _provider_columns,
    "applications": _application_columns,
    "board_meta": _board_meta_columns,
}


def written():
    """{table: frozenset(columns)} the mirror writes. Never empty for a table."""
    found, interpolated = {}, set()
    for path in (CLASS_B, BACKFILL):
        literal, skipped = _from_source(path)
        for table, columns in literal.items():
            found.setdefault(table, set()).update(columns)
        interpolated |= skipped

    unreadable = sorted(t for t in interpolated if t not in _EXPLICIT)
    if unreadable:
        raise RuntimeError(
            f"the mirror writes {unreadable} with an interpolated column list, "
            f"which this module cannot read, and no entry in _EXPLICIT answers "
            f"for it. Add one. A table nobody can discover is a table parity "
            f"covers none of, reported as fully covered.")
    for table, resolve in _EXPLICIT.items():
        found[table] = resolve()

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
#:
#: **And every entry states the property it gives up, not only why the column
#: is different.** Those are different sentences, and the difference has now
#: cost three times. A reason can be perfectly true of the *column* and false
#: of the *property* the column stands for, and when it is, the exemption reads
#: as caution while a class of fault walks through it.
#:
#: The one that cost most: `("postings", "employer_id")` read "a uuid Postgres
#: assigns. The store has no such id, and the employer is compared through
#: `employers.canonical_name` instead." The first sentence is true. The second
#: was not. `posting_row` returns thirty columns and none of them is the
#: employer, so parity asked "does employer row E have the right name" and
#: never "does posting P point at the right employer". Measured 2026-09-06 by
#: driving `verify_parity.compare`: a posting filed under the wrong employer,
#: with all thirty compared columns identical, produced no finding at all,
#: while changing `title` produced one. The employer resolver's entire output
#: is repointing postings.
#:
#: So each entry ends with a `GIVES UP:` clause naming what is now unchecked,
#: or saying that nothing is. Writing that sentence is what forces the author
#: to notice when the honest answer is "quite a lot", and
#: `tests/test_mirror_fields.py` requires it.
EXEMPT = {
    # -- keys, which are what the comparison is keyed on ------------------
    ("postings", "employer_decided_by"):
        "a literal this writer stamps, naming the rule that chose the "
        "employer -- 'ingest' from the arrival path at backfill.py:512, or a "
        "resolver rule's own name. The store has no counterpart, so there is "
        "nothing to compare it against. GIVES UP: nothing about the employer "
        "edge itself, which `verify_parity.compare` now compares directly. It "
        "gives up only cross-checking the label, and the label exists so a "
        "reader can tell which rule decided a row once there are two.",
    ("postings", "employer_decided_at"):
        "stamped as now() by the insert that decides an employer, so there is "
        "nothing on the file side it could disagree with. GIVES UP: nothing "
        "the file store knows. It is not a freshness check either -- see the "
        "updated_at entries for why a column written by the write cannot "
        "report a write that is not happening.",
    ("postings", "legacy_id"):
        "the join key. Both sides are keyed on it, so comparing it to itself "
        "is vacuous. GIVES UP: nothing. A row missing from either side is "
        "reported as only_in_file or only_in_postgres before the field loop "
        "runs.",
    ("postings", "employer_id"):
        "a uuid Postgres assigns. The store has no such id of its own, so the "
        "column cannot disagree with anything. GIVES UP: nothing, since "
        "2026-09-06. It used to give up the entire employer edge -- which "
        "posting points at which employer -- on the strength of a second "
        "sentence claiming the employer was compared through "
        "`employers.canonical_name`. That compares an employer's name, not a "
        "posting's employer. `verify_parity.compare` now compares the edge "
        "itself, normalised through `norm_company` against "
        "`employers.norm_name`, with two named allowances.",
    ("applications", "account_id"):
        "the tenant key, not content. One account today; it is the thing the "
        "comparison is scoped by rather than a field within it. GIVES UP: "
        "cross-tenant attribution, that this row belongs to the account the "
        "comparison is scoped to. Vacuous with one account and load-bearing "
        "with two, so this belongs on Phase 5's list rather than being "
        "permanent.",
    ("applications", "legacy_id"):
        "the join key. The comparison is keyed on it, so it cannot disagree "
        "with itself. GIVES UP: nothing, for the same reason. Presence on "
        "each side is compared; a key's equality with itself is not.",
    ("edit_outcomes", "account_id"):
        "the tenant key, not content. It scopes the comparison rather than "
        "being a field within it. GIVES UP: cross-tenant attribution, as "
        "above.",
    ("edit_outcomes", "edit_id"):
        "the join key. Both sides are keyed on it, so comparing it to itself "
        "is vacuous. GIVES UP: nothing. It is the key both sides are read by, "
        "so a row on one side only is reported as a presence difference.",
    ("application_events", "account_id"):
        "the tenant key, not content. It scopes the comparison rather than "
        "being a field within it. GIVES UP: cross-tenant attribution, as "
        "above.",
    ("application_events", "application_id"):
        "a uuid join key. The event is reached through its application, so "
        "the link is exercised by the lookup rather than by a comparison. "
        "GIVES UP: nothing. Events are compared within an application, so one "
        "filed under the wrong application is a missing event on one and an "
        "extra on the other.",
    ("answer_bank", "account_id"):
        "the tenant key, not content. It scopes the comparison rather than "
        "being a field within it. GIVES UP: cross-tenant attribution, as "
        "above.",
    ("answer_bank", "key"):
        "the join key. Both sides are keyed on it, so comparing it is "
        "vacuous. GIVES UP: nothing. An answer present on one side only is "
        "reported by the key comparison, and the delete ceiling refuses a "
        "mass disappearance before it could be mistaken for one.",
    ("profiles", "account_id"):
        "the tenant key. There is one profile row per account and it is what "
        "the lookup is scoped by. GIVES UP: cross-tenant attribution, as "
        "above.",
    ("job_scores", "account_id"):
        "the tenant key. A score is per reader, so this scopes the row rather "
        "than describing the job. GIVES UP: cross-tenant attribution, as "
        "above.",
    ("job_scores", "posting_id"):
        "a uuid join key Postgres assigns. The store has no such id; the "
        "score is reached through the posting's legacy id instead. GIVES UP: "
        "nothing, and this is the edge worth stating rather than assuming, "
        "because `postings.employer_id` looked identical and gave up the "
        "employer edge entirely. The difference is that this edge IS the join "
        "the comparison runs over: a score attached to the wrong posting is "
        "read back against that posting's file record and shows as a score "
        "mismatch. The employer edge had no such join, which is why nothing "
        "caught it.",
    ("job_decisions", "account_id"):
        "the tenant key. A decision is one reader's opinion, so this scopes "
        "the row rather than describing it. GIVES UP: cross-tenant "
        "attribution, as above.",
    ("job_decisions", "posting_id"):
        "a uuid join key Postgres assigns. The decision is compared through "
        "the posting's own legacy id, as `__state__`. GIVES UP: nothing, for "
        "the reason above. The decision is resolved through this edge to "
        "produce `__state__` for a legacy id, so a decision on the wrong "
        "posting surfaces as a state mismatch on both of them.",

    # -- written as now(), so there is nothing on the file side ------------
    ("applications", "updated_at"):
        "the mirror sets it to now() on every write and never reads it from "
        "the store, so it cannot disagree with anything. **It is therefore "
        "not a timestamp any lane may display**: v2's Submitted column drew "
        "it and showed the backfill time for all 104 rows, against a "
        "completed event three days to two weeks earlier. The submitted date "
        "lives in application_events.at. GIVES UP: whether the mirror is "
        "current. Nothing here would catch a mirror that stopped, because the "
        "column is stamped by the write that is not happening. "
        "`stale_sweep_warning` answers that from the pool's own mtime "
        "instead, which is the right place for it.",
    ("answer_bank", "updated_at"):
        "written as now(). The store's own entries carry no update time. "
        "GIVES UP: whether the mirror is current, as above.",
    ("profiles", "updated_at"):
        "written as now() on every mirror pass, so there is nothing on the "
        "file side it could disagree with. GIVES UP: whether the mirror is "
        "current, as above.",

    # -- provenance the mirror invents ------------------------------------
    ("provider_links", "account_id"):
        "the tenant key. One reader's connections, and it is what the lookup "
        "is scoped by rather than a field within it. GIVES UP: cross-tenant "
        "attribution, as above.",
    ("provider_links", "updated_at"):
        "written as now() on every mirror pass, so there is nothing on the "
        "file side it could disagree with. GIVES UP: whether the mirror is "
        "current, as above.",
    ("provider_links", "provider"):
        "the join key. Both sides are keyed on the provider id, so comparing "
        "it is vacuous. GIVES UP: nothing. Presence is compared per provider "
        "id, so a link filed under the wrong provider is a missing row and an "
        "extra one.",

    ("board_meta", "account_id"):
        "the tenant key. One board_meta row per account, and it is what the "
        "lookup is scoped by. GIVES UP: cross-tenant attribution, as above.",
    ("board_meta", "updated_at"):
        "written as now() on every mirror pass, so there is nothing on the "
        "file side it could disagree with. GIVES UP: whether the mirror is "
        "current, as above.",

    ("job_decisions", "decided_from"):
        "a literal the writer stamps ('backfill' from the backfill, 'v2' from "
        "the board), naming which code path wrote the row. The store has no "
        "such field, so there is nothing to compare it against. GIVES UP: the "
        "provenance of a decision, whether the board or a backfill wrote it. "
        "Named rather than waved past, because provenance is the one property "
        "an artefact cannot answer about itself and this project has been "
        "wrong about it three times. If a second writer of job_decisions ever "
        "matters, this is where it would have to be checked.",
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
