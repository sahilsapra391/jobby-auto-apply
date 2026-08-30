"""The slot browsers are signed in to Google, and something checks.

**For nine days nothing did.** The user signed in once on 2026-08-19 and
`clone_logins` carried that session to every slot. On 2026-08-28
`--use-mock-keychain` landed and Chrome could no longer decrypt anything
written under the old Safe Storage key: measured across all five profiles that
existed then, **zero cookies created before that day survived**, the Google
auth set included. Each profile kept its unencrypted *history*, so it still
showed a successful sign-in from nine days earlier while holding no session.

Nothing failed and nothing warned. A signed-out slot is indistinguishable from
a signed-in one until a form asks for an account, and then it looks like an
ordinary park. The user found it by noticing applications behaving differently.

These tests cover the three places that now stop it happening again: the gate
every start passes through, the pre-run refusal for a posting Google serves
itself, and the classification of a park on a code mailed to a mailbox the
browser could have opened.

Run from the repo root: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"tests"))
import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/"scripts"/f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


browser_identity = load("browser_identity")


def profile_with(tmp, names, host=".google.com"):
    """A profile directory holding a cookie store with these cookie names."""
    default = pathlib.Path(tmp)/"Default"
    default.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(default/"Cookies")
    db.execute("create table cookies (name text, host_key text, encrypted_value blob)")
    for n in names:
        db.execute("insert into cookies values (?,?,?)", (n, host, b"\x00" * 60))
    db.commit()
    db.close()
    return pathlib.Path(tmp)


class ReadingTheStateWithoutABrowser(unittest.TestCase):
    """The check has to work on a stopped slot, inside a health tick."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_full_session_reads_signed_in(self):
        p = profile_with(self.tmp.name, browser_identity.GOOGLE_AUTH_COOKIES)
        with mock.patch.object(browser_identity.browser, "_profile_dir", return_value=p):
            state = browser_identity.slot_state("apply-1")
        self.assertTrue(state["signedIn"])
        self.assertEqual(state["state"], "signed-in")
        self.assertEqual(state["cookies"], 6)

    def test_a_profile_with_no_google_cookies_reads_signed_out(self):
        p = profile_with(self.tmp.name, ("cf_bm", "_ga"), host=".greenhouse.io")
        with mock.patch.object(browser_identity.browser, "_profile_dir", return_value=p):
            state = browser_identity.slot_state("apply-1")
        self.assertFalse(state["signedIn"])
        self.assertEqual(state["state"], "signed-out")

    def test_a_partial_refresh_does_not_flicker_to_signed_out(self):
        """A session mid-refresh is briefly missing one of the six.

        An alarm that fires during a token refresh and clears itself a second
        later is an alarm the user learns to ignore, which costs more than the
        thing it was watching for.
        """
        p = profile_with(self.tmp.name, ("SID", "SSID", "HSID"))
        with mock.patch.object(browser_identity.browser, "_profile_dir", return_value=p):
            self.assertTrue(browser_identity.slot_state("apply-1")["signedIn"])

    def test_an_unused_profile_is_unknown_rather_than_signed_out(self):
        """Two different things, and telling a new user to sign in again on a
        machine where the browser has never launched is the wrong one."""
        empty = pathlib.Path(self.tmp.name)/"never-launched"
        empty.mkdir()
        with mock.patch.object(browser_identity.browser, "_profile_dir", return_value=empty):
            state = browser_identity.slot_state("apply-1")
        self.assertEqual(state["state"], "unknown")
        self.assertIn("never been used", state["why"])

    def test_the_live_cookie_store_is_copied_not_read_in_place(self):
        """Chrome holds it open; a direct reader gets 'database is locked'
        often enough that the check would intermittently report unknown."""
        source = pathlib.Path(__file__).parents[1]/"scripts"/"browser_identity.py"
        self.assertIn("shutil.copy2", source.read_text())

    def test_the_profile_path_is_asked_for_not_constructed(self):
        """apply-1 uses the unsuffixed `browser-profile`.

        The obvious f-string reports apply-1 as unprovisioned every single time.
        A peer session hit exactly that on 2026-08-29, which is why this asserts
        the module calls browser._profile_dir rather than building a path.
        """
        source = (ROOT/"scripts"/"browser_identity.py").read_text()
        self.assertIn("browser._profile_dir", source)
        self.assertNotIn('f"browser-profile-{', source)


class WhenAGoogleSessionIsRequired(unittest.TestCase):
    """Measured against the real corpus, not asserted from the pattern."""

    def test_a_google_hosted_posting_needs_it(self):
        for url in ("https://www.google.com/about/careers/applications/jobs/results/1",
                    "https://careers.google.com/jobs/results/1",
                    "https://careers.withgoogle.com/jobs/1"):
            self.assertTrue(browser_identity.needs_google(url), url)

    def test_a_url_that_merely_mentions_google_does_not(self):
        """The substring bug this repo has lost three afternoons to.

        `"google.com" in url` is true of both of these, and refusing a
        Greenhouse run because its tracking parameter names Google would stop
        the pipeline for a form that never wanted an account.
        """
        for url in ("https://boards.greenhouse.io/x/jobs/1?ref=https://google.com/search",
                    "https://jobs.lever.co/co/1?utm_source=google.com",
                    "https://notgoogle.com/jobs/1",
                    "https://google.com.evil.example/jobs"):
            self.assertFalse(browser_identity.needs_google(url), url)

    def test_nothing_is_not_a_google_posting(self):
        for url in ("", None, "not a url", "://broken"):
            self.assertFalse(browser_identity.needs_google(url))

    def test_a_code_mailed_to_the_users_gmail_is_a_wall(self):
        """The three real ones from the store on 2026-08-28, with the address
        replaced.

        The wording is verbatim because that is what the detector has to cope
        with; the mailbox is `someone@gmail.com` because the real one is the
        user's own and `test_no_pii` rightly fails a tracked file carrying it.
        The domain is load-bearing and the local part is not: `_MAILBOX`
        matches `@gmail.com`, which is the half of `verification_wall` that
        says the browser could have opened this mailbox itself."""
        for text in (
            "Email confirmation code (6-digit OTP) sent to someone@gmail.com",
            "Email verification code (8 characters) sent to someone@gmail.com",
            "Email verification code from JPMorganChase (sent to someone@gmail.com)",
        ):
            self.assertTrue(browser_identity.verification_wall(text), text)

    def test_a_code_mailed_somewhere_jobby_cannot_read_is_not(self):
        """Both halves are required, and this is the half that keeps the
        blocker with the user where it belongs."""
        self.assertFalse(browser_identity.verification_wall(
            "Verification code sent to your work address at acme.com"))

    def test_ordinary_blockers_are_not_walls(self):
        """Word-bounded. The prose that really sits in pendingFields."""
        for text in ("Select United States for the required phone country.",
                     "What is your average deal size?",
                     "Browser tools unavailable in this session",
                     "Personally review and accept the AIM Screening disclosure"):
            self.assertFalse(browser_identity.verification_wall(text), text)


class TheStandingRule(unittest.TestCase):
    """Where it stops being negotiable, and that the wiring actually reads."""

    def test_no_application_starts_while_the_fleet_is_signed_out(self):
        watch = load("watch")
        with mock.patch.object(watch.browser_identity, "any_signed_in", return_value=False):
            why = watch.do_not_start("any-application")
        self.assertIsNotNone(why)
        self.assertIn("signed in to Google", why)
        self.assertIn("--login", why)

    def test_the_gate_is_the_first_thing_do_not_start_checks(self):
        """Before the store reads. A signed-out fleet is true of every
        application at once, so answering it first saves a subprocess per
        queued application and reports the same thing."""
        source = (ROOT/"scripts"/"watch.py").read_text()
        body = source.split("def do_not_start(")[1].split("\ndef ")[0]
        self.assertLess(body.index("any_signed_in"), body.index("session-load"))

    def test_the_pre_run_gate_reads_the_posting_url_that_exists(self):
        """A gate reading a variable that is not there refuses nothing."""
        source = (ROOT/"scripts"/"run_apply.py").read_text()
        self.assertIn("browser_identity.needs_google(posting_url)", source)
        self.assertIn("posting_url = ", source)
        self.assertLess(source.index("posting_url = "),
                        source.index("browser_identity.needs_google(posting_url)"))

    def test_the_mid_form_gate_reads_blockers_from_the_session(self):
        """`verify_terminal_outcome` returns ok/terminal/why/safeToRestore and
        has never carried pendingFields.

        The first version of this gate read `outcome.get("pendingFields")`,
        which is always absent, so it would have found an empty list every time
        and never once fired while looking entirely correct. This asserts the
        source of the blockers, because that is the part that was wrong.
        """
        source = (ROOT/"scripts"/"run_apply.py").read_text()
        self.assertIn('parked_session.get("pendingFields")', source)
        self.assertNotIn('outcome.get("pendingFields")', source)

    def test_verify_terminal_outcome_really_does_not_carry_pending_fields(self):
        """The premise of the test above, checked rather than restated."""
        run_apply = load("run_apply")
        with mock.patch.object(run_apply, "_latest_event", return_value=(None, "")), \
             mock.patch.object(run_apply, "_current_session", return_value=None):
            out = run_apply.verify_terminal_outcome("x", {})
        self.assertNotIn("pendingFields", out)
        self.assertEqual(sorted(out), ["ok", "safeToRestore", "terminal", "why"])

    def test_setup_asks_when_nothing_is_signed_in(self):
        """The step that needs a person, at the one moment they are present.

        Not mock.patch("builtins.print") around this: the message goes to
        stderr through print, so patching print swallows exactly what is being
        checked. The first version of this check did that and reported silence.
        """
        import contextlib, io
        watch = load("watch")
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(watch.browser_identity, "any_signed_in", return_value=False), \
             mock.patch.object(watch.browser_identity, "signed_out_slots",
                               return_value=["apply-1"]), \
             mock.patch.object(watch, "run",
                               return_value=mock.Mock(returncode=0, stderr="")), \
             mock.patch("pathlib.Path.write_bytes"), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            watch.install()
        self.assertIn("--login", err.getvalue())
        self.assertIn("clone-logins", err.getvalue())

    def test_setup_stays_quiet_when_it_is_already_done(self):
        """An instruction printed on every reinstall is one nobody reads."""
        import contextlib, io
        watch = load("watch")
        err = io.StringIO()
        with mock.patch.object(watch.browser_identity, "any_signed_in", return_value=True), \
             mock.patch.object(watch.browser_identity, "signed_out_slots", return_value=[]), \
             mock.patch.object(watch, "run",
                               return_value=mock.Mock(returncode=0, stderr="")), \
             mock.patch("pathlib.Path.write_bytes"), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            watch.install()
        self.assertEqual(err.getvalue(), "")

    def test_status_reports_it_where_everyone_already_looks(self):
        source = (ROOT/"scripts"/"watch.py").read_text()
        self.assertIn('out["googleSignIn"]', source)


if __name__ == "__main__":
    unittest.main()
