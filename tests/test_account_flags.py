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

    def test_two_accounts_cannot_share_one_mailbox(self):
        """Two accounts for one mailbox is a support ticket nobody can resolve.

        §3 asks for `citext`. This uses `text` with a lower-case constraint
        instead, because an extension's type lives in one schema and `create
        extension if not exists` is satisfied database-wide, so a migration
        depending on citext succeeds or fails according to where a previous
        deployment happened to install it. The guarantee is the same and the
        refusal is louder: a mixed-case address is rejected on the way in
        rather than silently folded, so the caller has to normalise.
        """
        self.insert("i@example.com", {})
        with self.assertRaises(Exception):
            self.insert("i@example.com", {})          # exact duplicate
        with self.assertRaises(Exception) as caught:
            self.insert("I@Example.com", {})          # same mailbox, cased
        self.assertIn("accounts_email_is_lowercase", str(caught.exception))

    def test_an_address_with_no_mailbox_or_no_host_is_refused(self):
        """Deliberately the weakest useful check: something before the @ and
        something after it. Email validation by pattern is a famous way to
        reject real addresses, so this rejects only what cannot be one.

        It is here because an untested constraint is not a constraint, and
        this one was added without a test in the first draft."""
        for bad in ("@example.com", "nobody@", "nobody", "@"):
            with self.subTest(email=bad):
                with self.assertRaises(Exception) as caught:
                    self.insert(bad, {})
                self.assertIn("accounts_email_looks_like_one",
                              str(caught.exception))

    def test_unusual_but_legal_addresses_are_accepted(self):
        """The other half. A pattern that refuses these is worse than none."""
        for good in ("a+tag@example.co.uk", "x@y.z", "first.last@sub.example.io",
                     "user_name-1@example-host.com"):
            with self.subTest(email=good):
                self.insert(good, {})

    def test_the_schema_needs_no_extensions(self):
        """`gen_random_uuid()` is core since PostgreSQL 13, and nothing else
        here needs one. A migration that installs an extension behaves
        differently depending on which schema it landed in last time."""
        with self.conn.cursor() as cur:
            cur.execute("select count(*) from pg_extension "
                        "where extname in ('citext', 'pgcrypto')")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("insert into accounts (email) values ('u@example.com') "
                        "returning id")
            self.assertIsNotNone(cur.fetchone()[0])

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


class FlippingAFlag(pgtest.SchemaCase):
    """A flag that cannot be flipped is not a switch, it is a constant.

    Every cutover in the rebuild is meant to be turned on for one account
    before all of them, so `set_flag` is half the deliverable and the refusals
    below are what keep the other half honest: the same registry and the same
    boolean rule as the reader, enforced on the way in rather than discovered
    on the way out.
    """

    prefix = "flagset_test"

    def setUp(self):
        super().setUp()
        migrate.run(self.conn, migrate.MIGRATIONS)
        with self.conn.cursor() as cur:
            cur.execute("insert into accounts (email) values ('k@example.com') "
                        "returning id")
            self.account = cur.fetchone()[0]

    def test_a_flag_turns_on_and_off_again(self):
        flags.set_flag(self.conn, self.account, "live_board", True)
        self.assertTrue(flags.enabled(
            flags.for_account(self.conn, self.account), "live_board"))
        flags.set_flag(self.conn, self.account, "live_board", False)
        self.assertFalse(flags.enabled(
            flags.for_account(self.conn, self.account), "live_board"))

    def test_setting_one_flag_leaves_the_others_alone(self):
        """`jsonb_set`, not an overwrite. Turning on a Phase 2 flag must not
        silently turn off a Phase 1 one that is mid-cutover."""
        flags.set_flag(self.conn, self.account, "postgres_reads", True)
        flags.set_flag(self.conn, self.account, "live_board", True)
        stored = flags.for_account(self.conn, self.account)
        self.assertTrue(flags.enabled(stored, "postgres_reads"))
        self.assertTrue(flags.enabled(stored, "live_board"))

    def test_an_unregistered_name_is_refused(self):
        with self.assertRaises(flags.UnknownFlag):
            flags.set_flag(self.conn, self.account, "postgres_read", True)

    def test_a_non_boolean_is_refused_before_it_reaches_the_column(self):
        """The column would refuse it too. Refusing here as well means the
        error names the flag rather than the constraint."""
        for value in ("true", "false", 1, 0, None, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(flags.BadFlagValue):
                    flags.set_flag(self.conn, self.account, "live_board", value)

    def test_a_missing_account_is_refused_rather_than_silently_doing_nothing(self):
        """An UPDATE matching no rows succeeds and changes nothing. Somebody
        flipping a flag for the wrong account id would be told it worked."""
        import uuid as _uuid
        with self.assertRaises(flags.NoSuchAccount):
            flags.set_flag(self.conn, _uuid.uuid4(), "live_board", True)

    def test_a_refused_set_leaves_the_stored_flags_untouched(self):
        flags.set_flag(self.conn, self.account, "live_board", True)
        with self.assertRaises(flags.BadFlagValue):
            flags.set_flag(self.conn, self.account, "live_board", "false")
        self.assertTrue(flags.enabled(
            flags.for_account(self.conn, self.account), "live_board"))


if __name__ == "__main__":
    unittest.main()
