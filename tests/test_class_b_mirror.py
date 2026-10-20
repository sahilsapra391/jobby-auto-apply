"""Class B: sessions, history, answers and profile, mirrored the other way up.

Class A is Postgres first and a failure fails the operation. Class B is the
**opposite**, and the asymmetry is the point rather than an inconsistency:

    class A   Postgres, then the file.  Postgres fails -> the operation fails.
    class B   the file, then Postgres.  Postgres fails -> the operation stands.

A failed pool write costs a sweep and is fixed by running it again. A failed
completion record costs an application nobody can un-send: `finish.py` records a
submission through `session-save` and `history-append`, the duplicate guard
reads that history, and an application that submitted while recording nothing is
one the next attempt will submit again, to a real employer, under a real name.

So the mirror here is never allowed to fail the write. Which makes the danger
the other way round: a mirror that fails quietly is a store that drifts with
nothing saying so. Every failure is logged loudly and counted, and parity is
what turns the drift red.

These are the tests for both halves of that: it must not break the write, and
it must not be silent when it does not work.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import class_b  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402

STORE = REPO / "scripts" / "store.py"
DEAD_URL = "postgresql://nobody:nobody@127.0.0.1:1/none"


class TheMirrorNeverFailsTheWrite(unittest.TestCase):
    """The whole reason class B is inverted. Run against a database that
    cannot be reached, every one of these must still succeed on disk."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        root = self.home / ".job-application"
        root.mkdir(parents=True)
        config = root / "config.env"
        config.write_text(f"JOBBY_DATABASE_URL={DEAD_URL}\n")
        config.chmod(0o600)
        self.root = root
        self.env = dict(os.environ, HOME=str(self.home),
                        JOB_APPLICATION_DIR=str(root))
        self.env.pop("JOBBY_TEST_DATABASE_URL", None)
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        self.store(["init"])

    def store(self, args, payload=None):
        return subprocess.run([sys.executable, str(STORE)] + args,
                              env=self.env, input=payload,
                              capture_output=True, text=True, timeout=90)

    def test_a_completion_is_recorded(self):
        result = self.store(
            ["session-save", "--id", "probe", "--input", "-"],
            json.dumps({"status": "completed", "phase": "done",
                        "applicationId": "probe"}))
        self.assertEqual(result.returncode, 0, result.stderr[:300])
        self.assertTrue((self.root / "sessions" / "probe.json").exists())

    def test_history_is_appended(self):
        result = self.store(
            ["history-append", "--input", "-"],
            json.dumps({"applicationId": "probe", "event": "completed"}))
        self.assertEqual(result.returncode, 0, result.stderr[:300])
        self.assertTrue((self.root / "applications.jsonl").exists())

    def test_an_answer_is_stored(self):
        result = self.store(["answer-put", "--input", "-"],
                            json.dumps({"question": "Probe?", "value": "yes"}))
        self.assertEqual(result.returncode, 0, result.stderr[:300])

    def test_the_profile_is_written(self):
        result = self.store(["profile-replace", "--input", "-"],
                            json.dumps({"firstName": "A", "lastName": "B"}))
        self.assertEqual(result.returncode, 0, result.stderr[:300])

    def test_the_failure_is_not_silent(self):
        """Never failing the write makes silence the danger. Every skipped
        mirror leaves a line somebody can find."""
        self.store(["session-save", "--id", "probe", "--input", "-"],
                   json.dumps({"status": "completed", "applicationId": "probe"}))
        log = self.root / "logs" / "dual-write.log"
        self.assertTrue(log.exists(), "a failed mirror left no trace at all")
        self.assertIn("probe", log.read_text())


class TheDispatchKnowsWhatItIsLookingAt(unittest.TestCase):
    """A path-keyed dispatch that guesses wrong mirrors one file's contents
    into another's table."""

    def test_each_store_file_maps_to_its_own_kind(self):
        for name, expected in (("answers.json", "answers"),
                               ("profile.json", "profile"),
                               ("roles.json", "roles"),
                               ("sessions/abc123.json", "session")):
            with self.subTest(name=name):
                self.assertEqual(class_b.kind_of(Path("/store") / name),
                                 expected)

    def test_class_c_files_map_to_nothing(self):
        """docs/write-paths.md lists these as deliberately unmirrored. A
        dispatch that fell through to a default would mirror them anyway."""
        # `providers.json` was here and is not class C: migration 0002
        # creates `provider_links` for exactly this, so the class C test --
        # "writes something the migrations created no table for" -- does not
        # apply to it. It was never in `docs/write-paths.md`'s class C table
        # either; it was only in this list, which is the restatement problem
        # the parity field set had. v2's Settings page said one provider was
        # connected while all three were, reading a fixture over an empty
        # table, and this line is part of why the table was empty.
        # `meta.json` left this list for the same reason `providers.json`
        # did: `board_meta` exists for it, so "writes something the migrations
        # created no table for" does not apply. v2's scan banner was reading a
        # fixture over an empty table, showing a next run in the past.
        # `search-config.json` left this list for the third time in the same
        # way: `board_meta.search_config` exists for it. What it costs to leave
        # a file here wrongly is now measured three times over -- the scan
        # banner invented its numbers, Settings said one provider of three, and
        # Found could not say what location scope it was showing.
        for name in ("resume-content.json",
                     "companies.json", "jobs.json",
                     "browser-mcp-apply-1.json", "r2-manifest.json",
                     "passed.json", "source-cache.json"):
            with self.subTest(name=name):
                self.assertIsNone(class_b.kind_of(Path("/store") / name))

    def test_a_session_is_recognised_by_its_directory_not_its_name(self):
        """`sessions/meta.json` is a session called meta, not the meta file."""
        self.assertEqual(class_b.kind_of(Path("/store/sessions/meta.json")),
                         "session")
        self.assertEqual(class_b.kind_of(Path("/store/meta.json")), "meta",
                         "the store's own meta.json is the board's scan "
                         "state; a session called meta is a session")

    def test_an_unknown_file_maps_to_nothing_rather_than_guessing(self):
        self.assertIsNone(class_b.kind_of(Path("/store/something-new.json")))


class ItMirrorsIntoTheRightTables(pgtest.SchemaCase):
    prefix = "classb"

    def setUp(self):
        super().setUp()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('c@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]

    def count(self, table):
        with self.conn.cursor() as cur:
            cur.execute(f"select count(*) from {table}")
            return cur.fetchone()[0]

    def test_a_session_becomes_an_application(self):
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app1", "status": "completed",
                        "company": "Acme", "role": "PM",
                        "url": "https://example.com/j/1"})
        self.assertEqual(self.count("applications"), 1)
        with self.conn.cursor() as cur:
            cur.execute("select legacy_id, status from applications")
            self.assertEqual(cur.fetchone(), ("app1", "completed"))

    def test_the_lane_fields_of_migration_0025_reach_their_columns(self):
        # When the run moved, a capability park and the discipline. Then a
        # later save without them: the session is the authority on its own
        # fields, so a cleared `parkClass` must clear the column, which is what
        # takes a red card back to an ordinary one.
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app1", "status": "review",
                        "phaseAt": "2026-10-20T16:00:07Z",
                        "parkClass": "capability", "discipline": "PM"})
        with self.conn.cursor() as cur:
            cur.execute("select phase_at, park_class, discipline "
                        "from applications")
            at, park, discipline = cur.fetchone()
        self.assertEqual(at, datetime(2026, 10, 20, 16, 0, 7,
                                      tzinfo=timezone.utc))
        self.assertEqual((park, discipline), ("capability", "PM"))
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app1", "status": "review"})
        with self.conn.cursor() as cur:
            cur.execute("select phase_at, park_class, discipline "
                        "from applications")
            self.assertEqual(cur.fetchone(), (None, None, None))

    def test_a_session_is_upserted_not_duplicated(self):
        """Sessions are rewritten on every phase change. 88 files today, each
        saved many times."""
        for status in ("inflight", "review", "completed"):
            class_b.mirror(self.conn, self.account, "session",
                           {"applicationId": "app1", "status": status})
        self.assertEqual(self.count("applications"), 1)
        with self.conn.cursor() as cur:
            cur.execute("select status from applications")
            self.assertEqual(cur.fetchone()[0], "completed")

    def test_a_history_event_becomes_an_application_event(self):
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app1", "status": "inflight"})
        class_b.mirror(self.conn, self.account, "history",
                       {"applicationId": "app1", "event": "submitting",
                        "eventId": "e1", "at": "2026-08-30T00:00:00Z"})
        self.assertEqual(self.count("application_events"), 1)

    def test_the_same_event_twice_is_one_row(self):
        """History is an append-only file that gets re-read and re-mirrored.
        Without the event id as the key, every pass would duplicate it."""
        for _ in range(3):
            class_b.mirror(self.conn, self.account, "session",
                           {"applicationId": "app1", "status": "inflight"})
            class_b.mirror(self.conn, self.account, "history",
                           {"applicationId": "app1", "event": "submitting",
                            "eventId": "e1", "at": "2026-08-30T00:00:00Z"})
        self.assertEqual(self.count("application_events"), 1)

    def test_an_event_for_an_unknown_application_creates_it(self):
        """History can arrive before the session file is written. Dropping the
        event would lose a completion record."""
        class_b.mirror(self.conn, self.account, "history",
                       {"applicationId": "orphan", "event": "completed",
                        "eventId": "e9", "at": "2026-08-30T00:00:00Z"})
        self.assertEqual(self.count("applications"), 1)
        self.assertEqual(self.count("application_events"), 1)

    def test_two_completions_for_one_application_are_refused(self):
        """§3's submit invariant, in the database. The mirror must not be the
        thing that lets a replayed runner record two."""
        class_b.mirror(self.conn, self.account, "history",
                       {"applicationId": "app1", "event": "completed",
                        "eventId": "e1", "at": "2026-08-30T00:00:00Z"})
        with self.assertRaises(Exception):
            class_b.mirror(self.conn, self.account, "history",
                           {"applicationId": "app1", "event": "completed",
                            "eventId": "e2", "at": "2026-08-30T00:01:00Z"})

    def test_answers_are_mirrored_by_key(self):
        class_b.mirror(self.conn, self.account, "answers", {
            "work-auth": {"key": "work-auth", "question": "Authorised?",
                          "value": "Yes", "state": "answered",
                          "tags": ["legal"], "sensitive": False},
        })
        with self.conn.cursor() as cur:
            cur.execute("select key, question, value from answer_bank")
            self.assertEqual(cur.fetchone(),
                             ("work-auth", "Authorised?", "Yes"))

    def test_a_removed_answer_is_removed_from_postgres(self):
        """`answers.json` is written whole. An answer deleted from it must not
        survive in the mirror.

        Eight answers rather than the two this used to seed, and the count is
        load-bearing now. `_mirror_answers` gained `ANSWER_DELETE_CEILING` on
        2026-08-30 after it was found emptying the whole bank on an empty
        payload, and a proportional guard bites hardest on the smallest table:
        on two rows, removing one is 50% and every legitimate single deletion
        trips it. Eight rows makes one removal 12.5%, which is what a person
        deleting one of their 190 real answers actually looks like.

        Do not shrink this back to two. The behaviour under test is unchanged
        and still asserted; only the fixture became representative. A bank
        genuinely small enough for one deletion to cross the ceiling needs
        `force=True`, which is the caller saying it meant it."""
        seeded = {f"k{i}": {"key": f"k{i}", "question": f"{i}?", "value": str(i)}
                  for i in range(8)}
        class_b.mirror(self.conn, self.account, "answers", seeded)
        kept = {k: v for k, v in seeded.items() if k != "k7"}
        class_b.mirror(self.conn, self.account, "answers", kept)
        with self.conn.cursor() as cur:
            cur.execute("select key from answer_bank order by key")
            self.assertEqual([r[0] for r in cur.fetchall()], sorted(kept))

    def test_a_sensitive_answer_keeps_its_flag(self):
        """CLAUDE.md: the flag stays on the record because it is what a
        per-user board masks on and what Class 2 encryption keys on."""
        class_b.mirror(self.conn, self.account, "answers", {
            "pay": {"key": "pay", "question": "Expected pay?",
                    "value": "x", "sensitive": True}})
        with self.conn.cursor() as cur:
            cur.execute("select sensitive from answer_bank where key = 'pay'")
            self.assertTrue(cur.fetchone()[0])

    def test_the_profile_is_one_row_per_account(self):
        for name in ("A", "B"):
            class_b.mirror(self.conn, self.account, "profile",
                           {"firstName": name, "lastName": "X"})
        self.assertEqual(self.count("profiles"), 1)
        with self.conn.cursor() as cur:
            cur.execute("select identity->>'firstName' from profiles")
            self.assertEqual(cur.fetchone()[0], "B")

    def test_the_profile_carries_no_resume_wording(self):
        """`resume-content.json` is class C and travels in the job bundle.
        Mirroring it here would put it in two places with no rule about which
        wins, which is the failure that served one document's wording in place
        of another's for as long as the feature existed."""
        class_b.mirror(self.conn, self.account, "profile",
                       {"firstName": "A", "resumes": [{"id": "r1"}],
                        "writingSamples": ["secret draft"]})
        with self.conn.cursor() as cur:
            cur.execute("select identity, base_resume_text from profiles")
            identity, base = cur.fetchone()
        self.assertIsNone(base)
        self.assertNotIn("writingSamples", identity)

    def test_an_unknown_kind_raises_rather_than_writing_somewhere(self):
        with self.assertRaises(class_b.ClassBError):
            class_b.mirror(self.conn, self.account, "not-a-kind", {})


class OnlyASessionSetsAStatus(pgtest.SchemaCase):
    """The session file is the authority on what state an application is in.

    History events and `roles.json` entries both create application rows and
    neither knows the status. The first version defaulted them to `unknown` and
    `known` and wrote those unconditionally, so backfilling roles after sessions
    overwrote all 88 real statuses and parity opened at 88 divergences. Caught
    by parity rather than by a test, which is the argument for parity covering
    class B at all.
    """

    prefix = "classbstatus"

    def setUp(self):
        super().setUp()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('st@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]

    def status_of(self, legacy_id):
        with self.conn.cursor() as cur:
            cur.execute("select status from applications where legacy_id = %s",
                        (legacy_id,))
            row = cur.fetchone()
        return row[0] if row else None

    def test_roles_do_not_overwrite_a_session_status(self):
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app1", "status": "review"})
        class_b.mirror(self.conn, self.account, "roles", {"app1": {}})
        self.assertEqual(self.status_of("app1"), "review")

    def test_a_history_event_does_not_overwrite_a_session_status(self):
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app1", "status": "completed"})
        class_b.mirror(self.conn, self.account, "history",
                       {"applicationId": "app1", "event": "reviewed",
                        "eventId": "e1"})
        self.assertEqual(self.status_of("app1"), "completed")

    def test_the_order_of_the_backfill_does_not_matter(self):
        """Sessions after roles must give the same answer as roles after
        sessions. Order-dependence here is what the 88 divergences were."""
        class_b.mirror(self.conn, self.account, "roles", {"app2": {}})
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app2", "status": "dropped"})
        self.assertEqual(self.status_of("app2"), "dropped")

    def test_a_row_created_without_a_status_says_unknown(self):
        """Not a guess dressed as a fact: `unknown` is what it is."""
        class_b.mirror(self.conn, self.account, "history",
                       {"applicationId": "app3", "event": "started",
                        "eventId": "e2"})
        self.assertEqual(self.status_of("app3"), "unknown")

    def test_a_later_session_replaces_unknown(self):
        class_b.mirror(self.conn, self.account, "history",
                       {"applicationId": "app3", "event": "started",
                        "eventId": "e2"})
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app3", "status": "inflight"})
        self.assertEqual(self.status_of("app3"), "inflight")

    def test_a_session_with_a_blank_status_does_not_erase_one(self):
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app4", "status": "review"})
        class_b.mirror(self.conn, self.account, "session",
                       {"applicationId": "app4", "status": "  "})
        self.assertEqual(self.status_of("app4"), "review")
