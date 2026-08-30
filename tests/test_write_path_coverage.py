"""The gate's second condition: every documented write path has actually fired.

Three green days never proved the rare paths ran. Class A moves on every
four-hour sweep; class B moves only when the user acts, so a drop from In
flight or a mark-submitted can sit unexercised through a whole clock while the
counter of days ticks up regardless. This is the counter that says so.
"""

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import write_path_coverage as wpc  # noqa: E402


class TheInventoryIsReadTheSameWayTwice(unittest.TestCase):
    """`tests/test_write_paths.py` parses `docs/write-paths.md` to check the
    code against it; this module parses the same file to check firings against
    it. One contract in two places is how the logo derivation went wrong, and
    comparing outputs rather than restating the rule is how that was caught."""

    def test_both_parsers_agree_row_for_row(self):
        import test_write_paths
        self.assertEqual(wpc.documented(), test_write_paths.documented())

    def test_class_c_is_excluded_from_what_the_gate_requires(self):
        """Class C rows are the ones the document says are deliberately not
        mirrored. Waiting for them would wait forever for a thing that must
        not happen."""
        classes = wpc.documented()
        need = wpc.required()
        self.assertTrue(need, "the inventory parsed to nothing")
        for key in need:
            self.assertIn(classes[key], ("A", "B"))
        self.assertTrue(any(c == "C" for c in classes.values()),
                        "no class C rows at all, so this proves nothing")


class ItTicksTheWholeChain(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = Path(self.dir.name) / "write-paths.jsonl"
        patcher = mock.patch.object(wpc, "log_path", lambda: self.log)
        patcher.start(); self.addCleanup(patcher.stop)

    def recorded(self):
        return {(r["module"], r["function"], r["primitive"])
                for r in wpc.observations(self.log)}

    def test_a_wrapper_between_two_frames_does_not_break_the_chain(self):
        """**The bug the first version had.** It paired neighbouring frames,
        and `jobs_store.mark_watched -> edit` never ticked because `edit` is a
        context manager and `contextlib.__exit__` sits between the two. Any
        decorator, context manager or callback does the same."""
        inv = {("outer_mod", "outer_fn", "inner_fn")}

        def inner_fn():
            wpc.observe(primitive="inner_fn", inventory=inv)

        @contextlib.contextmanager
        def inner_fn_cm():
            yield
            # On EXIT, which is what `jobs_store.edit` does: the pool is
            # persisted as the context manager closes, so contextlib's own
            # frame sits between it and its caller. Calling inside the block
            # instead does not reproduce this and the first version of this
            # test did exactly that, which is why the adjacency mutation
            # survived it.
            inner_fn()

        def outer_fn():
            with inner_fn_cm():
                pass

        outer_fn.__code__ = outer_fn.__code__.replace(co_filename="outer_mod.py")
        inner_fn.__code__ = inner_fn.__code__.replace(co_filename="inner_mod.py")
        inner_fn_cm.__wrapped__.__code__ = inner_fn_cm.__wrapped__.__code__.replace(
            co_filename="inner_mod.py")
        outer_fn()
        self.assertIn(("outer_mod", "outer_fn", "inner_fn"), self.recorded(),
                      "a contextlib frame between the two broke the chain")

    def test_control_it_does_not_tick_a_path_that_is_not_on_the_stack(self):
        """Containment must not become "tick everything". A documented row
        whose function never ran stays unticked."""
        inv = {("outer_mod", "outer_fn", "inner_fn"),
               ("absent_mod", "never_called", "inner_fn")}

        def inner_fn():
            wpc.observe(primitive="inner_fn", inventory=inv)

        def outer_fn():
            inner_fn()

        outer_fn.__code__ = outer_fn.__code__.replace(co_filename="outer_mod.py")
        inner_fn.__code__ = inner_fn.__code__.replace(co_filename="inner_mod.py")
        outer_fn()
        got = self.recorded()
        self.assertIn(("outer_mod", "outer_fn", "inner_fn"), got)
        self.assertNotIn(("absent_mod", "never_called", "inner_fn"), got)

    def test_the_callee_must_be_inside_the_caller_not_merely_present(self):
        """Order matters: a function that ran *before* the primitive, not
        beneath it, is not a call site for it."""
        inv = {("outer_mod", "outer_fn", "inner_fn")}

        def inner_fn():
            # outer_fn is NOT on this stack, so nothing may tick.
            wpc.observe(primitive="inner_fn", inventory=inv)

        inner_fn.__code__ = inner_fn.__code__.replace(co_filename="inner_mod.py")
        inner_fn()
        self.assertEqual(self.recorded(), set())

    def test_the_hooks_own_frame_is_never_on_the_observed_chain(self):
        """Behavioural, through the real hook, and only the frame that is
        actually at risk.

        The first version asserted `observe` and `_callers` were absent, which
        passed with the exclusion deleted: `_callers` starts at
        `sys._getframe(2)`, so both are already past the start of the walk and
        neither was ever going to appear. The frame that does sit at the start
        is `store._observe_write_path`, and only a real write puts it there."""
        import store
        captured = []
        real = wpc._callers

        def spy(*a, **k):
            frames = real(*a, **k)
            captured.append(frames)
            return frames

        with mock.patch.object(wpc, "_callers", spy), \
             tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": d}):
                store.write_private(Path(d) / "x.json", {"a": 1})

        self.assertTrue(captured, "the hook never ran, so this proves nothing")
        chain = captured[0]
        self.assertNotIn(("store", "_observe_write_path"), chain)
        # The control: the frame it sits directly beneath is still there, so
        # the walk is not simply returning nothing.
        self.assertIn(("store", "write_private"), chain)


class ItNeverAffectsTheCaller(unittest.TestCase):
    def test_an_exception_inside_it_is_swallowed_and_reported(self):
        import io
        stream = io.StringIO()
        with mock.patch.object(wpc, "required", side_effect=RuntimeError("boom")), \
             mock.patch.object(sys, "stderr", stream), \
             mock.patch("builtins.print") as printed:
            self.assertEqual(wpc.observe(), [])
        self.assertIn("boom", stream.getvalue())
        printed.assert_not_called()

    def test_control_it_returns_keys_when_nothing_is_wrong(self):
        """Otherwise "it swallowed the error" passes when it swallows
        everything."""
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "w.jsonl"
            with mock.patch.object(wpc, "log_path", lambda: log):
                # A real two-frame chain. A function cannot be strictly inside
                # itself, so (mod, f, f) can never tick -- the first version of
                # this control asserted exactly that and failed, correctly.
                inv = {("outer_mod", "outer_fn", "inner_fn")}

                def inner_fn():
                    return wpc.observe(primitive="inner_fn", inventory=inv)

                def outer_fn():
                    return inner_fn()

                outer_fn.__code__ = outer_fn.__code__.replace(
                    co_filename="outer_mod.py")
                inner_fn.__code__ = inner_fn.__code__.replace(
                    co_filename="inner_mod.py")
                self.assertEqual(outer_fn(),
                                 [("outer_mod", "outer_fn", "inner_fn")])

    def test_a_real_write_still_works_with_the_hook_installed(self):
        """The hook is inside `store.write_private`, the one function that puts
        bytes in the store. A bug there is a bug in every write."""
        import store
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "thing.json"
            with mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": d}):
                store.write_private(target, {"a": 1})
            self.assertEqual(json.loads(target.read_text()), {"a": 1})
            self.assertEqual(oct(target.stat().st_mode)[-3:], "600")

    def test_store_write_private_actually_calls_the_hook(self):
        """**The check the previous test is not.** A write that succeeds proves
        the write works; it says nothing about whether the counter ran, and
        deleting the hook line entirely passed the whole suite until this
        existed. Driven through a documented chain so a real key is recorded."""
        import store, jobs_store
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "logs").mkdir(parents=True, exist_ok=True)
            # Dual-write off: this is about whether the counter's hook fires,
            # not about mirroring. With a throwaway database configured the
            # class A mirror would try to use it and fail on a schema it has
            # never had -- which is correct class A behaviour and nothing to do
            # with what is under test here.
            with mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": d,
                                              "JOBBY_TEST_DATABASE_URL": "",
                                              "JOBBY_DATABASE_URL": ""}):
                jobs_store.save_jobs(root, {"a": {"id": "a", "company": "C",
                                                  "title": "T",
                                                  "url": "https://e.com/1"}})
            seen = {(r.get("module"), r.get("function"), r.get("primitive"))
                    for r in wpc.observations(root / "logs" / "write-paths.jsonl")}
        self.assertIn(("jobs_store", "_persist_pool", "write_private"), seen,
                      "store.write_private did not tick anything, so the hook "
                      "is not wired in and the whole counter is inert")
        self.assertIn(("jobs_store", "save_jobs", "_persist_pool"), seen)


class TheDeployedBundleWorksWithoutTheSidecar(unittest.TestCase):
    """`scripts/store.py` is copied into `deploy/renderer/` and shipped to
    Vercel; `scripts/write_path_coverage.py` is not.

    So the hook's `try: import write_path_coverage` is load-bearing in the
    deployment rather than defensive, and it is the same class as the favicon
    the renderer once read from `deploy/public/`: present on every laptop,
    absent from the bundle, and invisible until it is live. Checked against the
    real bundle with nothing else on the path, because reading the try/except
    and being satisfied is what that outage was made of.
    """

    def test_the_bundled_store_writes_with_the_sidecar_absent(self):
        import shutil
        import subprocess
        bundle_src = REPO / "deploy" / "renderer"
        if not (bundle_src / "store.py").exists():
            self.skipTest("no rendered bundle in this checkout")
        with tempfile.TemporaryDirectory() as d:
            bundle = Path(d) / "bundle"
            shutil.copytree(bundle_src, bundle)
            self.assertFalse((bundle / "write_path_coverage.py").exists(),
                             "the sidecar is in the bundle now, so this test "
                             "no longer covers the case it was written for")
            target = Path(d) / "out.json"
            program = (
                "import sys\n"
                f"sys.path = [r'{bundle}'] + "
                "[p for p in sys.path if 'jobby-auto-apply' not in p]\n"
                "import store\n"
                f"store.write_private(r'{target}', {{'a': 1}})\n")
            result = subprocess.run([sys.executable, "-c", program],
                                    capture_output=True, text=True, cwd=d,
                                    env={**os.environ, "JOB_APPLICATION_DIR": d})
            self.assertEqual(result.returncode, 0,
                             f"the bundled store failed: {result.stderr[-400:]}")
            self.assertEqual(json.loads(target.read_text()), {"a": 1})


class TheReportAnswersTheGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = Path(self.dir.name) / "w.jsonl"

    def write(self, rows):
        self.log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_it_reports_what_has_not_fired(self):
        need = sorted(wpc.required())
        self.write([{"at": "2026-09-01T00:00:00Z", "module": need[0][0],
                     "function": need[0][1], "primitive": need[0][2],
                     "driven": False}])
        result = wpc.coverage("2026-08-01T00:00:00Z", self.log)
        self.assertEqual(len(result["ticked"]), 1)
        self.assertEqual(len(result["unticked"]), len(need) - 1)
        self.assertFalse(result["complete"])

    def test_observations_before_the_clock_started_do_not_count(self):
        """The whole point of tying it to the clock: a path that fired last
        week says nothing about the code running now."""
        need = sorted(wpc.required())[0]
        self.write([{"at": "2026-07-01T00:00:00Z", "module": need[0],
                     "function": need[1], "primitive": need[2], "driven": False}])
        result = wpc.coverage("2026-08-01T00:00:00Z", self.log)
        self.assertEqual(result["ticked"], [])

    def test_a_driven_firing_is_labelled_as_driven(self):
        need = sorted(wpc.required())[0]
        self.write([{"at": "2026-09-01T00:00:00Z", "module": need[0],
                     "function": need[1], "primitive": need[2], "driven": True}])
        result = wpc.coverage("2026-08-01T00:00:00Z", self.log)
        self.assertEqual(len(result["driven"]), 1)

    def test_one_organic_firing_outweighs_a_driven_one(self):
        need = sorted(wpc.required())[0]
        self.write([{"at": "2026-09-01T00:00:00Z", "module": need[0],
                     "function": need[1], "primitive": need[2], "driven": True},
                    {"at": "2026-09-01T01:00:00Z", "module": need[0],
                     "function": need[1], "primitive": need[2], "driven": False}])
        result = wpc.coverage("2026-08-01T00:00:00Z", self.log)
        self.assertEqual(result["driven"], [])

    def test_complete_is_only_true_when_nothing_is_unticked(self):
        rows = [{"at": "2026-09-01T00:00:00Z", "module": m, "function": f,
                 "primitive": p, "driven": False} for (m, f, p) in wpc.required()]
        self.write(rows)
        result = wpc.coverage("2026-08-01T00:00:00Z", self.log)
        self.assertTrue(result["complete"])
        self.assertEqual(result["unticked"], [])

    def test_an_unparseable_line_is_surfaced_rather_than_dropped(self):
        self.log.write_text('{"at": "2026-09-01T00:00:00Z"}\n{truncated\n')
        rows = wpc.observations(self.log)
        self.assertTrue(any("unparseable" in r for r in rows))

    def test_the_log_path_follows_the_store_root(self):
        """Not a Path.home() constant computed at import. See the 2026-08-30
        incident: that is the shape that let a redirected process read the
        device's own credentials."""
        with mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": "/tmp/elsewhere"}):
            self.assertEqual(wpc.log_path(),
                             Path("/tmp/elsewhere/logs/write-paths.jsonl"))


if __name__ == "__main__":
    unittest.main()
