"""A slow publish must not freeze the whole watcher.

The tick is single-threaded. `publish()` shells out to sync_board.py through
`watch.run`, and `run` had no timeout, so a publish that hung on Cloudflare KV
or Vercel took the queue, Apply and document polling down with it. Measured on
2026-08-25: the watcher lock sat frozen for nineteen minutes with an Apply
waiting behind it, and the user-visible symptom was a button that did nothing.

The queue.mjs calls in watch.py have carried timeout=60 all along. `run` was
simply the one that never got it.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watch = load("watch")


def alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, TypeError):
        return False
    return True


class RunStillWorksTests(unittest.TestCase):
    def test_a_quick_helper_returns_its_output_unchanged(self):
        r = watch.run([sys.executable, "-c", "print('hello')"])
        self.assertEqual(0, r.returncode)
        self.assertEqual("hello", r.stdout.strip())

    def test_stderr_is_still_captured_separately(self):
        r = watch.run([sys.executable, "-c",
                       "import sys; sys.stderr.write('boom'); sys.exit(3)"])
        self.assertEqual(3, r.returncode)
        self.assertEqual("boom", r.stderr.strip())

    def test_input_is_still_delivered_on_stdin(self):
        # watch.py line ~403 saves browser ownership with input=json.dumps(...),
        # so losing this would silently break slot affinity.
        r = watch.run([sys.executable, "-c",
                       "import sys; sys.stdout.write(sys.stdin.read().upper())"],
                      input="ownership")
        self.assertEqual(0, r.returncode)
        self.assertEqual("OWNERSHIP", r.stdout.strip())


class TimeoutTests(unittest.TestCase):
    def test_a_hung_helper_comes_back_as_a_failure_not_a_hang(self):
        started = time.monotonic()
        r = watch.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
        self.assertLess(time.monotonic() - started, 15, "run did not give up")
        self.assertEqual(watch.TIMED_OUT, r.returncode)
        self.assertIn("was killed", r.stderr)

    def test_a_timeout_does_not_raise(self):
        # Every caller checks returncode; none of them expects an exception, and
        # a raise here would turn one slow publish into a crashed tick.
        try:
            watch.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"run raised {type(exc).__name__} instead of reporting")

    def test_the_helpers_own_children_are_killed_too(self):
        """The whole point. What hung was node, not sync_board.py.

        subprocess.run's own timeout reaps the direct child and leaves its
        children running, so the hung connection stays open and the next tick
        stacks a second one on top of it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = pathlib.Path(tmp) / "grandchild.pid"
            script = (
                "import subprocess, sys, time\n"
                "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pidfile)!r}, 'w').write(str(g.pid))\n"
                "time.sleep(60)\n"
            )
            r = watch.run([sys.executable, "-c", script], timeout=3)
            self.assertEqual(watch.TIMED_OUT, r.returncode)

            for _ in range(50):
                if pidfile.exists():
                    break
                time.sleep(0.1)
            self.assertTrue(pidfile.exists(), "the grandchild never started")
            grandchild = int(pidfile.read_text().strip())

            for _ in range(50):
                if not alive(grandchild):
                    break
                time.sleep(0.1)
            if alive(grandchild):
                os.kill(grandchild, signal.SIGKILL)
                self.fail("the grandchild survived the timeout")

    def test_the_default_is_generous_enough_not_to_bite_real_work(self):
        # A tick runs every few seconds. Five minutes is only ever reached by
        # something genuinely hung, never by a helper doing its job.
        self.assertGreaterEqual(watch.SUBPROCESS_TIMEOUT_SECONDS, 120)
        self.assertLessEqual(watch.PUBLISH_TIMEOUT_SECONDS,
                             watch.SUBPROCESS_TIMEOUT_SECONDS)


class PublishReportsTheTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = pathlib.Path(self.tmp.name)
        for name in ("STATE_DIR", "LAST_DEPLOY"):
            self.addCleanup(setattr, watch, name, getattr(watch, name))
        watch.STATE_DIR = self.state
        watch.LAST_DEPLOY = self.state / "last-deploy"

    def test_a_publish_that_times_out_says_so_instead_of_blocking(self):
        calls = {}
        original = watch.run

        def slow(args, timeout=watch.SUBPROCESS_TIMEOUT_SECONDS, **kw):
            calls["timeout"] = timeout
            return subprocess.CompletedProcess(
                args, watch.TIMED_OUT, "", f"gave no result within {timeout:g}s and was killed")

        watch.run = slow
        try:
            with self.assertRaises(watch.TickError) as caught:
                watch.publish()
        finally:
            watch.run = original
        self.assertIn("was killed", str(caught.exception))
        self.assertEqual(watch.PUBLISH_TIMEOUT_SECONDS, calls["timeout"],
                         "publish must pass its own bound, not the default")

    def test_a_timed_out_publish_is_not_recorded_as_a_deployment(self):
        # Stamping last-deploy would make the next tick defer on the floor and
        # hide the failure behind "too soon after the last deployment".
        original = watch.run
        watch.run = lambda args, timeout=None, **kw: subprocess.CompletedProcess(
            args, watch.TIMED_OUT, "", "gave no result within 120s and was killed")
        try:
            with self.assertRaises(watch.TickError):
                watch.publish()
        finally:
            watch.run = original
        self.assertFalse(watch.LAST_DEPLOY.exists())
