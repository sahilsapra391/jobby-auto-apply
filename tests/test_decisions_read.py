"""The device can read its own decisions back, and the mapping is derived.

**`jobs_store` contains zero SELECTs, which is why the file store cannot be
deleted.** Every decision the device has ever made is mirrored into
`job_decisions` and nothing has read one back. Measured 2026-09-03: an absent
pool reads as empty, the next sweep repopulates from the connectors, and 16
watched, 100 applied and 11 passed vanish with no error anywhere.

This is the first Phase 4 module and it changes no authority. The file still
decides; this proves the read agrees with it.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "scripts"))

import backfill  # noqa: E402
import decisions  # noqa: E402


class Cursor:
    def __init__(self, rows): self.rows = rows
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, args=None): self.sql = sql
    def fetchall(self): return list(self.rows)
    def fetchone(self): return self.rows[0] if self.rows else (0,)


class Conn:
    def __init__(self, rows): self.rows = rows
    def cursor(self): return Cursor(self.rows)


class MappingTests(unittest.TestCase):
    def test_it_is_inverted_from_the_mirror_rather_than_restated(self):
        """A second copy of a field map is the shape this repo has paid for
        five times. A state added to `STATE_MAP` has to arrive here on its
        own."""
        expected = {decision: legacy
                    for legacy, (_c, decision) in backfill.STATE_MAP.items()
                    if decision}
        self.assertEqual(decisions.legacy_state(), expected)
        self.assertTrue(expected, "STATE_MAP yielded no decisions; vacuous")

    def test_the_two_directions_are_inverses(self):
        forward, back = decisions.decision_state(), decisions.legacy_state()
        for legacy, decision in forward.items():
            self.assertEqual(back[decision], legacy)
        self.assertEqual(len(forward), len(back),
                         "two pool states map to one decision, so the reverse "
                         "read cannot be unambiguous")

    def test_only_the_account_scoped_states_are_decisions(self):
        """`new` is the absence of a decision and `expired` is a fact about the
        posting, mirrored as `postings.closed_at`. Two accounts share the
        second and disagree about the first, which is why one table is
        account-scoped and the other is not."""
        mapped = set(decisions.decision_state())
        self.assertNotIn("new", mapped)
        self.assertNotIn("expired", mapped)
        self.assertEqual(mapped, {"watched", "passed", "applied"})

    def test_every_state_the_constraint_allows_can_be_read(self):
        """The migration's own check constraint is the authority on what the
        column can hold. A value it allows and this reader cannot map is a
        decision that would read as no decision."""
        sql = (REPO / "packages" / "schema" / "migrations"
               / "0002_corpus_and_decisions.sql").read_text(encoding="utf-8")
        block = sql[sql.index("job_decisions_state_is_known"):]
        block = block[:block.index(")")]
        allowed = {piece.strip().strip("',\n ")
                   for piece in block.split("'") if piece.strip(", \n()")}
        # `isalpha()` here silently dropped `needs_you`, which is the exact
        # failure this case exists to catch: a state the constraint allows,
        # invisible to the check that is supposed to enumerate them.
        allowed = {a for a in allowed
                   if a and a.replace("_", "").isalpha()
                   and a not in ("state", "in", "check")}
        self.assertTrue(allowed, "no states parsed out of the constraint")
        unreadable = sorted(allowed - set(decisions.legacy_state()))
        # `queued`, `applying` and `needs_you` are legal and nothing writes
        # them; v2_lanes.py says why. They are named rather than assumed.
        self.assertEqual(
            unreadable, ["applying", "needs_you", "queued"],
            f"the constraint allows {unreadable} and the reader maps none of "
            f"them; either the mirror can now write one, or this list moved")


class ReadTests(unittest.TestCase):
    def test_it_keys_on_the_pool_id_the_device_actually_holds(self):
        conn = Conn([("abc123", "submitted"), ("def456", "watching")])
        self.assertEqual(decisions.for_account(conn, "acct"),
                         {"abc123": "applied", "def456": "watched"})

    def test_an_unmappable_state_raises_rather_than_reading_as_no_decision(self):
        """The caller is about to treat an absent key as "no decision", which
        is the one reading that is always wrong. Silence here would make a
        schema change look like a user who had decided nothing."""
        conn = Conn([("abc123", "queued")])
        with self.assertRaises(ValueError) as caught:
            decisions.for_account(conn, "acct")
        self.assertIn("queued", str(caught.exception))
        self.assertIn("STATE_MAP", str(caught.exception))

    def test_a_decision_about_a_deleted_posting_is_counted_not_guessed(self):
        # The join is what makes the key resolvable, so an unjoinable decision
        # cannot be returned. "None matched" and "none exist" stay different.
        self.assertEqual(decisions.orphans(Conn([(3,)]), "acct"), 3)

    def test_from_pool_takes_the_three_decision_states_and_no_others(self):
        pool = {"a": {"state": "applied"}, "b": {"state": "new"},
                "c": {"state": "expired"}, "d": {"state": "watched"},
                "e": {}, "f": {"state": "passed"}}
        self.assertEqual(decisions.from_pool(pool),
                         {"a": "applied", "d": "watched", "f": "passed"})


class ComparisonTests(unittest.TestCase):
    def test_it_reports_the_sets_and_never_only_their_sizes(self):
        """Counts agreeing is the trap. Six Found filters each moved the count
        and one moved the rows."""
        out = decisions.compare({"a": "applied", "b": "watched"},
                                {"a": "applied", "c": "watched"})
        self.assertEqual(out["onlyOnDevice"], ["b"])
        self.assertEqual(out["onlyInPostgres"], ["c"])
        self.assertEqual(out["agreed"], 1)

    def test_a_state_disagreement_is_reported_with_both_values(self):
        out = decisions.compare({"a": "applied"}, {"a": "passed"})
        self.assertEqual(out["disagree"],
                         [{"id": "a", "device": "applied", "postgres": "passed"}])
        self.assertEqual(out["agreed"], 0)

    def test_two_sets_of_equal_size_that_share_nothing_are_not_agreement(self):
        out = decisions.compare({"a": "applied"}, {"b": "applied"})
        self.assertEqual(out["agreed"], 0)
        self.assertEqual(len(out["onlyOnDevice"]), 1)
        self.assertEqual(len(out["onlyInPostgres"]), 1)


class AppliedIndexTests(unittest.TestCase):
    """The index `ingest.already_decided` reads, built from Postgres.

    **This consumer goes first because its failure is the only visible one on
    the switching list.** `already_decided` returning nothing for a passed
    posting re-offers a role the user dropped, on the board, where they see it.
    Every other reader fails silently.
    """

    class Cur:
        def __init__(self, answers): self.answers = answers; self.rows = []
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, args=None):
            self.rows = (self.answers["events"] if "application_events" in sql
                         else self.answers["sessions"])
        def fetchall(self): return list(self.rows)

    class Conn:
        def __init__(self, answers): self.answers = answers
        def cursor(self): return AppliedIndexTests.Cur(self.answers)

    def index(self, events=(), sessions=()):
        conn = self.Conn({"events": list(events), "sessions": list(sessions)})
        return decisions.applied_index(conn, "acct")

    def test_a_completed_event_is_applied(self):
        out = self.index(events=[("a1", "completed", "Acme", "Product Manager",
                                  "https://boards.greenhouse.io/acme/jobs/1")])
        self.assertIn("applied", set(out.values()))

    def test_any_other_latest_event_is_not_an_application(self):
        """The file version keeps the last event by `at` and only `completed`
        counts. A `completed` later abandoned is not an application."""
        for kind in ("abandoned", "submitting", "needs_you"):
            out = self.index(events=[("a1", kind, "Acme", "PM", "https://x/1")])
            self.assertEqual(out, {}, f"{kind} was read as an application")

    def test_a_dropped_session_is_passed(self):
        out = self.index(sessions=[("dropped", "Acme", "Product Manager", None)])
        self.assertEqual(set(out.values()), {"passed"})

    def test_the_session_mapping_matches_ingest_s_own(self):
        """Two spellings of one mapping is the class this repo has five
        instances of, so the two are compared rather than both trusted."""
        import inspect
        import ingest
        source = inspect.getsource(ingest.applied_index)
        block = source[source.index("status_state = {"):]
        block = block[:block.index("}") + 1]
        theirs = eval(block.split("=", 1)[1].strip())      # noqa: S307
        self.assertEqual(decisions.SESSION_STATE, theirs)

    def test_a_status_neither_map_knows_is_not_a_decision(self):
        out = self.index(sessions=[("archived", "Acme", "PM", None)])
        self.assertEqual(out, {})

    def test_the_first_writer_of_a_key_wins_like_setdefault(self):
        # History runs before sessions, so a completed application outranks
        # whatever its session last said.
        out = self.index(events=[("a1", "completed", "Acme", "PM", None)],
                         sessions=[("dropped", "Acme", "PM", None)])
        self.assertEqual(set(out.values()), {"applied"})


class ShadowReadTests(unittest.TestCase):
    def test_a_disagreement_names_its_direction(self):
        """The direction matters more than the count. A key only the files
        hold is a posting Postgres would re-offer, which is the visible
        failure; only Postgres holds is one it would wrongly refuse."""
        out = decisions.index_disagreements(
            {("acme", "pm"): "passed", ("b", "c"): "applied"},
            {("acme", "pm"): "watched", ("d", "e"): "applied"})
        self.assertEqual(out["onlyInFiles"], ["b|c"])
        self.assertEqual(out["onlyInPostgres"], ["d|e"])
        self.assertEqual(out["differ"],
                         [{"key": "acme|pm", "files": "passed",
                           "postgres": "watched"}])
        self.assertEqual(out["agreed"], 0)

    def test_a_url_key_is_shown_as_one(self):
        out = decisions.index_disagreements({("url", "x/1"): "passed"}, {})
        self.assertEqual(out["onlyInFiles"], ["url:x/1"])

    def test_an_unreachable_database_is_not_recorded_as_agreement(self):
        """"Could not look" and "they agree" are different facts and only one
        of them is evidence a reader can be switched."""
        import sys as _sys
        import tempfile
        from unittest import mock
        root = tempfile.mkdtemp()
        # `jobs_db.connect` rather than `applied_index`, because the first
        # version patched the query and asserted its message -- and under a
        # redirected HOME the *connect* fails first with a different one, so
        # the case passed alone and failed in a full run. The property is that
        # any failure in the read path is recorded as unreadable; which line
        # raised is an arrangement.
        import jobs_db
        with mock.patch.object(jobs_db, "connect",
                               side_effect=OSError("no route to host")):
            entry = decisions.shadow_applied_index(root, {("a", "b"): "passed"})
        self.assertIsNone(entry["clean"])
        self.assertTrue(entry.get("unreadable"))
        self.assertNotIn("agreed", entry,
                         "an unreachable database was recorded as agreement")

    def test_any_failure_in_the_read_path_is_unreadable_not_agreement(self):
        """The property, without naming which line raises."""
        import tempfile
        from unittest import mock
        for target, boom in (("applied_index", ValueError("bad state")),):
            with mock.patch.object(decisions, target, side_effect=boom):
                entry = decisions.shadow_applied_index(
                    tempfile.mkdtemp(), {("a", "b"): "passed"})
            self.assertIsNone(entry["clean"])
            self.assertNotIn("agreed", entry)

    def test_the_helper_never_raises_whatever_the_reader_does(self):
        """`decisions_shadow` sits in `ingest` and is called from two scopes.
        The first version attached its result to `report` in `add_postings`,
        where `report` is assigned twenty lines later -- and the handler
        referenced it too, so the guard raised its own NameError and took six
        tests with it. Same shape as `root=root` on a function with no `root`.
        """
        import ingest
        from unittest import mock
        with mock.patch.object(ingest, "decisions_shadow") as spy:
            spy.side_effect = None
        with mock.patch.dict("sys.modules", {"decisions": None}):
            out = ingest.decisions_shadow("/nonexistent", {}, "probe")
        self.assertIsNone(out["clean"])
        self.assertIn("unreadable", out)

    def test_the_sweep_never_reads_the_shadow_result(self):
        """The file is authoritative for the whole window. A shadow read that
        fed `already_decided` would be the switch, not the window."""
        import inspect
        import ingest
        source = inspect.getsource(ingest.run)
        after = source[source.index("shadow_applied_index"):]
        self.assertNotIn("decided = ", after,
                         "the index was reassigned after the shadow read")
        self.assertNotIn("shadow[", after.split("report[")[0])


class WindowTests(unittest.TestCase):
    """The switch is checkable rather than recalled.

    **The window is not a duration.** 404 of 404 agreed on the first read, so
    forty-two more reads of the same 404 keys prove nothing about a reader: the
    check would pass on a subject that never moved, exactly as
    `test_v2_matches_the_board` passed on 99 classes when a real render emits
    164. What a window is for is disagreement under real conditions, and the
    conditions are three different processes writing.
    """

    class Cur:
        def __init__(self, answers): self.answers = list(answers); self.i = -1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, args=None): self.i += 1
        def fetchone(self): return (self.answers[self.i],)

    class Conn:
        def __init__(self, answers): self.answers = answers
        def cursor(self): return WindowTests.Cur(self.answers)

    # Not `run`: that is `TestCase.run`, and shadowing it hands the
    # helper a TestResult instead of the lines.
    def window_for(self, lines, v2=None, device=None):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        log = root / decisions.SHADOW_LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
        return decisions.window(root, self.Conn([v2, device]), "acct")

    def clean(self, at, digest, note="run"):
        # `note` defaults to a production one: a read with no note is a hand
        # probe and the window ignores it, which is what the two cases at the
        # end of this class are about.
        return {"at": at, "clean": True, "indexDigest": digest, "note": note}

    def test_nothing_is_met_before_anything_has_happened(self):
        out = self.window_for([self.clean("2026-09-08T22:00:00Z", "aaa")])
        self.assertFalse(out["ready"])
        self.assertFalse(any(v["met"] for v in out["writers"].values()))

    def test_a_write_predating_the_window_does_not_count(self):
        """The half that makes this evidence. Without it a write from three
        days before the shadow existed counts, because a clean read trivially
        follows it, and that says nothing about a reader tracking a change."""
        out = self.window_for([self.clean("2026-09-08T22:00:00Z", "aaa")],
                       v2="2026-09-05T10:00:00Z", device="2026-09-05T10:00:00Z")
        self.assertFalse(out["writers"]["v2"]["met"])
        self.assertFalse(out["writers"]["device"]["met"])

    def test_a_write_inside_the_window_with_a_clean_read_after_counts(self):
        out = self.window_for([self.clean("2026-09-08T22:00:00Z", "aaa"),
                        self.clean("2026-09-08T23:00:00Z", "bbb")],
                       v2="2026-09-08T22:30:00Z", device="2026-09-08T22:40:00Z")
        self.assertTrue(out["writers"]["v2"]["met"])
        self.assertTrue(out["writers"]["device"]["met"])

    def test_a_write_with_no_clean_read_after_it_does_not_count(self):
        out = self.window_for([self.clean("2026-09-08T22:00:00Z", "aaa")],
                       v2="2026-09-08T23:00:00Z")
        self.assertFalse(out["writers"]["v2"]["met"])

    def test_identical_reads_are_one_sample_however_many_there_are(self):
        lines = [self.clean(f"2026-09-08T2{i}:00:00Z", "same") for i in range(4)]
        out = self.window_for(lines, v2="2026-09-08T20:30:00Z",
                       device="2026-09-08T20:40:00Z")
        self.assertFalse(out["writers"]["sweep"]["met"],
                         "four reads of an unchanged index counted as movement")
        self.assertFalse(out["ready"])

    def test_one_disagreement_anywhere_keeps_the_window_open(self):
        out = self.window_for([self.clean("2026-09-08T22:00:00Z", "aaa"),
                        {"at": "2026-09-08T22:30:00Z", "clean": False,
                         "note": "run"},
                        self.clean("2026-09-08T23:00:00Z", "bbb")],
                       v2="2026-09-08T22:10:00Z", device="2026-09-08T22:20:00Z")
        self.assertEqual(out["disagreed"], 1)
        self.assertFalse(out["ready"], "a window with a disagreement was ready")

    def test_an_unreadable_read_is_counted_and_is_not_clean(self):
        out = self.window_for([{"at": "2026-09-08T22:00:00Z", "clean": None,
                                "note": "run", "unreadable": "OSError: down"}])
        self.assertEqual(out["unreadable"], 1)
        self.assertEqual(out["clean"], 0)

    def test_all_three_and_no_disagreement_is_ready(self):
        out = self.window_for([self.clean("2026-09-08T22:00:00Z", "aaa"),
                        self.clean("2026-09-08T23:00:00Z", "bbb")],
                       v2="2026-09-08T22:30:00Z", device="2026-09-08T22:40:00Z")
        self.assertTrue(out["ready"])

    def test_the_digest_is_stable_across_processes(self):
        """`hash()` is randomised per process, so a digest built on it would
        report every read as a change and every window as covered."""
        import subprocess
        index = {("acme", "pm"): "passed", ("url", "x/1"): "applied"}
        here = decisions.index_digest(index)
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import decisions; "
             "print(decisions.index_digest("
             "{('acme','pm'):'passed',('url','x/1'):'applied'}))"
             % str(REPO / "scripts")],
            capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), here)

    def test_the_digest_moves_when_a_decision_changes(self):
        a = decisions.index_digest({("acme", "pm"): "passed"})
        b = decisions.index_digest({("acme", "pm"): "watched"})
        self.assertNotEqual(a, b)

    def test_the_three_writers_are_named_with_what_each_needs(self):
        self.assertEqual(set(decisions.WINDOW_WRITERS), {"sweep", "device", "v2"})
        for name, need in decisions.WINDOW_WRITERS.items():
            self.assertGreater(len(need.split()), 4, f"{name} has no description")

    def test_a_hand_probe_does_not_open_the_window(self):
        """Provenance is the property the artifact cannot answer.

        The first version counted a probe run from a worktree and used its
        timestamp as `openedAt`, so the window would have been open since
        before the code was on the device and the three writers judged against
        a clock that started in the wrong place.
        """
        out = self.window_for(
            [{"at": "2026-09-08T22:00:00Z", "clean": True,
              "indexDigest": "aaa", "note": "probe"},
             {"at": "2026-09-09T03:00:00Z", "clean": True,
              "indexDigest": "bbb", "note": "run"}],
            v2="2026-09-08T22:30:00Z", device="2026-09-08T22:40:00Z")
        self.assertEqual(out["openedAt"], "2026-09-09T03:00:00Z")
        self.assertEqual(out["handProbes"], 1)
        self.assertEqual(out["reads"], 1)
        # Both writes now predate the window, so neither is met.
        self.assertFalse(out["writers"]["v2"]["met"])
        self.assertFalse(out["writers"]["device"]["met"])
        self.assertFalse(out["ready"])

    def test_a_probe_cannot_supply_the_movement_either(self):
        out = self.window_for(
            [{"at": "2026-09-09T03:00:00Z", "clean": True,
              "indexDigest": "aaa", "note": "run"},
             {"at": "2026-09-09T03:30:00Z", "clean": True,
              "indexDigest": "zzz", "note": "probe"}])
        self.assertFalse(out["writers"]["sweep"]["met"],
                         "a hand probe supplied the index movement")
