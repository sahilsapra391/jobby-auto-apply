#!/usr/bin/env python3
"""Mirror sessions, history, answers and profile into Postgres. File first.

Class A, the job pool, writes Postgres first and fails the operation when
Postgres fails. **Class B is the opposite, and the asymmetry is deliberate:**

    class A   Postgres, then the file.  Postgres fails -> the operation fails.
    class B   the file, then Postgres.  Postgres fails -> the operation stands.

A failed pool write costs a sweep and is fixed by running it again. A failed
completion record costs an application nobody can un-send. `finish.py` records a
submission through `store.py session-save` and `store.py history-append`, and
the duplicate guard reads that history: an application that submitted while
recording nothing is one the next attempt submits again, to a real employer,
under a real name. There is no ordering in which that is worth a network call.

**Which makes silence the danger here rather than failure.** A mirror that
cannot fail the write is a mirror that can stop working with nothing visibly
wrong, so every skipped or failed mirror is written to `logs/dual-write.log`
and to stderr, and parity is what turns the resulting drift red. The rule is
the repository's own: no diagnostic path is ever discarded.

The dispatch is by path, and it maps only what `docs/write-paths.md` puts in
class B. Everything else returns None rather than falling through to a default,
because a dispatch that guesses mirrors one file's contents into another's
table.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "packages" / "schema"))


class ClassBError(Exception):
    """A refusal inside the mirror. Never reaches the caller of a store write."""


# Store filename -> what it holds. Class C files are absent on purpose; see
# docs/write-paths.md. `sessions/` is matched by its directory, so a session
# that happens to be called `meta.json` is still a session.
_BY_NAME = {
    "answers.json": "answers",
    "profile.json": "profile",
    "roles.json": "roles",
    "providers.json": "providers",
}


def kind_of(path):
    """What this store path holds, or None if it is not mirrored."""
    path = Path(path)
    if path.parent.name == "sessions":
        return "session"
    return _BY_NAME.get(path.name)


# --------------------------------------------------------------------------

def _application_id(conn, account_id, legacy_id, session=None):
    """The applications row for a legacy application id, creating it if new.

    History can arrive before the session file is written, so an event for an
    unknown application creates it rather than being dropped. Dropping it would
    lose a completion record, which is the one thing this whole file exists to
    protect.

    **Only a caller holding a status may set one.** `session` is None for a
    history event and for a roles.json entry, and neither knows what state the
    application is in: the session file is the authority. The first version
    defaulted to `unknown` and `known` and wrote them unconditionally, so
    backfilling roles after sessions overwrote all 88 real statuses and parity
    opened at 88 divergences. A row that exists keeps the status it has unless
    something with a real one says otherwise.
    """
    built = session_row(session) if session else None
    status = built["status"] if built else None

    with conn.cursor() as cur:
        if status is None:
            # Create it if new, and touch nothing if it exists. `do nothing`
            # returns no row, so the id is read back separately.
            cur.execute(
                """insert into applications
                       (account_id, legacy_id, status, updated_at)
                   values (%s, %s, 'unknown', now())
                   on conflict (account_id, legacy_id) do nothing""",
                (account_id, legacy_id))
            cur.execute(
                "select id from applications "
                "where account_id = %s and legacy_id = %s",
                (account_id, legacy_id))
            return cur.fetchone()[0]

        # Every column the session is authoritative for, in one statement, so
        # a column added to `session_row` reaches the table without a second
        # place to remember. Parity discovers the set the same way.
        session_columns = [c for c in built if c != "status"]
        placeholders = ", ".join(
            "%s::jsonb" if c == "blockers" else "%s" for c in session_columns)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in session_columns)
        cur.execute(
            f"""insert into applications
                   (account_id, legacy_id, status, {', '.join(session_columns)},
                    updated_at)
               values (%s, %s, %s, {placeholders}, now())
               on conflict (account_id, legacy_id) do update
                 -- **Not `coalesce(excluded.phase, applications.phase)`.**
                 -- This statement is only reached when a session supplied a
                 -- status, and a session is the authority on its own phase
                 -- including when it has none: the coalesce made an absent
                 -- phase mean "keep whatever you had", so a phase could be
                 -- written once and never cleared. Found by widening parity,
                 -- on its first run: Harvey 17121dc2523fec5e had no `phase`
                 -- key at all, status `dropped`, and Postgres held `Queued`.
                 -- The caller that genuinely knows nothing about phase is a
                 -- history event, and it takes the `do nothing` branch above.
                 set status = excluded.status,
                     {assignments},
                     updated_at = now()
               returning id""",
            [account_id, legacy_id, status]
            + [json.dumps(built[c]) if c == "blockers" and built[c] is not None
               else built[c] for c in session_columns])
        return cur.fetchone()[0]


# --------------------------------------------------------------------------
# What each mirrored row holds, as functions rather than as inline tuples.
#
# **Parity calls these, so there is one transform and not two.** The class A
# side learned this the expensive way: `verify_parity.FIELDS` was eleven
# lambdas reimplementing `posting_row`, it covered 11 of 28 columns, and being
# a subset is exactly how a second implementation drifts. Extracting the row
# here means a column added to the mirror is a column parity compares, without
# anybody remembering to add it in two places.


def session_row(payload):
    """What `applications` holds for a session, from the session's own file."""
    payload = payload or {}
    status = (payload.get("status") or "").strip() or None
    phase = (payload.get("phase") or "").strip() or None
    # **Verbatim, and never normalised into columns.** 61 entries across eight
    # different key shapes on the real store; picking columns would drop the
    # keys that carry the instruction. Migration 0009 says why at length.
    #
    # Null and empty are one thing: nothing is blocked. An empty list stored
    # as `[]` would be a third spelling of that, and the lane would have to
    # know all three.
    blocked = payload.get("pendingFields") or None

    def text(key):
        return (payload.get(key) or "").strip() or None

    def number(key):
        value = payload.get(key)
        return int(value) if isinstance(value, (int, float)) else None

    return {
        "status": status, "phase": phase, "blockers": blocked,
        # `postedDate` is a calendar date in the session and a timestamptz in
        # the column; Postgres casts it, and the Found date contract says this
        # is `postedAt` and never `firstSeenAt`.
        "posted_at": text("postedDate"),
        # What only the session carries. Migration 0012 lists what each is for
        # and how many rows have it; every one of them is a lane currently
        # drawing "not captured" over data that exists.
        "core_skills": list(payload.get("coreSkills") or []) or None,
        "fit_score": number("fitScore"),
        "alignment": text("alignment"),
        "dropped_from": text("droppedFrom"),
        "dropped_stage": text("droppedStage"),
        "seen_on_boards": number("seenOnBoards"),
    }


def role_row(entry):
    """What `applications` holds from the role index.

    **The role index names an application; the session says what state it is
    in.** They write different columns of one row for that reason, and
    `_mirror_roles` passing a status once overwrote all 88 real ones with the
    word "known".

    Measured 2026-09-02: all 204 populated `company`/`role` pairs in production
    match `roles.json` exactly, so this is where the board's names come from.
    """
    entry = entry or {}

    def text(key):
        return (entry.get(key) or "").strip() or None

    return {
        "company": text("company"),
        "role": text("role"),
        "apply_url": text("url"),
        "region": text("region"),
        "source": text("source"),
        "ats": text("ats"),
    }


#: What the board draws about a provider, and nothing else.
#:
#: **`connectionKey` is deliberately absent and `provider_links` has no column
#: for it.** The schema already made this decision and it is worth not
#: re-opening: the board needs to know a provider is connected, healthy and in
#: what failover order, and it needs none of the credential machinery to say
#: so. CLAUDE.md's rule is that nothing about a provider's credentials reaches
#: the queue, Vercel or KV, and a mirror is exactly the path that would.
PROVIDER_FIELDS = ("connected", "health", "detail", "connectionOrder",
                   "connectedAt", "lastCheckedAt", "modelVerifiedAt")


def provider_row(provider_id, entry):
    """What `provider_links` holds for one connection."""
    entry = entry or {}
    import providers as _providers
    return {
        "provider": provider_id,
        "status": "connected" if entry.get("connected") else "disconnected",
        "model_pinned": _providers.MODEL_IDS.get(provider_id),
        "connected_at": entry.get("connectedAt"),
        "connection_order": entry.get("connectionOrder"),
        "health": (entry.get("health") or "").strip() or None,
        "detail": (entry.get("detail") or "").strip() or None,
        "last_checked_at": entry.get("lastCheckedAt"),
        "model_verified_at": entry.get("modelVerifiedAt"),
    }


def history_row(payload):
    """What `application_events` holds for one history line."""
    payload = payload or {}
    return {
        "kind": (payload.get("event") or "").strip() or None,
        # The whole line, which is where the confirmation text, the resume
        # filename and the cover-letter note live. Compared as a value: it is
        # the only record of what an employer received.
        "payload": payload,
        "at": payload.get("at"),
    }


def answer_row(entry):
    """What `answer_bank` holds for one stored answer."""
    entry = entry or {}
    return {
        "question": entry.get("question"),
        "value": entry.get("value"),
        "state": entry.get("state"),
        "tags": list(entry.get("tags") or []),
        "sensitive": bool(entry.get("sensitive")),
    }


def profile_row(payload):
    """What `profiles.identity` holds, minus what the mirror strips."""
    return {"identity": {k: v for k, v in (payload or {}).items()
                         if k not in _PROFILE_EXCLUDED}}


def _mirror_session(conn, account_id, payload):
    legacy_id = (payload.get("applicationId") or "").strip()
    if not legacy_id:
        raise ClassBError("a session with no applicationId cannot be filed")
    _application_id(conn, account_id, legacy_id, payload)


def _mirror_history(conn, account_id, payload):
    legacy_id = (payload.get("applicationId") or "").strip()
    kind = (payload.get("event") or "").strip()
    if not legacy_id or not kind:
        raise ClassBError("a history event needs an applicationId and an event")

    application = _application_id(conn, account_id, legacy_id)
    # Keyed on the store's own event id. `applications.jsonl` is append-only
    # and gets re-read, so without this every pass would duplicate the event.
    event_id = (payload.get("eventId") or "").strip() or None
    with conn.cursor() as cur:
        if event_id:
            cur.execute(
                "select 1 from application_events "
                "where application_id = %s and payload->>'eventId' = %s",
                (application, event_id))
            if cur.fetchone():
                return
        cur.execute(
            """insert into application_events
                   (account_id, application_id, kind, payload, at)
               values (%s, %s, %s, %s, coalesce(%s::timestamptz, now()))""",
            (account_id, application, kind, json.dumps(payload),
             payload.get("at")))


ANSWER_DELETE_CEILING = 0.25


def _mirror_answers(conn, account_id, payload, force=False):
    """`answers.json` is written whole, so the mirror is a whole replacement.

    An answer deleted from the file must not survive here, which an upsert
    alone would leave behind. But a whole replacement is a delete statement
    wearing a sync's clothes, and until 2026-08-30 this one had neither a floor
    nor a ceiling while `dual_write.resync` next door had both.

    Measured that day, four ways, against four seeded answers in an isolated
    scratch schema:

        _mirror_answers(conn, account, {})                 -> ALL 4 DELETED
        _mirror_answers(conn, account, None)               -> ALL 4 DELETED
        _mirror_answers(conn, account, {"gender": "oops"}) -> ALL 4 DELETED
        _mirror_answers(conn, account, {"gender": {...}})  -> 1 left, correct

    On the real store that is 190 standing answers -- relocation, visa, work
    authorisation, the ones an employer's form asks and the agent answers from
    memory -- removed by a payload that arrives empty for any reason at all.

    **The floor: an empty payload refuses the whole mirror and deletes
    nothing.** An empty `answers.json` is a read failure or a first run far
    more often than it is an instruction to empty the bank, which is the same
    sentence `resync` already makes about an empty pool.

    **The keep set is every key the file names, not every key that parsed.**
    That is what makes the third case above the worst of the four: the payload
    looked valid enough to proceed, one unreadable entry produced an empty
    `rows`, and the emptiness was then read as "the file lists no answers".
    A malformed entry is a reason not to update that one answer. It is never a
    reason to delete it, and never a reason to delete its neighbours.

    **The ceiling: `ANSWER_DELETE_CEILING`, the same 0.25 class A uses.**
    Deleting most of the bank is the shape of a bad read rather than a person
    clearing their answers, and `force=True` is how a caller says it meant it.

    Class B has exactly one delete and this is it; `_mirror_roles`,
    `_mirror_profile`, `_mirror_session` and `_mirror_history` all upsert. So
    the asymmetry with class A was confined to this function, and closing it
    here closes it everywhere.
    """
    if not payload:
        raise ClassBError(
            "refusing to mirror an empty answers payload: that would delete "
            "every stored answer, and an empty answers.json is a read failure "
            "or a first run far more often than it is the truth")
    if not isinstance(payload, dict):
        raise ClassBError(
            f"answers payload must be a mapping, got {type(payload).__name__}")

    rows, unreadable = [], []
    for key, entry in payload.items():
        if not isinstance(entry, dict):
            # Skipped for the upsert, kept for the keep set below.
            unreadable.append(key)
            continue
        built = answer_row(entry)
        rows.append((
            account_id, key, built["question"], built["value"],
            built["state"], built["tags"], built["sensitive"],
        ))

    keep = set(payload)

    with conn.cursor() as cur:
        cur.execute("select key from answer_bank where account_id = %s",
                    (account_id,))
        held = {row[0] for row in cur.fetchall()}
        doomed = sorted(held - keep)

        # Checked before any write, so a refusal costs nothing and cannot
        # leave half an update behind.
        if held and doomed and not force:
            share = len(doomed) / len(held)
            if share > ANSWER_DELETE_CEILING:
                raise ClassBError(
                    f"refusing to delete {len(doomed)} of {len(held)} stored "
                    f"answers ({share:.0%}), over the "
                    f"{ANSWER_DELETE_CEILING:.0%} ceiling. That is the shape of "
                    f"a truncated answers.json rather than a person clearing "
                    f"their answers. Pass force=True if it really did shrink "
                    f"that far.")

        for chunk in _chunks(rows, 500):
            values = ", ".join(["(%s,%s,%s,%s,%s,%s,%s,now())"] * len(chunk))
            cur.execute(
                f"""insert into answer_bank (account_id, key, question, value,
                                         state, tags, sensitive, updated_at)
                    values {values}
                    on conflict (account_id, key) do update
                      set question = excluded.question,
                          value = excluded.value,
                          state = excluded.state,
                          tags = excluded.tags,
                          sensitive = excluded.sensitive,
                          updated_at = now()""",
                [field for row in chunk for field in row])

        if doomed:
            cur.execute(
                "delete from answer_bank where account_id = %s and key = any(%s)",
                (account_id, doomed))

    if unreadable:
        # Never discarded. An entry that could not be read is the reason a
        # later parity divergence exists, and it is the only place that fact
        # is ever produced.
        sys.stderr.write(
            f"class_b: {len(unreadable)} answers entr"
            f"{'y was' if len(unreadable) == 1 else 'ies were'} not a mapping "
            f"and {'was' if len(unreadable) == 1 else 'were'} left unchanged "
            f"rather than deleted: {', '.join(sorted(unreadable)[:5])}\n")


# Profile keys that do not travel. `resumes` and `writingSamples` belong to
# the resume registry and the job bundle, both class C: mirroring them here
# would put the same wording in two places with no rule about which wins,
# which is exactly the failure that served one document's wording in place of
# another's for as long as the feature existed.
_PROFILE_EXCLUDED = {"resumes", "writingSamples"}


def _mirror_providers(conn, account_id, payload):
    """`providers.json` -> `provider_links`.

    **v2's Settings page showed one provider connected while all three were.**
    It was reading a hardcoded fixture, and behind that fixture there was
    nothing to read: `provider_links` existed, with the right columns, holding
    zero rows, because no handler ever wrote it.

    A whole replacement like `_mirror_answers`, and for the same reason: the
    file is written whole and a disconnect has to be able to remove a row. It
    carries the same floor -- an empty payload refuses rather than deleting
    every connection -- because an unreadable `providers.json` is a read
    failure far more often than it is three simultaneous disconnects. No
    ceiling: there are three providers, so any proportion is one or two rows
    and the ceiling would either never fire or fire on a real disconnect.
    """
    connections = (payload or {}).get("connections")
    if not connections:
        raise ClassBError(
            "refusing to mirror an empty providers payload: that would record "
            "every provider as disconnected, and an empty providers.json is a "
            "read failure far more often than it is the truth")

    rows = [provider_row(pid, entry) for pid, entry in sorted(connections.items())
            if isinstance(entry, dict)]
    if not rows:
        raise ClassBError("no readable provider connections in the payload")

    columns = list(rows[0])
    with conn.cursor() as cur:
        placeholders = ", ".join(
            "(%s, " + ", ".join(["%s"] * len(columns)) + ", now())"
            for _ in rows)
        cur.execute(
            f"""insert into provider_links (account_id, {', '.join(columns)},
                                            updated_at)
                values {placeholders}
                on conflict (account_id, provider) do update
                  set {', '.join(f'{c} = excluded.{c}' for c in columns
                                 if c != 'provider')},
                      updated_at = now()""",
            [v for row in rows
             for v in [account_id] + [row[c] for c in columns]])
        cur.execute(
            "delete from provider_links where account_id = %s "
            "and provider <> all(%s)",
            (account_id, [row["provider"] for row in rows]))


def _mirror_profile(conn, account_id, payload):
    identity = profile_row(payload)["identity"]
    with conn.cursor() as cur:
        cur.execute(
            """insert into profiles (account_id, identity, updated_at)
               values (%s, %s, now())
               on conflict (account_id) do update
                 set identity = excluded.identity, updated_at = now()""",
            (account_id, json.dumps(identity)))


def _mirror_roles(conn, account_id, payload):
    """`roles.json` is the durable index of every role ever seen.

    Every entry in it corresponds to an application the store knows about, so
    it is mirrored as applications rather than as a table of its own: §3 has no
    `roles`, and inventing one would be a fifth spelling of the same fact.
    """
    for legacy_id, entry in (payload or {}).items():
        if not isinstance(entry, dict):
            continue
        # No status: roles.json is an index of roles seen, not a record of
        # what state an application is in. Passing one here overwrote every
        # session's real status with the word "known".
        _application_id(conn, account_id, legacy_id)
        row = role_row(entry)
        # **The role index owns the name and nothing else.** A session writes
        # status, phase, blockers and the fit fields; these six are the ones
        # only the index has. Two writers, disjoint columns, one row.
        with conn.cursor() as cur:
            cur.execute(
                f"""update applications
                       set {', '.join(f'{c} = %s' for c in row)},
                           updated_at = now()
                     where account_id = %s and legacy_id = %s""",
                list(row.values()) + [account_id, legacy_id])


_HANDLERS = {
    "session": _mirror_session,
    "history": _mirror_history,
    "answers": _mirror_answers,
    "profile": _mirror_profile,
    "roles": _mirror_roles,
    "providers": _mirror_providers,
}


def mirror(conn, account_id, kind, payload):
    """Write one store artifact into Postgres, inside one transaction."""
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise ClassBError(f"no mirror for {kind!r}")
    with conn.transaction():
        handler(conn, account_id, payload)


def _chunks(rows, size):
    rows = list(rows)
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


# --------------------------------------------------------------------------

def writes_the_device_store(path):
    """Is this path inside the store the device's database mirrors?

    **The refusal lives at the write, not at the caller**, which is the shape
    `provider_usage.record` established and the only one that has held. Four
    separate holes on 2026-08-30 were one property: a process reaching the
    device's database while whoever launched it believed it was isolated.
    Patching the caller closes hole four and waits for hole five.

    The last of those, found by instrumenting `resolved_url` to log a stack
    trace rather than by grepping for callers: tests spawn
    `python3 scripts/store.py --root <tmpdir>`, and `store.py` takes `--root`
    as an argument without exporting `JOB_APPLICATION_DIR`. So the child wrote
    a temporary store, resolved credentials from the real `config.env`, and
    mirrored seven `completed` events into production while a fully green suite
    reported `Ran 2871 tests ... OK`. 40 traced calls, `JOB_APPLICATION_DIR`
    unset in every one. `finish.py` exports its `--root` and is why that path
    was already closed; nothing made `store.py` do the same.

    Comparing the written path against the device root covers every caller,
    including the ones nobody has found: a write to somebody's temporary
    directory is not a write to the store this database mirrors, whatever the
    process thinks its root is.

    Fails closed. An unresolvable path is not proof of belonging.
    """
    import store
    try:
        target = Path(path).resolve()
        device = Path(store.root_dir(None)).resolve()
    except OSError:                                      # pragma: no cover
        return False
    return target == device or device in target.parents


def after_write(path, payload, root=None):
    """Called after a class B file has been written. Never raises.

    This is the whole contract: the file is already on disk and the caller has
    already succeeded, so nothing here may change that. A failure is logged and
    swallowed, and parity is what makes the resulting drift visible.
    """
    kind = kind_of(path)
    if kind is None:
        return None

    if not writes_the_device_store(path):
        # Never silent: this is the branch that would otherwise have mirrored
        # a fixture into the account's real history.
        sys.stderr.write(
            f"class_b: refusing to mirror {Path(path).name} -- it is outside "
            f"the device store, so this process is writing somewhere the "
            f"device's database does not mirror\n")
        return None

    import dual_write
    if not dual_write.enabled():
        return None

    conn = None
    try:
        conn = dual_write.connect()
        mirror(conn, dual_write.account_id(conn), kind, payload)
        return True
    except Exception as exc:
        _report(path, kind, exc, root)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:                            # pragma: no cover
                pass


def backfill(conn, account_id, root=None):
    """Load every class B artifact already on disk into Postgres.

    The counterpart to the pool backfill, and needed for the same reason: the
    mirror only sees writes from the moment it is live, and 88 sessions, 213
    history events, 190 answers and one profile were written before that.
    Without this, parity opens red on every one of them.

    Idempotent, like the pool backfill and for the same reason: it cannot be
    atomic with the mirror going live.
    """
    import store
    base = Path(root) if root else Path(store.root_dir(None))
    paths = store.paths(base)
    report = {"sessions": 0, "history": 0, "answers": 0, "profile": 0,
              "roles": 0, "providers": 0}

    sessions = Path(paths["sessions"])
    if sessions.is_dir():
        for path in sorted(sessions.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ClassBError(f"{path.name} could not be read: {exc}")
            mirror(conn, account_id, "session", payload)
            report["sessions"] += 1

    roles = Path(paths["roles"])
    if roles.exists():
        mirror(conn, account_id, "roles",
               json.loads(roles.read_text(encoding="utf-8")))
        report["roles"] = 1

    # The provider registry lives beside the store rather than inside it: it is
    # `providers.py`'s file and `store.paths` deliberately has no key for it.
    registry = base / "providers.json"
    if registry.exists():
        mirror(conn, account_id, "providers",
               json.loads(registry.read_text(encoding="utf-8")))
        report["providers"] = 1

    history = Path(paths["history"])
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise ClassBError(f"applications.jsonl line unreadable: {exc}")
            # A replayed `completed` is refused by the database's own unique
            # index, which is correct for a live write and wrong for a
            # backfill re-run: the row is already there. Skipping it keeps the
            # backfill idempotent without weakening the invariant.
            try:
                mirror(conn, account_id, "history", event)
                report["history"] += 1
            except Exception as exc:
                if "one_completion_per_application" not in str(exc):
                    raise

    answers = Path(paths["answers"])
    if answers.exists():
        mirror(conn, account_id, "answers",
               json.loads(answers.read_text(encoding="utf-8")))
        report["answers"] = 1

    profile = Path(paths["profile"])
    if profile.exists():
        mirror(conn, account_id, "profile",
               json.loads(profile.read_text(encoding="utf-8")))
        report["profile"] = 1

    return report


def _report(path, kind, exc, root=None):
    """Loud, on two paths, because this failure cannot announce itself any
    other way: the write it follows has already succeeded."""
    name = Path(path).name
    message = (f"class B mirror failed for {kind} {name}: "
               f"{type(exc).__name__}: {str(exc)[:200]}. The file is written "
               f"and Postgres is behind; parity will report it.")
    sys.stderr.write(f"class_b: {message}\n")
    try:
        base = Path(root) if root else Path("~/.job-application").expanduser()
        log = base / "logs" / "dual-write.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass                        # stderr already carried it
