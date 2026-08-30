#!/usr/bin/env python3
"""Move the job store into `postings`, `employers` and `job_decisions`.

    python3 packages/schema/backfill.py --account <email>            # dry run
    python3 packages/schema/backfill.py --account <email> --apply

**Idempotent and re-runnable, by construction rather than by luck.** It cannot
be atomic with the start of dual-write: the daemon is running, a sweep lands
every few hours, and any record created between the backfill reading the file
and dual-write going live belongs to neither. A backfill that can be run again
closes that window. One that can only be run once turns it into a permanent
hole that parity reports forever, which is exactly the 4,779-record shape
docs/parity.md describes.

So every write is an upsert keyed on something stable: employers on
`norm_name`, postings on `legacy_id`, decisions on `(account_id, posting_id)`.
Running it twice changes nothing the second time, and that is asserted rather
than assumed.

**Every field is accounted for.** `packages/schema/BACKFILL.md` lists each
legacy key against its column or against a reason for dropping it, and
`tests/test_backfill.py` reads that file and fails when a key in the real
records appears in neither list. "No silent field loss" is not a rule anybody
can hold in their head across 20,103 records and 30 keys; it is a list a test
compares against reality.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import board_key  # noqa: E402
import migrate  # noqa: E402

POOL = Path("~/.job-application/jobs.json").expanduser()

# Legacy state -> (is the posting closed, the decision to record).
# `None` means no decision row, which is Found. Inverted by `legacy_state`,
# which parity uses, so the two directions cannot drift apart.
STATE_MAP = {
    "new":     (False, None),
    "expired": (True,  None),
    "watched": (False, "watching"),
    "passed":  (False, "dropped"),
    "applied": (False, "submitted"),
}

_DECISION_TO_LEGACY = {v[1]: k for k, v in STATE_MAP.items() if v[1]}

# Where a posting goes when the feed named no employer.
#
# Three records on 2026-08-29, all careerjet, all with a real title and a real
# apply URL and an empty `company`. `postings.employer_id` is not null, so the
# choice is a placeholder or dropping three real postings, and dropping them
# is the worse half of that.
#
# The parentheses are load-bearing. `identity.norm_company` emits only
# lowercase alphanumerics and single spaces, so no real employer can ever
# normalise to this string and quietly absorb the bucket. It also never
# receives a domain or a logo: an unknown employer with a logo is the
# "stranger's mark" failure `company_logos` is built to avoid, wearing a
# different hat.
UNKNOWN_EMPLOYER = "(unknown employer)"
UNKNOWN_EMPLOYER_NAME = "Unknown employer"


def BOARD_KEY_WINNER(record):
    """The sort key deciding which record survives a board-key collision.

    Two stored records can derive one `(source, board_slug, external_id)`, and
    that is not a defect: it means two rows for one posting, which is what the
    unique index exists to collapse. One pair in the pool on 2026-08-29, the
    same Braze posting reached through `boards.` and `job-boards.`. Phase 3
    will hit these by the dozen per sweep, because a connector fetching a board
    sees every posting on it however many hostnames the legacy pool reached
    them through.

    So the winner is a **rule** rather than whichever row happened to be
    iterated first: the lexicographically smallest legacy id. It has no meaning
    beyond being stable, and stable is the whole requirement. A winner that
    moves between runs makes parity flap, because the posting's uuid changes
    and every decision pointing at it has to be rewritten.

    The loser is reported by `run()`, never dropped quietly.
    """
    return record.get("id") or ""


class BackfillError(Exception):
    pass


def legacy_state(closed_at, decision):
    """The legacy `state` a posting and decision represent.

    The inverse of STATE_MAP, and the only place the inverse exists. Parity
    compares against this rather than restating the table, because two copies
    of a mapping is two things to keep in step and one of them will lose.
    """
    if decision:
        if decision not in _DECISION_TO_LEGACY:
            raise BackfillError(
                f"decision state {decision!r} has no legacy equivalent; it was "
                f"created after the migration and cannot be compared to the "
                f"file store")
        return _DECISION_TO_LEGACY[decision]
    return "expired" if closed_at else "new"


def norm_company(name):
    """The employer upsert key. Delegates to the store's own normaliser so one
    rule serves ingest, the board and this."""
    import identity
    return (identity.norm_company(name or "") or "").strip().lower()


def _text(value):
    value = (value or "").strip()
    return value or None


def _country(value):
    value = (value or "").strip().upper()
    return value if len(value) == 2 and value.isalpha() else None


def hostname(value):
    """The bare host of an employer website, or None.

    The store keeps `companyWebsite` as a full URL: `https://www.spacex.com`.
    §3's column is `domain`, and a domain is what a logo endpoint and an
    employer-identity check both want, so the URL is reduced here rather than
    stored raw and stripped by every reader.

    `www.` goes too. `www.spacex.com` and `spacex.com` are one employer, and
    keeping both would make the unique constraint miss exactly the duplicates
    it exists to catch.
    """
    text = (value or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(text).hostname or "").lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def posting_row(record):
    """One legacy record as the columns of `postings`.

    Raises rather than guessing when a required field is absent. A backfill
    that invents an `apply_url` produces a row nothing can apply to, and finding
    that out later costs more than stopping now.
    """
    legacy_id = _text(record.get("id"))
    if not legacy_id:
        raise BackfillError("a record has no id")
    source = _text(record.get("canonicalSource"))
    url = _text(record.get("canonicalUrl"))
    if not source or not url:
        raise BackfillError(f"{legacy_id} has no source or no url")

    slug, external = board_key.derive(source, url, legacy_id)
    extracted = record.get("extracted") or {}
    location = record.get("location") or {}
    workplace = _text(record.get("workplace"))
    summary = _text(record.get("descriptionExcerpt"))
    if summary and len(summary) > 2048:
        summary = summary[:2048]

    return {
        "legacy_id": legacy_id,
        "source": source,
        "board_slug": slug,
        "external_id": external,
        "title": _text(record.get("title")) or "(untitled)",
        "norm_title": _text(record.get("normTitle")),
        "location_raw": _text(location.get("raw")),
        "city": _text(location.get("city")),
        "region": _text(location.get("region")),
        "country": _country(location.get("country")),
        "remote": workplace == "remote",
        "workplace": workplace,
        "posted_at": _text(record.get("postedAt")),
        "first_seen_at": _text(record.get("firstSeenAt")),
        "last_seen_at": _text(record.get("lastSeenAt")),
        "closed_at": _text(record.get("closedAt")),
        "apply_url": url,
        "years_min": _int(extracted.get("yearsMin")),
        "years_max": _int(extracted.get("yearsMax")),
        "years_text": _text(extracted.get("yearsText")),
        "summary": summary,
        "desc_fingerprint": _text(record.get("descFingerprint")),
        "seen_count": max(1, _int(record.get("seenCount")) or 1),
        "salary_raw": _text(record.get("salary")),
        "lang": _text(record.get("lang")),
        "tags": list(record.get("tags") or []),
        "sources": json.dumps(record.get("sources") or []),
        "sections": (json.dumps(record["sections"])
                     if record.get("sections") else None),
    }


def _chunks(rows, size):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


_POSTING_COLUMNS = None


def _columns():
    global _POSTING_COLUMNS
    if _POSTING_COLUMNS is None:
        _POSTING_COLUMNS = list(posting_row({
            "id": "x", "canonicalSource": "s", "canonicalUrl": "https://x/y"}))
    return _POSTING_COLUMNS


def upsert_employers(conn, records, batch=500):
    """One row per distinct normalised company name. Returns norm_name -> id.

    `domain` and `logo_url` are filled from the first record that has them and
    never overwritten with a blank, because the records disagree: 6,481 carry a
    website and the rest carry an empty string for the same employer.
    """
    best = {}
    for record in records:
        name = (record.get("company") or "").strip()
        norm = norm_company(name)
        if not norm:
            # Named rather than skipped. See UNKNOWN_EMPLOYER.
            best.setdefault(UNKNOWN_EMPLOYER,
                            {"name": UNKNOWN_EMPLOYER_NAME, "domain": None,
                             "logo": None})
            continue
        entry = best.setdefault(norm, {"name": name, "domain": None,
                                       "logo": None})
        entry["domain"] = entry["domain"] or hostname(record.get("companyWebsite"))
        entry["logo"] = entry["logo"] or _text(record.get("logoUrl"))

    # `employers.domain` is unique per §3, and on the real corpus that
    # constraint catches something real: `sharegate.com` is claimed by both
    # `sharegate` and `sharegate migrate chezworkleap`, which are one employer
    # the name normaliser did not merge. One collision in 1,934 employers,
    # measured 2026-08-29.
    #
    # Failing the whole 20,103-record run over it would be wrong, and so would
    # merging two employer identities silently. The domain goes to the
    # lexicographically first claimant, deterministically so a re-run does not
    # move it, the others keep their row with no domain, and the losers are
    # reported rather than swallowed.
    # The placeholder never carries either, whatever a record claims.
    if UNKNOWN_EMPLOYER in best:
        best[UNKNOWN_EMPLOYER]["domain"] = None
        best[UNKNOWN_EMPLOYER]["logo"] = None

    claimed, contested = {}, []
    for norm in sorted(best):
        domain = best[norm]["domain"]
        if not domain:
            continue
        if domain in claimed:
            contested.append((norm, domain, claimed[domain]))
            best[norm]["domain"] = None
            best[norm]["merged_into"] = claimed[domain]
        else:
            claimed[domain] = norm

    rows = [(entry["name"] or norm, norm, entry["domain"], entry["logo"])
            for norm, entry in best.items()]
    with conn.cursor() as cur:
        for chunk in _chunks(rows, batch):
            values = ", ".join(["(%s, %s, %s, %s)"] * len(chunk))
            cur.execute(
                f"""insert into employers (canonical_name, norm_name, domain,
                                           logo_url)
                    values {values}
                    on conflict (norm_name) do update
                      set canonical_name = excluded.canonical_name,
                          domain   = coalesce(employers.domain, excluded.domain),
                          logo_url = coalesce(employers.logo_url,
                                              excluded.logo_url)""",
                [field for row in chunk for field in row])
        cur.execute("select norm_name, id from employers")
        ids = dict(cur.fetchall())

        # Record where a contested employer's domain went, so the loser is not
        # left with a null domain and no trace of why. Nothing follows this
        # pointer yet; see docs/employer-identity.md.
        links = [(ids[winner], ids[norm]) for norm, entry in best.items()
                 if entry.get("merged_into")
                 for winner in [entry["merged_into"]]
                 if winner in ids and norm in ids]
        for chunk in _chunks(links, batch):
            values = ", ".join(["(%s::uuid, %s::uuid)"] * len(chunk))
            cur.execute(
                f"""update employers set merged_into = v.winner
                    from (values {values}) as v(winner, loser)
                    where employers.id = v.loser
                      and employers.merged_into is distinct from v.winner""",
                [field for row in chunk for field in row])
        return ids, contested


def upsert_postings(conn, records, employers, batch=500):
    """Insert or update every record. Returns (legacy_id -> id, merged ids).

    **Bulk by construction, for the reason jobs_db.py already records.** Neon
    answers a round trip from this machine in about 60 ms, so a statement per
    record is 20,103 round trips and twenty minutes; the first version of this
    was exactly that and timed out. Batched, the same work is ~40 statements.

    Board-key collisions are resolved in Python before anything is written
    rather than caught per row, which is what makes batching possible at all:
    one row raising inside a multi-row INSERT aborts the whole batch, and
    catching that per row is the per-row round trip again. Measured on the real
    pool there is exactly one such collision, two records for one Greenhouse
    posting reached through two hostnames. The loser is reported, not dropped
    quietly.
    """
    columns = _columns()

    prepared, seen, merged = [], {}, []
    for record in sorted(records, key=BOARD_KEY_WINNER):
        row = posting_row(record)
        norm = norm_company(record.get("company")) or UNKNOWN_EMPLOYER
        employer_id = employers.get(norm)
        if employer_id is None:
            raise BackfillError(
                f"{row['legacy_id']} names employer {norm!r}, which was "
                f"not created; employers must be upserted first")
        key = (row["source"], row["board_slug"], row["external_id"])
        if key in seen:
            merged.append(row["legacy_id"])
            continue
        seen[key] = row["legacy_id"]      # see BOARD_KEY_WINNER
        prepared.append([employer_id] + [row[c] for c in columns])

    assignments = ", ".join(f"{c} = excluded.{c}" for c in columns
                            if c != "legacy_id")
    one = "(" + ", ".join(["%s"] * (len(columns) + 1)) + ")"

    mapping = {}
    with conn.cursor() as cur:
        for chunk in _chunks(prepared, batch):
            cur.execute(
                f"insert into postings (employer_id, {', '.join(columns)}) "
                f"values {', '.join([one] * len(chunk))} "
                f"on conflict (legacy_id) do update set {assignments} "
                f"returning legacy_id, id",
                [field for row in chunk for field in row])
            mapping.update(dict(cur.fetchall()))
    return mapping, merged


def upsert_decisions(conn, records, postings, account_id, batch=500):
    """The per-account half of the legacy `state` field."""
    rows = []
    for record in records:
        state = record.get("state")
        if state not in STATE_MAP:
            raise BackfillError(
                f"{record.get('id')} has unknown state {state!r}")
        _closed, decision = STATE_MAP[state]
        posting_id = postings.get(record.get("id"))
        if decision is None or posting_id is None:
            continue
        rows.append((account_id, posting_id, decision))

    with conn.cursor() as cur:
        for chunk in _chunks(rows, batch):
            values = ", ".join(["(%s, %s, %s, 'backfill')"] * len(chunk))
            cur.execute(
                f"""insert into job_decisions
                        (account_id, posting_id, state, decided_from)
                    values {values}
                    on conflict (account_id, posting_id) do update
                      set state = excluded.state""",
                [field for row in chunk for field in row])
    return len(rows)


def load_pool(path=None):
    """The file store, or a named failure. Never an empty dict.

    `jobs_store._read` makes the same choice for the same reason, and
    `migrate_to_postgres` records what happened without it: a truncated file
    migrated as zero records and a count-only verify certified it correct.
    """
    path = Path(path) if path else POOL
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BackfillError(f"{path} could not be read: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise BackfillError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BackfillError(f"{path} holds {type(data).__name__}, not a mapping")
    return data


def account_id_for(conn, email):
    with conn.cursor() as cur:
        cur.execute("select id from accounts where email = %s",
                    (email.strip().lower(),))
        row = cur.fetchone()
    if row is None:
        raise BackfillError(
            f"no account for {email!r}. Create it before backfilling: the "
            f"decisions are account-scoped and cannot be attributed without "
            f"one.")
    return row[0]


def run(conn, account_id, records):
    """Everything, in dependency order. Returns a report."""
    values = list(records.values()) if isinstance(records, dict) else list(records)
    employers, contested = upsert_employers(conn, values)
    postings, merged = upsert_postings(conn, values, employers)
    decisions = upsert_decisions(conn, values, postings, account_id)
    unnamed = sum(1 for r in values if not norm_company(r.get("company")))
    return {
        "records": len(values),
        "unnamed_employer": unnamed,
        "employers": len(employers),
        "postings": len(postings),
        "merged": merged,
        "contested_domains": contested,
        "decisions": decisions,
    }


def real_employer_count(conn):
    """Employers a person would call employers.

    Excludes the `(unknown employer)` sentinel, which is a filing location for
    postings whose feed named no company. Counting it makes every total one too
    many, and worse, a board lane headed "Unknown employer (3)" invites
    somebody to read it as a company with three openings.
    """
    with conn.cursor() as cur:
        cur.execute("select count(*) from employers where norm_name <> %s",
                    (UNKNOWN_EMPLOYER,))
        return cur.fetchone()[0]


def groupable_employers(conn):
    """(id, canonical_name) for every employer a board may group under.

    The sentinel is absent for the reason above. So is any employer merged into
    another, because showing both halves of a known duplicate as separate
    groups is exactly what the merge recorded.
    """
    with conn.cursor() as cur:
        cur.execute("""select id, canonical_name from employers
                       where norm_name <> %s and merged_into is null
                       order by canonical_name""", (UNKNOWN_EMPLOYER,))
        return cur.fetchall()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--account", required=True,
                        help="the account decisions are attributed to")
    parser.add_argument("--apply", action="store_true",
                        help="write; without it the run reports and rolls back")
    parser.add_argument("--pool", help="override the job store path")
    parser.add_argument("--url", help="override the connection URL")
    args = parser.parse_args(argv)

    try:
        records = load_pool(args.pool)
        conn = migrate.connect(args.url)
    except (BackfillError, migrate.MigrationError) as exc:
        print(f"backfill: {exc}", file=sys.stderr)
        return 2

    try:
        account = account_id_for(conn, args.account)
        if args.apply:
            report = run(conn, account, records)
        else:
            # A dry run has to do the work to report on it, and then leave
            # nothing behind. One transaction, rolled back at the end.
            with conn.transaction() as tx:
                report = run(conn, account, records)
                report["dry_run"] = True
                raise _Rollback(report)
    except _Rollback as done:
        report = done.report
    except BackfillError as exc:
        print(f"backfill: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    for key in ("records", "employers", "postings", "decisions"):
        print(f"{key:12} {report[key]}")
    if report["unnamed_employer"]:
        print(f"unnamed      {report['unnamed_employer']} postings name no "
              f"employer and are filed under {UNKNOWN_EMPLOYER!r}")
    if report["contested_domains"]:
        print(f"contested    {len(report['contested_domains'])} employers "
              f"share a website with another and kept no domain:")
        for norm, domain, winner in report["contested_domains"][:5]:
            print(f"               {norm} wanted {domain}, held by {winner}")
    if report["merged"]:
        print(f"merged       {len(report['merged'])} records shared a board key "
              f"with one already present: {', '.join(report['merged'][:5])}")
    if report.get("dry_run"):
        print("dry run: nothing was written. Re-run with --apply.")
    return 0


class _Rollback(Exception):
    def __init__(self, report):
        super().__init__("dry run")
        self.report = report


if __name__ == "__main__":
    raise SystemExit(main())
