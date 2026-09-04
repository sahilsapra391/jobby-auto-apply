"""A test run may not reach the device's own database.

On 2026-08-30 one unredirected `unittest discover` wrote the live Neon store:
39 rows into `applications` against 197 real ones, 21 into
`application_events`, `answer_bank` emptied from about 180 rows to 0, the
single `profiles` row overwritten with `{}`, two scratch schemas orphaned.
Parity went from green to 221 divergences and the seven-day clock restarted.

`tests/store_isolation.py` did not prevent it and could not: it moves the file
store by walking module constants and contains no reference to a database.
`migrate.database_url` fell through from the unset `JOBBY_TEST_DATABASE_URL` to
the device's `JOBBY_DATABASE_URL`, which is right for the watcher and
catastrophic for a test, and every test driving class_b, dual_write or backfill
goes that way.

The tests here are paired on purpose. Each refusal has a control that removes
only the guard and shows the same inputs would otherwise reach the device,
because a refusal test passes just as happily when there is nothing to refuse.
"""

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))

import migrate  # noqa: E402


class TheGuardRefusesTheDeviceDatabase(unittest.TestCase):
    """This module runs under unittest, so `_under_test_runner()` is already
    true here. That is the condition under test, not a fixture."""

    def setUp(self):
        self.assertTrue(migrate._under_test_runner(),
                        "this suite must itself count as a test runner")

    def test_the_env_var_naming_the_device_database_is_refused(self):
        with mock.patch.dict(os.environ,
                             {"JOBBY_DATABASE_URL": "postgresql://real/db",
                              "JOBBY_TEST_DATABASE_URL": ""}):
            self.assertIsNone(migrate.database_url())

    def test_control_without_the_guard_that_same_env_var_is_returned(self):
        """The half that makes the test above mean something. Without it, a
        refusal is indistinguishable from there being nothing to refuse."""
        with mock.patch.dict(os.environ,
                             {"JOBBY_DATABASE_URL": "postgresql://real/db",
                              "JOBBY_TEST_DATABASE_URL": ""}), \
             mock.patch.object(migrate, "_under_test_runner", lambda: False):
            self.assertEqual(migrate.database_url(), "postgresql://real/db")

    def test_the_config_file_is_refused_too(self):
        """The path that actually did the damage. The env var is usually unset
        on a laptop; the URL lives in ~/.job-application/config.env, which is
        why a redirected HOME happened to cover this and why removing the
        redirect uncovered it."""
        with mock.patch.dict(os.environ, {"JOBBY_DATABASE_URL": "",
                                          "JOBBY_TEST_DATABASE_URL": ""}):
            config = Path(self.enterContext(
                __import__("tempfile").TemporaryDirectory())) / "config.env"
            config.write_text("JOBBY_DATABASE_URL=postgresql://real/from-config\n")
            self.assertIsNone(migrate.database_url(config_path=config))

            with mock.patch.object(migrate, "_under_test_runner", lambda: False):
                self.assertEqual(migrate.database_url(config_path=config),
                                 "postgresql://real/from-config",
                                 "control: the config file is readable and holds "
                                 "a URL, so the refusal above was the guard")

    def test_connect_names_the_guard_rather_than_claiming_nothing_is_set(self):
        """A wrong reason costs more than no reason. "JOBBY_DATABASE_URL is not
        set" would be false here: it is set, and it is refused."""
        with mock.patch.dict(os.environ,
                             {"JOBBY_DATABASE_URL": "postgresql://real/db",
                              "JOBBY_TEST_DATABASE_URL": ""}):
            with self.assertRaises(migrate.MigrationError) as caught:
                migrate.connect(announce=False)
        message = str(caught.exception)
        self.assertIn("refusing", message)
        self.assertNotIn("is not set", message)

    def test_the_refusal_is_written_to_stderr_not_print(self):
        """The runs that trigger this are wrapped in
        mock.patch("builtins.print") to keep the runner readable. A diagnostic
        silenced by exactly the runs it exists to explain is this repository's
        oldest failure shape, and it has already happened once to the sibling
        guard in provider_usage."""
        import io
        stream = io.StringIO()
        with mock.patch.dict(os.environ,
                             {"JOBBY_DATABASE_URL": "postgresql://real/db",
                              "JOBBY_TEST_DATABASE_URL": ""}), \
             mock.patch("builtins.print") as printed, \
             mock.patch.object(sys, "stderr", stream):
            migrate.database_url()
        self.assertIn("refusing", stream.getvalue())
        printed.assert_not_called()


class TheGuardDoesNotBlockLegitimateUse(unittest.TestCase):
    def test_a_test_database_is_honoured_and_never_refused(self):
        """CI supplies a throwaway container and must keep working. A guard
        that also blocked this would be found by CI going red, which is a
        slower and more confusing way to learn it."""
        with mock.patch.dict(os.environ,
                             {"JOBBY_TEST_DATABASE_URL": "postgresql://ci/throwaway",
                              "JOBBY_DATABASE_URL": "postgresql://real/db"}):
            self.assertEqual(migrate.database_url(), "postgresql://ci/throwaway")

    def test_an_explicit_url_bypasses_the_guard(self):
        """An operator naming a database is not a fall-through. This is the
        escape hatch, and it is the only one: there is deliberately no
        environment variable that switches the guard off, because a variable
        set once and forgotten is how the guard would stop existing."""
        with mock.patch.dict(os.environ, {"JOBBY_TEST_DATABASE_URL": "",
                                          "JOBBY_DATABASE_URL": ""}):
            captured = {}
            with mock.patch.object(migrate, "psycopg", create=True):
                try:
                    migrate.connect(url="postgresql://named/explicitly",
                                    announce=False)
                except Exception as exc:                 # driver may be absent
                    captured["error"] = str(exc)
        self.assertNotIn("refusing", captured.get("error", ""),
                         "an explicitly named URL was refused")


class TheGuardCannotFireOnTheDevice(unittest.TestCase):
    """Measured, not asserted.

    The whole guard rests on `unittest` and `pytest` being absent from
    `sys.modules` during real work. If either were imported anywhere in the
    daemon's import closure, this would refuse the watcher's own mirror writes
    and the first symptom would be dual-write silently failing in production.
    So the claim is checked by importing the real modules in a fresh
    interpreter rather than by reading the import lines.
    """

    def test_importing_the_daemon_paths_leaves_no_test_runner_behind(self):
        program = textwrap.dedent("""
            import sys
            sys.path.insert(0, %r)
            sys.path.insert(0, %r)
            import dual_write, class_b, jobs_store, migrate
            found = [n for n in ("unittest", "pytest") if n in sys.modules]
            # A sentinel rather than an empty line: strip() eats the blank and
            # the reader then unpacks one line into two, which is a test
            # failing on its own plumbing instead of on its subject.
            print("leaked=" + (",".join(found) or "none"))
            print("under_test=" + str(migrate._under_test_runner()))
        """) % (str(REPO / "scripts"), str(REPO / "packages" / "schema"))
        result = subprocess.run([sys.executable, "-c", program],
                                capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0,
                         f"the daemon modules did not import: {result.stderr}")
        lines = dict(line.split("=", 1)
                     for line in result.stdout.strip().splitlines() if "=" in line)
        self.assertEqual(lines.get("leaked"), "none",
                         f"a daemon import path pulls in {lines.get('leaked')}, so "
                         f"this guard would refuse the watcher's own mirror writes")
        self.assertEqual(lines.get("under_test"), "False")
