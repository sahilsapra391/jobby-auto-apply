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
import sys
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
        """
        composition = SOURCE[SOURCE.index('stale = "; ".join('):]
        composition = composition[:composition.index("if message")]
        self.assertIn("stale_sweep_warning()", composition,
                      "stale_sweep_warning is not in the dead-man composition, "
                      "so it can never fire")

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

    def test_a_missing_pool_is_none_rather_than_an_alarm(self):
        with mock.patch.dict(sys.modules):
            with mock.patch("jobs_store.paths",
                            return_value={"jobs": Path("/nonexistent/pool.json")}):
                self.assertIsNone(watch.pool_staleness())


if __name__ == "__main__":
    unittest.main()
