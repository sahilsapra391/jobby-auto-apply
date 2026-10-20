"""A fill-only run cannot submit, and the tool set is what makes that true.

**The blocker this exists for.** A Chainguard application reached `submitting`
on 2026-09-07, parked on an 8-character code Greenhouse emails, and could not
be finished: the slot browsers are headless so there was nothing on screen, and
"Open & finish" could not re-fill the form because `do_not_start` refuses a run
whose latest event is `submitting`. That refusal is right. Re-running a form
that already reached Submit is how an application goes in twice under a real
name.

So the way out is not to waive the refusal. It is to make submitting
impossible, and then the refusal has nothing left to refuse:

    normal run     jobby advertises pre_submit and record_completed
    fill-only run  it advertises neither, and refuses both by name

**The mode is argv and never a sentence in the prompt.** A prompt is advice to
one model; a tool that is not in the set cannot be called by any of them. The
tests below drive the real server process rather than the class, because the
property being claimed is about what a command line produces -- the class could
be right and the flag could fail to reach it, and that failure would look
exactly like working.

The prompt does say the run cannot submit, and that is a courtesy so the agent
parks usefully instead of spending its turns discovering the tools are gone.
`test_the_prompt_is_not_what_stops_it` is the one that says the courtesy is not
the mechanism.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    # Paired, because `_ACTIVE` is a stack and a module that begins without
    # ending leaves the next one running inside its root. It also fails
    # `test_ending_without_beginning_is_an_error_rather_than_a_shrug`, which
    # only shows up in a full discover run and not when this file runs alone.
    store_isolation.end()


import application_mcp  # noqa: E402
import browser  # noqa: E402
import mcp_preflight  # noqa: E402
import run_apply  # noqa: E402
import watch  # noqa: E402


#: Chainguard `31c8aa9c1e988c56` as it actually stood, read off the device on
#: 2026-09-07. The point of copying it rather than inventing a fixture is that
#: this state is the one the feature exists for, and a fixture drifting away
#: from it would leave the tests passing about a case nobody has.
CHAINGUARD_SESSION = {
    "applicationId": "31c8aa9c1e988c56",
    "company": "Chainguard",
    "role": "Senior Technical Program Manager",
    "url": "https://job-boards.greenhouse.io/chainguard/jobs/4707825006",
    "status": "review",
    "phase": "Submitting",
    "pendingFields": [{
        "field": "Enter the 8-character security code sent to the user's inbox",
        "question": "Enter the 8-character security code sent to the user's inbox",
        "reason": "Chainguard requires a human verification code before submission.",
    }],
    "submissionGuard": {
        "applicationId": "31c8aa9c1e988c56",
        "nonceHash": "e4" + "0" * 62,
        "used": False,
    },
}
CHAINGUARD_HISTORY = [{
    "applicationId": "31c8aa9c1e988c56",
    "at": "2026-09-07T02:48:32Z",
    "company": "Chainguard",
    "event": "submitting",
    "eventId": "2203c115-ef6e-4c43-9684-3b298980b413",
    "submissionNonceHash": "e4" + "0" * 62,
}]


class DoNotStartTests(unittest.TestCase):
    """Which runs the gate lets past, in the state the gate was written for."""

    def gate(self, session, history, **mode):
        """`do_not_start` with the store answering exactly this state.

        It shells out to `store.py` for both reads, so both are stubbed here
        rather than a real store being built: the subject is the decision, and
        a temporary store would exercise `store.py`'s own file handling
        instead.
        """
        def fake_store(args):
            if args[:1] == ["session-load"]:
                return subprocess.CompletedProcess(
                    args, 0, json.dumps(session or {}) if session else "", "")
            if args[:1] == ["history-list"]:
                return subprocess.CompletedProcess(
                    args, 0, json.dumps(history or []), "")
            return subprocess.CompletedProcess(args, 1, "", "")

        real_store = watch.store
        real_signed_in = watch.browser_identity.any_signed_in
        watch.store = fake_store
        watch.browser_identity.any_signed_in = lambda: True
        try:
            return watch.do_not_start("31c8aa9c1e988c56", **mode)
        finally:
            watch.store = real_store
            watch.browser_identity.any_signed_in = real_signed_in

    def test_a_normal_run_is_refused_on_the_real_chainguard_state(self):
        why = self.gate(CHAINGUARD_SESSION, CHAINGUARD_HISTORY)
        self.assertIsNotNone(why)
        self.assertIn("submit was already attempted", why)

    def test_a_retry_is_refused_too_because_it_waives_the_park_and_nothing_else(self):
        # Retry used to skip this function outright: the caller read
        # `apply_head.get("retry") is not True` and never asked. So the board's
        # Retry could start a fresh run of a form already at `submitting`.
        why = self.gate(CHAINGUARD_SESSION, CHAINGUARD_HISTORY, retry=True)
        self.assertIsNotNone(why)
        self.assertIn("submit was already attempted", why)

    def test_a_fill_only_run_proceeds_on_that_same_state(self):
        self.assertIsNone(
            self.gate(CHAINGUARD_SESSION, CHAINGUARD_HISTORY, fill_only=True))

    def test_the_message_is_the_one_that_names_an_action(self):
        # Chainguard was parked *and* submitting. The park was checked first,
        # so the tick log printed "waiting on the user" -- true, circular, and
        # naming nothing to press -- while the sentence naming Confirm
        # submitted and Stop never appeared. Same set of refusals, different
        # sentence, and the sentence is the whole value of a refusal. It now
        # names the two answers on the card, Confirm submitted and It did not
        # go through; Stop recorded nothing and is no longer offered.
        why = self.gate(CHAINGUARD_SESSION, CHAINGUARD_HISTORY)
        self.assertIn("Confirm submitted", why)
        self.assertIn("It did not go through", why)
        self.assertNotIn("parked in Needs you", why)

    def test_the_park_alone_refuses_a_normal_run_and_admits_both_others(self):
        parked = dict(CHAINGUARD_SESSION)
        why = self.gate(parked, [])
        self.assertIsNotNone(why)
        self.assertIn("parked in Needs you", why)
        self.assertIsNone(self.gate(parked, [], retry=True))
        self.assertIsNone(self.gate(parked, [], fill_only=True))

    def test_nothing_waives_a_drop(self):
        dropped = dict(CHAINGUARD_SESSION, status="dropped")
        for mode in ({}, {"retry": True}, {"fill_only": True}):
            with self.subTest(mode=mode):
                self.assertEqual(self.gate(dropped, [], **mode),
                                 "the user dropped it")

    def test_nothing_waives_an_application_already_submitted(self):
        done = CHAINGUARD_HISTORY + [{
            "applicationId": "31c8aa9c1e988c56",
            "at": "2026-09-07T02:59:00Z",
            "event": "completed",
        }]
        for mode in ({}, {"retry": True}, {"fill_only": True}):
            with self.subTest(mode=mode):
                self.assertEqual(self.gate(CHAINGUARD_SESSION, done, **mode),
                                 "it is already submitted")

    def test_nothing_waives_a_history_that_could_not_be_read(self):
        # "Could not look" is never "is not there". Not knowing whether a
        # submit happened is not permission to run a form again.
        def unreadable(args):
            if args[:1] == ["session-load"]:
                return subprocess.CompletedProcess(args, 0, "{}", "")
            return subprocess.CompletedProcess(args, 1, "", "boom")

        real_store, real_signed = watch.store, watch.browser_identity.any_signed_in
        watch.store = unreadable
        watch.browser_identity.any_signed_in = lambda: True
        try:
            for mode in ({}, {"retry": True}, {"fill_only": True}):
                with self.subTest(mode=mode):
                    why = watch.do_not_start("31c8aa9c1e988c56", **mode)
                    self.assertIn("history could not be read", why or "")
        finally:
            watch.store = real_store
            watch.browser_identity.any_signed_in = real_signed


class SlotlessParkTests(unittest.TestCase):
    """A parked application has no slot, and None must not travel.

    `reconcile_browser_slots` releases a parked application's slot within a
    tick and `release_browser_slot` clears `browserSlot` off the session, so
    the application "Open & finish" exists for is precisely the one that has
    none. Measured on Chainguard `31c8aa9c1e988c56`, 2026-09-07: parked
    02:50:55Z, released 02:54:29Z, `browserSlot` None afterwards.

    Both of the things None reached resolved it silently to something wrong,
    which is why they are refused at the entry rather than checked at the call.
    """

    def test_holding_an_unknown_slot_raises_instead_of_writing_none_lock(self):
        # `lock_path(None)` is `None.lock`: a real file holding nothing, while
        # every reader goes on seeing the slot as free.
        for name in (None, "", "apply-99", "scan"):
            with self.subTest(name=name):
                with self.assertRaises(watch.TickError):
                    watch.hold_slot(name, "31c8aa9c1e988c56")
        self.assertFalse((watch.LOCK_DIR / "None.lock").exists())

    def test_holding_a_real_slot_still_works(self):
        # The positive control. Every assertion above is about a refusal, and
        # a function that refused everything would satisfy all of them.
        slot = watch.APPLY_LOCKS[-1]
        try:
            watch.hold_slot(slot, "31c8aa9c1e988c56")
            held = watch.lock_holder(slot)
            self.assertEqual(held.get("note"), "31c8aa9c1e988c56")
            self.assertTrue(held.get("parked"))
            self.assertFalse(watch.hold_expired(held))
        finally:
            watch.lock_path(slot).unlink(missing_ok=True)

    def test_showing_an_unknown_slot_never_resolves_to_apply_one(self):
        # `port_for` answers the base port for anything not in SLOTS, so
        # `show(None)` stopped and relaunched apply-1 headed, taking its pages
        # with it, whoever was filling a form in there. That is arithmetic and
        # needs no browser to state.
        self.assertEqual(browser.port_for(None), browser.port_for(browser.SLOTS[0]))

        # **The lifecycle calls are patched even though the guard returns
        # before reaching them.** A store redirect moves the profile directory
        # and not the port: without these, removing the guard to check this
        # test can fail would send `stop_slot` and `serve` at the real apply-1
        # on this machine, which is the mutation destroying somebody's
        # application to prove a test works.
        calls = []
        real = (browser.running, browser.is_headed, browser.stop_slot, browser.serve)
        browser.running = lambda slot=None: calls.append(("running", slot)) or False
        browser.is_headed = lambda slot=None: calls.append(("is_headed", slot)) or False
        browser.stop_slot = lambda slot: calls.append(("stop_slot", slot)) or True
        browser.serve = lambda slot=None, headed=False: (
            calls.append(("serve", slot)) or {"started": True})
        try:
            for name in (None, "apply-99"):
                with self.subTest(name=name):
                    answered = browser.show(name)
                    self.assertEqual(answered.get("error"), "unknown browser slot")
                    self.assertFalse(answered.get("restarted"))
            self.assertEqual(calls, [])
        finally:
            (browser.running, browser.is_headed,
             browser.stop_slot, browser.serve) = real


class ToolSurfaceTests(unittest.TestCase):
    """What the server advertises, and what it will run."""

    def test_the_submission_tools_exist_on_a_normal_run(self):
        # The positive control. Every assertion below is about an absence, and
        # an absence is satisfied by a server that advertises nothing at all.
        names = {tool["name"] for tool in application_mcp.tools_for(False)}
        self.assertLessEqual({"pre_submit", "record_completed"}, names)
        self.assertGreaterEqual(len(names), 9)

    def test_a_fill_only_run_advertises_neither(self):
        names = {tool["name"] for tool in application_mcp.tools_for(True)}
        self.assertEqual(names & application_mcp.SUBMISSION_TOOLS, set())
        # And keeps everything else: a run that cannot fill is not the point.
        self.assertEqual(
            names,
            {t["name"] for t in application_mcp.TOOLS}
            - application_mcp.SUBMISSION_TOOLS)

    def test_the_callable_set_is_narrowed_as_well_as_the_advertised_one(self):
        # Two surfaces, because a client that read `tools/list` once and kept
        # the answer reaches `call` with a name that was never advertised.
        self.assertEqual(
            application_mcp.tool_names_for(True) & application_mcp.SUBMISSION_TOOLS,
            frozenset())

    def test_the_preflight_asks_for_the_surface_this_mode_has(self):
        # A fence that demanded `pre_submit` would refuse to start a fill-only
        # run, which is the fence being right about the wrong contract.
        self.assertNotIn("pre_submit", mcp_preflight.required_for(True)["jobby"])
        self.assertIn("pre_submit", mcp_preflight.required_for(False)["jobby"])

    def test_the_preflight_requires_every_tool_the_server_advertises(self):
        # It was seven hand-written names against nine advertised, so a server
        # that had lost `record_answers` -- the tool writing every
        # application's answer manifest -- passed the fence.
        for fill_only in (False, True):
            with self.subTest(fill_only=fill_only):
                self.assertEqual(
                    set(mcp_preflight.required_for(fill_only)["jobby"]),
                    {t["name"] for t in application_mcp.tools_for(fill_only)})


class ArgvTests(unittest.TestCase):
    """The mode reaches the server on a command line the agent never touches."""

    def context(self, **extra):
        return {"applicationId": "31c8aa9c1e988c56",
                "resume": "/tmp/r.pdf", "cover": "/tmp/c.pdf", **extra}

    def test_the_flag_is_on_the_argv_when_the_run_is_fill_only(self):
        args = browser.application_server_config(self.context(fillOnly=True))["args"]
        self.assertIn("--fill-only", args)

    def test_it_is_absent_otherwise(self):
        for context in (self.context(), self.context(fillOnly=False)):
            with self.subTest(context=sorted(context)):
                self.assertNotIn(
                    "--fill-only",
                    browser.application_server_config(context)["args"])

    def test_a_non_boolean_mode_is_refused_rather_than_read_for_truthiness(self):
        # "false" is truthy, and a mode that turned itself on for it would take
        # the submission tools away from a real application.
        for value in ("false", "true", 1, 0, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    browser.application_server_config(self.context(fillOnly=value))


class ServedSurfaceTests(unittest.TestCase):
    """The real process, over real JSON-RPC, started the way the runner starts it.

    Everything above could hold with the flag failing to reach the server, and
    that failure looks exactly like the feature working: the prompt says the
    run cannot submit, the agent believes it, and nothing tests the gap.
    """

    def speak(self, *messages, fill_only):
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "r.pdf"
            cover = Path(tmp) / "c.pdf"
            for path in (resume, cover):
                path.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
                                 b"trailer\n<<>>\n%%EOF\n")
            argv = [sys.executable, str(REPO / "scripts" / "application_mcp.py"),
                    "--application-id", "31c8aa9c1e988c56",
                    "--resume", str(resume), "--cover", str(cover),
                    "--root", tmp]
            if fill_only:
                argv.append("--fill-only")
            stdin = "".join(json.dumps(m) + "\n" for m in messages)
            done = subprocess.run(argv, input=stdin, capture_output=True,
                                  text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            return [json.loads(line) for line in done.stdout.splitlines() if line.strip()]

    LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "pre_submit", "arguments": {}}}

    def test_the_served_list_carries_the_submission_tools_normally(self):
        listed = self.speak(self.LIST, fill_only=False)[0]
        names = {t["name"] for t in listed["result"]["tools"]}
        self.assertIn("pre_submit", names)
        self.assertIn("record_completed", names)

    def test_the_served_list_omits_them_under_the_flag(self):
        listed = self.speak(self.LIST, fill_only=True)[0]
        names = {t["name"] for t in listed["result"]["tools"]}
        self.assertNotIn("pre_submit", names)
        self.assertNotIn("record_completed", names)
        # Not empty, which is the other way this could pass.
        self.assertGreaterEqual(len(names), 7)

    def test_calling_a_withheld_tool_is_refused_and_says_why(self):
        answered = self.speak(self.CALL, fill_only=True)[0]
        body = json.dumps(answered)
        self.assertIn("fill-only", body)
        self.assertTrue(answered["result"]["isError"])

    def test_the_prompt_is_not_what_stops_it(self):
        # **The property the user asked for, stated as a test.** The server is
        # started fill-only and handed the *normal* prompt's instruction: call
        # pre_submit, then submit. Nothing about the request differs from a
        # real submitting run. It is refused anyway, because the refusal is a
        # property of the process's command line and not of anything a model
        # was told.
        self.assertIn("jobby.pre_submit",
                      run_apply.build_prompt({"applicationId": "x"}, self.ARTIFACTS))
        answered = self.speak(self.CALL, fill_only=True)[0]
        self.assertTrue(answered["result"]["isError"])

    ARTIFACTS = {"resume": {"path": "/tmp/r.pdf"},
                 "coverLetter": {"path": "/tmp/c.pdf"}}


class PromptTests(unittest.TestCase):
    """The wording, which is a courtesy and is still supposed to be right."""

    ARTIFACTS = {"resume": {"path": "/tmp/r.pdf"},
                 "coverLetter": {"path": "/tmp/c.pdf"}}

    def prompt(self, **kw):
        return run_apply.build_prompt({"applicationId": "x"}, self.ARTIFACTS, **kw)

    def test_a_fill_only_prompt_never_asks_for_a_tool_the_run_lacks(self):
        text = self.prompt(fill_only=True)
        for tool in sorted(application_mcp.SUBMISSION_TOOLS):
            self.assertNotIn(tool, text)

    def test_it_says_who_finishes_the_form(self):
        text = self.prompt(fill_only=True)
        self.assertIn("Confirm submitted", text)
        self.assertIn("Do not click Submit", text)

    def test_a_normal_prompt_is_unchanged_in_the_part_that_submits(self):
        text = self.prompt()
        self.assertIn("jobby.pre_submit", text)
        self.assertIn("jobby.record_completed", text)

