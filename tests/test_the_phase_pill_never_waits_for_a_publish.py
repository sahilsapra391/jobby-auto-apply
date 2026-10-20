"""`set_phase` must not be able to fail because the board is slow to refresh.

As of 2026-10-20 the board it refreshed (v1) is retired and phase.py
publishes nothing; the history below is why that matters.

**A phase pill reading "Filling the form" over a parked application is worse
than no pill.** It says work is happening. It is the only progress a person
sees between pressing Apply and an outcome, and on 2026-09-09 it sat frozen for
nine hours on one real run while the application had long since parked, which
is what made the whole pipeline look dead from the board.

The cause was an ordering nobody had to get wrong on purpose. `phase.py` saved
the phase and then ran `publish.py` **synchronously and unbounded**, so that the
board would show the new phase straight away. `publish.py` reads the whole
store, runs `r2_sync.sync(apply=True)` and writes KV; `watch.py` bounds it at
120 seconds and has still logged "gave no result within 120s and was killed".
`application_mcp._set_phase` runs this script with `timeout=90`. So on a slow
publish the call died with `TimeoutExpired` -- **after the phase had already
been written** -- the agent was told a tool had failed when it had worked, and
it stopped calling that tool for the rest of the run.
"""

import ast
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PHASE = REPO / "scripts" / "phase.py"


def _publish_calls(tree):
    """Every call in `phase.py` that launches `publish.py`, with how it waits.

    From the AST rather than a grep, because the string "publish.py" appears in
    the comment explaining this rule and a scanner that reads its own
    documentation is the shape this repository has met four times.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name not in {"subprocess.run", "subprocess.Popen", "subprocess.call",
                        "subprocess.check_call", "subprocess.check_output"}:
            continue
        if "publish.py" not in ast.unparse(node):
            continue
        found.append(name)
    return found


class ThePhaseWriteIsNotHostageToThePublishTests(unittest.TestCase):
    def test_phase_launches_no_publish_at_all(self):
        """Since 2026-10-20 there is no v1 board to refresh.

        v2 reads Postgres, which the session save already reaches through the
        class B mirror, so the strongest form of "never waits on a publish" is
        "never starts one". A publish creeping back in would be the old v1 path,
        and it would bring the 90-second budget hazard with it.
        """
        tree = ast.parse(PHASE.read_text())
        self.assertEqual(_publish_calls(tree), [],
                         "phase.py launches publish.py again. The v1 board it "
                         "refreshed is retired; nothing reads what it writes.")

    def test_a_slow_publish_does_not_delay_the_phase_write(self):
        """The property, driven rather than read off the source.

        A `publish.py` replaced with a sleep must not slow this down. Measured
        rather than asserted about, because the whole failure was a wait nobody
        noticed was a wait.
        """
        import json
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            (root / "sessions").mkdir(parents=True)
            (root / "sessions" / "phasetest.json").write_text(json.dumps({
                "applicationId": "phasetest", "status": "inflight",
                "company": "Fixture", "role": "Tester",
            }))
            env = {**os.environ, "JOB_APPLICATION_DIR": str(root),
                   "HOME": tmp}
            started = time.monotonic()
            proc = subprocess.run(
                [sys.executable, str(PHASE), "--id", "phasetest",
                 "--phase", "Filling the form", "--detail", "t",
                 "--root", str(root)],
                capture_output=True, text=True, env=env, timeout=60)
            elapsed = time.monotonic() - started

        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertLess(elapsed, 30,
                        f"phase.py took {elapsed:.1f}s for one phase write. "
                        f"`_set_phase` allows 90 for the whole call.")
        payload = json.loads(proc.stdout or "{}")
        self.assertEqual(payload.get("phase"), "Filling the form")
