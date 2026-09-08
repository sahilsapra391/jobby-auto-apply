"""The watcher tick does not block on the provider registry's lock.

**`watch.tick` reaches this lock through `fingerprint` every five seconds**, and
it took `LOCK_EX` with no `LOCK_NB`. `ProviderRegistry.refresh` held that same
lock across its probes, bounded by `PROBE_TIMEOUT_SECONDS` (30) plus
`MODEL_PROBE_TIMEOUT_SECONDS` (120) for every connected provider: 150 seconds
each, 450 with three. `watch.py --status`, the command every deploy in this
repository is told to run first, took it too.

Two independent fixes, and this file covers both, because either alone leaves
the fault available. The writer no longer holds the lock over a network call.
The reader no longer waits.

**Every lock case here drives a real `flock` from a real second process.** A
mocked lock would prove the code calls a function; the property is about two
processes contending, and only two processes can show it.
"""

import fcntl
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import providers  # noqa: E402
import watch  # noqa: E402


#: `_normalize_state` drops any record without `connected: True`, so a fixture
#: missing it produces an empty registry that looks populated. The first
#: version of this file had exactly that and four cases passed against a
#: registry with nothing in it.
CONNECTED = {
    "version": None,  # filled in setUp from the module `watch` actually calls
    "nextConnectionOrder": 2,
    "connections": {"openai": {"connected": True, "connectionOrder": 1,
                               "health": "healthy",
                               "connectedAt": "2026-09-08T00:00:00Z",
                               "lastCheckedAt": "2026-09-08T00:00:00Z"}},
}

#: A real second process taking the real lock. It is handed the state file by
#: path rather than through the store root, for the same reason the fixture
#: patches `_registry`: `STATE_FILE` is bound at import and an environment
#: variable cannot move it.
READER = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[2])
    import providers
    reg = providers.ProviderRegistry(state_file=sys.argv[1])
    t = time.time()
    try:
        reg.public_status()
        print("returned %.2f" % (time.time() - t), flush=True)
    except Exception as exc:
        print("%s %.2f" % (type(exc).__name__, time.time() - t), flush=True)
""")


class HeldLockTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        os.environ["JOB_APPLICATION_DIR"] = str(self.root)
        self.addCleanup(os.environ.pop, "JOB_APPLICATION_DIR", None)
        self.state = self.root / "providers.json"
        # **Everything comes from `watch.providers`, including the registry.**
        # Four modules in this suite execute a `scripts/` module by hand and
        # assign it over its `sys.modules` entry, so there can be two live
        # `providers` module objects with two distinct `ProviderBusy` classes.
        # Building the registry from one and asserting the exception class of
        # the other passes alone and errors in a full run, which is what
        # happened: `assertRaises` saw a `ProviderBusy` that was not its
        # `ProviderBusy` and the `BlockingIOError` cause surfaced instead.
        self.providers = watch.providers
        self.registry = self.providers.ProviderRegistry(state_file=self.state)
        self.registry._prepare_directory()
        self.registry._write_locked(dict(CONNECTED, version=self.providers.STATE_VERSION))
        # **Patch the module `watch` actually calls, not the one this file
        # imported.** Four test modules in this suite execute a `scripts/`
        # module by hand and assign it over its `sys.modules` entry, so
        # `watch.providers` and the `providers` imported at the top of this
        # file can be different objects. Patching the wrong one leaves
        # `provider_signature` reading the developer's own registry: measured,
        # two cases here passed alone and failed in a full run, and the failure
        # was a *successful* read against a lock this test was holding.
        #
        # `providers.STATE_FILE` is a module constant bound at import, so no
        # environment variable can move it either.
        original = self.providers._registry
        self.providers._registry = (
            lambda: self.providers.ProviderRegistry(state_file=self.state))
        self.addCleanup(setattr, self.providers, "_registry", original)
        # Provenance, which the artifact cannot answer: prove the registry
        # `watch` will read is the one holding the lock this test takes.
        self.assertEqual(str(self.providers._registry().lock_file),
                         str(self.registry.lock_file),
                         "watch reads a different registry than this test locks")
        watch.BUSY_READS.clear()
        watch._last_provider_signature = None

    def hold(self, seconds=30):
        """Hold the registry's flock from another thread and prove it holds."""
        ready, release = threading.Event(), threading.Event()

        def run():
            fd = os.open(self.registry.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            ready.set()
            release.wait(seconds)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5), "the holder never acquired the lock")
        self.addCleanup(lambda: (release.set(), thread.join(5)))
        return release

    def test_a_held_lock_really_does_block_a_blocking_reader(self):
        """The control. Without it every case below could pass on a free lock.

        This is the anti-vacuity guard the rest of the file depends on: a test
        that holds a lock nothing contends for measures nothing, and looks
        exactly like one that does.
        """
        script = self.root / "reader.py"
        script.write_text(READER)
        self.hold()
        proc = subprocess.Popen(
            [sys.executable, str(script), str(self.state), str(REPO / "scripts")],
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)
        time.sleep(3)
        self.assertIsNone(proc.poll(),
                          "a blocking public_status returned while the lock was "
                          "held, so no case in this file is measuring contention")

    def test_the_signature_answers_immediately_while_the_lock_is_held(self):
        free = watch.provider_signature()
        self.assertNotEqual(free, "unreadable",
                            "the fixture produced no real signature, so the "
                            "comparison below would be vacuous")
        self.assertIn("openai", free)
        self.hold()
        began = time.monotonic()
        held = watch.provider_signature()
        self.assertLess(time.monotonic() - began, 1.0,
                        "the tick waited for the provider lock")
        self.assertEqual(held, free)

    def test_a_busy_lock_does_not_change_the_fingerprint(self):
        """A changed fingerprint is a deployment, and Vercel allows 100 a day.

        Returning "unreadable" would flip the signature on the way in and back
        on the way out: two publishes per contended probe, for a lock that was
        briefly busy.
        """
        free = watch.provider_signature()
        self.hold()
        self.assertEqual(watch.provider_signature(), free)

    def test_a_busy_lock_is_counted_rather_than_silent(self):
        watch.provider_signature()
        self.hold()
        watch.provider_signature()
        self.assertEqual(watch.BUSY_READS.get("provider"), 1)

    def test_a_free_lock_is_never_counted_busy(self):
        watch.provider_signature()
        watch.provider_signature()
        self.assertEqual(watch.BUSY_READS.get("provider"), None)

    def test_with_no_previous_read_a_busy_lock_says_unreadable(self):
        """There is no last-known value to fall back to, and inventing one
        would be a claim about provider state nobody has observed."""
        self.hold()
        self.assertEqual(watch.provider_signature(), "unreadable")

    def test_the_blocking_read_still_blocks_because_a_person_wants_an_answer(self):
        """`public_status` must NOT become non-blocking.

        The Settings lane asks a question a person is waiting on. Making the one
        method non-blocking would hand them "busy" instead of an answer, which
        is why there are two.
        """
        self.hold()
        with self.assertRaises(self.providers.ProviderBusy):
            self.registry.public_status_nowait()
        # That `public_status` itself still waits is the control case above,
        # which drives a real second process. Repeating the subprocess here
        # would spawn a second one per run for a property already proven, and a
        # full-suite run is where subprocess launches get expensive.

    def test_busy_is_its_own_class_and_not_an_unreadable_registry(self):
        # "could not look" and "is not there" are different facts, and only one
        # of them says anything about provider state.
        mod = self.providers
        self.assertTrue(issubclass(mod.ProviderBusy, mod.ProviderError))
        self.assertFalse(issubclass(mod.ProviderBusy, mod.ProviderStateError))


class ProbesOutsideTheLockTests(unittest.TestCase):
    """`refresh` must not hold the lock across a subprocess.

    This is the half that removes the 450-second hold that exists today. The
    reader fix alone would leave every other blocking caller waiting on it.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def registry(self, probe):
        reg = providers.ProviderRegistry(state_file=self.root / "providers.json",
                                         probe_fn=probe)
        reg._prepare_directory()
        reg._write_locked(dict(CONNECTED, version=providers.STATE_VERSION))
        self.assertIn("openai", reg._load_locked()["connections"],
                      "the fixture wrote a record `_normalize_state` dropped, "
                      "so `refresh` would have nothing to probe and every case "
                      "below would pass on an empty registry")
        return reg

    def test_the_lock_is_free_while_a_probe_runs(self):
        seen = {}

        def probe(provider_id):
            # Another process must be able to take the lock right now.
            fd = os.open(reg.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen["free"] = True
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                seen["free"] = False
            finally:
                os.close(fd)
            return providers.ProbeResult(provider_id, True, True, "healthy")

        reg = self.registry(probe)
        reg.refresh()
        self.assertTrue(seen.get("free"),
                        "the registry lock was held while a probe ran, which is "
                        "the 450-second stall this fix removes")

    def test_a_disconnect_during_the_probe_is_not_undone(self):
        """The second read is not belt and braces.

        Writing back a snapshot taken before the probes would resurrect a
        connection the user removed while they ran.
        """
        reg = None

        def probe(provider_id):
            state = reg._load_locked()
            state["connections"].pop("openai", None)
            reg._write_locked(state)
            return providers.ProbeResult(provider_id, True, True, "healthy")

        reg = self.registry(probe)
        reg.refresh()
        self.assertNotIn("openai", reg._load_locked()["connections"],
                         "refresh resurrected a provider disconnected while it "
                         "was probing")
