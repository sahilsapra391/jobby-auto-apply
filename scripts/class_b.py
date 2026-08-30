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
    status = None
    phase = None
    if session:
        status = (session.get("status") or "").strip() or None
        phase = (session.get("phase") or "").strip() or None

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

        cur.execute(
            """insert into applications
                   (account_id, legacy_id, status, phase, updated_at)
               values (%s, %s, %s, %s, now())
               on conflict (account_id, legacy_id) do update
                 set status = excluded.status,
                     phase = coalesce(excluded.phase, applications.phase),
                     updated_at = now()
               returning id""",
            (account_id, legacy_id, status, phase))
        return cur.fetchone()[0]


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


def _mirror_answers(conn, account_id, payload):
    """`answers.json` is written whole, so the mirror is a whole replacement.

    An answer deleted from the file must not survive here, which an upsert
    alone would leave behind.
    """
    rows = []
    for key, entry in (payload or {}).items():
        if not isinstance(entry, dict):
            continue
        rows.append((
            account_id, key,
            entry.get("question"), entry.get("value"), entry.get("state"),
            list(entry.get("tags") or []),
            bool(entry.get("sensitive")),
        ))
    with conn.cursor() as cur:
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
        cur.execute(
            "delete from answer_bank where account_id = %s and not (key = any(%s))",
            (account_id, [row[1] for row in rows] or [""]))


# Profile keys that do not travel. `resumes` and `writingSamples` belong to
# the resume registry and the job bundle, both class C: mirroring them here
# would put the same wording in two places with no rule about which wins,
# which is exactly the failure that served one document's wording in place of
# another's for as long as the feature existed.
_PROFILE_EXCLUDED = {"resumes", "writingSamples"}


def _mirror_profile(conn, account_id, payload):
    identity = {k: v for k, v in (payload or {}).items()
                if k not in _PROFILE_EXCLUDED}
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


_HANDLERS = {
    "session": _mirror_session,
    "history": _mirror_history,
    "answers": _mirror_answers,
    "profile": _mirror_profile,
    "roles": _mirror_roles,
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

def after_write(path, payload, root=None):
    """Called after a class B file has been written. Never raises.

    This is the whole contract: the file is already on disk and the caller has
    already succeeded, so nothing here may change that. A failure is logged and
    swallowed, and parity is what makes the resulting drift visible.
    """
    kind = kind_of(path)
    if kind is None:
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
              "roles": 0}

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
