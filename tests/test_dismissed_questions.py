"""A question the user refuses, and the loop that must stop planting it.

The Memory Bank grows on its own: every application that parks on a question it
could not answer plants that question here, unanswered, so it can be answered
somewhere calmer than a form at 11pm. Nothing took one back out. Measured on the
real store on 2026-08-28, all five outstanding questions were Korean-language
fields from one application the user had already dropped -- nationality,
a Korea work visa, the Public Officials Ethics Act, veterans' affairs, a data
consent -- badging the nav with a 5 that could not reach zero, because the only
way to clear a row was to answer it and the user was never going to answer any
of them.

**A delete would not have fixed it, and that is what these tests are for.**
`cmd_answer_open` reads an absent entry as "never asked", so the next park would
plant the same question again. The dismissal is therefore a state on the record,
and the tests below are about the two things that state has to do at once: stop
the loop, and never let a refused question act like an answered one.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import itertools
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _node():
    spec = importlib.util.spec_from_file_location(
        "toolpath", ROOT / "scripts" / "toolpath.py")
    toolpath = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(toolpath)
    return toolpath.find("node", required=False)


def _plan_answer_edit(body):
    """Run `deploy/profile_edits.mjs` on one body, in node.

    The rules are JavaScript because both boards are, and running them beats
    restating them in Python: a Python copy of the action list would be the
    third declaration of it, which is the thing that module exists to prevent.
    """
    node = _node()
    if node is None:
        raise unittest.SkipTest("node is not installed")
    src = ROOT / "deploy" / "profile_edits.mjs"
    result = subprocess.run(
        [node, "--input-type=module", "-e",
         f"import {{ planAnswerEdit }} from {json.dumps(str(src))};"
         "let raw='';process.stdin.on('data',c=>{raw+=c;})"
         ".on('end',()=>{console.log("
         "JSON.stringify(planAnswerEdit(JSON.parse(raw))));});"],
        input=json.dumps(body), capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"the planner would not run: {result.stderr[:400]}")
    return json.loads(result.stdout or "{}")
sys.path.insert(0, str(ROOT / "tests"))
import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = load("store")
watch = load("watch")


def bank_counts(answers):
    """The Needs you arithmetic, from the store's own predicates.

    Three reasons an entry carries no value and only one of them is
    outstanding: `remembered is False` was filled and not kept, and a
    dismissed one the user has refused. Counting either would badge the nav
    with a number nothing can clear, which is what dismissal exists to end.
    """
    entries = [e for e in (answers.values() if isinstance(answers, dict) else answers)
               if isinstance(e, dict)]
    return {
        "total": len(entries),
        "unanswered": sum(1 for e in entries
                          if not store.answered(e)
                          and e.get("remembered") is not False
                          and not store.dismissed(e)),
        "dismissed": sum(1 for e in entries if store.dismissed(e)),
    }

# The five that were really sitting there, verbatim, so the fixture is the
# corpus rather than a tidied version of it.
KOREAN = [
    "1. 국적이 어떻게 되십니까? *",
    "2. 대한민국에서 합법적으로 근무를 하기 위한 비자가 필요합니까?*",
    "1. 공직자윤리법 제17조 등에 따라 퇴직일로부터 3년간 취업심사대상기관에의 "
    "취업이 제한되는 취업심사대상자에 해당합니까?*",
    "「국가유공자 등 예우 및 지원에 관한 법률」 등에 따른 취업지원 대상자(보훈)에 해당하십니까?*",
    "채용 지원 또는 법령상 의무를 준수하기 위해 최소한의 개인정보를 수집합니다.*",
]


class BankCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = str(pathlib.Path(self.tmp.name) / "store")
        store.cmd_init(self.ns())
        self._serial = itertools.count()

    def ns(self, **kw):
        kw.setdefault("root", self.root)
        return SimpleNamespace(**kw)

    def write(self, payload):
        path = pathlib.Path(self.tmp.name) / f"in-{next(self._serial)}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def open_question(self, question, application="app-1"):
        return store.cmd_answer_open(self.ns(input=self.write(
            {"question": question, "applicationId": application})))

    def answer(self, question, value):
        return store.cmd_answer_edit(self.ns(input=self.write(
            {"question": question, "value": value, "version": 0,
             "editId": f"e-{next(self._serial)}"})))

    def dismiss(self, key, version=0, edit_id=None):
        return store.cmd_answer_dismiss(self.ns(
            key=key, version=version, edit_id=edit_id or f"d-{next(self._serial)}"))

    def restore(self, key, version, edit_id=None):
        return store.cmd_answer_restore(self.ns(
            key=key, version=version, edit_id=edit_id or f"r-{next(self._serial)}"))

    def entry(self, key):
        return store.cmd_answer_list(self.ns())[key]


class TheLoopStopsPlantingIt(BankCase):
    """The claim the whole feature rests on."""

    def test_a_dismissed_question_is_not_reopened_by_the_next_park(self):
        key = self.open_question(KOREAN[0], "app-1")["key"]
        self.dismiss(key)
        again = self.open_question(KOREAN[0], "app-2")
        self.assertFalse(again["opened"])
        self.assertEqual(again["reason"], "the user dismissed this question")
        self.assertEqual(self.entry(key)["state"], "dismissed")

    def test_deleting_it_instead_would_not_have_worked(self):
        """Why this is a state and not a delete, pinned rather than asserted.

        If this ever passes with `opened` False, a delete has become durable and
        the state is redundant. It does not, and that is the reason for it.
        """
        key = self.open_question(KOREAN[1], "app-1")["key"]
        store.cmd_answer_delete(self.ns(key=key, version=0, edit_id="del-1"))
        self.assertNotIn(key, store.cmd_answer_list(self.ns()))
        self.assertTrue(self.open_question(KOREAN[1], "app-2")["opened"])

    def test_the_question_still_records_who_asked_it(self):
        """Dismissed is not deaf. Nine more employers asking is worth knowing."""
        key = self.open_question(KOREAN[2], "app-1")["key"]
        self.dismiss(key)
        self.open_question(KOREAN[2], "app-2")
        self.open_question(KOREAN[2], "app-3")
        self.assertEqual(self.entry(key)["askedBy"], ["app-1", "app-2", "app-3"])
        self.assertEqual(self.entry(key)["state"], "dismissed")

    def test_the_whole_dropped_set_clears_and_stays_clear(self):
        keys = [self.open_question(q, "000000000000000a")["key"] for q in KOREAN]
        self.assertEqual(self.counts()["unanswered"], 5)
        for key in keys:
            self.dismiss(key)
        self.assertEqual(self.counts()["unanswered"], 0)
        self.assertEqual(self.counts()["dismissed"], 5)
        # The application is retried and parks on the same five.
        for question in KOREAN:
            self.open_question(question, "000000000000000a")
        self.assertEqual(self.counts()["unanswered"], 0)

    def counts(self):
        return bank_counts(store.cmd_answer_list(self.ns()))


class ARefusedQuestionNeverFillsAForm(BankCase):
    def test_a_dismissed_entry_does_not_satisfy_a_lookup(self):
        key = self.open_question(KOREAN[0])["key"]
        self.dismiss(key)
        self.assertEqual(self.find(KOREAN[0])["match"], "none")

    def test_nor_does_it_satisfy_a_fuzzy_one(self):
        """The near-miss path is the one that reaches past the exact key."""
        key = self.open_question("What is your expected base salary in USD?")["key"]
        self.dismiss(key)
        self.assertEqual(self.find("What is the expected base salary in USD?")["match"],
                         "none")

    def test_dismissing_an_entry_that_holds_an_answer_is_refused(self):
        """Otherwise the answer stays on the record and keeps filling forms.

        `find_answer` selects on the value, not on the state, so a dismissed
        row that still carried one would be invisible on the board and live in
        every application. The two acts stay separate: Forget removes an
        answer, dismissal refuses a question.
        """
        self.answer("What is your notice period?", "Two weeks")
        key = store.answer_key("What is your notice period?")
        out = self.dismiss(key, version=1)
        self.assertFalse(out["dismissed"])
        self.assertEqual(out["reason"], "this entry holds an answer")
        self.assertIn("forget the answer first", out["hint"])
        self.assertEqual(self.find("What is your notice period?").get("value"),
                         "Two weeks")

    def test_no_resolver_can_reach_one(self):
        """The invariant a clean merge broke once already.

        Three resolvers arrived from another branch each iterating
        `answers.values()`, with no idea the valueless rule existed. A dismissed
        entry is valueless, so it inherits that guard, and this is the test that
        says so rather than a comment claiming it.
        """
        question = "Are you a US person for export control?"
        key = self.open_question(question)["key"]
        self.dismiss(key)
        answers = store.cmd_answer_list(self.ns())
        checked = 0
        for name in ("acknowledgement", "expected_pay", "us_person", "topic_answer"):
            resolver = getattr(store, name, None)
            self.assertIsNotNone(resolver, f"store.{name} is gone; this test rotted")
            self.assertIsNone(resolver(answers, question),
                              f"{name} returned a dismissed entry")
            checked += 1
        # Counted, so a renamed resolver fails here rather than passing an
        # empty loop. The same reason test_diagnostics_are_kept counts the
        # launches it inspected.
        self.assertEqual(checked, 4)

    def find(self, question):
        return store.cmd_answer_find(self.ns(question=question))


class RestoreReturnsItToTheLoop(BankCase):
    def test_a_restored_question_is_outstanding_again(self):
        key = self.open_question(KOREAN[0])["key"]
        self.dismiss(key)
        out = self.restore(key, version=1)
        self.assertTrue(out["restored"])
        entry = self.entry(key)
        self.assertEqual(entry["state"], "missing")
        self.assertIn("unanswered", entry["tags"])
        self.assertNotIn("dismissedAt", entry)
        self.assertTrue(self.open_question(KOREAN[0], "app-2")["joined"])

    def test_a_born_sensitive_question_is_restored_sensitive(self):
        """Derived from the question, not from whatever the record held.

        Restoring to a flat `missing` would strip the Class 2 marking off a
        compensation question, and the encryption work in #82 selects on it.
        """
        key = self.open_question("What is your current base salary?")["key"]
        self.assertEqual(self.entry(key)["state"], "sensitive")
        self.dismiss(key)
        self.assertTrue(self.entry(key)["sensitive"])
        self.restore(key, version=1)
        self.assertEqual(self.entry(key)["state"], "sensitive")
        self.assertTrue(self.entry(key)["sensitive"])

    def test_answering_a_restored_question_still_works(self):
        key = self.open_question("How did you hear about us?")["key"]
        self.dismiss(key)
        self.restore(key, version=1)
        out = store.cmd_answer_edit(self.ns(input=self.write(
            {"question": "How did you hear about us?", "value": "LinkedIn",
             "version": 2, "editId": "e-after"})))
        self.assertTrue(out["applied"])
        self.assertEqual(
            store.cmd_answer_find(self.ns(question="How did you hear about us?"))["value"],
            "LinkedIn")


class ReplayedClicksDoNothingTwice(BankCase):
    """The board re-sends what it remembers. So this arrives more than once."""

    def test_the_same_dismissal_twice_is_a_no_op(self):
        key = self.open_question(KOREAN[0])["key"]
        first = self.dismiss(key, edit_id="dis-1")
        self.assertTrue(first["dismissed"])
        second = store.cmd_answer_dismiss(self.ns(key=key, version=0, edit_id="dis-1"))
        self.assertFalse(second["dismissed"])
        self.assertEqual(second["reason"], "already applied")
        self.assertEqual(self.entry(key)["version"], 1)

    def test_a_different_click_at_a_stale_version_is_refused(self):
        key = self.open_question(KOREAN[0])["key"]
        self.dismiss(key, edit_id="dis-1")
        out = store.cmd_answer_dismiss(self.ns(key=key, version=0, edit_id="dis-2"))
        self.assertFalse(out["dismissed"])
        self.assertEqual(out["reason"], "conflict")

    def test_dismissing_a_key_the_store_does_not_hold_says_so(self):
        out = self.dismiss("no-such-question")
        self.assertFalse(out["dismissed"])
        self.assertEqual(out["reason"], "no such entry")


class TheWatcherCarriesBothActions(unittest.TestCase):
    def test_dismiss_and_restore_become_their_own_store_commands(self):
        for action, command in (("dismiss", "answer-dismiss"),
                                ("restore", "answer-restore"),
                                ("delete", "answer-delete")):
            argv, stdin = watch._bank_command(
                {"action": action, "key": "notice-period", "version": 3,
                 "applicationId": "edit-9"})
            self.assertEqual(argv[0], command)
            self.assertEqual(argv[1:], ["--key", "notice-period",
                                        "--version", "3", "--edit-id", "edit-9"])
            self.assertIsNone(stdin)

    def test_a_keyless_dismissal_is_refused_rather_than_guessed_at(self):
        self.assertEqual(watch._bank_command({"action": "dismiss"}), (None, None))

    def test_an_unknown_action_still_falls_through_to_save(self):
        """The default has to stay `save`; the queue's older entries have none."""
        argv, stdin = watch._bank_command(
            {"question": "How did you hear about us?", "value": "LinkedIn",
             "applicationId": "edit-1"})
        self.assertEqual(argv[0], "answer-edit")
        self.assertIn("LinkedIn", stdin)


class ARefusedEditIsReportedNotSwallowed(BankCase):
    """The tick log reads `applied`, and a dismissal does not say `applied`.

    Reading one verb is how a refused dismissal would have been counted a
    success and reported to nobody, which is this repo's oldest failure shape.
    So the check runs against the real store through the real watcher path.
    """

    def test_a_conflicting_dismissal_reaches_the_refused_list(self):
        key = self.open_question(KOREAN[0])["key"]
        self.dismiss(key, edit_id="dis-1")
        changed = self.run_queue([
            {"applicationId": "dis-2", "action": "dismiss", "key": key, "version": 0},
        ])
        self.assertNotIn("answerEdits", changed)
        refused = changed["answerEditsRefused"]
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["editId"], "dis-2")
        self.assertIn("version 1", refused[0]["why"])

    def test_dismissing_an_answered_row_reports_the_way_out(self):
        self.answer("What is your notice period?", "Two weeks")
        key = store.answer_key("What is your notice period?")
        changed = self.run_queue([
            {"applicationId": "dis-3", "action": "dismiss", "key": key, "version": 1},
        ])
        why = changed["answerEditsRefused"][0]["why"]
        self.assertIn("holds an answer", why)
        self.assertIn("forget the answer first", why)

    def test_a_dismissal_that_lands_is_not_reported_as_refused(self):
        key = self.open_question(KOREAN[0])["key"]
        changed = self.run_queue([
            {"applicationId": "dis-4", "action": "dismiss", "key": key, "version": 0},
        ])
        self.assertEqual(changed["answerEdits"], [key])
        self.assertNotIn("answerEditsRefused", changed)

    def run_queue(self, items):
        """The watcher's own loop over claimed edits, against the real store.

        `apply_profile_edits` claims from KV, which no test can reach, so this
        runs the part after the claim: the same build, the same subprocess, the
        same refusal reading.
        """
        changed = {}
        done, refused = [], []
        for item in items:
            argv, stdin = watch._bank_command(item)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "store.py"), "--root", self.root]
                + argv, input=stdin, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads(result.stdout or "{}")
            refusal = next((out.get(v) is False for v in
                            ("applied", "dismissed", "restored", "deleted")
                            if v in out), None)
            if refusal and out.get("reason") not in (None, "already applied"):
                why = out.get("reason")
                if why == "conflict":
                    why = f"the entry moved to version {out.get('version')}"
                elif out.get("hint"):
                    why = f"{why}: {out['hint']}"
                refused.append({"editId": item.get("applicationId"), "why": why})
                continue
            done.append(out.get("key") or out.get("default") or out.get("removed") or True)
        if done:
            changed["answerEdits"] = done
        if refused:
            changed["answerEditsRefused"] = refused
        return changed


class TheBoardIsToldWhichRowsAreDismissed(BankCase):
    def test_the_needs_you_count_excludes_them(self):
        key = self.open_question(KOREAN[0])["key"]
        self.open_question(KOREAN[1])
        counts = self.counts()
        self.assertEqual(counts["unanswered"], 2)
        self.dismiss(key)
        counts = self.counts()
        self.assertEqual(counts["unanswered"], 1)
        self.assertEqual(counts["dismissed"], 1)
        self.assertEqual(counts["total"], 2)

    def test_the_route_accepts_both_actions_and_wants_the_key(self):
        """Both boards plan an edit the same way, and the planner is run.

        **This used to read `board.js` for `const ANSWER_ACTIONS = ` and split
        on it.** The rules moved to `deploy/profile_edits.mjs` when v2 grew a
        write half -- two boards writing one queue, so one validator -- and the
        check broke on a refactor that improved exactly what it guards. That is
        the `GATE_PATHS` equality test again: its subject was the arrangement
        of the source rather than the property the arrangement is for.

        So it calls the planner. `dismiss` and `restore` have to be accepted
        and each has to insist on the key, because a keyless dismissal is an
        edit the store cannot file and the queue would carry it anyway.
        """
        plan = _plan_answer_edit
        for action in ("save", "delete", "dismiss", "restore"):
            body = {"editId": "e1", "action": action, "key": "k",
                    "question": "Q", "value": "V"}
            self.assertNotIn("error", plan(body),
                             f"{action} is not accepted by the planner")
        for action in ("delete", "dismiss", "restore"):
            refused = plan({"editId": "e1", "action": action})
            self.assertEqual(refused.get("error"), f"{action} needs the key")
        self.assertIn("error", plan({"editId": "e1", "action": "invent"}))

    def counts(self):
        return bank_counts(store.cmd_answer_list(self.ns()))
