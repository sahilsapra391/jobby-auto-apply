"""Dual-write: Postgres first, then the file, and which failure is survivable.

The two negative tests the plan names come first, because both describe a
success that is really a failure.

**A write that fails in Postgres must fail the whole operation.** If the file
write happens anyway, the file holds a decision the cloud never learns about,
and nothing in the system will ever look for it. That is the unrecoverable
direction: no later reconciliation can find a change nobody recorded. The
recoverable direction is the other one, Postgres ahead of the file, because the
backfill is idempotent and parity reports the gap loudly meanwhile.

**A read returning zero rows must be distinguishable from a read that errored.**
`json.loads(stdout or "[]")` is the shape: a subprocess that died and a
subprocess that found nothing produce the same empty list, so an outage reads as
an empty pool and every downstream count is confidently wrong.

Everything else here is about the moment between the commit and the file write,
which is real and is not a fault, and which must never become an excuse to
suppress a divergence.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import backfill  # noqa: E402
import dual_write  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402


class TheDigestSeesEveryChange(unittest.TestCase):
    """The diff is by per-record digest rather than by trusting a caller to
    say what it touched. Measured on the real pool that costs 0.27s against a
    99 MB file write, so the exact answer is cheaper than approximating it."""

    def pool(self):
        return {"a": {"id": "a", "state": "new", "title": "PM"},
                "b": {"id": "b", "state": "new", "title": "SRE"}}

    def test_no_change_is_no_work(self):
        before = dual_write.digest_pool(self.pool())
        added, changed, removed = dual_write.delta(before, self.pool())
        self.assertEqual((added, changed, removed), (set(), set(), set()))

    def test_a_changed_state_is_seen(self):
        before = dual_write.digest_pool(self.pool())
        after = self.pool()
        after["a"]["state"] = "watched"
        _added, changed, _removed = dual_write.delta(before, after)
        self.assertEqual(changed, {"a"})

    def test_a_change_to_any_field_is_seen(self):
        """Not just the fields somebody thought to track. A digest over the
        whole record cannot miss one."""
        for field, value in (("title", "Staff PM"), ("postedAt", "2026-01-01"),
                             ("seenCount", 9), ("tags", ["x"]),
                             ("descFingerprint", "simhash64:1")):
            with self.subTest(field=field):
                before = dual_write.digest_pool(self.pool())
                after = self.pool()
                after["a"][field] = value
                _a, changed, _r = dual_write.delta(before, after)
                self.assertEqual(changed, {"a"})

    def test_key_order_is_not_a_change(self):
        """A record rewritten with its keys in another order is the same
        record. Without sorted keys every save would look like a full rewrite
        and dual-write would push 20,103 rows every time."""
        before = dual_write.digest_pool({"a": {"id": "a", "x": 1, "y": 2}})
        _a, changed, _r = dual_write.delta(before, {"a": {"y": 2, "x": 1, "id": "a"}})
        self.assertEqual(changed, set())

    def test_an_added_record_is_seen(self):
        before = dual_write.digest_pool(self.pool())
        after = self.pool()
        after["c"] = {"id": "c", "state": "new", "title": "Analyst"}
        added, _c, _r = dual_write.delta(before, after)
        self.assertEqual(added, {"c"})

    def test_a_pruned_record_is_seen(self):
        """`prune` deletes. A diff that only looks at what is present would
        leave the row in Postgres forever."""
        before = dual_write.digest_pool(self.pool())
        after = self.pool()
        del after["b"]
        _a, _c, removed = dual_write.delta(before, after)
        self.assertEqual(removed, {"b"})


class AFailedPostgresWriteFailsEverything(pgtest.SchemaCase):
    """The first negative test the plan names."""

    prefix = "dualwrite"

    def setUp(self):
        super().setUp()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('d@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]

    def record(self, job_id, **kw):
        base = {"id": job_id, "canonicalSource": "greenhouse",
                "canonicalUrl": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
                "company": "Acme", "title": "PM", "state": "new",
                "firstSeenAt": "2026-08-01T00:00:00Z",
                "lastSeenAt": "2026-08-29T00:00:00Z", "postedAt": None}
        base.update(kw)
        return base

    def test_a_broken_record_raises_rather_than_writing_half(self):
        """A record Postgres refuses must abort the mirror, so the caller
        never reaches its file write."""
        pool = {"a": self.record("a"), "b": self.record("b", canonicalUrl=None)}
        with self.assertRaises(Exception):
            dual_write.mirror_pool(self.conn, self.account, {}, pool)

    def test_nothing_from_a_failed_mirror_is_left_behind(self):
        """One transaction. A partial mirror would be worse than none: the
        file would be behind on some rows and ahead on others."""
        pool = {"a": self.record("a"), "b": self.record("b", canonicalUrl=None)}
        with self.assertRaises(Exception):
            dual_write.mirror_pool(self.conn, self.account, {}, pool)
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from postings")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_a_failure_in_the_second_batch_rolls_back_the_first(self):
        """The transaction, tested where it actually matters.

        The obvious version of this feeds a record that `posting_row` refuses,
        and that raises in Python before any SQL runs, so nothing is written
        with or without a transaction: a mutation removing `conn.transaction()`
        survived it. What exercises the transaction is a failure *after* rows
        have already gone to the server, which needs more than one statement.
        `batch=1` gives one statement per record, and the second record carries
        a timestamp Postgres rejects.
        """
        pool = {"a": self.record("a"),
                "b": self.record("b", lastSeenAt="not-a-timestamp")}
        with self.assertRaises(Exception):
            dual_write.mirror_pool(self.conn, self.account, {}, pool, batch=1)
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from postings")
            self.assertEqual(cur.fetchone()[0], 0,
                             "the first batch committed before the second "
                             "failed, so the mirror is not atomic")

    def test_a_good_two_batch_mirror_still_lands_both(self):
        """The control: batching itself must not be what fails above."""
        pool = {"a": self.record("a"), "b": self.record("b")}
        dual_write.mirror_pool(self.conn, self.account, {}, pool, batch=1)
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from postings")
            self.assertEqual(cur.fetchone()[0], 2)

    def test_the_file_is_not_written_when_postgres_refuses(self):
        """The property end to end, through the real persist path. The file's
        bytes are the evidence, not a mocked call count."""
        import tempfile
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        target = root / "jobs.json"
        target.write_text(json.dumps({"a": self.record("a")}))
        before = target.read_bytes()

        pool = {"a": self.record("a"), "b": self.record("b", canonicalUrl=None)}
        with self.assertRaises(Exception):
            dual_write.persist(self.conn, self.account, target, {}, pool)
        self.assertEqual(target.read_bytes(), before,
                         "the file moved while Postgres refused")

    def test_a_good_write_reaches_both(self):
        import tempfile
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        target = root / "jobs.json"
        target.write_text("{}")

        pool = {"a": self.record("a")}
        dual_write.persist(self.conn, self.account, target, {}, pool)
        self.assertEqual(set(json.loads(target.read_text())), {"a"})
        with self.conn.cursor() as cur:
            cur.execute("select legacy_id from postings")
            self.assertEqual([r[0] for r in cur.fetchall()], ["a"])

    def test_a_file_failure_after_a_commit_is_loud_and_re_raised(self):
        """The recoverable direction, which still must not be silent. The
        commit stands, the caller is told, and the log names both halves."""
        pool = {"a": self.record("a")}
        unwritable = Path("/proc/definitely/not/writable/jobs.json")
        reported = []
        with self.assertRaises(Exception):
            dual_write.persist(self.conn, self.account, unwritable, {}, pool,
                               on_file_failure=reported.append)
        self.assertTrue(reported, "the file failure was swallowed")
        message = str(reported[0])
        self.assertIn("Postgres", message)
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from postings")
            self.assertEqual(cur.fetchone()[0], 1,
                             "the commit was rolled back by a file failure")

    def test_a_decision_change_reaches_postgres(self):
        pool = {"a": self.record("a")}
        dual_write.mirror_pool(self.conn, self.account, {}, pool)
        before = dual_write.digest_pool(pool)
        pool["a"]["state"] = "watched"
        dual_write.mirror_pool(self.conn, self.account, before, pool)
        with self.conn.cursor() as cur:
            cur.execute("select state from job_decisions")
            self.assertEqual([r[0] for r in cur.fetchall()], ["watching"])

    def test_a_decision_that_goes_back_to_new_removes_the_row(self):
        """The revive path and un-watching both land here. A decision row left
        behind would keep the posting out of Found forever."""
        pool = {"a": self.record("a", state="watched")}
        dual_write.mirror_pool(self.conn, self.account, {}, pool)
        before = dual_write.digest_pool(pool)
        pool["a"]["state"] = "new"
        dual_write.mirror_pool(self.conn, self.account, before, pool)
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from job_decisions")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_a_pruned_record_is_deleted_from_postgres(self):
        pool = {"a": self.record("a"), "b": self.record("b")}
        dual_write.mirror_pool(self.conn, self.account, {}, pool)
        before = dual_write.digest_pool(pool)
        del pool["b"]
        dual_write.mirror_pool(self.conn, self.account, before, pool)
        with self.conn.cursor() as cur:
            cur.execute("select legacy_id from postings")
            self.assertEqual([r[0] for r in cur.fetchall()], ["a"])

    def test_an_unchanged_pool_touches_nothing(self):
        """A Watch pressed twice must not rewrite 20,103 rows."""
        pool = {"a": self.record("a")}
        dual_write.mirror_pool(self.conn, self.account, {}, pool)
        before = dual_write.digest_pool(pool)
        report = dual_write.mirror_pool(self.conn, self.account, before, pool)
        self.assertEqual((report["added"], report["changed"], report["removed"]),
                         (0, 0, 0))


class EmptyIsNotTheSameAsBroken(unittest.TestCase):
    """The second negative test. `json.loads(stdout or "[]")` is the shape."""

    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)

    def test_a_genuinely_empty_pool_reads_as_empty(self):
        path = self.dir / "jobs.json"
        path.write_text("{}")
        self.assertEqual(dual_write.read_pool(path), {})

    def test_a_missing_file_raises_rather_than_reading_empty(self):
        with self.assertRaises(dual_write.DualWriteError):
            dual_write.read_pool(self.dir / "gone.json")

    def test_a_truncated_file_raises_rather_than_reading_empty(self):
        path = self.dir / "jobs.json"
        path.write_text('{"a": {"id": "a"}, "b": {')
        with self.assertRaises(dual_write.DualWriteError):
            dual_write.read_pool(path)

    def test_an_empty_file_raises_rather_than_reading_empty(self):
        """Zero bytes is a truncated write, not an empty pool.

        The message is asserted, not just the exception type. Deleting the
        empty check entirely still raises, because `json.loads("")` fails and
        the next branch catches it, so a mutation removing this branch survived
        a test that only checked the type. What the branch buys is a reader
        being told which of the two happened."""
        path = self.dir / "jobs.json"
        path.write_text("")
        with self.assertRaises(dual_write.DualWriteError) as caught:
            dual_write.read_pool(path)
        self.assertIn("truncated write", str(caught.exception))

    def test_whitespace_only_is_also_a_truncated_write(self):
        path = self.dir / "jobs.json"
        path.write_text("   \n  ")
        with self.assertRaises(dual_write.DualWriteError) as caught:
            dual_write.read_pool(path)
        self.assertIn("truncated write", str(caught.exception))

    def test_the_error_names_the_file_and_the_cause(self):
        path = self.dir / "jobs.json"
        path.write_text("not json")
        with self.assertRaises(dual_write.DualWriteError) as caught:
            dual_write.read_pool(path)
        self.assertIn("jobs.json", str(caught.exception))


class BeingOffIsNotBeingFine(unittest.TestCase):
    """Dual-write with no database configured is a no-op, which is necessary
    on a machine that has none and is dangerous if nothing notices.

    `enabled()` is the one place that decides, and `verify_parity` asserts it,
    so a run that mirrors nothing cannot also report parity green.
    """

    def test_it_is_off_without_a_database(self):
        self.assertFalse(dual_write.enabled(url=None))

    def test_it_is_on_with_one(self):
        self.assertTrue(dual_write.enabled(url="postgresql://u:p@h/db"))

    def test_an_empty_url_is_off_not_on(self):
        for url in ("", "   "):
            with self.subTest(url=url):
                self.assertFalse(dual_write.enabled(url=url))


if __name__ == "__main__":
    unittest.main()
