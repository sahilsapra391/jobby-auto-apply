#!/usr/bin/env python3
"""The device's own decisions, read back from Postgres.

**`jobs_store` contains zero SELECTs, and that is the whole reason the file
store cannot be deleted.** The pool file carries a `state` on every record and
`dual_write` mirrors the three account-scoped values of it into
`job_decisions`, but nothing has ever read them back. Measured 2026-09-03: an
absent pool reads as empty, the next sweep repopulates from the connectors, and
the device silently loses 16 watched, 100 applied and 11 passed. Those
decisions were in Postgres the whole time with no reader.

So this is the first Phase 4 module and it is deliberately small: a reader, and
nothing that changes who decides. The file stays authoritative until a consumer
is switched over one at a time, and each switch is its own change with its own
evidence.

**What is a decision and what is a fact about the posting.** `STATE_MAP` has
five values and only three are decisions:

    new       not a decision; the absence of one
    expired   a fact about the posting, mirrored as `postings.closed_at`
    watched   -> watching
    passed    -> dropped
    applied   -> submitted

That split is the multi-tenant design rather than an accident of this schema.
Two accounts looking at one posting share `closed_at` and disagree about
`state`, which is why `job_decisions` is account-scoped and `postings` is not.

**The mapping is inverted from `backfill.STATE_MAP` rather than written out.**
A second copy of it is the shape this repository has paid for five times, most
recently `verify_parity.FIELDS` reimplementing `posting_row` and comparing 11
of 28 columns. A value added to the writer arrives here on its own, and
`tests/test_decisions_read.py` fails if the two ever disagree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "scripts"))


def legacy_state():
    """`{decision state: pool state}`, inverted from the mirror's own map."""
    import backfill
    return {decision: legacy
            for legacy, (_closed, decision) in backfill.STATE_MAP.items()
            if decision}


def decision_state():
    """`{pool state: decision state}`, the direction the mirror writes."""
    return {legacy: decision for decision, legacy in legacy_state().items()}


def for_account(conn, account_id):
    """`{legacy_id: pool state}` for every decision this account has recorded.

    Keyed on the pool's own id rather than on `postings.id`, because that is
    what every consumer on the device holds and translating at the boundary
    beats translating at each call site.

    A decision whose posting has been deleted is dropped rather than raised on:
    the join is what makes the key resolvable at all, and a decision about a
    posting that no longer exists is not a decision the device can act on. It
    is counted, so "none matched" and "none exist" stay different facts.
    """
    back = legacy_state()
    with conn.cursor() as cur:
        cur.execute(
            """select p.legacy_id, d.state
                 from job_decisions d
                 join postings p on p.id = d.posting_id
                where d.account_id = %s""",
            (account_id,))
        rows = cur.fetchall()
    out, unknown = {}, {}
    for legacy_id, state in rows:
        mapped = back.get(state)
        if mapped is None:
            # A state the mirror can write and this map does not know is a
            # schema change nobody carried across. Never silently skipped: the
            # caller is about to treat its absence as "no decision", which is
            # the one reading that is always wrong.
            unknown[state] = unknown.get(state, 0) + 1
            continue
        out[legacy_id] = mapped
    if unknown:
        raise ValueError(
            f"job_decisions holds states this reader cannot map: {unknown}. "
            f"backfill.STATE_MAP is the source of the mapping; a value added "
            f"to the constraint in migration 0002 and not to that map arrives "
            f"here")
    return out


def orphans(conn, account_id):
    """Decisions whose posting row is gone. Counted, never inferred as zero."""
    with conn.cursor() as cur:
        cur.execute(
            """select count(*) from job_decisions d
                left join postings p on p.id = d.posting_id
               where d.account_id = %s and p.id is null""",
            (account_id,))
        return cur.fetchone()[0]


def from_pool(jobs):
    """`{legacy_id: pool state}` for the same three states, off the pool."""
    keep = set(legacy_state().values())
    return {jid: record["state"] for jid, record in (jobs or {}).items()
            if (record or {}).get("state") in keep}


def compare(pool, stored):
    """The three sets, never their sizes.

    `counts agreeing` is the trap this repository has met twice: six Found
    filters each moved the row count and one moved the rows, and 205
    applications produced 205 filenames from a rule that collides three ways.
    So this returns what is only on each side and what disagrees, and the
    caller reports those rather than a total.
    """
    only_pool = sorted(set(pool) - set(stored))
    only_stored = sorted(set(stored) - set(pool))
    disagree = sorted(k for k in set(pool) & set(stored) if pool[k] != stored[k])
    return {"onlyOnDevice": only_pool, "onlyInPostgres": only_stored,
            "disagree": [{"id": k, "device": pool[k], "postgres": stored[k]}
                         for k in disagree],
            "agreed": len(set(pool) & set(stored)) - len(disagree)}



# --- the applied index, read from Postgres ----------------------------------
#
# `ingest.applied_index` builds the same map from three store files -- the
# history events, the sessions, and the role index -- and `already_decided`
# reads it to refuse a posting the user has passed on. All three are class B
# and all three are mirrored into `applications` and `application_events`, so
# the same index is derivable from Postgres and nothing has ever derived it.
#
# **`seen`'s failure mode is visible, which is why this consumer goes first.**
# A miss here re-offers a role the user already dropped, on the board, where
# they will see it. Every other reader on the switching list fails silently.

#: How a session's `status` becomes a decision. Lifted verbatim from
#: `ingest.applied_index` rather than re-derived, because two spellings of one
#: mapping is the class this repository has five instances of. The test
#: compares this against that function's own source.
SESSION_STATE = {"inflight": "applied", "review": "watched",
                 "active": "watched", "watch": "watched", "dropped": "passed"}


def applied_index(conn, account_id, root=None):
    """`ingest.applied_index`'s map, built from Postgres instead of the files.

    Same keys, same precedence, same `setdefault` semantics: the first writer
    of a key wins, and the order is history events then sessions, because an
    application that completed outranks whatever its session last said.

    `applications` carries the session half and the role half in one row --
    `class_b.session_row` and `class_b.role_row` write disjoint columns of it
    -- so what the files keep in three places is one join here. The url comes
    off the role half, exactly as the file version prefers `role.get("url")`.
    """
    import identity
    import jobs_store

    index = {}

    def remember(company, title, url, state):
        if company and title:
            index.setdefault(
                (identity.norm_company(company), jobs_store.norm_title(title)),
                state)
        link = identity.url_key(url)
        if link:
            index.setdefault(("url", link), state)

    with conn.cursor() as cur:
        # The latest event per application, and only `completed` counts as
        # applied -- the file version sorts every event by `at` and keeps the
        # last, so a `completed` followed by an `abandoned` is not an
        # application. `distinct on` is that same "last one wins".
        cur.execute(
            """select distinct on (a.id)
                      a.legacy_id, e.kind, a.company, a.role, a.apply_url
                 from application_events e
                 join applications a on a.id = e.application_id
                where e.account_id = %s
                order by a.id, e.at desc, e.id desc""",
            (account_id,))
        for _legacy, kind, company, role, url in cur.fetchall():
            if kind == "completed":
                remember(company, role, url, "applied")

        cur.execute(
            """select status, company, role, apply_url from applications
                where account_id = %s and status is not null""",
            (account_id,))
        for status, company, role, url in cur.fetchall():
            state = SESSION_STATE.get(status)
            if state:
                remember(company, role, url, state)
    return index


def index_disagreements(from_files, from_postgres):
    """Where the two indexes differ, keyed the way the index is keyed.

    Reported as three lists rather than a count, and the direction matters
    more than the total: a key only the files hold is a posting Postgres would
    re-offer, which is the visible failure, and a key only Postgres holds is
    one it would wrongly refuse, which is silent.
    """
    def show(key):
        return "url:" + key[1] if key[0] == "url" else "%s|%s" % key

    only_files = sorted(show(k) for k in set(from_files) - set(from_postgres))
    only_pg = sorted(show(k) for k in set(from_postgres) - set(from_files))
    differ = sorted(
        (show(k), from_files[k], from_postgres[k])
        for k in set(from_files) & set(from_postgres)
        if from_files[k] != from_postgres[k])
    return {"onlyInFiles": only_files, "onlyInPostgres": only_pg,
            "differ": [{"key": k, "files": f, "postgres": p}
                       for k, f, p in differ],
            "agreed": len(set(from_files) & set(from_postgres)) - len(differ)}



#: Where a shadow read records what it saw. One line per run, appended.
SHADOW_LOG = "logs/shadow-reads.jsonl"


def shadow_applied_index(root, from_files, note=""):
    """Build the index from Postgres too, and record where the two differ.

    **The file is authoritative throughout and this returns nothing the caller
    uses.** That is the whole shape: a reader is switched only after a window
    in which the two agreed on real traffic, exactly as the mirror was written
    before anything read it. One reader at a time, each with its own window.

    Never raises and never slows the sweep down by more than the read. A
    Postgres that is unreachable is recorded as unreachable, not as agreement:
    "could not look" and "they agree" are different facts and only one of them
    is evidence.
    """
    import time
    from pathlib import Path
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "reader": "applied_index", "note": note,
             "fileKeys": len(from_files),
             # What was read, not how much. A window of identical reads
             # measures nothing however long it runs, so the digest is what
             # says the subject moved between two of them.
             "indexDigest": index_digest(from_files)}
    started = time.time()
    try:
        import dual_write
        import jobs_db
        conn = jobs_db.connect()
        try:
            account = dual_write.account_id(conn)
            stored = applied_index(conn, account, root)
        finally:
            conn.close()
        entry["postgresKeys"] = len(stored)
        entry.update(index_disagreements(from_files, stored))
        entry["clean"] = not (entry["onlyInFiles"] or entry["onlyInPostgres"]
                              or entry["differ"])
    except Exception as exc:                              # noqa: BLE001
        entry["unreadable"] = f"{type(exc).__name__}: {exc}"[:200]
        entry["clean"] = None
    entry["seconds"] = round(time.time() - started, 2)

    try:
        path = Path(root) / SHADOW_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:                                # noqa: BLE001
        sys.stderr.write(f"shadow read: could not record: {exc}\n")
    return entry



def index_digest(index):
    """A stable digest of an index's contents, so "it changed" is checkable.

    `blake2b` rather than `hash()`, which is randomised per process: a digest
    that changes on restart would report every read as a change and every
    window as covered.
    """
    import hashlib
    parts = []
    for key in sorted(index, key=lambda k: (str(k[0]), str(k[1]))):
        parts.append(f"{key[0]}\x1f{key[1]}\x1f{index[key]}")
    return hashlib.blake2b("\x1e".join(parts).encode("utf-8"),
                           digest_size=8).hexdigest()


#: The three writers of the thing `applied_index` reads. A window is covered
#: when each has written and a clean shadow read has happened since.
#:
#: **Not a duration, and the reason is the anti-vacuity rule this repository
#: already carries.** 404 of 404 agreed on the first read, so forty-two more
#: reads of the same 404 keys prove nothing about a reader: the check would
#: pass on a subject that never moved, exactly as `test_v2_matches_the_board`
#: passed on 99 classes when a real render emits 164. What a window is for is
#: disagreement under real conditions, and the conditions are three different
#: processes writing.
#:
#: Enumerated from the code rather than remembered, which is this week's third
#: census: the writers of `applications` and `application_events` are the
#: device's own class B mirror, the board's Pass and Watch, and v2's route.
#: The notes a production shadow read carries. `ingest.decisions_shadow` passes
#: the name of the function it was called from, so anything else is somebody
#: running the reader by hand.
#:
#: **A hand probe must not open a window that measures production traffic.**
#: The first version counted one, from a worktree, and used its timestamp as
#: `openedAt` -- so the window would have been "open" since before the code was
#: on the device, and the three writers would have been judged against a clock
#: that started in the wrong place. Same shape as the `activity.json` files
#: two sessions each dated to their own run: provenance is the property the
#: artifact cannot answer, so it is written down rather than inferred.
PRODUCTION_NOTES = ("run", "add_postings")

WINDOW_WRITERS = {
    "sweep": "a sweep whose merge moved the decided set",
    "device": "a Pass or Watch taken on the board and mirrored by the device",
    "v2": "a write from the board itself, through the v2 route",
}


def window(root, conn, account_id):
    """Which of the three writers has been exercised with a clean read since.

    Returns a verdict per writer and an overall `ready`. The switch is
    permanent when `ready` is true, and this exists so that decision is
    checkable rather than recalled.
    """
    from pathlib import Path
    reads = []
    path = Path(root) / SHADOW_LOG
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                reads.append(json.loads(line))
            except ValueError:
                continue

    # Hand probes are recorded and never counted. See `PRODUCTION_NOTES`.
    probes = [r for r in reads if r.get("note") not in PRODUCTION_NOTES]
    reads = [r for r in reads if r.get("note") in PRODUCTION_NOTES]

    clean = [r for r in reads if r.get("clean") is True and r.get("at")]
    dirty = [r for r in reads if r.get("clean") is False]
    unreadable = [r for r in reads if r.get("clean") is None]
    latest_clean = max((r["at"] for r in clean), default=None)

    with conn.cursor() as cur:
        # v2 stamps `decided_from` on a decision and `pending_order_at` on an
        # application, and either is a v2 write.
        cur.execute(
            """select greatest(
                 (select max(decided_at) from job_decisions
                   where account_id = %s and decided_from = 'v2'),
                 (select max(pending_order_at) from applications
                   where account_id = %s))""",
            (account_id, account_id))
        last_v2 = cur.fetchone()[0]
        cur.execute(
            """select max(decided_at) from job_decisions
                where account_id = %s and decided_from <> 'v2'""",
            (account_id,))
        last_device = cur.fetchone()[0]

    # A sweep moving the decided set shows as two clean reads with different
    # digests. Two reads of one unchanged subject are one sample, not two.
    digests = [r.get("indexDigest") for r in clean if r.get("indexDigest")]
    moved = len(set(digests)) > 1

    opened = min((r["at"] for r in reads if r.get("at")), default=None)

    def covered(when):
        """Written *inside* the window, with a clean read after it.

        **The window bound is the half that makes this evidence.** Without it a
        write from three days before the shadow read existed counts, because a
        clean read trivially follows it -- and that proves nothing about a
        reader tracking a change, which is what the window is for. Measured on
        the first run: two of the three writers read `met` on writes that
        predated the window entirely.
        """
        if when is None or not opened or not latest_clean:
            return False
        stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(when, "strftime") \
            else str(when)
        return opened <= stamp <= latest_clean

    verdict = {
        # Whatever moved it. Two clean reads of an unchanged subject are one
        # sample, and a window of those measures nothing however long it runs.
        "sweep": {"met": moved,
                  "how": f"{len(set(digests))} distinct index digests across "
                         f"{len(clean)} clean reads"},
        "device": {"met": covered(last_device),
                   "how": f"last device decision {last_device}"},
        "v2": {"met": covered(last_v2),
               "how": f"last v2 write {last_v2}"},
    }
    for name, entry in verdict.items():
        entry["needs"] = WINDOW_WRITERS[name]
    return {"reads": len(reads), "clean": len(clean), "disagreed": len(dirty),
            "unreadable": len(unreadable), "openedAt": opened,
            "handProbes": len(probes),
            "latestCleanAt": latest_clean,
            "writers": verdict,
            "ready": bool(not dirty and all(v["met"] for v in verdict.values()))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--compare", action="store_true",
                    help="read both sides and report where they differ")
    ap.add_argument("--window", action="store_true",
                    help="has the shadow window covered its three writers")
    args = ap.parse_args(argv)

    import jobs_db
    import jobs_store
    conn = jobs_db.connect()
    try:
        import dual_write
        account = dual_write.account_id(conn)
        stored = for_account(conn, account)
        missing = orphans(conn, account)
    finally:
        conn.close()

    report = {"account": str(account), "decisions": len(stored),
              "byState": {}, "orphanedDecisions": missing}
    for state in sorted(set(stored.values())):
        report["byState"][state] = sum(1 for v in stored.values() if v == state)

    if args.window:
        conn = jobs_db.connect()
        try:
            report["window"] = window(jobs_store.root_dir(None), conn, account)
        finally:
            conn.close()

    if args.compare:
        pool = from_pool(jobs_store.load_jobs(jobs_store.root_dir(None)))
        report["pool"] = len(pool)
        report["comparison"] = compare(pool, stored)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if args.window and not args.compare:
        return 0 if report["window"]["ready"] else 1
    if args.compare:
        c = report["comparison"]
        return 0 if not (c["onlyOnDevice"] or c["onlyInPostgres"]
                         or c["disagree"]) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
