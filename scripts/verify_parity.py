#!/usr/bin/env python3
"""Do the two stores agree? Field by field, and non-zero when they do not.

    python3 scripts/verify_parity.py
    python3 scripts/verify_parity.py --json
    python3 scripts/verify_parity.py --limit 20     # how many to print

Exit codes, and they are the whole interface:

    0   the stores agree, and a real comparison happened
    1   they disagree, or the run could not conclude

Everything in here is arranged so that 0 cannot be returned for the wrong
reason. A parity job that can quietly decline to answer is a parity job that
reports green while checking nothing, which is this repository's oldest failure
shape and the one the seven-day clock would otherwise be measuring.

**It compares the file store against the new normalised tables**, never against
the legacy jsonb one. docs/parity.md carries that reasoning: the legacy table is
a snapshot taken four hours before the plan's baseline, so a job pointed at it
would open with 4,961 divergences and not one of them a fault, and a red build
nobody believes is worse than no build.

**A record present on one side only is a divergence, not a filter.** Comparing
the intersection is what makes a parity job green, and the intersection is
exactly where the 4,779 missing records hid.

**Mid-write is real and is not an excuse.** Three apply slots write
concurrently and a sweep runs every few hours, so parity reading between the
Postgres commit and the file write sees a momentary difference. On a divergence
it re-reads once. If the difference persists it is reported. If it clears, it is
still recorded, at a lower level and with both readings, because a path that is
always mid-write when parity looks is a path with a very slow write. What it
never does is drop a divergence on the grounds that it was probably a race: the
board has published an empty Submitted tab over a good one before, and that is
what that reasoning buys.
"""

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import backfill  # noqa: E402
import dual_write  # noqa: E402
import jobs_store  # noqa: E402
import class_b  # noqa: E402
import mirror_fields  # noqa: E402

# The fields compared, and the file-store expression for each. Anything not
# here is either dropped by BACKFILL.md with a reason or is a container whose
# parts are compared individually.
#: The posting columns parity compares, discovered from the mirror.
#:
#: **This used to be eleven lambdas, and being a subset is how they drifted.**
#: They were a second implementation of `backfill.posting_row`, and they
#: covered 11 of the 28 columns it writes: `posted_at`, `summary`, `years_min`,
#: `sections`, `remote`, `tags` and eleven more were never compared, through
#: seven consecutive green runs. Nothing was diverging -- measured over all
#: 22,019 shared records, every column agreed -- but a whole class of field
#: could have gone wrong with the clock green by construction.
#:
#: So the expected side is now the mirror's own transform, called rather than
#: reimplemented, and the field set is derived. `mirror_fields` explains why
#: neither is written down twice.
FIELDS = sorted(mirror_fields.compared()["postings"])


def same_value(want, got):
    """Is this the same value in two representations?

    **The store speaks JSON and Postgres speaks its own types**, so a naive
    comparison calls every timestamp and every jsonb column a divergence. The
    first run of the widened comparison reported `first_seen_at` and
    `last_seen_at` differing on 100% of records, `posted_at` on 90% and
    `sources` on 76%, and every one of them was `2026-08-11T17:58:07Z` against
    `datetime(2026, 8, 11, 17, 58, 7, tzinfo=utc)`. Reporting those as
    divergences would have been worse than not comparing at all: a check that
    cries wolf on 22,000 rows is a check nobody reads.

    Emptiness is one value. The store writes `""` where Postgres holds NULL,
    and neither is a statement about the field.
    """
    empty = (None, "", [], {})
    if want in empty and got in empty:
        return True
    if isinstance(got, str):
        got = got.strip() or None
    if isinstance(want, str):
        want = want.strip() or None
    if want in empty and got in empty:
        return True
    if want == got:
        return True

    if isinstance(got, _dt.datetime) or isinstance(want, _dt.datetime):
        a, b = _as_instant(want), _as_instant(got)
        return a is not None and a == b

    # jsonb comes back parsed; the mirror hands psycopg a JSON string.
    if isinstance(got, (dict, list)) or isinstance(want, (dict, list)):
        return _as_json(want) == _as_json(got)

    # A number written into a text column. `job_scores.config_version` is
    # `text` and the pool carries an int, so the mirror's own transform
    # produces 23 and the stored value is '23'. That is one value in two
    # representations, and reading it as a divergence produced 22,001 findings
    # on the first widened run, which is the shape of a check nobody reads.
    #
    # Narrow on purpose: only when one side is a number and the other is a
    # string. It does not coerce two strings, so '1' and '1.0' stay different.
    if isinstance(want, (int, float)) and isinstance(got, str):
        return str(want) == got
    if isinstance(got, (int, float)) and isinstance(want, str):
        return want == str(got)

    return False


def _as_instant(value):
    """A timezone-aware datetime, or None if this is not one."""
    if isinstance(value, _dt.datetime):
        return (value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc))
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)
    return None


def _as_json(value):
    """Whatever this is, as comparable Python."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


class ParityError(Exception):
    """The run could not conclude. Never raised for a genuine divergence."""


def file_state(root=None):
    """(records, mtime). Raises rather than reading an unreadable pool as empty."""
    path = Path(jobs_store.paths(jobs_store.root_dir(root))["jobs"])
    records = dual_write.read_pool(path)
    return records, path.stat().st_mtime


def postgres_state(conn, account_id):
    """legacy_id -> the comparable fields, from the normalised tables."""
    columns = ", ".join(f"p.{name}" for name in FIELDS)
    with conn.cursor() as cur:
        cur.execute(
            f"""select p.legacy_id, p.closed_at, d.state,
                       p.merged_legacy_ids, e.norm_name, {columns}
                from postings p
                left join job_decisions d
                  on d.posting_id = p.id and d.account_id = %s
                left join employers e on e.id = p.employer_id""",
            (account_id,))
        rows = cur.fetchall()

    state, real, folded_into = {}, set(), {}
    for row in rows:
        legacy_id, closed_at, decision, folded = row[0], row[1], row[2], row[3]
        values = dict(zip(FIELDS, row[5:]))
        values["__state__"] = backfill.legacy_state(closed_at, decision)
        # **Which employer this posting points at, which no column carries.**
        # See `compare` for why this is not one of FIELDS.
        values["__employer__"] = row[4]
        state[legacy_id] = values
        real.add(legacy_id)
        # A legacy record folded into this posting by the board key has no row
        # of its own, and it is still represented here. Resolving it through
        # the winner is a recorded fact; skipping it would be a filter, and a
        # filter is where the missing records hid.
        for other in (folded or []):
            folded_into.setdefault(other, legacy_id)

    # **A real row always wins over a fold reference, whatever the row order
    # was.** Both used to be written into one dict as the rows were read, so an
    # id with its own row that is *also* named in another row's
    # `merged_legacy_ids` took whichever came last. That id genuinely exists in
    # Postgres, and letting the fold entry win would hand it to the
    # pruned-loser exemption below.
    for other, winner in folded_into.items():
        if other not in real:
            state[other] = dict(state[winner], __merged_into__=winner)
    return state


def employers_state(conn):
    """norm_name -> the columns the employer mirror writes."""
    columns = sorted(mirror_fields.compared()["employers"])
    with conn.cursor() as cur:
        cur.execute(f"select norm_name, {', '.join(columns)} from employers")
        return {r[0]: dict(zip(columns, r[1:])) for r in cur.fetchall()}


def scores_state(conn, account_id):
    """legacy_id -> the columns the score mirror writes."""
    columns = sorted(mirror_fields.compared()["job_scores"])
    with conn.cursor() as cur:
        cur.execute(
            f"""select p.legacy_id, {', '.join('s.' + c for c in columns)}
                  from job_scores s join postings p on p.id = s.posting_id
                 where s.account_id = %s""", (account_id,))
        return {r[0]: dict(zip(columns, r[1:])) for r in cur.fetchall()}


def compare_employers(records, database):
    """**The employer is how a posting is named, so it is compared.**

    It went unchecked entirely until 2026-09-02: parity read `postings` and
    nothing else on the class A side, so a company could lose its domain or
    acquire another's logo without the clock noticing.

    **The comparison applies the mirror's own update rule, and that is not a
    softening.** `upsert_employers` writes
    `domain = coalesce(employers.domain, excluded.domain)`, deliberately, so a
    domain learned once is never overwritten with a blank: the records
    disagree, and 6,481 carry a website where the rest carry an empty string
    for the same employer. A stored row is therefore a running union across
    every sweep, not a function of today's pool, and comparing it as though it
    were reports 19 divergences that are the mirror working. What is still a
    divergence, and still caught: a column NULL in Postgres while the pool
    offers a value, or a stored value the pool contradicts with a *different*
    non-blank one.

    `canonical_name` is compared through `norm_company`, which is the identity
    the row is keyed on. The display name is whichever record the fold met
    first and the pool's order changes between sweeps, so 'Akamai' and 'Akamai
    Technologies' alternate for one employer. **That non-determinism is a real
    defect and it is not this function's to hide**: it belongs with the
    employer-resolution work that item 27's `Appcast Enterprise` / `Appcastenterprise`
    split also belongs to. Comparing the normalised form keeps the check that
    matters, which is that two different employers never collapse into one row.
    """
    findings = []
    expected = backfill.employer_rows(list(records.values()))
    for norm in sorted(set(expected) - set(database)):
        findings.append({"kind": "only_in_file", "id": norm,
                         "field": "employer"})
    for norm in sorted(set(expected) & set(database)):
        want, got = expected[norm], database[norm]
        for column in sorted(want):
            if column not in got:
                continue
            mine, yours = want[column], got[column]
            if column == "canonical_name":
                mine, yours = backfill.norm_company(mine or ""), backfill.norm_company(yours or "")
            elif column in ("domain", "logo_url") and yours:
                # Kept by the mirror's coalesce. A different non-blank value in
                # the pool is still reported; a blank one is not a claim.
                if not mine or same_value(mine, yours):
                    continue
            if same_value(mine, yours):
                continue
            # **A pinned field is a recorded decision, not a fault**, and it
            # gets its own kind for the same reason `merged_field_dropped`
            # does. `domain` and `logo_url` take the first non-blank the
            # mirror ever saw and never move, so when the pool later offers a
            # different one the two stores disagree permanently and no correct
            # write resolves it. Measured 2026-09-02: 11 employers, and the
            # stored value is the worse one more often than not --
            # `blog.duolingo.com` over `careers.duolingo.com`,
            # `cts.businesswire.com` over `impact.com`,
            # `descriptinc.github.io` over `descript.com`.
            #
            # **Reported and counted rather than hidden.** A growing count is
            # the signal that the pin is costing more than it saves, and these
            # eleven are the first real input to the employer-resolution work
            # item 27 opens. `canonical_name` is never pinned, so it is never
            # excused here: two employers collapsing into one row still fails.
            kind = ("employer_field_pinned"
                    if column in ("domain", "logo_url") else "field_mismatch")
            findings.append({"kind": kind, "id": norm,
                             "field": f"employer.{column}",
                             "file": want[column], "postgres": got[column]})
    return findings


def compare_scores(records, database):
    """The Found lane's ordering, which nothing was comparing either."""
    findings = []
    for legacy_id, record in sorted(records.items()):
        want = backfill.score_row(record)
        got = database.get(legacy_id)
        if want is None or got is None:
            # A record with no score is not mirrored, and a folded loser has
            # no posting row to hang one on. Neither is a divergence; the
            # posting comparison already covers whether the row should exist.
            continue
        for column in sorted(want):
            if same_value(want[column], got.get(column)):
                continue
            findings.append({"kind": "field_mismatch", "id": legacy_id,
                             "field": f"score.{column}",
                             "file": want[column], "postgres": got.get(column)})
    return findings


def providers_state(conn, account_id):
    """provider -> the columns the provider mirror writes."""
    columns = sorted(mirror_fields.compared()["provider_links"])
    with conn.cursor() as cur:
        cur.execute(
            f"select provider, {', '.join(columns)} from provider_links "
            f"where account_id = %s", (account_id,))
        return {r[0]: dict(zip(columns, r[1:])) for r in cur.fetchall()}


def compare_providers(files, database):
    """**Which providers are connected is a fact the board draws.**

    v2's Settings page said one of three, from a fixture, while all three were
    connected and healthy. Behind the fixture there was nothing: the table
    existed with the right columns and held zero rows, because no handler ever
    wrote it. Nothing compared it either, which is why nobody found out from
    the clock.
    """
    findings = []
    for name in sorted(set(files) - set(database)):
        findings.append({"kind": "only_in_file", "id": name,
                         "field": "provider"})
    for name in sorted(set(database) - set(files)):
        findings.append({"kind": "only_in_postgres", "id": name,
                         "field": "provider"})
    for name in sorted(set(files) & set(database)):
        mine, yours = files[name], database[name]
        for column in sorted(set(mine) & set(yours)):
            if same_value(mine.get(column), yours.get(column)):
                continue
            findings.append({"kind": "field_mismatch", "id": name,
                             "field": f"provider.{column}",
                             "file": mine.get(column),
                             "postgres": yours.get(column)})
    return findings


def providers_file_state(root=None):
    """provider -> the row the mirror would write, from providers.json."""
    # `store.paths` has no `providers` key: the registry is `providers.py`'s
    # file rather than the application store's, and it sits beside them in the
    # same directory. Named here rather than added there, because giving the
    # store a key for a file it does not own is how two owners of one path
    # start.
    import store
    path = Path(store.root_dir(root)) / "providers.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    compared_columns = mirror_fields.compared()["provider_links"]
    out = {}
    for pid, entry in (payload.get("connections") or {}).items():
        if not isinstance(entry, dict):
            continue
        row = class_b.provider_row(pid, entry)
        out[pid] = {k: v for k, v in row.items() if k in compared_columns}
    return out


def meta_state(conn, account_id):
    """The one board_meta row, as the columns the mirror writes."""
    columns = sorted(mirror_fields.compared()["board_meta"])
    with conn.cursor() as cur:
        cur.execute(f"select {', '.join(columns)} from board_meta "
                    f"where account_id = %s", (account_id,))
        row = cur.fetchone()
    return dict(zip(columns, row)) if row else {}


def meta_file_state(root=None):
    """The `board_meta` row's file side, from both the files that write it.

    `meta.json` owns the scan columns and `search-config.json` owns
    `search_config`. Disjoint writers on one row, the same shape sessions and
    roles have on `applications`; comparing only the first would leave the
    saved search unchecked, which is how `applications.company` came to exist
    unmaintained.
    """
    import store
    base = Path(store.root_dir(root))
    row = {}
    path = Path(store.paths(base)["meta"])
    if path.exists():
        row.update(class_b.meta_row(json.loads(path.read_text(encoding="utf-8"))))
    saved = base / "search-config.json"
    if saved.exists():
        row.update(class_b.search_row(
            json.loads(saved.read_text(encoding="utf-8"))))
    keep = mirror_fields.compared()["board_meta"]
    return {k: v for k, v in row.items() if k in keep}


def compare_meta(files, database):
    """The scan banner's numbers, which v2 was inventing."""
    findings = []
    if files and not database:
        return [{"kind": "only_in_file", "id": "board_meta", "field": "meta"}]
    for column in sorted(files):
        if same_value(files[column], database.get(column)):
            continue
        findings.append({"kind": "field_mismatch", "id": "board_meta",
                         "field": f"meta.{column}", "file": files[column],
                         "postgres": database.get(column)})
    return findings


def coverage():
    """{table: {columns}} parity actually compares.

    Read by `tests/test_parity_compares_what_the_mirror_writes.py`, which fails
    when the mirror gains a column absent from here. Written as a function
    rather than a constant so it reports what the code above uses: `FIELDS` is
    itself derived, so a column added to `posting_row` arrives here on its own.
    """
    return {
        "postings": set(FIELDS),
        "job_decisions": {"state"},
        "employers": set(mirror_fields.compared()["employers"]),
        "job_scores": set(mirror_fields.compared()["job_scores"]),
        "applications": (set(class_b.session_row({}))
                         | set(class_b.role_row({}))),
        "application_events": set(class_b.history_row({})),
        "answer_bank": set(class_b.answer_row({})),
        "profiles": set(class_b.profile_row({})),
        "provider_links": set(mirror_fields.compared()["provider_links"]),
        "board_meta": set(mirror_fields.compared()["board_meta"]),
        "edit_outcomes": set(class_b.outcome_row({})),
    }


def merge_groups(database):
    """{winner: {every legacy id that resolves to that posting}}.

    Read from the database side, because `__merged_into__` is what
    `postgres_state` stamps on a folded id and it is the only place the
    grouping exists.
    """
    groups = {}
    for legacy_id, row in database.items():
        winner = row.get("__merged_into__") or legacy_id
        groups.setdefault(winner, set()).add(legacy_id)
    return groups


def states_disagree(records, group):
    """Do the legacy records folded into one posting disagree about `state`?

    **This is the whole exemption and it is deliberately narrow.** Two legacy
    records sharing a board key collapse into one posting row, so when a sweep
    expires one and leaves the other new, the file holds two answers and
    Postgres can hold one. Neither side is wrong and no correct write resolves
    it -- parity was red for a day on exactly that.

    But only then. If every folded record agrees on a state and Postgres says
    something else, that is a real divergence and must still fail: exempting
    `state` on merged records generally would blind the check on five records
    and growing, which is the shape of a filter that hides what it was built
    to find.
    """
    seen = {records[i].get("state") for i in group if i in records}
    return len(seen) > 1


#: The two employer-edge disagreements that stand on 2026-09-06, each with the
#: property it gives up. Measured over all 23,633 postings the two stores
#: share; everything else agrees exactly.
#:
#: An allowance here is narrower than it looks: it suppresses one *pair*, not a
#: posting and not an employer, so a genuine repointing involving either side
#: is still reported.
EMPLOYER_EDGE_ALLOWED = {
    ("", "(unknown employer)"):
        "15 postings whose feed named no company at all. `backfill` files them "
        "under a sentinel whose parentheses are load-bearing -- `norm_company` "
        "emits only lowercase alphanumerics and spaces, so no real employer "
        "can normalise to it -- and the pool simply holds an empty string. "
        "GIVES UP: that a posting with no company is filed under the sentinel "
        "rather than under some real employer. Nothing else about those 15 is "
        "unchecked, and `real_employer_count` already excludes the bucket.",
    ("honeycomb io", "honeycomb"):
        "1 posting. The pool says Honeycomb.io and the employer row says "
        "Honeycomb, and `norm_company` does not collapse a `.io` suffix the "
        "way it collapses Inc and Ltd. GIVES UP: nothing about the edge, "
        "because this is one company under two spellings rather than a "
        "posting pointing at the wrong employer. It is a finding about "
        "`norm_company` parked here rather than a decision about parity, and "
        "the staleness test below is what stops it outliving that.",
}


def employer_edge_allowed(want, got):
    """Whether this exact (pool, database) pair is a named allowance."""
    return (want or "", got or "") in EMPLOYER_EDGE_ALLOWED


def compare(records, database):
    """Every difference, as a list of dicts. Never a boolean."""
    findings = []
    groups = merge_groups(database)
    #: legacy id -> the ids it shares a posting row with, when there are any.
    shared = {i: g for g in groups.values() if len(g) > 1 for i in g}

    for legacy_id in sorted(set(records) - set(database)):
        findings.append({"kind": "only_in_file", "id": legacy_id})
    for legacy_id in sorted(set(database) - set(records)):
        winner = database[legacy_id].get("__merged_into__")
        if winner:
            # **No record on either side.** This id has no posting row: it is a
            # string in the winner's `merged_legacy_ids`, recording that it once
            # folded there, and a sweep has since pruned it from the pool. The
            # question "does this exist on both sides" does not apply to it.
            #
            # Exactly that and no wider. An id with its own row never reaches
            # here, because `postgres_state` refuses to overwrite a real row
            # with a fold reference.
            findings.append({"kind": "pruned_loser", "id": legacy_id,
                             "merged_into": winner})
        else:
            findings.append({"kind": "only_in_postgres", "id": legacy_id})

    for legacy_id in sorted(set(records) & set(database)):
        record, row = records[legacy_id], database[legacy_id]
        winner = row.get("__merged_into__")

        # State is compared for every record, folded ones included, and is
        # exempt in exactly one case: the records sharing this posting row
        # disagree with each other, so the file holds two answers and the row
        # can hold one. See `states_disagree` for why it is not wider.
        expected_state = record.get("state")
        if row["__state__"] != expected_state:
            group = shared.get(legacy_id)
            if group and states_disagree(records, group):
                findings.append({"kind": "merged_field_dropped",
                                 "id": legacy_id, "field": "state",
                                 "file": expected_state,
                                 "postgres": row["__state__"],
                                 "merged_into": winner or legacy_id})
            else:
                findings.append({"kind": "field_mismatch", "id": legacy_id,
                                 "field": "state", "file": expected_state,
                                 "postgres": row["__state__"]})

        # **The employer edge, which nothing compared until 2026-09-06.**
        #
        # `mirror_fields.EXEMPT` excuses `postings.employer_id` as "a uuid
        # Postgres assigns... the employer is compared through
        # `employers.canonical_name` instead". The first half is true and the
        # second is not: `posting_row` returns thirty columns and none of them
        # is the employer, and `compared()["employers"]` is that table's own
        # four columns. So parity asked "does employer row E have the right
        # name" and never "does posting P point at the right employer". Two
        # different claims, and the exemption's reason blended them.
        #
        # Measured by driving this function: a row whose employer edge was
        # wrong and whose thirty compared columns were identical produced no
        # finding at all, while changing `title` produced one. The employer
        # resolver's whole output is repointing postings, so its correct and
        # its catastrophic runs would have been the same green.
        #
        # **Normalised, because the naive comparison is 307 false positives.**
        # `canonical_name` against the pool's `company` disagrees on 307 of
        # 23,633 rows, and every one is a single employer under two spellings
        # -- 249 are Davies against Davies Group. `norm_company` is what the
        # mirror itself keys employers on, so comparing through it asks whether
        # the posting points at the same *employer* rather than at the same
        # string. That is 16 of 23,633, in the two groups EMPLOYER_EDGE_ALLOWED
        # names.
        want_employer = backfill.norm_company(record.get("company") or "")
        got_employer = row.get("__employer__")
        if want_employer != (got_employer or "") and not employer_edge_allowed(
                want_employer, got_employer):
            findings.append({"kind": "employer_edge", "id": legacy_id,
                             "field": "employer",
                             "file": want_employer, "postgres": got_employer})

        expected = backfill.posting_row(record)
        for name in FIELDS:
            want, got = expected.get(name), row[name]
            if same_value(want, got):
                continue
            if winner:
                # A folded record's own values were discarded by the merge,
                # which is the merge working: the two records differed, that is
                # why they were two. Reported as its own kind rather than
                # dropped, because a filter is where missing records hide, and
                # rather than as a divergence, because the loss is a recorded
                # design decision and not a fault. `merged_legacy_ids` is what
                # keeps it findable at all.
                findings.append({"kind": "merged_field_dropped",
                                 "id": legacy_id, "field": name,
                                 "file": want, "postgres": got,
                                 "merged_into": winner})
            else:
                findings.append({"kind": "field_mismatch", "id": legacy_id,
                                 "field": name, "file": want, "postgres": got})
    return findings


# --------------------------------------------------------------------------
# Class B: sessions, history, answers and profile
#
# Compared the same way and reported in the same list. The gate is "the file
# store is deleted", and a store that is the sole record for sessions, history,
# answers and profile cannot be deleted, so parity that only covers the pool is
# parity that cannot open that gate.
# --------------------------------------------------------------------------

def class_b_file_state(root=None):
    """What the file store holds for class B, keyed for comparison."""
    import store
    base = Path(store.root_dir(root))
    paths = store.paths(base)

    sessions = {}
    directory = Path(paths["sessions"])
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ParityError(f"{path.name} could not be read: {exc}")
            key = (payload.get("applicationId") or path.stem).strip()
            # **Every column the mirror writes, through the mirror's own
            # builder.** This was `status` alone: one string per session,
            # compared across seven green runs while `phase` went unchecked.
            sessions[key] = class_b.session_row(payload)

    # **The row has two writers, so the expected row is both.** A session says
    # what state an application is in; `roles.json` names it, and it names 205
    # where there are 97 session files. Comparing only the sessions would leave
    # `company` and `role` unchecked on more than half the table -- which is
    # how they came to be populated in production by code that is not in this
    # repository, unmaintained and invisible.
    roles_path = Path(paths["roles"])
    if roles_path.exists():
        index = json.loads(roles_path.read_text(encoding="utf-8"))
        for key, entry in (index or {}).items():
            if not isinstance(entry, dict):
                continue
            key = (entry.get("applicationId") or key).strip()
            row = dict(class_b.role_row(entry))
            if key in sessions:
                sessions[key] = {**sessions[key], **row}
            else:
                sessions[key] = row

    events = {}
    history = Path(paths["history"])
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise ParityError(f"applications.jsonl unreadable: {exc}")
            if event.get("eventId"):
                events[event["eventId"]] = class_b.history_row(event)

    answers = {}
    path = Path(paths["answers"])
    if path.exists():
        for key, entry in json.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(entry, dict):
                answers[key] = class_b.answer_row(entry)

    profile = None
    path = Path(paths["profile"])
    if path.exists():
        built = class_b.profile_row(json.loads(path.read_text(encoding="utf-8")))
        keep = mirror_fields.compared()["profiles"]
        profile = {k: v for k, v in built.items() if k in keep}

    # What happened to each submitted edit. The device is the only writer of
    # this file, which is the whole reason the table stays inside parity's
    # premise -- see class_b._mirror_edit_outcomes.
    outcomes = {}
    path = Path(paths["editOutcomes"])
    if path.exists():
        keep = mirror_fields.compared()["edit_outcomes"]
        for edit_id, entry in json.loads(path.read_text(encoding="utf-8")).items():
            if isinstance(entry, dict):
                built = class_b.outcome_row(entry)
                outcomes[edit_id] = {k: v for k, v in built.items() if k in keep}

    return {"sessions": sessions, "events": events, "answers": answers,
            "profile": profile, "outcomes": outcomes}


def class_b_postgres_state(conn, account_id):
    with conn.cursor() as cur:
        columns = sorted(mirror_fields.compared()["applications"])
        # `pending_order` and `pending_order_at` are read beside the compared
        # columns and are not compared: they are the mirror's own bookkeeping
        # and the device file has no equivalent. See `AWAITING_COLUMNS`.
        cur.execute(
            f"select legacy_id, {', '.join(columns)}, "
            f"pending_order, pending_order_at from applications "
            f"where account_id = %s", (account_id,))
        rows = cur.fetchall()
        sessions = {r[0]: dict(zip(columns, r[1:1 + len(columns)]))
                    for r in rows}
        pending = {r[0]: (r[1 + len(columns)], r[2 + len(columns)])
                   for r in rows if r[1 + len(columns)]}
        # **Derived, like the applications select above.** These were
        # hand-written column lists, and adding `kind` and `category` to
        # `answer_row` therefore added them to the file side and not to this
        # one: 355 rows reported as divergent with `postgres=None`, on columns
        # Postgres actually held. That is the declared-twice bug this whole
        # module exists to end, inside the module that ends it -- the file side
        # was derived and the database side was not, so the two halves of one
        # comparison drifted the moment the mirror grew.
        event_columns = sorted(mirror_fields.compared()["application_events"])
        cur.execute(
            f"select payload->>'eventId', {', '.join(event_columns)} "
            f"from application_events "
            f"where account_id = %s and payload ? 'eventId'", (account_id,))
        events = {r[0]: dict(zip(event_columns, r[1:])) for r in cur.fetchall()}

        answer_columns = sorted(mirror_fields.compared()["answer_bank"])
        cur.execute(
            f"select key, {', '.join(answer_columns)} from answer_bank "
            f"where account_id = %s", (account_id,))
        answers = {r[0]: {c: (list(v) if c == "tags" and v is not None else v)
                          for c, v in zip(answer_columns, r[1:])}
                   for r in cur.fetchall()}
        profile_columns = sorted(mirror_fields.compared()["profiles"])
        cur.execute(f"select {', '.join(profile_columns)} from profiles "
                    f"where account_id = %s", (account_id,))
        row = cur.fetchone()
        profile = dict(zip(profile_columns, row)) if row else None

        outcome_columns = sorted(mirror_fields.compared()["edit_outcomes"])
        cur.execute(
            f"select edit_id, {', '.join(outcome_columns)} from edit_outcomes "
            f"where account_id = %s", (account_id,))
        outcomes = {r[0]: dict(zip(outcome_columns, r[1:])) for r in cur.fetchall()}

    return {"sessions": sessions, "events": events, "answers": answers,
            "profile": profile, "outcomes": outcomes, "pending": pending}


#: The only columns a v2 write can put into `applications`, so the only ones an
#: outstanding work order can excuse. Derived from what `route.ts` accepts, not
#: from what is compared: it writes `status` and `phase` and nothing else, and
#: leaving the other sixteen compared throughout is what keeps this from being
#: a row-level exemption. A blind spot the width of a table is how a marker
#: becomes the thing it was added to prevent.
AWAITING_COLUMNS = ("session.status", "session.phase")

#: `pending_order` and `pending_order_at` are **not** in `mirror_fields.EXEMPT`,
#: and that is correct rather than an omission. `compared()` is `written()`
#: minus the exemptions, and `written()` is derived from the mirror's INSERT
#: column list: `class_b` only ever *clears* these two, in a `do update`, so
#: they are outside the write set and were never candidates for comparison.
#: An exemption for a column the mirror does not write covers nothing, and
#: `test_parity_compares_what_the_mirror_writes` refuses one -- correctly; it
#: caught these on their first full run.

#: How long a work order may be outstanding before its row is a divergence
#: again.
#:
#: **A specification, not a percentile.** Four v2 writes have reconciled and
#: they took 83, 130, 138 and 284 seconds; a queued apply additionally waits
#: for a free browser slot, and the tick that answers runs every five seconds
#: against a pool it parses whole. An hour is roughly thirteen times the
#: largest observed. The `awaiting_device` ages in the report are the series to
#: re-derive it from once there are enough of them.
#:
#: **The case this bound exists for is not the one that motivated the marker.**
#: Scale Media 49dac26ae2c5c6ef has no marker at all: it predates the column,
#: so it stays a plain divergence, which is correct. The case here is live and
#: post-#206: v2 orders an apply, the device receives it, `do_not_start`
#: refuses because the application is parked or its latest event is
#: `submitting`, no session is ever written, and nothing clears the marker.
#: Without the bound that row would be excused for ever, which is exactly the
#: permanent exemption this design was chosen to avoid.
AWAITING_SECONDS = 60 * 60


def classify_awaiting(finding, pending, now=None):
    """`(kind, order, age)` if the device has simply not answered yet.

    Three conditions, and the third is deliberately the weakest of the three
    rather than a transition check. A transition check would need a model of
    the *file* side, and `route.ts` has none: it knows the statuses it can
    write and nothing about what the device held before. So this is a
    membership test on the Postgres side only, and it is said here rather than
    implied so that nobody later softens the age bound believing this clause
    is carrying weight. **The age bound is the only real narrowing.**
    """
    if finding.get("kind") != "field_mismatch":
        return None
    if finding.get("field") not in AWAITING_COLUMNS:
        return None
    order, at = (pending or {}).get(finding.get("id")) or (None, None)
    if not order or at is None:
        return None
    from datetime import datetime, timezone
    moment = now or datetime.now(timezone.utc)
    try:
        age = (moment - at).total_seconds()
    except TypeError:
        # A naive timestamp is "could not compare", which is not "young".
        return None
    return ("awaiting_device" if age < AWAITING_SECONDS
            else "reconciliation_overdue", order, round(age))


def compare_class_b(files, database):
    """Divergences across sessions, history events, answers and profile."""
    findings = []

    # **The exemption was drawn wider than the fact it rests on, and a probe
    # row proved it.** The reasoning was sound -- `roles.json` also creates
    # `applications` rows, and `finish.py` deletes a session on submission, so
    # Postgres legitimately holds ids the sessions directory does not -- and
    # the conclusion drawn from it was "skip the direction entirely". A peer
    # session then wrote `legacy_id '!!probe-not-a-real-id!!'` to production
    # through the route, and it would have survived a green parity run: no
    # session, no roles entry, no completed event, and nothing looking.
    #
    # This is the same exemption CLAUDE.md records as having made 27 fixture
    # applications invisible, and it is narrowable in one line, because the two
    # legitimate reasons are both checkable:
    #
    #   roles.json   `class_b_file_state` merges the role index into
    #                `sessions`, so a roles-only id is already on the file side
    #                and never reaches this set
    #   completed    `finish.py` deletes a session on submission
    #
    # **Only the first of those is real today, and the check ships with no
    # exemption at all.** Measured against production 2026-09-09: `roles.json`
    # holds 208 entries against 208 `applications` rows and 100 session files,
    # nothing anywhere prunes it, and the Postgres-only set is therefore
    # **empty**. A completed-event exemption would have explained 0 of 0 rows.
    #
    # That was worth measuring rather than accepting: the finding arrived with
    # 108 / 103 / 5 attached, and those numbers are against session files
    # alone, which is not the side this comparison uses. Adding the exemption
    # on the strength of them would have put a permanent blind spot in the one
    # direction that had just let a row through, to excuse rows that do not
    # exist. If `roles.json` is ever pruned, this check names the orphans and
    # the exemption gets added then, with a count behind it.
    for group, key in (("sessions", "session"), ("events", "event"),
                       ("outcomes", "editOutcome"),
                       ("answers", "answer")):
        ours, theirs = files[group], database[group]
        for name in sorted(set(ours) - set(theirs)):
            findings.append({"kind": f"only_in_file", "id": name,
                             "field": key})
        for name in sorted(set(ours) & set(theirs)):
            mine, yours = ours[name], theirs[name]
            # Each side is now a dict of the columns the mirror writes, so the
            # finding names which column disagreed rather than saying the
            # session differs and leaving the reader to guess which of four.
            # The file side of a row is whatever wrote it: a session file, a
            # roles.json entry, or both. A column neither supplied is not a
            # claim, so it is not a divergence -- an application with no
            # session file has no `phase` to disagree about.
            for column in sorted(set(mine)):
                if same_value(mine.get(column), yours.get(column)):
                    continue
                finding = {"kind": "field_mismatch", "id": name,
                           "field": f"{key}.{column}",
                           "file": mine.get(column),
                           "postgres": yours.get(column)}
                verdict = classify_awaiting(finding, database.get("pending"))
                if verdict:
                    finding["kind"], finding["pendingOrder"], finding["ageSeconds"] = verdict
                findings.append(finding)

    # An application row with nothing behind it on the device.
    for name in sorted(set(database["sessions"]) - set(files["sessions"])):
        findings.append({"kind": "only_in_postgres", "id": name,
                         "field": "session"})
    for name in sorted(set(database["outcomes"]) - set(files["outcomes"])):
        findings.append({"kind": "only_in_postgres", "id": name,
                         "field": "editOutcome"})
    for name in sorted(set(database["events"]) - set(files["events"])):
        findings.append({"kind": "only_in_postgres", "id": name,
                         "field": "event"})
    for name in sorted(set(database["answers"]) - set(files["answers"])):
        findings.append({"kind": "only_in_postgres", "id": name,
                         "field": "answer"})

    if files["profile"] is not None:
        if database["profile"] is None:
            findings.append({"kind": "only_in_file", "id": "profile",
                             "field": "profile"})
        else:
            # Column by column, like every other class B comparison. It used to
            # compare the identity blob whole and report two sorted key lists,
            # which said "they differ" and left the reader to find out how.
            for column in sorted(files["profile"]):
                if same_value(files["profile"][column],
                              database["profile"].get(column)):
                    continue
                findings.append({"kind": "field_mismatch", "id": "profile",
                                 "field": f"profile.{column}",
                                 "file": files["profile"][column],
                                 "postgres": database["profile"].get(column)})
    return findings


def run_once(conn, account_id, root=None):
    """One reading of both stores. Returns (findings, mtime)."""
    records, mtime = file_state(root)
    database = postgres_state(conn, account_id)
    findings = compare(records, database)
    findings.extend(compare_employers(records, employers_state(conn)))
    findings.extend(compare_scores(records, scores_state(conn, account_id)))
    findings.extend(compare_meta(meta_file_state(root),
                                 meta_state(conn, account_id)))
    findings.extend(compare_providers(providers_file_state(root),
                                      providers_state(conn, account_id)))
    findings.extend(compare_class_b(class_b_file_state(root),
                                    class_b_postgres_state(conn, account_id)))
    return findings, mtime


def run(conn, account_id, root=None, on_note=None):
    """The parity check, with the one re-read the concurrency rule allows.

    Returns a report. `divergences` is what makes the run red; `transient` is
    what cleared on the second reading and is recorded rather than dropped.
    """
    note = on_note or (lambda message: None)

    first, first_mtime = run_once(conn, account_id, root)
    report = {"checked": True, "divergences": [], "transient": [],
              "merged_field_dropped": [], "pruned_losers": [],
              "first_count": len(first),
              "reread": False, "first_mtime": first_mtime,
              "second_mtime": None}

    # A merge discarding the loser's own field values is a recorded design
    # decision, not a difference between the stores. Split out before the
    # re-read so a corpus with merges does not re-read on every clean run.
    #: Both are recorded facts rather than differences between the stores, and
    #: both accumulate: `mergedFieldDropped` went 2, 5, 7 and its pinned
    #: assertion had to become a derived one. The report carries the ids and
    #: nothing pins a length.
    #: `awaiting_device` joins them: a v2 write whose work order the device has
    #: not answered yet is a recorded fact about timing, not a difference
    #: between the stores. `reconciliation_overdue` is deliberately NOT here --
    #: it is the same row past the bound, and it is the finding.
    ASIDES = ("merged_field_dropped", "pruned_loser", "employer_field_pinned",
              "awaiting_device")
    report["merged_field_dropped"] = [f for f in first
                                      if f["kind"] == "merged_field_dropped"]
    report["pruned_losers"] = [f for f in first if f["kind"] == "pruned_loser"]
    report["employer_field_pinned"] = [f for f in first
                                       if f["kind"] == "employer_field_pinned"]
    report["awaiting_device"] = [f for f in first
                                 if f["kind"] == "awaiting_device"]
    first = [f for f in first if f["kind"] not in ASIDES]

    if not first:
        return report

    # Something differs. Read both stores again before believing it.
    note(f"parity: {len(first)} differences on the first reading, re-reading")
    report["reread"] = True
    second, second_mtime = run_once(conn, account_id, root)
    second = [f for f in second if f["kind"] not in ASIDES]
    report["second_mtime"] = second_mtime

    def key(finding):
        return (finding["kind"], finding["id"], finding.get("field"))

    persisted = {key(f) for f in second}
    for finding in first:
        if key(finding) in persisted:
            report["divergences"].append(finding)
        else:
            # Cleared between the two readings: a write landed in between.
            # Recorded, with both readings, never dropped. A path that is
            # always mid-write when parity looks is a slow path and that is
            # worth knowing.
            report["transient"].append(
                dict(finding, cleared_on_reread=True,
                     first_mtime=first_mtime, second_mtime=second_mtime))
    # A difference that only the second reading saw is as real as one both saw.
    seen = {key(f) for f in first}
    for finding in second:
        if key(finding) not in seen:
            report["divergences"].append(dict(finding, seen_on="second"))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--root", help="override the store root")
    parser.add_argument("--url", help="override the connection URL")
    args = parser.parse_args(argv)

    def note(message):
        if not args.json:
            print(message, file=sys.stderr)

    # A run that checked nothing must never exit 0. Dual-write being off is
    # the way that happens without anything looking broken.
    if not dual_write.enabled():
        print("verify_parity: dual-write is not configured, so there is "
              "nothing to compare and nothing can be concluded.",
              file=sys.stderr)
        return 1

    try:
        conn = dual_write.connect() if not args.url else __import__(
            "migrate").connect(args.url, announce=False)
    except Exception as exc:
        print(f"verify_parity: no database: {exc}", file=sys.stderr)
        return 1

    try:
        account = dual_write.account_id(conn)
        report = run(conn, account, args.root, on_note=note)
    except (ParityError, dual_write.DualWriteError, backfill.BackfillError) as exc:
        print(f"verify_parity: inconclusive: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.json:
        print(json.dumps(report, indent=1, default=str))
    else:
        for finding in report["transient"][:args.limit]:
            print(f"transient  {finding['kind']:18} {finding['id']} "
                  f"{finding.get('field', '')} (cleared on re-read)")
        for finding in report["divergences"][:args.limit]:
            extra = ""
            if finding["kind"] == "field_mismatch":
                extra = (f" {finding['field']}: file={finding['file']!r} "
                         f"postgres={finding['postgres']!r}")
            print(f"DIVERGENCE {finding['kind']:18} {finding['id']}{extra}")
        hidden = len(report["divergences"]) - args.limit
        if hidden > 0:
            print(f"... and {hidden} more")
        print()
        print(f"divergences {len(report['divergences'])}, "
              f"transient {len(report['transient'])}, "
              f"merged-field drops {len(report['merged_field_dropped'])}, "
              f"pruned losers {len(report['pruned_losers'])}, "
              f"pinned employer fields "
              f"{len(report.get('employer_field_pinned') or [])}")

    return 1 if report["divergences"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
