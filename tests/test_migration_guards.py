"""The expand-only guard, and the ways a naive version of it is wrong.

These tests need no database. That is deliberate: the destructive-SQL scanner is
the part of the migration runner most likely to be quietly wrong, and a guard
that only runs when a Postgres container happens to be up is a guard that is
absent on the day it matters.

The shape being defended against is the one CLAUDE.md names three times: testing
category membership with `marker in text`. A scanner that answers "is this
destructive?" with `"drop" in sql` reads correctly and is wrong on the corpus.
`dropped_at` is a column name, `-- we will drop this in N+2` is a comment, and
`insert into audit values ('drop table')` is a string literal. All three contain
the marker and none of them drops anything. The false negatives matter more: a
migration that really does drop a column must never slip through because it was
written `DROP    COLUMN` or `drop\ncolumn`.

So the negative tests come in two directions and both are load-bearing:
over-binding refuses honest migrations until someone weakens the guard, and
under-binding is the release that loses a column.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))

import migrate  # noqa: E402


class StripsWhatIsNotCode(unittest.TestCase):
    """Comments and string literals are text, not statements.

    Every one of these contains a destructive keyword and none of them is a
    destructive statement. A scanner that reads the raw file fails all of them.
    """

    def test_a_line_comment_mentioning_a_drop_is_not_a_drop(self):
        sql = ("-- Release N+2 will drop table employers_old.\n"
               "create table employers_new (id uuid primary key);")
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_block_comment_mentioning_a_drop_is_not_a_drop(self):
        sql = ("/* The contract half of this is a later migration:\n"
               "   drop column jobs.legacy_blob;\n"
               "*/\n"
               "alter table jobs add column summary text;")
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_string_literal_containing_ddl_is_not_ddl(self):
        sql = ("insert into audit_log (action) values ('drop table jobs');")
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_dollar_quoted_body_is_not_scanned_as_statements(self):
        sql = ("create function f() returns void as $$\n"
               "begin\n"
               "  raise notice 'drop table jobs';\n"
               "end;\n"
               "$$ language plpgsql;")
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_tagged_dollar_quote_is_handled(self):
        sql = ("create function f() returns void as $body$\n"
               "  select 'truncate jobs';\n"
               "$body$ language sql;")
        self.assertEqual(migrate.destructive_statements(sql), [])


class DoesNotOverBind(unittest.TestCase):
    """Honest expand migrations must pass. Each of these contains a marker as a
    substring of something else, which is the `"india" in "Indiana"` shape."""

    def test_a_column_named_dropped_at_is_not_a_drop(self):
        sql = "alter table jobs add column dropped_at timestamptz;"
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_column_named_truncated_body_is_not_a_truncate(self):
        sql = "alter table jobs add column truncated_body text;"
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_table_named_dropbox_links_is_not_a_drop(self):
        sql = "create table dropbox_links (id uuid primary key);"
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_drop_inside_an_identifier_is_not_a_drop(self):
        sql = "create index jobs_backdrop on jobs (id);"
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_an_identifier_holding_drop_before_an_object_keyword_is_not_a_drop(self):
        """`drop` as a substring, and `column` standing alone later in the
        same statement. An ordinary two-column add, one of them `dropped_at`.

        This is the case that pins the word boundary rather than merely
        benefiting from it. Every other over-binding test here passes just as
        well against a pattern written `drop.*column`, because none of them
        contains both halves; a mutation run replacing `\\bdrop\\s+` with
        `drop.*` broke nothing until this existed. The first attempt at this
        test used a column named `table_name`, which also failed to catch it,
        because `\\b` does not hold between `table` and `_name`.
        """
        sql = ("alter table job_decisions\n"
               "  add column dropped_at timestamptz,\n"
               "  add column decided_from text;")
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_column_named_renamed_by_is_not_a_rename(self):
        sql = "alter table jobs add column renamed_to text;"
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_a_delete_with_a_where_clause_is_allowed(self):
        """Migrations legitimately fix data. Only the unqualified form empties
        the table."""
        sql = "delete from job_decisions where state = 'bogus';"
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_create_index_and_add_column_are_expand(self):
        sql = ("alter table accounts add column flags jsonb not null "
               "default '{}'::jsonb;\n"
               "create index accounts_email on accounts (email);")
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_drop_default_is_not_destructive(self):
        """Dropping a default changes no stored row. Refusing it would block
        the ordinary expand step of making a backfilled column required."""
        sql = "alter table jobs alter column remote drop default;"
        self.assertEqual(migrate.destructive_statements(sql), [])

    def test_if_not_exists_forms_are_expand(self):
        sql = ("create table if not exists accounts (id uuid primary key);\n"
               "create extension if not exists citext;")
        self.assertEqual(migrate.destructive_statements(sql), [])


class DoesNotUnderBind(unittest.TestCase):
    """Every one of these destroys data or shape and must be caught."""

    def test_drop_table_is_caught(self):
        found = migrate.destructive_statements("drop table jobs;")
        self.assertEqual(len(found), 1)
        self.assertIn("drop table", found[0].lower())

    def test_drop_table_if_exists_is_caught(self):
        self.assertEqual(len(migrate.destructive_statements(
            "drop table if exists jobs cascade;")), 1)

    def test_drop_column_is_caught(self):
        self.assertEqual(len(migrate.destructive_statements(
            "alter table jobs drop column summary;")), 1)

    def test_drop_column_with_irregular_whitespace_is_caught(self):
        """`drop   column` and a newline between the words are the same
        statement. A guard keyed on the literal two-word string misses both."""
        for sql in ("alter table jobs drop    column summary;",
                    "alter table jobs drop\ncolumn summary;",
                    "alter table jobs drop\tcolumn summary;"):
            with self.subTest(sql=sql):
                self.assertEqual(len(migrate.destructive_statements(sql)), 1)

    def test_case_does_not_matter(self):
        for sql in ("DROP TABLE jobs;", "Drop Table jobs;", "dRoP tAbLe jobs;"):
            with self.subTest(sql=sql):
                self.assertEqual(len(migrate.destructive_statements(sql)), 1)

    def test_truncate_is_caught(self):
        self.assertEqual(len(migrate.destructive_statements(
            "truncate table application_events;")), 1)

    def test_truncate_without_the_table_keyword_is_caught(self):
        self.assertEqual(len(migrate.destructive_statements(
            "truncate application_events;")), 1)

    def test_drop_schema_and_database_are_caught(self):
        self.assertEqual(len(migrate.destructive_statements(
            "drop schema public cascade;")), 1)
        self.assertEqual(len(migrate.destructive_statements(
            "drop database neondb;")), 1)

    def test_drop_not_null_is_caught(self):
        """Not data loss, but a shape a reader may depend on. It belongs in a
        contract migration where somebody has to say why."""
        self.assertEqual(len(migrate.destructive_statements(
            "alter table jobs alter column apply_url drop not null;")), 1)

    def test_alter_column_type_is_caught(self):
        """A type change rewrites the column and can truncate silently."""
        self.assertEqual(len(migrate.destructive_statements(
            "alter table jobs alter column summary type varchar(100);")), 1)

    def test_rename_is_caught(self):
        """A rename is a drop and an add to every reader still on the old
        name, which is exactly the skew expand/contract exists to prevent."""
        self.assertEqual(len(migrate.destructive_statements(
            "alter table jobs rename column summary to blurb;")), 1)
        self.assertEqual(len(migrate.destructive_statements(
            "alter table jobs rename to postings;")), 1)

    def test_delete_without_a_where_clause_is_caught(self):
        self.assertEqual(len(migrate.destructive_statements(
            "delete from job_decisions;")), 1)

    def test_drop_constraint_and_index_are_caught(self):
        self.assertEqual(len(migrate.destructive_statements(
            "alter table jobs drop constraint jobs_source_key;")), 1)
        self.assertEqual(len(migrate.destructive_statements(
            "drop index jobs_search;")), 1)

    def test_several_destructive_statements_are_all_reported(self):
        """Reporting the first one and stopping teaches whoever is fixing it
        that there was one problem."""
        sql = ("drop table a;\n"
               "alter table b drop column c;\n"
               "truncate d;")
        self.assertEqual(len(migrate.destructive_statements(sql)), 3)

    def test_a_destructive_statement_after_a_comment_is_still_caught(self):
        """Comment stripping must not swallow the code after it."""
        sql = "-- harmless note\ndrop table jobs;"
        self.assertEqual(len(migrate.destructive_statements(sql)), 1)

    def test_a_destructive_statement_after_a_string_literal_is_caught(self):
        sql = "insert into t values ('ok');\ndrop table jobs;"
        self.assertEqual(len(migrate.destructive_statements(sql)), 1)


class ContractMigrationsAreDeclared(unittest.TestCase):
    """A destructive migration is legal only when it says so in the file and
    the operator says so on the command line. Either alone is not enough."""

    def test_a_plain_migration_with_a_drop_is_refused(self):
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.check_expand_only("0002_x.sql", "drop table jobs;",
                                      allow_contract=False)
        self.assertIn("drop table", str(caught.exception).lower())

    def test_a_declared_contract_is_still_refused_without_the_flag(self):
        sql = "-- jobby:contract release N+2, jobs.legacy_blob\ndrop table jobs;"
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.check_expand_only("0002_contract_x.sql", sql,
                                      allow_contract=False)
        self.assertIn("--allow-contract", str(caught.exception))

    def test_the_flag_alone_does_not_permit_an_undeclared_drop(self):
        """Passing --allow-contract must not turn every migration in the run
        into a licence to drop. The file has to have said so."""
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.check_expand_only("0002_x.sql", "drop table jobs;",
                                      allow_contract=True)
        self.assertIn("jobby:contract", str(caught.exception))

    def test_a_declared_contract_with_the_flag_is_allowed(self):
        sql = "-- jobby:contract release N+2, jobs.legacy_blob\ndrop table jobs;"
        migrate.check_expand_only("0002_contract_x.sql", sql,
                                  allow_contract=True)

    def test_a_contract_declaration_needs_a_reason(self):
        sql = "-- jobby:contract\ndrop table jobs;"
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.check_expand_only("0002_contract_x.sql", sql,
                                      allow_contract=True)
        self.assertIn("reason", str(caught.exception).lower())

    def test_the_declaration_must_be_a_real_comment_not_a_string(self):
        """`values ('-- jobby:contract ...')` is data. If that declared a
        contract, any migration could smuggle one in."""
        sql = ("insert into t values ('-- jobby:contract because');\n"
               "drop table jobs;")
        with self.assertRaises(migrate.MigrationError):
            migrate.check_expand_only("0002_x.sql", sql, allow_contract=True)

    def test_an_expand_migration_passes_with_no_flag(self):
        migrate.check_expand_only(
            "0001_accounts.sql",
            "create table accounts (id uuid primary key);",
            allow_contract=False)


class FileNamesAreOrdered(unittest.TestCase):
    def test_a_well_formed_name_parses(self):
        self.assertEqual(migrate.parse_version("0001_accounts_and_flags.sql"), 1)
        self.assertEqual(migrate.parse_version("0042_x.sql"), 42)

    def test_a_name_without_a_version_is_refused(self):
        for name in ("accounts.sql", "_accounts.sql", "abcd_accounts.sql",
                     "1_accounts.sql", "0001.sql", "0001_accounts.txt"):
            with self.subTest(name=name):
                with self.assertRaises(migrate.MigrationError):
                    migrate.parse_version(name)

    def test_version_zero_is_refused(self):
        """Zero is the value an unset integer takes. Reserving it means a
        parse that silently returns nothing cannot look like migration 0."""
        with self.assertRaises(migrate.MigrationError):
            migrate.parse_version("0000_x.sql")


class DiscoveryRefusesAmbiguity(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, sql="select 1;"):
        (self.dir / name).write_text(sql, encoding="utf-8")

    def test_duplicate_versions_are_refused(self):
        self.write("0001_a.sql")
        self.write("0001_b.sql")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.discover(self.dir)
        self.assertIn("0001", str(caught.exception))

    def test_a_stray_file_is_refused_rather_than_ignored(self):
        """Ignoring what it does not understand is how a migration named
        wrongly never runs and nobody finds out until the column is missing."""
        self.write("0001_a.sql")
        self.write("notes.md", "# scratch")
        with self.assertRaises(migrate.MigrationError) as caught:
            migrate.discover(self.dir)
        self.assertIn("notes.md", str(caught.exception))

    def test_an_empty_migration_is_refused(self):
        self.write("0001_a.sql", "\n\n-- nothing here\n")
        with self.assertRaises(migrate.MigrationError):
            migrate.discover(self.dir)

    def test_discovery_returns_version_order_not_directory_order(self):
        self.write("0010_j.sql")
        self.write("0002_b.sql")
        self.write("0001_a.sql")
        got = [m.version for m in migrate.discover(self.dir)]
        self.assertEqual(got, [1, 2, 10])

    def test_a_missing_directory_is_named_not_treated_as_empty(self):
        """An empty plan and an unreadable directory are the same output from
        a runner that returns [] on error, and they mean opposite things."""
        with self.assertRaises(migrate.MigrationError):
            migrate.discover(self.dir / "does-not-exist")

    def test_checksums_are_content_addressed(self):
        self.write("0001_a.sql", "create table a (id int);")
        first = migrate.discover(self.dir)[0].checksum
        self.write("0001_a.sql", "create table a (id bigint);")
        second = migrate.discover(self.dir)[0].checksum
        self.assertNotEqual(first, second)


class TheShippedMigrationsAreValid(unittest.TestCase):
    """The guards above are worth nothing if the repo's own migrations were
    never run through them."""

    def test_the_repo_migrations_discover_cleanly(self):
        found = migrate.discover(migrate.MIGRATIONS)
        self.assertGreater(len(found), 0, "no migrations found in the repo")

    def test_every_shipped_migration_is_expand_only(self):
        for m in migrate.discover(migrate.MIGRATIONS):
            with self.subTest(migration=m.name):
                migrate.check_expand_only(m.name, m.sql, allow_contract=False)

    def test_versions_are_contiguous_from_one(self):
        """A gap means a migration was deleted or never committed. Both are
        the failure where the database and the repo disagree about history."""
        versions = [m.version for m in migrate.discover(migrate.MIGRATIONS)]
        self.assertEqual(versions, list(range(1, len(versions) + 1)))


class PooledConnectionsAreRefused(unittest.TestCase):
    """Migrations need a session, and a transaction pooler does not give one.

    `pg_advisory_lock` is session-scoped: taken through a pooler it lands on
    whatever backend served that statement, so the next runner takes it on a
    different backend and both proceed. The lock appears to work and excludes
    nobody, which is worse than having no lock, because the failure only
    appears when two deploys overlap.

    Neon's own pooler refuses `options=-c search_path=...` with "Please use
    unpooled connection". This is that advice, applied to the runner.
    """

    POOLED = ("postgresql://u:p@ep-weathered-rice-ayyav4uq-pooler.c-5."
              "us-east-2.aws.neon.tech/neondb?sslmode=require")
    DIRECT = ("postgresql://u:p@ep-weathered-rice-ayyav4uq.c-5."
              "us-east-2.aws.neon.tech/neondb?sslmode=require")

    def test_a_neon_pooler_host_is_recognised(self):
        self.assertTrue(migrate.is_pooled(self.POOLED))

    def test_a_direct_host_is_not(self):
        self.assertFalse(migrate.is_pooled(self.DIRECT))

    def test_a_pgbouncer_flag_is_recognised(self):
        self.assertTrue(migrate.is_pooled(
            "postgresql://u:p@db.example.com/x?pgbouncer=true"))

    def test_the_rewrite_reaches_the_direct_endpoint(self):
        self.assertEqual(migrate.direct_url(self.POOLED), self.DIRECT)

    def test_the_rewrite_is_a_no_op_on_a_direct_url(self):
        self.assertEqual(migrate.direct_url(self.DIRECT), self.DIRECT)

    def test_the_rewrite_keeps_credentials_database_and_options(self):
        """Rewriting the host must not quietly drop the rest. A URL that loses
        its sslmode or its database name fails in a way that reads as a
        network problem."""
        rewritten = migrate.direct_url(self.POOLED)
        self.assertIn("u:p@", rewritten)
        self.assertTrue(rewritten.endswith("/neondb?sslmode=require"))

    def test_the_rewrite_keeps_a_non_default_port(self):
        pooled = "postgresql://u:p@host-pooler.example.com:6543/db"
        self.assertEqual(migrate.direct_url(pooled),
                         "postgresql://u:p@host.example.com:6543/db")

    def test_pooler_inside_a_longer_label_is_left_alone(self):
        """`-pooler` is a suffix of the endpoint label, not a substring to
        hunt for. A host legitimately containing it elsewhere must survive."""
        url = "postgresql://u:p@my-pooler-metrics.example.com/db"
        self.assertFalse(migrate.is_pooled(url))
        self.assertEqual(migrate.direct_url(url), url)

    def test_the_result_of_the_rewrite_is_never_still_pooled(self):
        for url in (self.POOLED, self.DIRECT,
                    "postgresql://u:p@db.example.com/x?pgbouncer=true"):
            with self.subTest(url=url):
                self.assertFalse(migrate.is_pooled(migrate.direct_url(url)))
