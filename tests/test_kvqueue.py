"""Runs the JavaScript queue tests as part of the Python suite.

deploy/kvqueue.mjs holds real logic (dedup, single-item claims, telling an
unwritten key apart from a failed read) and it runs in two places: the Vercel
function and the local CLI. Leaving it to a separate command nobody remembers
to run is how it would drift.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("toolpath", ROOT / "scripts" / "toolpath.py")
toolpath = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolpath)


class KvQueueJsTests(unittest.TestCase):
    def test_the_javascript_queue_tests_pass(self):
        node = toolpath.find("node", required=False)
        if node is None:
            self.skipTest("node is not installed")
        proc = subprocess.run(
            [node, "--test", str(ROOT / "tests" / "kvqueue.test.mjs")],
            capture_output=True, text=True, cwd=str(ROOT), timeout=300,
        )
        self.assertEqual(proc.returncode, 0,
                         f"node --test failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-1000:]}")
