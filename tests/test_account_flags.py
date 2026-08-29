"""Per-account feature flags, and the two ways a flag lies.

A flag gates a cutover: Phase 1 flips reads to Postgres for one account before
all, Phase 2 does the same for the live board. So a flag has exactly one job,
which is to be right about whether it is on, and there are two ways to get that
wrong that no test of the happy path can see.

**A typo reads as off, forever.** `enabled(flags, "postgres_read")` when the
flag is `postgres_reads` returns False from any dict-shaped implementation. The
cutover is switched on, nothing happens, and the flag says it is off because it
is off -- there is no such flag. So an unregistered name raises here rather
than answering. That is the difference between "not enabled" and "not a thing",
and collapsing them is how a Phase 1 cutover gets debugged for a day.

**A non-boolean is true in one language and false in another.** `"false"` is a
truthy string in JavaScript and in Python, and these flags will be read from
both a Python runner and a TypeScript route handler. A flags column that can
hold `"false"` is a flags column that is on in the browser and off on the
device. The database refuses it, which is better than every reader having to
remember to.
"""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))
sys.path.insert(0, str(REPO / "tests"))

import flags  # noqa: E402
import migrate  # noqa: E402
import pgtest  # noqa: E402


class TheRegistryIsTheSpelling(unittest.TestCase):
    def test_an_unregistered_name_raises_rather_than_reading_false(self):
        with self.assertRaises(flags.UnknownFlag) as caught:
            flags.enabled({}, "postgres_read")
        self.assertIn("postgres_read", str(caught.exception))

    def test_the_error_names_the_nearest_registered_flag(self):
        """A refusal that does not say what was meant is a refusal somebody
        works around by adding the misspelling to the registry."""
        known = sorted(flags.REGISTRY)
        if not known:
            self.skipTest("no flags registered yet")
        typo = known[0] + "s"
        with self.assertRaises(flags.UnknownFlag) as caught:
            flags.enabled({}, typo)
        self.assertIn(known[0], str(caught.exception))

    def test_every_registered_flag_documents_what_it_gates(self):
        for name, entry in flags.REGISTRY.items():
            with self.subTest(flag=name):
                self.assertTrue(entry.get("description"),
                                f"{name} has no description")
                self.assertTrue(entry.get("phase"),
                                f"{name} does not say which phase it gates")

    def test_flag_names_are_lowercase_snake_case(self):
        """One spelling convention, because a flag read as `postgresReads` in
        TypeScript and `postgres_reads` in Python is two flags."""
        import re
        for name in flags.REGISTRY:
            with self.subTest(flag=name):
                self.assertRegex(name, r"^[a-z][a-z0-9_]*$")


class AbsentMeansOff(unittest.TestCase):
    def test_a_registered_flag_absent_from_the_account_is_off(self):
        for name in flags.REGISTRY:
            with self.subTest(flag=name):
                self.assertFalse(flags.enabled({}, name))

    def test_none_is_treated_as_no_flags_rather_than_crashing(self):
        """A row read before the column was backfilled, or a mock that
        returned nothing. Off is the safe reading; a traceback is not."""
        for name in flags.REGISTRY:
            with self.subTest(flag=name):
                self.assertFalse(flags.enabled(None, name))

    def test_a_flag_set_true_is_on(self):
        for name in flags.REGISTRY:
            with self.subTest(flag=name):
                self.assertTrue(flags.enabled({name: True}, name))

    def test_a_flag_set_false_is_off(self):
        for name in flags.REGISTRY:
            with self.subTest(flag=name):
                self.assertFalse(flags.enabled({name: False}, name))

    def test_another_accounts_flag_does_not_leak_in(self):
        name = sorted(flags.REGISTRY)[0]
        other = sorted(flags.REGISTRY)[-1]
        if name == other:
            self.skipTest("needs two registered flags")
        self.assertFalse(flags.enabled({other: True}, name))


class NonBooleansAreRefused(unittest.TestCase):
    """The reader's half of the rule the database also enforces."""

    def test_a_string_false_is_not_read_as_true(self):
        name = sorted(flags.REGISTRY)[0]
        with self.assertRaises(flags.BadFlagValue) as caught:
            flags.enabled({name: "false"}, name)
        self.assertIn(name, str(caught.exception))

    def test_a_truthy_string_is_refused_too(self):
        """Refusing only `"false"` would be a special case for the one that
        embarrasses us, leaving `"true"` to mean different things later."""
        name = sorted(flags.REGISTRY)[0]
        for value in ("true", "1", "yes", 1, 0, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(flags.BadFlagValue):
                    flags.enabled({name: value}, name)

    def test_null_is_refused_rather_than_read_as_off(self):
        """An explicit null is somebody having written something. Reading it
        as off hides which of the two states they meant."""
        name = sorted(flags.REGISTRY)[0]
        with self.assertRaises(flags.BadFlagValue):
            flags.enabled({name: None}, name)


class TheDatabaseEnforcesItToo(pgtest.SchemaCase):
    """A reader that validates is a reader that can be bypassed by the next
    one. The column refuses a bad value at write time, so no reader has to."""

    prefix = "flags_test"

    def setUp(self):
        super().setUp()
        migrate.run(self.conn, migrate.MIGRATIONS)

    def insert(self, email, value):
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email, flags) values (%s, %s)",
                        (email, json.dumps(value)))

    def test_the_scratch_schema_holds_the_work(self):
        self.assert_isolated()

    def test_a_boolean_flag_is_accepted(self):
        self.insert("a@example.com", {"postgres_reads": True})
        with self.conn.cursor() as cur:
            cur.execute("select flags from accounts where email = 'a@example.com'")
            self.assertEqual(cur.fetchone()[0], {"postgres_reads": True})

    def test_a_string_valued_flag_is_refused_by_the_column(self):
        with self.assertRaises(Exception) as caught:
            self.insert("b@example.com", {"postgres_reads": "false"})
        self.assertIn("flags", str(caught.exception).lower())

    def test_a_number_valued_flag_is_refused(self):
        with self.assertRaises(Exception):
            self.insert("c@example.com", {"postgres_reads": 1})

    def test_a_null_valued_flag_is_refused(self):
        with self.assertRaises(Exception):
            self.insert("d@example.com", {"postgres_reads": None})

    def test_flags_may_not_be_an_array_or_a_scalar(self):
        """`not null` is satisfied by `[]` and by `"x"`, and every read of
        either returns nothing, which looks exactly like all flags off.

        The constraint is named in the assertion rather than any exception
        being accepted. Postgres raises for a non-object either way -- if the
        check function is not total, `jsonb_each` throws "cannot call
        jsonb_each on a non-object" from inside the CHECK, and the insert
        fails just the same. A test accepting any exception passed with the
        constraint deleted; this one does not.
        """
        for value in ([], "x", 3):
            with self.subTest(value=value):
                with self.assertRaises(Exception) as caught:
                    self.insert(f"e{value}@example.com", value)
                self.assertIn("accounts_flags_is_a_boolean_object",
                              str(caught.exception))

    def test_the_check_function_answers_rather_than_raising(self):
        """A CHECK whose function throws reports the plumbing, not the rule."""
        with self.conn.cursor() as cur:
            for value, expected in (("{}", True), ('{"a":true}', True),
                                    ('{"a":"x"}', False), ("[]", False),
                                    ('"x"', False), ("3", False),
                                    ("null", False)):
                with self.subTest(value=value):
                    cur.execute("select jobby_flags_are_boolean(%s::jsonb)",
                                (value,))
                    self.assertIs(cur.fetchone()[0], expected)

    def test_an_empty_object_is_the_default(self):
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('f@example.com')")
            cur.execute("select flags from accounts where email = 'f@example.com'")
            self.assertEqual(cur.fetchone()[0], {})

    def test_flags_are_per_account(self):
        self.insert("g@example.com", {"postgres_reads": True})
        self.insert("h@example.com", {})
        with self.conn.cursor() as cur:
            cur.execute("select email, flags from accounts order by email")
            got = dict(cur.fetchall())
        self.assertTrue(flags.enabled(got["g@example.com"], "postgres_reads"))
        self.assertFalse(flags.enabled(got["h@example.com"], "postgres_reads"))

    def test_email_is_unique_case_insensitively(self):
        """citext, so `A@example.com` and `a@example.com` are one account.
        Two accounts for one mailbox is a support ticket nobody can resolve."""
        self.insert("i@example.com", {})
        with self.assertRaises(Exception):
            self.insert("I@Example.com", {})

    def test_for_account_reads_the_stored_flags(self):
        self.insert("j@example.com", {"postgres_reads": True})
        with self.conn.cursor() as cur:
            cur.execute("select id from accounts where email = 'j@example.com'")
            account_id = cur.fetchone()[0]
        self.assertTrue(
            flags.enabled(flags.for_account(self.conn, account_id),
                          "postgres_reads"))

    def test_for_account_on_a_missing_account_is_not_an_empty_dict(self):
        """"No such account" and "an account with no flags on" are different
        facts, and returning {} for both means a caller cannot tell a deleted
        account from a default one."""
        import uuid as _uuid
        with self.assertRaises(flags.NoSuchAccount):
            flags.for_account(self.conn, _uuid.uuid4())


if __name__ == "__main__":
    unittest.main()
