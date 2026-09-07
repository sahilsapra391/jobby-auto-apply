"""The circuit breaker, and the two things it must not do.

**Nothing writes per-board health today.** Measured 2026-09-07 over all 895
`companies.json` entries: `lastFetchAt` is null on 895 of 895 and `lastStatus`
is "unfetched" on 890, written once at seed time by `seed_companies.py:747`.
The fields exist and no sweep has ever touched them, so the only failure signal
is a per-sweep aggregate that names a count and no board: "6 token(s) 404 and
need re-resolving", 9 dead boards of 638 registry-driven ones across the
2026-09-07 sweep, 54 wasted requests a day, and no way to say which nine.

The waste is not the point at 895 boards. §5 grows this registry by tenant
probing toward 200k postings, where a discovered board that never answers is
the common case.
"""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "schema"))

import board_health as bh  # noqa: E402


def board(slug, failures=0, disabled=None, source="greenhouse"):
    return {"source": source, "board_slug": slug,
            "consecutive_failures": failures, "disabled_at": disabled}


class ClassifyTests(unittest.TestCase):
    def test_a_404_is_about_the_board(self):
        self.assertEqual(bh.classify(404), "definitive")
        self.assertEqual(bh.classify(410), "definitive")

    def test_a_server_error_is_about_the_day(self):
        for status in (500, 502, 503, 429, 418):
            with self.subTest(status=status):
                self.assertEqual(bh.classify(status), "transient")

    def test_no_response_at_all_is_always_transient(self):
        # Nothing said anything about this board, so nothing was learned about
        # it. A timeout is the ATS being slow or the network being bad.
        self.assertEqual(bh.classify(None, "timed out"), "transient")
        self.assertEqual(bh.classify(None, "connection reset"), "transient")

    def test_a_success_is_a_success(self):
        for status in (200, 201, 204):
            with self.subTest(status=status):
                self.assertEqual(bh.classify(status), "ok")


class CounterTests(unittest.TestCase):
    def test_only_a_definitive_failure_moves_the_counter(self):
        b = {}
        for _ in range(10):
            b = bh.apply_outcome(b, status=503, at="t")
        self.assertEqual(b["consecutive_failures"], 0)
        self.assertFalse(bh.should_trip(b))

    def test_a_transient_failure_is_still_recorded(self):
        # It does not count and it is not discarded. The occurrence able to
        # name a cause is the one guaranteed not to be reproducible later.
        b = bh.apply_outcome({}, status=503, at="t1")
        self.assertEqual(b["last_failed_at"], "t1")
        self.assertEqual(b["last_failure"], "http 503")
        b = bh.apply_outcome({}, error="timed out after 20s", at="t2")
        self.assertEqual(b["last_failure"], "timed out after 20s")

    def test_the_ceiling_is_consecutive_and_a_success_clears_it(self):
        b = {}
        for _ in range(2):
            b = bh.apply_outcome(b, status=404, at="t")
        b = bh.apply_outcome(b, status=200, at="t")
        self.assertEqual(b["consecutive_failures"], 0)
        for _ in range(2):
            b = bh.apply_outcome(b, status=404, at="t")
        self.assertFalse(bh.should_trip(b), "a success did not clear the count")

    def test_a_success_reopens_a_disabled_board(self):
        # A board that answers is a board that works, and leaving it retired
        # would need a person to notice.
        b = board("acme", failures=9, disabled="yesterday")
        b = bh.apply_outcome(b, status=200, at="today")
        self.assertIsNone(b["disabled_at"])
        self.assertIsNone(b["disabled_reason"])
        self.assertEqual(b["consecutive_failures"], 0)

    def test_an_already_disabled_board_does_not_trip_again(self):
        self.assertFalse(bh.should_trip(board("acme", failures=99,
                                              disabled="yesterday")))


class TripPlanTests(unittest.TestCase):
    def test_a_few_dead_boards_are_retired(self):
        boards = [board(f"live{i}") for i in range(100)]
        boards += [board(f"dead{i}", failures=bh.FAILURE_CEILING) for i in range(6)]
        plan, refusal = bh.trip_plan(boards, at="now")
        self.assertIsNone(refusal)
        self.assertEqual(len(plan), 6)
        self.assertTrue(all(b["disabled_at"] == "now" for b in plan))
        self.assertIn("consecutive definitive failures", plan[0]["disabled_reason"])

    def test_a_whole_source_going_404_is_refused(self):
        # An ATS changing its URL scheme returns 404 from a healthy server for
        # every board it serves. Definitive, honest, and not about any board.
        boards = [board(f"b{i}", failures=bh.FAILURE_CEILING) for i in range(353)]
        plan, refusal = bh.trip_plan(boards, at="now")
        self.assertEqual(plan, [])
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal["wouldDisable"], 353)
        self.assertEqual(refusal["live"], 353)
        self.assertIn("change at the ATS", refusal["why"])

    def test_the_real_registry_shape_trips_nothing_wrongly(self):
        # greenhouse on 2026-09-07: 353 boards, 6 dead. 1.7%, well under the
        # ceiling, so the ordinary case is retired and the source survives.
        boards = [board(f"live{i}") for i in range(347)]
        boards += [board(f"dead{i}", failures=bh.FAILURE_CEILING) for i in range(6)]
        plan, refusal = bh.trip_plan(boards, at="now")
        self.assertIsNone(refusal)
        self.assertEqual(len(plan), 6)

    def test_a_small_source_is_not_measured_by_ratio(self):
        # Four live boards losing one is 25% and means nothing.
        boards = [board(f"b{i}") for i in range(3)]
        boards += [board("dead", failures=bh.FAILURE_CEILING)]
        plan, refusal = bh.trip_plan(boards, at="now")
        self.assertIsNone(refusal)
        self.assertEqual(len(plan), 1)

    def test_already_disabled_boards_are_not_in_the_denominator(self):
        # Otherwise a source retires a few boards, the live count shrinks, and
        # the next pass's share is computed against the survivors -- a ratchet
        # that gets easier to trip the more it has already tripped.
        boards = [board(f"live{i}") for i in range(30)]
        boards += [board(f"old{i}", disabled="last week") for i in range(300)]
        boards += [board(f"dead{i}", failures=bh.FAILURE_CEILING) for i in range(3)]
        plan, refusal = bh.trip_plan(boards, at="now")
        self.assertIsNone(refusal)
        self.assertEqual(len(plan), 3)

    def test_nothing_to_do_is_not_a_refusal(self):
        plan, refusal = bh.trip_plan([board(f"b{i}") for i in range(50)], at="now")
        self.assertEqual(plan, [])
        self.assertIsNone(refusal)

    def test_a_disabled_board_is_not_enqueued_and_not_deleted(self):
        boards = [board("live"), board("dead", disabled="now")]
        self.assertEqual([b["board_slug"] for b in bh.enqueueable(boards)],
                         ["live"])
        self.assertEqual(len(boards), 2)


class TimingPathTests(unittest.TestCase):
    """A class A write records its timing into the store it was told to use.

    **`persist` takes no `root`, which is how the first fix for this was a
    `NameError` waiting on a branch nothing exercises.** `_record_timing` fell
    back to `Path("~/.job-application")` and both call sites passed nothing, so
    a redirected write logged into the device store; the obvious repair,
    `root=root`, compiles, passes `py_compile`, passes the whole suite, and
    raises the first time a file write fails -- because the name is not bound
    in that function. `path` is bound, and its parent is the store root by
    construction (`paths(root)["jobs"]`).

    `NoStorePathIsAFrozenDefaultTests` cannot see this class of fault: it walks
    module-level constants, and this was a fallback inside a function body.
    """

    def test_both_call_sites_pass_a_root_derived_from_the_pool_path(self):
        import ast
        import inspect
        sys.path.insert(0, str(REPO / "scripts"))
        import dual_write

        # The whole module, and `persist` found by walking it. Re-parsing one
        # function's source needs its signature rewritten and a multi-line
        # `def` defeats that, which is a formatting detail deciding whether the
        # check runs at all.
        tree = ast.parse(Path(dual_write.__file__).read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "persist")
        calls = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "_record_timing"]
        self.assertEqual(len(calls), 2,
                         "persist no longer has two timing calls; this test is "
                         "about both of them")
        for call in calls:
            kwargs = {k.arg: k.value for k in call.keywords}
            self.assertIn("root", kwargs, "a timing call passes no root, so it "
                                          "writes to the device store")
            # And the root must come from `path`, which is the only thing in
            # scope that knows the store. A bare `root` name would raise.
            self.assertIn("path", ast.dump(kwargs["root"]))

    def test_persist_has_no_root_parameter_to_pass(self):
        # The premise of the test above, pinned so it fails loudly rather than
        # silently becoming vacuous if a `root=` parameter is ever added.
        import inspect
        sys.path.insert(0, str(REPO / "scripts"))
        import dual_write
        self.assertNotIn("root",
                         inspect.signature(dual_write.persist).parameters)

    def test_the_timing_lands_under_the_root_it_is_given(self):
        import tempfile
        sys.path.insert(0, str(REPO / "scripts"))
        import dual_write
        with tempfile.TemporaryDirectory() as tmp:
            dual_write._record_timing({"records": 1}, root=Path(tmp))
            written = Path(tmp) / "logs" / "mirror-timing.jsonl"
            self.assertTrue(written.exists())
            self.assertIn('"records": 1', written.read_text())
