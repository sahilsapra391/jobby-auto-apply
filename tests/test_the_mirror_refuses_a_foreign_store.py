"""The mirror refuses a write that is not to the device's own store.

Four separate holes on 2026-08-30 were one property: a process reaching the
device's database while whoever launched it believed it was isolated. Each was
patched at the caller, and each patch closed that hole and waited for the next.

The last one was found by instrumenting `dual_write.resolved_url` to log a
stack trace whenever it returned the device URL, then running the suite: 249
resolutions, 152 of them from `python3 scripts/store.py --root <tmpdir>` with
`JOB_APPLICATION_DIR` unset. `store.py` takes `--root` as an argument without
exporting it, so the child wrote a temporary store and read credentials from
the real `config.env`. `finish.py` exports its `--root`, which is the only
reason that path was already closed.

So the refusal is at the write, the shape `provider_usage.record` established:
a write to somebody's temporary directory is not a write to the store this
database mirrors, whatever the process believes its root to be. Every caller is
covered, including the ones nobody has found.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import class_b  # noqa: E402
import store    # noqa: E402


class ItKnowsWhichStoreIsTheDevices(unittest.TestCase):
    def test_a_path_inside_the_device_store_belongs(self):
        root = Path(store.root_dir(None))
        self.assertTrue(class_b.writes_the_device_store(root / "answers.json"))
        self.assertTrue(class_b.writes_the_device_store(
            root / "sessions" / "abc.json"))

    def test_a_path_in_somebody_elses_directory_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(
                class_b.writes_the_device_store(Path(d) / "answers.json"))

    def test_the_device_root_itself_belongs(self):
        self.assertTrue(class_b.writes_the_device_store(store.root_dir(None)))

    def test_a_redirected_root_moves_what_belongs(self):
        """`JOB_APPLICATION_DIR` is how a redirected process declares its
        store, and the check follows it rather than hardcoding a home."""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": d}):
                self.assertTrue(
                    class_b.writes_the_device_store(Path(d) / "answers.json"))
                self.assertFalse(class_b.writes_the_device_store(
                    Path.home() / ".job-application" / "answers.json"))

    def test_a_sibling_directory_with_a_shared_prefix_does_not_belong(self):
        """String prefixes are not path containment. `/tmp/store-evil` is not
        inside `/tmp/store`, and comparing text would say it was."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "store"
            root.mkdir()
            sibling = Path(d) / "store-evil"
            sibling.mkdir()
            with mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": str(root)}):
                self.assertTrue(class_b.writes_the_device_store(root / "a.json"))
                self.assertFalse(
                    class_b.writes_the_device_store(sibling / "a.json"))


class TheMirrorRefusesAndSaysSo(unittest.TestCase):
    def test_after_write_refuses_a_foreign_path_before_asking_for_a_url(self):
        """It must refuse *before* resolving a URL, so a machine with a
        configured database never even opens a connection for a write it is
        going to reject."""
        import io
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "answers.json"
            target.write_text("{}")
            with mock.patch.object(sys, "stderr", stream), \
                 mock.patch("builtins.print") as printed, \
                 mock.patch("dual_write.enabled") as enabled:
                self.assertIsNone(class_b.after_write(target, {}))
            enabled.assert_not_called()
        self.assertIn("refusing", stream.getvalue())
        printed.assert_not_called()

    def test_control_a_path_inside_the_device_store_is_not_refused(self):
        """Otherwise "it refused" passes with the mirror disabled entirely.
        This one must get as far as asking whether a database is configured."""
        root = Path(store.root_dir(None))
        with mock.patch("dual_write.enabled", return_value=False) as enabled:
            class_b.after_write(root / "answers.json", {})
        enabled.assert_called_once()

    def test_the_history_append_path_is_guarded_too(self):
        """`applications.jsonl` is appended rather than written through
        `write_private`, so it never passes `after_write` and needed the
        refusal explicitly. Five of the traced leaks came through here."""
        import io
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sys, "stderr", stream), \
                 mock.patch("dual_write.enabled") as enabled:
                store._mirror_history_event({"event": "completed"},
                                            Path(d) / "applications.jsonl")
            enabled.assert_not_called()
        self.assertIn("refusing", stream.getvalue())

    def test_control_the_history_path_inside_the_store_is_not_refused(self):
        root = Path(store.root_dir(None))
        with mock.patch("dual_write.enabled", return_value=False) as enabled:
            store._mirror_history_event({"event": "completed"},
                                        root / "applications.jsonl")
        enabled.assert_called_once()


class ItFailsClosed(unittest.TestCase):
    def test_an_unresolvable_path_does_not_belong(self):
        """An unresolvable path is not proof of belonging. The dangerous
        direction is the one that mirrors."""
        with mock.patch("pathlib.Path.resolve", side_effect=OSError("nope")):
            self.assertFalse(class_b.writes_the_device_store("/anything"))


if __name__ == "__main__":
    unittest.main()
