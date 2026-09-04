"""A signal must clear the staged PDFs, and the evidence is the filesystem.

`run_apply._install_discard_signal_handlers` exists because a `finally` block
does not run when a process is terminated by a signal, and a signal is exactly
how a mid-flight run ends: `drop.py` sends SIGTERM to the runner's process
group. Without the handler, cancelling an application leaves that application's
resume and cover letter staged on disk, bound by path, inode, size and hash to
a run that is over.

The handler was written 2026-08-28 and had never been observed firing. It was
driven by hand on 2026-09-03 and it works. This is that reading, kept, because
`docs/plans/jobby-v2-rebuild-plan.md` Phase 4 lists exactly this as a negative
test to write, and a guard nobody runs is a guard that decays.

Two things this test does deliberately.

It sends a **real signal to a real process** and then reads the directory. A
mocked handler proves the function was called, which is not the claim; the
claim is that files are gone from disk afterwards, and only the filesystem can
answer that. `docs/adr/0008-one-device-process.md` says the same thing about
the same code.

And it asserts what is **kept** as well as what is removed. `activity.json` is
written into the staging directory by the firewall and is what tells a
capability failure apart from a real park, so a cleanup that took it would
trade a duplicate PDF for the ability to say why a run stopped. A test that
only checked the PDFs were gone would pass on a handler that deleted
everything.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The child stages two documents and one firewall record, installs the real
# handlers, and then does nothing until it is signalled. `_STAGED` holds paths
# that are already resolved, so a temporary directory is a complete fixture and
# nothing here reaches the device's own store.
CHILD = """
import pathlib, sys, time
sys.path.insert(0, %(scripts)r)
import browser, run_apply
target = pathlib.Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
(target / "resume.pdf").write_bytes(b"%%PDF-1.4 staged")
(target / "cover-letter.pdf").write_bytes(b"%%PDF-1.4 staged")
(target / "activity.json").write_text('{"browser_find": 1}')
browser._STAGED.append(target)
%(install)s
sys.stdout.write("ready\\n")
sys.stdout.flush()
# A deadline, because a fixture that waits forever becomes an orphan the
# first time anything goes wrong. The parent's `finally` kills this child and
# is correct; no `finally` survives the parent being SIGKILLed, which is what
# happens when a run hangs and somebody stops it. One was found spinning at
# 20 wakeups a second, 34 hours after the run that made it, staging files into
# a temp directory that no longer existed.
deadline = time.time() + 120
while time.time() < deadline:
    time.sleep(0.05)
sys.exit("the signal never arrived; this fixture is not going to wait forever")
"""

INSTALL = "run_apply._install_discard_signal_handlers()"


class SignalClearsStagedDocumentsTests(unittest.TestCase):

    def _run(self, staging, install=INSTALL, number=signal.SIGTERM):
        """Start the child, wait until it has staged, signal it, return the code."""
        source = CHILD % {"scripts": str(REPO / "scripts"), "install": install}
        env = dict(os.environ)
        # The store is not this test's subject and must not be this test's
        # casualty. `browser.ARTIFACTS` derives from `Path.home()` at import.
        env["HOME"] = tempfile.mkdtemp(prefix="jobby-signal-home-")
        env["JOB_APPLICATION_DIR"] = str(Path(env["HOME"]) / ".job-application")
        child = subprocess.Popen(
            [sys.executable, "-c", source, str(staging)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            line = child.stdout.readline()
            if line.strip() != "ready":
                # Only now is it safe to read stderr: the child holds it open
                # for the life of its sleep loop, so an eager read here blocks
                # forever and the test hangs instead of failing. Kill first,
                # then read what it managed to say.
                child.kill()
                self.fail("the child never staged; stderr was:\n%s"
                          % child.communicate()[1])
            # The handler is installed before "ready" is printed, so there is no
            # window here. The wait is for the interpreter to reach the sleep.
            deadline = time.time() + 5
            while time.time() < deadline and not (staging / "resume.pdf").exists():
                time.sleep(0.02)
            child.send_signal(number)
            child.wait(timeout=30)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            # A suite of three thousand tests cannot afford a module that leaks
            # two descriptors per case.
            for pipe in (child.stdout, child.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        return child.returncode

    def test_sigterm_removes_the_staged_documents_from_disk(self):
        with tempfile.TemporaryDirectory() as root:
            staging = Path(root) / "uploads" / "endtoend"
            code = self._run(staging)
            left = sorted(p.name for p in staging.iterdir())
            self.assertNotIn("resume.pdf", left)
            self.assertNotIn("cover-letter.pdf", left)
            self.assertEqual(["activity.json"], left,
                             "the firewall's own record must survive the cleanup")
            self.assertEqual(-signal.SIGTERM, code,
                             "the handler must restore the default disposition and "
                             "re-raise, so the exit status still reports the signal")

    def test_sigint_and_sighup_do_the_same(self):
        for number in (signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=number), tempfile.TemporaryDirectory() as root:
                staging = Path(root) / "uploads" / "endtoend"
                code = self._run(staging, number=number)
                left = sorted(p.name for p in staging.iterdir())
                self.assertEqual(["activity.json"], left)
                self.assertEqual(-number, code)

    def test_without_the_handler_the_documents_survive(self):
        """The mutation. Without this the test above passes on a no-op handler.

        Everything else is identical, so a failure here means the assertion in
        the first test is measuring the handler and not the temporary directory
        being cleaned up by something else.
        """
        with tempfile.TemporaryDirectory() as root:
            staging = Path(root) / "uploads" / "endtoend"
            code = self._run(staging, install="pass  # handler deliberately absent")
            left = sorted(p.name for p in staging.iterdir())
            self.assertEqual(
                ["activity.json", "cover-letter.pdf", "resume.pdf"], left,
                "with no handler installed a signal must leave the staged "
                "documents in place -- if it does not, the first test is "
                "passing for a reason that has nothing to do with the handler")
            self.assertEqual(-signal.SIGTERM, code)


if __name__ == "__main__":
    unittest.main()
