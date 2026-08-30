"""`enabled()` and `connect()` may not disagree about which database exists.

The root cause of all three of 2026-08-30's incidents rather than a fourth.
`enabled()` asked `jobs_db.database_url()`, which reads config.env under the
store root; `connect()` asked `migrate.database_url()`, which prefers
`JOBBY_TEST_DATABASE_URL`. A caller could be told the device had a database and
then be handed a different one.

`HOME=$(mktemp -d)` hid it by emptying both sides at once. Resolving
credentials through the store root removed that accident. Pointing
`JOBBY_TEST_DATABASE_URL` at production was the same hole wearing a third name.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "packages" / "schema"))

import dual_write  # noqa: E402
import migrate     # noqa: E402

DEVICE = "postgresql://u:p@ep-real-pooler.c-5.us-east-2.aws.neon.tech/neondb"
OTHER = "postgresql://u:p@localhost:5432/jobby_test"


class TheyCannotDisagree(unittest.TestCase):
    """Every combination of the two inputs that decide a URL."""

    def combinations(self):
        """Both env vars, a redirected store, and both sides of the test-runner
        guard. The guard has to vary: this module runs under unittest, so
        without it every combination resolves to None and an agreement test
        over them would be vacuous. The control below is what caught that."""
        for test_url in ("", OTHER):
            for device_url in ("", DEVICE):
                for redirected in (False, True):
                    for under_test in (True, False):
                        yield test_url, device_url, redirected, under_test

    def test_enabled_and_connect_resolve_the_same_url_every_time(self):
        for test_url, device_url, redirected, under_test in self.combinations():
            with self.subTest(test=bool(test_url), device=bool(device_url),
                              redirected=redirected, under_test=under_test):
                with tempfile.TemporaryDirectory() as d:
                    env = {"JOBBY_TEST_DATABASE_URL": test_url,
                           "JOBBY_DATABASE_URL": device_url}
                    if redirected:
                        env["JOB_APPLICATION_DIR"] = d
                    with mock.patch.dict(os.environ, env), \
                         mock.patch.object(migrate, "_under_test_runner",
                                           lambda: under_test):
                        resolved = dual_write.resolved_url()
                        # What connect() would open, without opening it.
                        opened = {}
                        with mock.patch.object(
                                migrate, "connect",
                                side_effect=lambda url=None, announce=True:
                                    opened.setdefault("url", url)):
                            try:
                                dual_write.connect()
                            except Exception:
                                pass
                        self.assertEqual(
                            opened.get("url"), resolved,
                            "connect() opens a different URL than the one "
                            "enabled() reported on")
                        self.assertEqual(dual_write.enabled(), bool(resolved))

    def test_control_the_combinations_actually_produce_different_answers(self):
        """Otherwise "they always agree" passes because the resolver always
        returns None."""
        answers = set()
        for test_url, device_url, redirected, under_test in self.combinations():
            with tempfile.TemporaryDirectory() as d:
                env = {"JOBBY_TEST_DATABASE_URL": test_url,
                       "JOBBY_DATABASE_URL": device_url}
                if redirected:
                    env["JOB_APPLICATION_DIR"] = d
                with mock.patch.dict(os.environ, env), \
                     mock.patch.object(migrate, "_under_test_runner",
                                       lambda: under_test):
                    answers.add(dual_write.resolved_url())
        self.assertGreater(len(answers), 1,
                           "every combination resolved identically, so the "
                           "agreement test above proves nothing")

    def test_enabled_goes_through_the_one_resolver(self):
        """Not through jobs_db, which reads a different place."""
        with mock.patch.object(dual_write, "resolved_url",
                               return_value="postgresql://x/y") as resolver:
            self.assertTrue(dual_write.enabled())
        resolver.assert_called_once()


class TheMirrorTargetIsNotWhateverDatabaseIsAround(unittest.TestCase):
    """`resolved_url` answers "where does this device's store mirror to".
    `migrate.database_url` answers "which database is the schema tool pointed
    at". A test database is the right answer to the second and never to the
    first, and consulting `JOBBY_TEST_DATABASE_URL` here turned dual-write on
    for every store-writing test in CI, which sets it for pgtest: 57 errors
    across test_ingest and test_found_script."""

    def test_a_test_database_does_not_become_the_mirror_target(self):
        with mock.patch.dict(os.environ,
                             {"JOBBY_TEST_DATABASE_URL": OTHER,
                              "JOBBY_DATABASE_URL": ""}), \
             tempfile.TemporaryDirectory() as d:
            os.environ["JOB_APPLICATION_DIR"] = d
            try:
                self.assertIsNone(dual_write.resolved_url())
                self.assertFalse(dual_write.enabled())
            finally:
                os.environ.pop("JOB_APPLICATION_DIR", None)

    def test_control_the_schema_tool_does_use_it(self):
        """The other half. If `migrate.database_url` stopped honouring it,
        pgtest and the migration runner would both lose their database and the
        test above would still pass."""
        with mock.patch.dict(os.environ,
                             {"JOBBY_TEST_DATABASE_URL": OTHER,
                              "JOBBY_DATABASE_URL": ""}):
            self.assertEqual(migrate.database_url(), OTHER)

    def test_the_device_url_is_still_the_mirror_target_outside_a_test_run(self):
        with mock.patch.dict(os.environ,
                             {"JOBBY_TEST_DATABASE_URL": OTHER,
                              "JOBBY_DATABASE_URL": DEVICE}), \
             mock.patch.object(migrate, "_under_test_runner", lambda: False):
            self.assertEqual(dual_write.resolved_url(), DEVICE)


class ATestUrlMayNotBeProduction(unittest.TestCase):
    """pgtest's own docstring says the only Postgres on this Mac is the live
    one, so the obvious way to run the database tests is to point the escape
    hatch at production. That is what defeated the test-runner guard on
    2026-08-30, hours after it was written."""

    def config(self, directory, url=DEVICE):
        path = Path(directory) / "config.env"
        path.write_text(f"JOBBY_DATABASE_URL={url}\n")
        return path

    def test_the_same_database_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ,
                                 {"JOBBY_TEST_DATABASE_URL": DEVICE,
                                  "JOBBY_DATABASE_URL": ""}):
                with self.assertRaises(migrate.MigrationError) as caught:
                    migrate.database_url(config_path=self.config(d))
        self.assertIn("same database", str(caught.exception))

    def test_the_direct_form_of_the_same_database_is_refused(self):
        """`direct_url` strips `-pooler`, and pgtest connects through exactly
        that rewrite. Comparing raw hosts would call the two forms of one
        database two databases and let the match straight through."""
        direct = migrate.direct_url(DEVICE)
        self.assertNotEqual(direct, DEVICE, "the fixture is not actually pooled")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ,
                                 {"JOBBY_TEST_DATABASE_URL": direct,
                                  "JOBBY_DATABASE_URL": ""}):
                with self.assertRaises(migrate.MigrationError):
                    migrate.database_url(config_path=self.config(d))

    def test_control_a_genuinely_different_database_is_allowed(self):
        """Otherwise the refusal passes by refusing everything, and CI's
        throwaway container would stop working."""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ,
                                 {"JOBBY_TEST_DATABASE_URL": OTHER,
                                  "JOBBY_DATABASE_URL": ""}):
                self.assertEqual(
                    migrate.database_url(config_path=self.config(d)), OTHER)

    def test_a_different_database_on_the_same_host_is_allowed(self):
        """The comparison is (host, database), not host alone. A local server
        hosting both would otherwise be unusable."""
        same_host = DEVICE.rsplit("/", 1)[0] + "/somethingelse"
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ,
                                 {"JOBBY_TEST_DATABASE_URL": same_host,
                                  "JOBBY_DATABASE_URL": ""}):
                self.assertEqual(
                    migrate.database_url(config_path=self.config(d)), same_host)

    def test_it_refuses_whether_or_not_a_test_runner_is_present(self):
        """A script that thinks it has a throwaway database and has production
        is wrong either way."""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ,
                                 {"JOBBY_TEST_DATABASE_URL": DEVICE,
                                  "JOBBY_DATABASE_URL": ""}), \
                 mock.patch.object(migrate, "_under_test_runner",
                                   lambda: False):
                with self.assertRaises(migrate.MigrationError):
                    migrate.database_url(config_path=self.config(d))

    def test_same_database_normalises_both_sides(self):
        self.assertTrue(migrate.same_database(DEVICE, migrate.direct_url(DEVICE)))
        self.assertFalse(migrate.same_database(DEVICE, OTHER))
        self.assertFalse(migrate.same_database(DEVICE, ""))
        self.assertFalse(migrate.same_database("", ""))


if __name__ == "__main__":
    unittest.main()
