"""An edit that was refused says so somewhere the reader can still see it.

**A refusal used to exist only as a log line and a health ping.** `watch.py`
built `changed["answerEditsRefused"]`, printed it, and pinged it; nothing
persisted it, and v2 reads Postgres, so no board could have drawn it however it
was written. `store.py` genuinely refuses edits -- a shape mismatch on the
resume wording, a version conflict on an answer -- and its own docstring says
what that costs: "An answer the user typed and believes is saved, which
silently is not, is the worst outcome available here."

**Every edit gets a row, not only the refused ones.** An accepted edit and a
lost one leave the same nothing otherwise, and the reader is told nothing
either way.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import store_isolation  # noqa: E402
import watch  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


def put(root, entry):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "store.py"), "--root", str(root),
         "edit-outcome-put", "--input", "-"],
        input=json.dumps(entry), capture_output=True, text=True)


class TheStoreRecordsWhatHappened(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def held(self):
        path = self.root / "edit-outcomes.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def test_a_refusal_keeps_what_was_submitted_from_the_pending_row(self):
        """**The three facts, and the one that is easiest to lose.** A refusal
        arrives from the watcher carrying the store's reason and what the store
        holds; what the reader *typed* was recorded when the edit was claimed
        and is not restated. Dropping it leaves a refusal that cannot show them
        their own text, which is the whole point of the state."""
        put(self.root, {"editId": "e1", "queue": "answerEdits", "state": "pending",
                        "target": "salary", "submitted": "135000"})
        put(self.root, {"editId": "e1", "state": "refused", "held": "110000",
                        "reason": "the entry moved to version 4"})
        row = self.held()["e1"]
        self.assertEqual(row["submitted"], "135000")
        self.assertEqual(row["held"], "110000")
        self.assertEqual(row["reason"], "the entry moved to version 4")
        self.assertEqual(row["state"], "refused")
        self.assertEqual(row["target"], "salary", "the target carries forward too")
        self.assertEqual(row["queue"], "answerEdits")

    def test_an_unknown_state_is_refused_rather_than_stored(self):
        done = put(self.root, {"editId": "e2", "state": "maybe"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unknown edit state", done.stderr)
        self.assertEqual(self.held(), {})

    def test_an_edit_with_no_id_is_refused(self):
        done = put(self.root, {"state": "pending"})
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.held(), {})

    def test_the_file_is_bounded(self):
        """Read whole and mirrored whole on every write, so an unbounded file
        turns one edit into a growing write. Oldest go first."""
        import store
        for i in range(store.EDIT_OUTCOMES_KEPT + 20):
            put(self.root, {"editId": f"e{i:04d}", "state": "accepted"})
        self.assertEqual(len(self.held()), store.EDIT_OUTCOMES_KEPT)


class EveryRefusalPathRecordsOne(unittest.TestCase):
    """**The failure this guards is silent by construction.** A new refusal
    branch that appends to `refused` and forgets the outcome leaves the reader
    with a row that says `pending` for ever, which is indistinguishable from a
    daemon that never got there."""

    def test_each_refusal_branch_records_an_outcome(self):
        # Parsed from the file rather than `inspect.getsource`, which re-reads
        # from disk against a stale `co_firstlineno` and reports a different
        # block when anything edits watch.py mid-run. That cost an afternoon
        # one file over today.
        import ast
        source = (REPO / "scripts" / "watch.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "apply_profile_edits")
        tree = fn
        appends, records = 0, 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", "")
            if name == "append" and getattr(node.func, "value", None) is not None \
                    and getattr(node.func.value, "id", "") == "refused":
                appends += 1
            if name == "_record_outcome":
                records += 1
        self.assertGreaterEqual(appends, 4, "the walker found no refusal branches")
        # pending + accepted + one per refusal
        self.assertEqual(records, appends + 2,
                         f"{appends} refusal branches and {records} outcome "
                         f"writes: every refusal needs one, plus the pending "
                         f"on claim and the accepted at the end.")
