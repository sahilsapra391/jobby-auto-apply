"""The work-authorisation table lives in the store, and a missing one answers nothing.

A user's immigration facts used to be written into `scripts/store.py`, which
put them in a public repository and made the resolver answer every user's form
with one person's visa status. They are read from `work-authorization.json` in
the store now. These tests pin the three ways that read can go: a good file
answers, a missing file answers nothing, and a file with any cell missing also
answers nothing, because a half-read table is how a US answer lands on another
country's form.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "store_work_auth", ROOT / "scripts" / "store.py")
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def table():
    cell = {"value": "Yes", "note": "a note"}
    return {"home": {"label": "New Zealand", "pattern": r"\bnew zealand\b"},
            "answers": {shape: {bucket: dict(cell) for bucket in store.SPONSORSHIP_BUCKETS}
                        for shape in store.SPONSORSHIP_SHAPES}}


class WorkAuthorizationFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, value):
        (self.root / store.WORK_AUTHORIZATION_FILE).write_text(json.dumps(value))

    def test_a_complete_table_answers_from_the_store(self):
        value = table()
        value["answers"]["sponsorship"]["home"]["value"] = "No"
        self.write(value)
        answer = store.sponsorship_status("Do you require sponsorship to work in New Zealand?")
        self.assertEqual(answer["value"], "No")
        self.assertEqual(answer["topic"], "sponsorship-zealand")

    def test_no_file_answers_nothing(self):
        self.assertIsNone(store.work_authorization())
        self.assertIsNone(store.sponsorship_status("Do you require visa sponsorship?"))

    def test_one_missing_cell_refuses_the_whole_table(self):
        value = table()
        del value["answers"]["without-sponsorship"]["other"]
        self.write(value)
        self.assertIsNone(store.work_authorization())
        self.assertIsNone(store.sponsorship_status("Do you require visa sponsorship?"))

    def test_a_blank_answer_refuses_the_whole_table(self):
        value = table()
        value["answers"]["sponsorship"]["us"]["value"] = "  "
        self.write(value)
        self.assertIsNone(store.work_authorization())

    def test_a_pattern_that_does_not_compile_refuses_the_table(self):
        value = table()
        value["home"]["pattern"] = "("
        self.write(value)
        self.assertIsNone(store.work_authorization())

    def test_a_question_that_is_not_about_work_never_reads_the_file(self):
        with mock.patch.object(store, "work_authorization") as loader:
            self.assertIsNone(store.sponsorship_status("What is your notice period?"))
        loader.assert_not_called()
