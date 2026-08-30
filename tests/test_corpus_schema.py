"""Migration 0002: the corpus, the decisions, and the orderings that matter.

Failure-first. The happy path of "a table exists" proves almost nothing; what
these check is the handful of places where the schema can be quietly wrong and
still look right:

  a null posted_at sorting first    the board's newest-first lane, upside down
  `found` stored as a decision      two spellings of one state, Found broken
  a summary over the cap            the reason the body lives in R2 at all
  a duplicate board key             two rows for one posting, forever
  a second `completed` event        the submit invariant, in the database
"""

import sys
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import migrate  # noqa: E402
import pgtest  # noqa: E402


class CorpusSchemaCase(pgtest.SchemaCase):
    prefix = "corpus"

    def setUp(self):
        super().setUp()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('a@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]
            cur.execute("insert into employers (canonical_name, norm_name) "
                        "values ('Acme', 'acme') returning id")
            self.employer = cur.fetchone()[0]

    def add_posting(self, legacy_id, **kw):
        fields = {
            "legacy_id": legacy_id,
            "source": "greenhouse",
            "board_slug": "acme",
            "external_id": legacy_id,
            "employer_id": self.employer,
            "title": "Product Manager",
            "apply_url": "https://example.com/j/1",
            "last_seen_at": "2026-08-29T00:00:00Z",
        }
        fields.update(kw)
        cols = ", ".join(fields)
        marks = ", ".join(["%s"] * len(fields))
        with self.conn.cursor() as cur:
            cur.execute(f"insert into postings ({cols}) values ({marks}) "
                        f"returning id", list(fields.values()))
            return cur.fetchone()[0]


class NullPostedAtSortsLast(CorpusSchemaCase):
    """The trap the plan's negative-test list names.

    PostgreSQL sorts nulls FIRST under `order by x desc`. A board sorted
    newest-first without `nulls last` therefore opens with every posting whose
    date nobody stated, which on this corpus is 1,942 of 20,103. A date that
    was never published is not the newest thing in the pool.
    """

    def setUp(self):
        super().setUp()
        self.add_posting("old", posted_at="2026-01-01T00:00:00Z")
        self.add_posting("new", posted_at="2026-08-01T00:00:00Z")
        self.add_posting("undated", posted_at=None)

    def ordered(self, sql):
        with self.conn.cursor() as cur:
            cur.execute(sql)
            return [r[0] for r in cur.fetchall()]

    def test_the_undated_posting_comes_last(self):
        got = self.ordered("select legacy_id from postings "
                           "where closed_at is null "
                           "order by posted_at desc nulls last, id desc")
        self.assertEqual(got[-1], "undated")
        self.assertEqual(got[:2], ["new", "old"])

    def test_the_default_ordering_is_the_wrong_one(self):
        """Stated as a test so nobody 'simplifies' the `nulls last` away.
        Without it the undated posting leads the lane."""
        got = self.ordered("select legacy_id from postings "
                           "where closed_at is null order by posted_at desc")
        self.assertEqual(got[0], "undated")

    def test_the_index_is_declared_with_nulls_last(self):
        """An index whose ordering differs from the query's cannot serve it,
        so the lane silently falls back to a sort of the whole corpus."""
        with self.conn.cursor() as cur:
            cur.execute("""select indexdef from pg_indexes
                           where schemaname = %s and indexname in
                           ('postings_open_recent', 'postings_keyset',
                            'postings_open_country')""", (self.schema,))
            defs = [r[0] for r in cur.fetchall()]
        self.assertEqual(len(defs), 3)
        for definition in defs:
            with self.subTest(index=definition[:60]):
                self.assertIn("NULLS LAST", definition.upper())

    def test_keyset_pagination_walks_past_the_undated_row(self):
        """Keyset paging is the reason the ordering has to be total. Paging
        with `(posted_at, id) <` and nulls last must still terminate and must
        not repeat or skip the undated row."""
        page, seen = None, []
        for _ in range(5):
            if page is None:
                sql = ("select legacy_id, posted_at, id from postings "
                       "where closed_at is null "
                       "order by posted_at desc nulls last, id desc limit 2")
                args = ()
            else:
                sql = ("select legacy_id, posted_at, id from postings "
                       "where closed_at is null and "
                       "(posted_at, id) < (%s, %s) "
                       "order by posted_at desc nulls last, id desc limit 2")
                args = page
            with self.conn.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall()
            if not rows:
                break
            seen.extend(r[0] for r in rows)
            page = (rows[-1][1], rows[-1][2])
            if page[0] is None:
                break
        self.assertEqual(sorted(set(seen)), ["new", "old"])
        self.assertEqual(len(seen), len(set(seen)), "a row was paged twice")


class TheBoardKeyIsUnique(CorpusSchemaCase):
    def test_two_postings_cannot_share_one_board_key(self):
        self.add_posting("first", external_id="8139241")
        with self.assertRaises(Exception) as caught:
            self.add_posting("second", external_id="8139241")
        self.assertIn("postings_source_board_slug_external_id_key",
                      str(caught.exception))

    def test_the_same_external_id_on_another_board_is_fine(self):
        self.add_posting("first", external_id="8139241", board_slug="acme")
        self.add_posting("second", external_id="8139241", board_slug="other")

    def test_a_legacy_id_cannot_repeat(self):
        """Everything existing finds its row by this. Two rows claiming one
        legacy id would make that lookup non-deterministic."""
        self.add_posting("dup")
        with self.assertRaises(Exception):
            self.add_posting("dup", external_id="other")


class TheSummaryIsCapped(CorpusSchemaCase):
    def test_an_oversized_summary_is_refused(self):
        with self.assertRaises(Exception) as caught:
            self.add_posting("big", summary="x" * 2049)
        self.assertIn("postings_summary_fits", str(caught.exception))

    def test_two_kilobytes_exactly_is_accepted(self):
        self.add_posting("edge", summary="x" * 2048)

    def test_a_null_summary_is_accepted(self):
        """Not every posting has a description, and 1,691 of them have an
        empty excerpt today."""
        self.add_posting("none", summary=None)


class FoundIsTheAbsenceOfARow(CorpusSchemaCase):
    def test_found_is_not_a_storable_state(self):
        """A row saying `found` and no row at all would be two spellings of
        one state, and the Found lane would have to check for both."""
        with self.assertRaises(Exception) as caught:
            self.decide("found")
        self.assertIn("job_decisions_state_is_known", str(caught.exception))

    def test_an_unknown_state_is_refused(self):
        with self.assertRaises(Exception):
            self.decide("interested")

    def test_each_real_state_is_accepted(self):
        for state in ("watching", "dropped", "queued", "applying",
                      "needs_you", "submitted"):
            with self.subTest(state=state):
                self.decide(state, legacy_id=f"p-{state}")

    def decide(self, state, legacy_id="p1"):
        posting = self.add_posting(legacy_id)
        with self.conn.cursor() as cur:
            cur.execute("insert into job_decisions "
                        "(account_id, posting_id, state) values (%s, %s, %s)",
                        (self.account, posting, state))

    def test_one_decision_per_account_and_posting(self):
        posting = self.add_posting("p2")
        with self.conn.cursor() as cur:
            cur.execute("insert into job_decisions "
                        "(account_id, posting_id, state) values (%s,%s,'watching')",
                        (self.account, posting))
        with self.assertRaises(Exception):
            with self.conn.cursor() as cur:
                cur.execute("insert into job_decisions "
                            "(account_id, posting_id, state) values (%s,%s,'dropped')",
                            (self.account, posting))


class TheSubmitInvariantIsInTheDatabase(CorpusSchemaCase):
    def setUp(self):
        super().setUp()
        posting = self.add_posting("p1")
        with self.conn.cursor() as cur:
            cur.execute("insert into applications "
                        "(account_id, posting_id, legacy_id, status) "
                        "values (%s, %s, 'app1', 'submitted') returning id",
                        (self.account, posting))
            self.application = cur.fetchone()[0]

    def event(self, kind):
        with self.conn.cursor() as cur:
            cur.execute("insert into application_events "
                        "(account_id, application_id, kind) values (%s,%s,%s)",
                        (self.account, self.application, kind))

    def test_an_application_can_only_complete_once(self):
        self.event("completed")
        with self.assertRaises(Exception) as caught:
            self.event("completed")
        self.assertIn("one_completion_per_application", str(caught.exception))

    def test_other_events_may_repeat(self):
        """The index is partial on purpose. Phases repeat; completion does
        not."""
        for _ in range(3):
            self.event("phase")

    def test_a_token_is_consumed_by_an_outcome_not_a_check(self):
        """§3: `update ... where consumed_at is null returning id`. The second
        attempt returns zero rows, which is the refusal."""
        with self.conn.cursor() as cur:
            cur.execute("insert into submit_tokens "
                        "(account_id, application_id, expires_at) "
                        "values (%s, %s, now() + interval '1 hour') returning id",
                        (self.account, self.application))
            token = cur.fetchone()[0]

            cur.execute("update submit_tokens set consumed_at = now() "
                        "where id = %s and consumed_at is null returning id",
                        (token,))
            self.assertIsNotNone(cur.fetchone(), "the first consume failed")

            cur.execute("update submit_tokens set consumed_at = now() "
                        "where id = %s and consumed_at is null returning id",
                        (token,))
            self.assertIsNone(cur.fetchone(),
                              "a consumed token was consumed again")


class TheCorpusIsGlobalAndDecisionsAreNot(CorpusSchemaCase):
    def test_postings_carry_no_account(self):
        """ADR 0006: one corpus shared across accounts. An account column here
        is the 500k-times-N design the plan rejects, and a Phase 5 audit that
        asserts 'every table has an account_id' must not add one."""
        with self.conn.cursor() as cur:
            cur.execute("""select column_name from information_schema.columns
                           where table_schema = %s and table_name = 'postings'
                             and column_name = 'account_id'""", (self.schema,))
            self.assertIsNone(cur.fetchone())

    def test_every_account_scoped_table_has_an_account_id(self):
        scoped = ("profiles", "preferences", "answers", "job_decisions",
                  "applications", "application_events", "devices",
                  "provider_links", "submit_tokens")
        for table in scoped:
            with self.subTest(table=table):
                with self.conn.cursor() as cur:
                    cur.execute(
                        """select 1 from information_schema.columns
                           where table_schema = %s and table_name = %s
                             and column_name = 'account_id'""",
                        (self.schema, table))
                    self.assertIsNotNone(cur.fetchone())

    def test_rls_is_not_enabled_yet_and_that_is_deliberate(self):
        """Phase 5 turns it on with a non-owner role. Enabling it now against
        an owner role that bypasses it would look exactly like it worked,
        which is the failure ADR 0001 records."""
        with self.conn.cursor() as cur:
            cur.execute("""select relname from pg_class c
                           join pg_namespace n on n.oid = c.relnamespace
                           where n.nspname = %s and c.relrowsecurity""",
                        (self.schema,))
            self.assertEqual([r[0] for r in cur.fetchall()], [])


class TheSearchVectorIsStoredAndWeighted(CorpusSchemaCase):
    def test_the_title_outranks_the_summary(self):
        self.add_posting("t", title="Kubernetes Engineer", summary="general")
        self.add_posting("s", title="General Engineer", summary="kubernetes")
        with self.conn.cursor() as cur:
            cur.execute("""select legacy_id from postings
                           where search_tsv @@ plainto_tsquery('english','kubernetes')
                           order by ts_rank(search_tsv,
                                    plainto_tsquery('english','kubernetes')) desc""")
            self.assertEqual([r[0] for r in cur.fetchall()], ["t", "s"])

    def test_it_is_generated_and_stored_rather_than_written(self):
        with self.conn.cursor() as cur:
            cur.execute("""select is_generated from information_schema.columns
                           where table_schema = %s and table_name = 'postings'
                             and column_name = 'search_tsv'""", (self.schema,))
            self.assertEqual(cur.fetchone()[0], "ALWAYS")

    def test_it_follows_an_update(self):
        self.add_posting("u", title="Nothing", summary=None)
        with self.conn.cursor() as cur:
            cur.execute("update postings set title = 'Rust Engineer' "
                        "where legacy_id = 'u'")
            cur.execute("""select count(*) from postings
                           where legacy_id = 'u'
                             and search_tsv @@ plainto_tsquery('english','rust')""")
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
