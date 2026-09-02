"""The gate's stopwatch: the two percentiles, and what a run may claim.

The percentile is written twice, in `apps/web/app/_ui/measure.ts` for the
browser and `scripts/gate_latency.py` for the recorder, because one runs where
the clicks are and the other where the file is written. That is the shape
`LogoContractTests` exists for, so the two are compared over every (p, n) pair
below 200 samples rather than each being read and agreed with.

**It found a real disagreement on the first run**: 27 of 19,800 pairs, because
`(p / 100) * n` makes `0.07 * 100` into 7.000000000000001 and `Math.ceil` takes
it to 8. Restating the rule in a comment would have caught none of them.
"""

import json
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import store_isolation  # noqa: E402

import gate_latency  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MEASURE = ROOT / "apps" / "web" / "app" / "_ui" / "measure.ts"


def setUpModule():
    store_isolation.begin(gate_latency)


def tearDownModule():
    # **Paired, or the next module inherits a redirect it did not ask for.**
    # Leaving it open passed this file and failed two of
    # `test_store_isolation`'s own tests in a full run, which is the
    # test-order dependency CLAUDE.md already records about `sys.modules`.
    store_isolation.end()


def sample(kind, lane, ms):
    return {"kind": kind, "lane": lane, "ms": ms, "at": 0}


def complete_run(ms=10):
    """A run with every required kind on every lane, all comfortably inside."""
    return [sample(kind, lane, ms)
            for kind in gate_latency.REQUIRED_KINDS
            for lane in gate_latency.lanes()]


class TheTwoPercentilesAgree(unittest.TestCase):
    """One rule, two languages, compared by output."""

    @classmethod
    def setUpClass(cls):
        script = (
            'import { percentile } from "%s";\n'
            "const out = [];\n"
            "for (let p = 1; p <= 99; p++) for (let n = 1; n <= 200; n++)\n"
            "  out.push(percentile("
            "Array.from({length: n}, (_, i) => i + 1), p));\n"
            "console.log(JSON.stringify(out));\n" % MEASURE.as_posix()
        )
        try:
            done = subprocess.run(
                [
                    "node",
                    "--no-warnings",
                    "--input-type=module",
                    "-e",
                    script,
                ],
                capture_output=True, text=True, timeout=120, cwd=ROOT)
        except (OSError, subprocess.SubprocessError) as err:  # pragma: no cover
            raise unittest.SkipTest(f"node would not run: {err}")
        if done.returncode != 0:  # pragma: no cover
            raise unittest.SkipTest(
                "node could not strip types from measure.ts, which needs "
                f"node 22.18 or newer:\n{done.stderr[-400:]}")
        cls.js = json.loads(done.stdout)

    def test_every_percentile_and_length_agrees(self):
        i, divergences = 0, []
        for p in range(1, 100):
            for n in range(1, 201):
                mine = gate_latency.percentile(list(range(1, n + 1)), p)
                if mine != self.js[i]:
                    divergences.append((p, n, self.js[i], mine))
                i += 1
        self.assertEqual(i, 19800, "the enumeration itself stopped matching")
        self.assertEqual(
            divergences, [],
            "the browser and the recorder disagree about a percentile, so the "
            "number a run reports depends on which half of the rig produced "
            "it. Multiply before dividing on both sides.")

    def test_the_comparison_can_fail(self):
        """The control. An enumeration that compares nothing passes silently."""
        wrong = [v + 1 for v in self.js]
        divergences = [
            1 for i, (p, n) in enumerate(
                (p, n) for p in range(1, 100) for n in range(1, 201))
            if gate_latency.percentile(list(range(1, n + 1)), p) != wrong[i]
        ]
        self.assertEqual(
            len(divergences), 19800,
            "shifting every JS answer by one must disagree everywhere; if it "
            "does not, the loop above is not comparing what it claims to")


class APercentileIsNearestRank(unittest.TestCase):
    def test_the_edges(self):
        self.assertTrue(gate_latency.percentile([], 95) != gate_latency.percentile([], 95),
                        "an empty run is NaN, not zero: zero would pass the gate")
        self.assertEqual(gate_latency.percentile([7], 95), 7)
        self.assertEqual(gate_latency.percentile([1, 2, 3, 4], 100), 4)
        self.assertEqual(gate_latency.percentile([1, 2, 3, 4], 1), 1)

    def test_it_does_not_mutate_what_it_is_given(self):
        values = [3, 1, 2]
        gate_latency.percentile(values, 50)
        self.assertEqual(values, [3, 1, 2])


class LanesAreNeverPooled(unittest.TestCase):
    """The gate says "across all lanes", which is the worst one."""

    #: The lane that hides is the one with few samples, not the one that is
    #: slowest. A percentile keeps a slow tail as long as the tail is more than
    #: five percent of the pool; a lane contributing less than that can be
    #: entirely above budget and leave the pooled number untouched. That is the
    #: realistic case here rather than a contrived one, because the quiet lanes
    #: are exactly the ones a reader clicks least: In flight held 0 rows on
    #: 2026-08-31 and Watching held 17, against Found's 17,341.
    SLOW = [sample("apply-event", "flight", 400)] * 5
    FAST = [sample("apply-event", lane, 20)
            for lane in ("found", "watch", "act", "done") for _ in range(50)]

    def test_a_quiet_slow_lane_is_not_pooled_away(self):
        rows = gate_latency.summarise(self.SLOW + self.FAST)
        flight = next(r for r in rows if r["lane"] == "flight")
        self.assertEqual(flight["p95"], 400)
        state, why = gate_latency.verdict(rows, required=("apply-event",))
        self.assertIn(state, ("fail", "incomplete"))
        self.assertEqual([r["lane"] for r in why["over"]], ["flight"],
                         "one lane over budget is the verdict, and it is the "
                         "lane nobody clicked that goes missing when pooled")

    def test_pooling_them_would_have_passed(self):
        """The control, which is the number that makes the rule worth having.

        If the pooled p95 were over budget anyway, the test above would be
        satisfied by a rig that pooled everything, and would say nothing.
        """
        pooled = [s["ms"] for s in self.SLOW + self.FAST]
        self.assertLess(gate_latency.percentile(pooled, 95),
                        gate_latency.BUDGET_MS)
        self.assertEqual(max(pooled), 400,
                         "and the 400 ms lane really is in the pool being "
                         "measured, not absent from it")

    def test_rows_are_worst_first(self):
        rows = gate_latency.summarise(
            [sample("a", "x", 5), sample("b", "y", 500), sample("c", "z", 50)])
        self.assertEqual([r["lane"] for r in rows], ["y", "z", "x"])


class IncompleteIsNeverAPass(unittest.TestCase):
    def test_a_missing_kind_is_incomplete_and_says_which(self):
        rows = gate_latency.summarise(
            [s for s in complete_run() if s["kind"] != "write-optimistic"])
        state, why = gate_latency.verdict(rows)
        self.assertEqual(state, "incomplete")
        self.assertEqual(why["missingKinds"], ["write-optimistic"])

    def test_a_missing_lane_is_incomplete_too(self):
        rows = gate_latency.summarise(
            [s for s in complete_run() if s["lane"] != "dropped"])
        state, why = gate_latency.verdict(rows)
        self.assertEqual(state, "incomplete")
        self.assertEqual(why["missingLanes"], ["dropped"])

    def test_the_lanes_come_from_the_generator_not_a_list_here(self):
        import v2_lanes
        self.assertEqual(gate_latency.lanes(), tuple(v2_lanes.LANES),
                         "a lane added to the board and not to the rig is a "
                         "lane nobody measured, reported as complete")

    def test_a_complete_run_inside_budget_passes(self):
        """The control. Every assertion above would hold for a rig that only
        ever answered incomplete."""
        state, why = gate_latency.verdict(gate_latency.summarise(complete_run()))
        self.assertEqual(state, "pass")
        self.assertEqual(why["over"], [])

    def test_a_complete_run_over_budget_fails(self):
        run = complete_run()
        run.append(sample("apply-event", "found", gate_latency.BUDGET_MS + 1))
        state, _ = gate_latency.verdict(gate_latency.summarise(run))
        self.assertEqual(state, "fail")

    def test_exactly_the_budget_passes(self):
        run = [sample(k, l, gate_latency.BUDGET_MS)
               for k in gate_latency.REQUIRED_KINDS for l in gate_latency.lanes()]
        state, _ = gate_latency.verdict(gate_latency.summarise(run))
        self.assertEqual(state, "pass", "under 250 is inclusive at 250 here, "
                                        "and that is a choice worth pinning")


class ColdIsRecordedAndNotGatedOn(unittest.TestCase):
    """A board's first clicks are a real experience and a different one."""

    def test_a_slow_cold_number_does_not_fail_the_gate(self):
        run = complete_run()
        run += [sample("apply-event-cold", "found", 800)] * 10
        state, why = gate_latency.verdict(gate_latency.summarise(run))
        self.assertEqual(state, "pass",
                         "hydration, image loads and a cold route handler are "
                         "not what the gate is asking about")
        self.assertEqual(why["over"], [])

    def test_but_it_is_still_in_the_table(self):
        run = complete_run() + [sample("apply-event-cold", "found", 800)] * 10
        rows = gate_latency.summarise(run)
        text = gate_latency.render(rows, *gate_latency.verdict(rows), "", "x")
        self.assertIn("apply-event-cold", text,
                      "recorded and not gated on, not dropped: a reader meets "
                      "it once per page load")

    def test_the_warm_number_is_still_gated_on(self):
        """The control. Excusing the cold kind must not excuse its neighbour."""
        run = complete_run()
        run += [sample("apply-event", "found", 800)] * 10
        state, _ = gate_latency.verdict(gate_latency.summarise(run))
        self.assertEqual(state, "fail")

    def test_the_two_lists_do_not_overlap(self):
        self.assertEqual(
            set(gate_latency.REQUIRED_KINDS) & set(gate_latency.INFORMATIONAL_KINDS),
            set(), "a kind that is both required and excused is excused")


class NoSourceFileCarriesAnInvisibleByte(unittest.TestCase):
    """A control character in source is a defect nobody can see.

    `measure.ts` grouped its samples on a literal NUL between the kind and the
    lane. The code was correct, both halves used the same byte, and every test
    passed. It surfaced only because `grep` refused the file as binary in the
    middle of a mutation run. An editor draws nothing, a review sees nothing,
    and any tool that normalises the file can silently change what the key is.
    """

    #: Tab and newline are ordinary whitespace; nothing else below space is.
    FORBIDDEN = {c for c in range(0x20)} - {0x09, 0x0A, 0x0D}

    def test_the_v2_source_tree_is_plain_text(self):
        root = ROOT / "apps" / "web" / "app"
        checked, bad = 0, []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in (".ts", ".tsx", ".css"):
                continue
            checked += 1
            data = path.read_bytes()
            found = sorted({b for b in data if b in self.FORBIDDEN})
            if found:
                where = data.split(b"\n")
                lines = [i + 1 for i, l in enumerate(where)
                         if any(b in self.FORBIDDEN for b in l)]
                bad.append((path.relative_to(ROOT), [hex(b) for b in found], lines))
        self.assertGreater(checked, 10, "the walk stopped finding source files")
        self.assertEqual(bad, [],
                         "a control character in source is invisible in an "
                         "editor and turns the file binary to grep and diff; "
                         "write it as an escape instead")

    def test_the_walk_would_notice_one(self):
        """The control. A scan that inspects nothing passes on any tree."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp) / "x.ts"
            f.write_bytes(b"const k = `a\x00b`;\n")
            data = f.read_bytes()
            self.assertTrue(any(b in self.FORBIDDEN for b in data),
                            "the forbidden set stopped matching a real NUL")


class TheRecordedFileSaysWhatWasNotMeasured(unittest.TestCase):
    def test_an_incomplete_run_names_the_missing_kinds_in_the_file(self):
        rows = gate_latency.summarise(
            [s for s in complete_run()
             if not s["kind"].startswith("write-")])
        state, why = gate_latency.verdict(rows)
        text = gate_latency.render(rows, state, why, "", "2026-09-02T00:00Z")
        self.assertIn("INCOMPLETE", text)
        self.assertIn("write-optimistic", text)
        self.assertIn("write-ack", text)
        self.assertIn("live_board", text,
                      "the reason the write half is missing belongs in the "
                      "file, or the next reader re-derives it")
        self.assertNotIn("PASS", text)

    def test_recording_an_incomplete_run_exits_nonzero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            samples = pathlib.Path(tmp) / "s.json"
            out = pathlib.Path(tmp) / "out.md"
            samples.write_text(json.dumps(
                [s for s in complete_run() if s["kind"] != "write-ack"]))
            code = gate_latency.main(
                ["record", str(samples), "--out", str(out)])
            self.assertEqual(code, 1, "an incomplete run must not exit 0: a "
                                      "zero exit is what a caller reads as a "
                                      "gate that closed")
            self.assertIn("INCOMPLETE", out.read_text())

    def test_recording_a_complete_run_exits_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            samples = pathlib.Path(tmp) / "s.json"
            out = pathlib.Path(tmp) / "out.md"
            samples.write_text(json.dumps(complete_run()))
            self.assertEqual(
                gate_latency.main(["record", str(samples), "--out", str(out)]), 0)
            self.assertIn("PASS", out.read_text())


if __name__ == "__main__":
    unittest.main()
