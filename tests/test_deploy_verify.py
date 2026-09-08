"""`deploy_verify` has to fail on the state production was actually in.

The four pulls of 2026-09-01 and 2026-09-02 landed with no restart and nothing
said anything for 35 hours. Every check anyone ran that day passed, because the
checks asked whether the checkout was current and the daemons hold whatever
they started on. So the property under test is not "does it run" but **does it
say no on the shape that went unnoticed**, and each case below is one way that
shape occurs.

The subject is deliberately the two pure functions. `pid_of` and `started_at`
shell out to `launchctl` and `ps`, so a test that drove `main()` would be
testing this machine; `stale_modules` and `reflog_between` take the timestamp
as an argument, which is what makes both halves checkable without one.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy_verify  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


class StaleModuleTests(unittest.TestCase):
    """A file newer than the process that imported it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="deployverify-"))
        (self.root / "scripts").mkdir()
        self.write("entry", "import helper\nimport untouched\n")
        self.write("helper", "x = 1\n")
        self.write("untouched", "y = 2\n")

    def write(self, name, body, mtime=None):
        path = self.root / "scripts" / f"{name}.py"
        path.write_text(body)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_a_module_written_after_the_service_started_is_stale(self):
        started = time.time() - 3600
        self.write("helper", "x = 2\n", mtime=time.time())
        found = {name: verdict
                 for name, _, verdict in deploy_verify.stale_modules(
                     self.root, "entry", started)}
        self.assertEqual(found.get("helper"), "stale")

    def test_a_module_older_than_the_service_is_not_reported(self):
        started = time.time()
        old = time.time() - 7200
        for name in ("entry", "helper", "untouched"):
            os.utime(self.root / "scripts" / f"{name}.py", (old, old))
        self.assertEqual(deploy_verify.stale_modules(self.root, "entry",
                                                     started), [])

    def test_the_closure_is_what_this_service_imports_and_not_the_tree(self):
        """A file the service never imports is not its problem.

        The gate list this replaces named the caller and not the code that
        writes, and nine green runs sat on the wrong subject for a day.
        """
        self.write("stranger", "z = 3\n", mtime=time.time())
        names = [n for n, _, _ in deploy_verify.stale_modules(
            self.root, "entry", time.time() - 3600)]
        self.assertNotIn("stranger", names)

    def test_a_write_inside_the_ps_resolution_is_indeterminate_not_fresh(self):
        """`ps -o lstart` prints whole seconds.

        A file written in the same second the process started cannot be ordered
        against it, and calling that fresh asserts something the clock cannot
        support. It stays a failure -- a check that cannot see is not a check
        that saw nothing -- but it says which it is.
        """
        now = time.time()
        self.write("helper", "x = 3\n", mtime=now)
        found = {name: verdict
                 for name, _, verdict in deploy_verify.stale_modules(
                     self.root, "entry", now)}
        self.assertEqual(found.get("helper"), "indeterminate")

    def test_a_module_with_no_file_leaves_the_closure_rather_than_reporting(self):
        """Worth pinning because it is the opposite of what the code reads like.

        `stale_modules` has an `unreadable` branch and a deleted module never
        reaches it: `closure` is built from a glob of `scripts/*.py`, so a name
        with no file is filtered out one step earlier. The branch guards the
        race between that glob and the `stat`, which is real and narrow, and it
        is not the case a reader would assume it covers.

        The absence is not a gap in this check. A service whose import is
        missing does not start, and `pid_of` reporting it as not running is the
        louder and earlier signal.
        """
        (self.root / "scripts" / "helper.py").unlink()
        names = [n for n, _, _ in deploy_verify.stale_modules(
            self.root, "entry", time.time() - 3600)]
        self.assertNotIn("helper", names)


class ReflogWindowTests(unittest.TestCase):
    """Did the tree move between the pull and the restarts.

    On 2026-08-28 two sessions each checked that their services postdated the
    entry they expected, and both were right, and production still ended on two
    commits at once. The question that catches it is whether anything landed
    *after* a service came up, which is this.
    """

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="deployverify-git-"))
        git(self.repo, "init", "-q", ".")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("one\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-qm", "one")

    def test_a_commit_after_the_start_time_is_reported(self):
        started = time.time() - 60
        (self.repo / "a.txt").write_text("two\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-qm", "two")
        moves = deploy_verify.reflog_between(self.repo, started,
                                             time.time() + 5)
        self.assertTrue(moves, "a HEAD move after the service started must be "
                               "reported; this is the case that went unnoticed "
                               "for 35 hours")

    def test_nothing_is_reported_when_the_tree_held_still(self):
        moves = deploy_verify.reflog_between(self.repo, time.time() + 1,
                                             time.time() + 5)
        self.assertEqual(moves, [])

    def test_an_unreadable_repo_is_none_rather_than_empty(self):
        """`None` and `[]` are different claims and only one is good news.

        "nothing moved" and "I could not look" reaching the caller as the same
        value is how a check ends up silent about the thing it exists for.
        """
        self.assertIsNone(
            deploy_verify.reflog_between(Path(tempfile.mkdtemp()), 0,
                                         time.time()))


class ServiceEntryTests(unittest.TestCase):
    """What a service runs is read off its own plist, never restated here.

    **The map was wrong about a third of the fleet and nothing could see it.**
    `ai.jobby.providers` executes `watch.py --provider-controls`; `SERVICES`
    said `provider_login`. Measured 2026-09-08 against the real closures: 6
    modules checked, 40 held resident, so 34 were never compared against that
    service's start time -- `watch` itself, `cloud_sweep`, `dual_write`,
    `class_b`, `jobs_store`, `store`, `ingest`. A deploy touching any of them
    that left the service unrestarted verified clean.

    Same shape as `parity_clock.GATE_PATHS` naming the caller rather than the
    code that writes, and defended by a test asserting the list. So this file
    asserts the derivation and a floor, and never the mapping.
    """

    def plist(self, tmp, label, args):
        import plistlib
        (tmp / f"{label}.plist").write_bytes(
            plistlib.dumps({"Label": label, "ProgramArguments": args}))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.original = deploy_verify.LAUNCH_AGENTS
        deploy_verify.LAUNCH_AGENTS = self.tmp
        self.addCleanup(setattr, deploy_verify, "LAUNCH_AGENTS", self.original)

    def test_the_entry_is_the_script_the_plist_actually_names(self):
        self.plist(self.tmp, "ai.jobby.providers",
                   ["/usr/bin/python3", "/repo/scripts/watch.py",
                    "--provider-controls", "--interval", "2"])
        self.assertEqual(deploy_verify.entry_for("ai.jobby.providers"), "watch")

    def test_a_flag_that_looks_like_a_script_is_not_taken_as_one(self):
        self.plist(self.tmp, "ai.jobby.watch",
                   ["/usr/bin/python3", "/repo/scripts/watch.py", "--daemon"])
        self.assertEqual(deploy_verify.entry_for("ai.jobby.watch"), "watch")

    def test_a_missing_plist_is_none_and_not_a_guess(self):
        self.assertIsNone(deploy_verify.entry_for("ai.jobby.nothing"))

    def test_a_plist_naming_no_script_is_none_rather_than_empty(self):
        # "could not look" is not "nothing to check". An empty closure would
        # report zero stale modules, which reads as fresh.
        self.plist(self.tmp, "ai.jobby.odd", ["/usr/bin/true"])
        self.assertIsNone(deploy_verify.entry_for("ai.jobby.odd"))

    def test_services_is_a_floor_of_labels_and_not_a_mapping(self):
        # A mapping is a second implementation of what the plists already say,
        # and it is the copy that goes stale.
        self.assertNotIsInstance(deploy_verify.SERVICES, dict)
        for label in ("ai.jobby.watch", "ai.jobby.documents", "ai.jobby.providers"):
            self.assertIn(label, deploy_verify.SERVICES)

    def test_the_closure_of_the_real_entry_is_the_larger_one(self):
        """The measurement that made this a bug, as a test.

        Not a literal count, which would rot. The property is that the entry
        read off the plist reaches strictly more than the name that was
        hardcoded, so a regression to any hand-written map fails here.
        """
        repo = Path(__file__).resolve().parents[1]
        actual = deploy_verify.closure(repo, "watch")
        hardcoded = deploy_verify.closure(repo, "provider_login")
        self.assertGreater(len(actual), len(hardcoded))
        for name in ("watch", "cloud_sweep", "jobs_store", "store"):
            self.assertIn(name, actual)
            self.assertNotIn(name, hardcoded)
