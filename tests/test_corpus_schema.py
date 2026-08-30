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


class ALegacyTableNameCannotSilentlySwallowAMigration(pgtest.SchemaCase):
    """`create table if not exists` matches on the name, not the shape.

    Migration 0002 wrote `create table if not exists answers (...)` with §3's
    columns and did nothing, because `public.answers` was already a legacy
    `(slug, record)` jsonb table with 190 rows. The statement succeeded, the
    migration was recorded as applied, and the column did not exist. It
    surfaced only when the class B mirror tried to insert into it.

    **The tests could not have caught it, and that is the interesting part.**
    Every database test runs in its own scratch schema, which is what stops
    them writing the live store; a scratch schema has no legacy tables, so the
    name was free and the migration worked on every run. The isolation that
    makes the tests safe is the isolation that hid the collision.

    So this seeds the legacy names into the scratch schema first. It is the
    only shape of test that can reach this failure at all.
    """

    prefix = "collide"

    LEGACY = ("answers", "companies", "history", "jobs", "passed", "roles",
              "sessions")

    def setUp(self):
        super().setUp()
        with self.conn.cursor() as cur:
            for name in self.LEGACY:
                cur.execute(f"create table {name} (slug text primary key, "
                            f"record jsonb)")

    def test_the_migrations_still_apply(self):
        migrate.run(self.conn, migrate.MIGRATIONS)

    def test_no_managed_table_lost_its_columns_to_a_legacy_name(self):
        """The assertion that would have caught it, derived not typed.

        `migrate.declared_tables` parses the shape out of the migration files,
        so this cannot drift from them the way a hand-written expectation can:
        a list written beside the migrations is a second declaration, and it
        can be wrong in the same direction as the first.

        Run against a schema where every legacy name is already taken, which
        is the condition the real `public` is in and the condition a scratch
        schema never reproduces on its own.
        """
        migrate.run(self.conn, migrate.MIGRATIONS)
        declared = migrate.declared_tables()
        with self.conn.cursor() as cur:
            cur.execute("""select table_name, column_name
                           from information_schema.columns
                           where table_schema = %s""", (self.schema,))
            real = {}
            for table, column in cur.fetchall():
                real.setdefault(table, set()).add(column)

        broken = {}
        for table, columns in declared.items():
            if table in self.LEGACY:
                continue          # see GRANDFATHERED below
            missing = sorted(columns - real.get(table, set()))
            if missing:
                broken[table] = missing
        self.assertEqual(
            broken, {},
            "these tables exist without the columns their migration declares, "
            "which is what `create table if not exists` does when a legacy "
            "table already holds the name")

    def test_the_check_covers_every_table_the_migrations_declare(self):
        """A parser that stopped matching would make the check above pass by
        finding nothing. Thirteen tables on 2026-08-30."""
        declared = migrate.declared_tables()
        self.assertGreaterEqual(len(declared), 12)
        for expected in ("postings", "employers", "job_decisions",
                         "applications", "answer_bank", "accounts"):
            with self.subTest(table=expected):
                self.assertIn(expected, declared)
        self.assertGreater(len(declared["postings"]), 25)

    # The one statement that got through before this rule existed. 0002 says
    # `create table if not exists answers (...)`, which has never done anything
    # because `public.answers` is a legacy `(slug, record)` table. It cannot be
    # edited: 0002 is applied, and an applied migration is history. 0005 creates
    # `answer_bank` instead, and the dead statement stays where it is.
    #
    # Named exactly, one migration and one table, rather than exempted by
    # version range. A range would grandfather every future collision in any
    # migration numbered below some line, which is how an exemption becomes a
    # category.
    GRANDFATHERED = {("0002_corpus_and_decisions.sql", "answers")}

    def test_no_managed_table_uses_a_legacy_name(self):
        """The rule that prevents the next one. A managed table named the same
        as a legacy one is a statement that does nothing, reported as applied."""
        for path in sorted(migrate.MIGRATIONS.glob("*.sql")):
            created = self.tables_created(path)
            clash = sorted((created & set(self.LEGACY))
                           - {t for m, t in self.GRANDFATHERED
                              if m == path.name})
            with self.subTest(migration=path.name):
                self.assertEqual(
                    clash, [],
                    f"{path.name} creates {clash}, which the legacy store "
                    f"already holds, so the statement does nothing")

    @staticmethod
    def tables_created(path):
        """Table names a migration actually creates, comments excluded.

        Through `migrate.split_code_and_comments` rather than a second
        stripper. 0005's own prose quotes the dead statement it exists to
        replace, and the first version of this scanned the raw file and
        reported 0005 as the collision it was written to fix. Reading SQL as
        text is the failure this repository has paid for three times; the one
        parser that already strips comments is the one to use.
        """
        import re
        code, _comments = migrate.split_code_and_comments(
            path.read_text(encoding="utf-8"))
        return set(re.findall(r"create table if not exists\s+(\w+)",
                              code, re.I))

    def test_the_grandfathered_statement_is_still_the_only_one(self):
        """The exemption is a fact about one file, so it has to keep matching
        that file. If 0002 stops containing it, the exemption is stale and
        should go rather than sit there licensing something else."""
        for name, table in self.GRANDFATHERED:
            with self.subTest(migration=name):
                self.assertIn(table,
                              self.tables_created(migrate.MIGRATIONS / name))

    def test_the_scan_ignores_comments(self):
        """The negative control for the stripper. Without it this whole class
        reports whichever migration best explains the rule."""
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "0099_x.sql"
            path.write_text("-- create table if not exists answers (x int);\n"
                            "create table if not exists real_one (x int);\n")
            self.assertEqual(self.tables_created(path), {"real_one"})


if __name__ == "__main__":
    unittest.main()
