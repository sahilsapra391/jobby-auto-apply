"""A sweep that stops landing fails the dead-man check.

**Nothing alerted on this before, and the reason is worth keeping.** The
watcher spawns `ingest.py` detached and `spawn` never waits on it, so a sweep
that dies writes a traceback to `ingest-runs.log`, exits non-zero, releases its
lock, and is observed by nothing at all. The dead-man ping was composed from
three warnings and not one of them could see it:

  · `stale_board_warning` reads LAST_DEPLOY and LAST_PUBLISH, which answer
    "when did we last publish" and not "when did the data last change". The
    publish keeps succeeding over a frozen store, because it publishes whatever
    the store holds.
  · `parked_slot_warning` fired three hundred times during the outage, about a
    different and much less serious problem.
  · `chrome_flag_warning` is unrelated.

Measured on 2026-09-01: the pool went unwritten from 15:08Z to 14:31 the next
afternoon, seventeen hours, while the board republished throughout.
"""
import ast
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import watch  # noqa: E402

SOURCE = (REPO / "scripts" / "watch.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def code_of(name):
    """One function's source with its docstring removed.

    The assertions below are about what the code *reads*, and both of these
    docstrings name the constants they exist to distinguish themselves from. A
    check that cannot tell a mention from a use fails on prose.
    """
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            statements = list(node.body)
            if (statements and isinstance(statements[0], ast.Expr)
                    and isinstance(statements[0].value, ast.Constant)
                    and isinstance(statements[0].value.value, str)):
                statements = statements[1:]
            return "\n".join(ast.get_source_segment(SOURCE, s) or ""
                              for s in statements)
    raise AssertionError(f"{name} is not defined in watch.py")


def body_of(name):
    """The source of one function, by parsing rather than by slicing strings.

    The first version sliced from `def board_staleness` to the next `def
    stale_`, which swallowed `pool_staleness` in between and reported the new
    function's own text as the old one's. An instrument that reads adjacent
    code as its subject is the same defect as a span extractor that takes half
    a script.
    """
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node) or ""
    raise AssertionError(f"{name} is not defined in watch.py")


class SweepStalenessTests(unittest.TestCase):
    def test_a_fresh_pool_says_nothing(self):
        with mock.patch.object(watch, "pool_staleness", return_value=600):
            self.assertEqual(watch.stale_sweep_warning(), "")

    def test_a_pool_older_than_two_intervals_fails_the_check(self):
        with mock.patch.object(watch, "sweep_interval_seconds",
                               return_value=4 * 3600), \
             mock.patch.object(watch, "pool_staleness", return_value=17 * 3600):
            message = watch.stale_sweep_warning()
        self.assertIn("17.0 hours", message)
        self.assertIn("8.0 hour limit", message)
        self.assertIn("ingest-runs.log", message,
                      "the alarm has to name where the reason is, or it is a "
                      "notification rather than a diagnostic")

    def test_one_missed_sweep_is_tolerated(self):
        """An alarm that fires on a single skipped sweep stops being believed,
        which is what happened to the parked-slot warning."""
        with mock.patch.object(watch, "sweep_interval_seconds",
                               return_value=4 * 3600), \
             mock.patch.object(watch, "pool_staleness", return_value=5 * 3600):
            self.assertEqual(watch.stale_sweep_warning(), "")

    def test_a_short_interval_still_gets_a_floor(self):
        """At a one-minute configured interval two intervals is two minutes,
        and a sweep takes twenty-five. Without the floor the alarm would be
        permanently on."""
        with mock.patch.object(watch, "sweep_interval_seconds", return_value=60), \
             mock.patch.object(watch, "pool_staleness", return_value=90 * 60):
            self.assertEqual(watch.stale_sweep_warning(), "")

    def test_no_pool_at_all_says_nothing(self):
        """A machine that has never swept is not a machine whose sweeps died."""
        with mock.patch.object(watch, "pool_staleness", return_value=None):
            self.assertEqual(watch.stale_sweep_warning(), "")

    def test_it_is_actually_wired_into_the_dead_man_ping(self):
        """**The half that matters.** A warning function nothing calls is the
        `activity.json` counter again: correct, exercised, and never once run.

        **And it used to check that by reading the source**, anchored on the
        literal `stale = "; ".join(` inside the tick loop, because the
        composition lived in the loop and a loop cannot be run for one
        iteration. That anchor broke the moment the composition was lifted
        into `unhealthy_reason` to make it callable -- a scanner failing on
        the refactor that improved the thing it guards, which is the same
        shape as a check whose subject is the configuration rather than the
        property.

        This calls it instead. Each warning is silenced but one, and the
        surviving message has to come out the other side.
        """
        quiet = {name: (lambda: "") for name in
                 ("stale_board_warning", "parked_slot_warning",
                  "chrome_flag_warning")}
        with mock.patch.multiple(watch, **quiet), \
                mock.patch.object(watch.corpus_check, "warning", lambda: ""), \
                mock.patch.object(watch, "stale_sweep_warning",
                                  lambda: "THE POOL IS OLD"):
            self.assertEqual(watch.unhealthy_reason(), "THE POOL IS OLD")

    def test_every_warning_reaches_the_ping_not_just_this_one(self):
        """Five reasons on one alarm, and each is checked by silencing the
        other four. A reason that is composed but never joined is the
        `activity.json` counter again: correct, exercised, never once run."""
        names = ("stale_board_warning", "stale_sweep_warning",
                 "parked_slot_warning", "chrome_flag_warning")
        for speaking in names:
            with self.subTest(warning=speaking):
                quiet = {n: (lambda: "") for n in names if n != speaking}
                with mock.patch.multiple(watch, **quiet), \
                        mock.patch.object(watch.corpus_check, "warning",
                                          lambda: ""), \
                        mock.patch.object(watch, speaking, lambda: "HEARD"):
                    self.assertEqual(watch.unhealthy_reason(), "HEARD")
        quiet = {n: (lambda: "") for n in names}
        with mock.patch.multiple(watch, **quiet), \
                mock.patch.object(watch.corpus_check, "warning",
                                  lambda: "CORPUS"):
            self.assertEqual(watch.unhealthy_reason(), "CORPUS")

    def test_the_publish_check_and_this_one_measure_different_things(self):
        """They are two reasons on one alarm, not one reason twice. Merging
        them would lose the distinction the outage turned on: the board was
        publishing perfectly and the data was a day old."""
        board = code_of("board_staleness")
        self.assertIn("LAST_PUBLISH", board)
        self.assertNotIn("jobs_store", board,
                         "board staleness must not start reading the pool; "
                         "the two questions are different")


class PoolStalenessTests(unittest.TestCase):
    def test_it_reads_the_pool_and_not_the_publish_stamp(self):
        body = code_of("pool_staleness")
        self.assertIn("jobs_store", body)
        self.assertNotIn("LAST_PUBLISH", body)
        self.assertNotIn("LAST_DEPLOY", body)

    def test_it_returns_a_number_for_a_pool_that_exists(self):
        """**The test the first version did not have, and the fault it hid.**

        `jobs_store.paths` takes the store root and this passed it None, which
        raises TypeError; a broad `except Exception` turned that into None, and
        `stale_sweep_warning` reads None as "nothing to say". The alarm could
        never fire on any machine.

        Every test in this file mocked `pool_staleness`, including the one
        named for testing it, which mocked `jobs_store.paths` as well and so
        never exercised the call that was wrong. A test that supplies an input
        the real caller does not supply is testing a configuration nobody
        ships. This one gives it a real directory and reads a real number.
        """
        with tempfile.TemporaryDirectory() as root:
            pool = Path(root) / "jobs.json"
            pool.write_text("{}")
            os.utime(pool, (time.time() - 7200, time.time() - 7200))
            with mock.patch.object(watch, "STATE_DIR", Path(root)):
                age = watch.pool_staleness()
        self.assertIsNotNone(age, "a pool that exists must produce a number")
        self.assertAlmostEqual(age, 7200, delta=60)

    def test_a_missing_pool_is_none_rather_than_an_alarm(self):
        """A machine that has never swept is not one whose sweeps died. This is
        the only None it may return; anything else is a fault."""
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(watch, "STATE_DIR", Path(root)):
                self.assertIsNone(watch.pool_staleness())

    def test_it_does_not_swallow_a_programming_error_into_healthy(self):
        """The broad `except Exception` is what made a TypeError read as a
        clean bill of health. Only OSError is caught now."""
        body = code_of("pool_staleness")
        self.assertNotIn("except Exception", body)
        self.assertIn("except OSError", body)

    def test_the_alarm_is_silent_on_this_machine_for_the_right_reason(self):
        """Silence is only meaningful if the thing underneath it is measuring.

        Skipped where there is no store, rather than passing vacuously.
        """
        real = Path.home() / ".job-application" / "jobs.json"
        if not real.exists():
            self.skipTest("no job pool on this machine")
        self.assertIsNotNone(watch.pool_staleness(),
                             "pool_staleness returns None on a machine that "
                             "has a pool, so the alarm cannot fire")


if __name__ == "__main__":
    unittest.main()
