"""The suite may not write to the device's own store, and cannot silently start.

Between 2026-08-25 and 2026-08-28 `python3 -m unittest discover -s tests` wrote
3,222 fabricated rows into ~/.job-application/usage.jsonl, against 26 real ones,
at fifteen rows a run. It also filed an application called acme-pm in the
device's sessions/ and roles.json, rewrote the live apply-1 browser fence config,
and touched the provider registry. Nothing failed and nothing said anything,
because every one of those writes went through the same code a real run uses and
produced a file shaped exactly like a real one.

Two mechanisms answer that, and this module holds both to their claims.
`store_isolation` moves a test module's store somewhere temporary, which is the
fix. The guard in `provider_usage.record` is the backstop for the one file whose
corruption is silent by construction, and a backstop is only worth having if it
cannot fire on the apply path, so that is measured here rather than asserted.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import providers  # noqa: E402
import provider_usage  # noqa: E402
import store_isolation  # noqa: E402


class LiveLedgerGuardTests(unittest.TestCase):
    """record() refuses the real ledger under a test runner, and only that one.

    Every case here redirects `live_ledger` at a temporary path first. The guard
    is about a file this suite must not go near, so proving it works by writing
    near it would be the same mistake in a smaller font. What the real path is
    gets its own test below, by comparison rather than by writing.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.pretend_live = self.root / "live"
        patch = mock.patch.object(
            provider_usage, "live_ledger", lambda: self.pretend_live / "usage.jsonl")
        patch.start()
        self.addCleanup(patch.stop)

    def record(self, root):
        with mock.patch.object(sys, "stderr", open(os.devnull, "w")) as quiet:
            self.addCleanup(quiet.close)
            return provider_usage.record(
                "acme-pm", "anthropic", "claude-sonnet-5", "form", None,
                root=root, prices=None)

    def test_a_write_aimed_at_the_real_ledger_is_refused(self):
        self.assertIsNone(self.record(self.pretend_live))
        self.assertFalse((self.pretend_live / "usage.jsonl").exists())

    def test_the_refusal_happens_before_anything_is_created(self):
        # Not just "no ledger": no directory either. A refusal that has already
        # run mkdir has already decided the write was going ahead.
        self.record(self.pretend_live)
        self.assertFalse(self.pretend_live.exists())

    def test_naming_the_real_ledger_explicitly_does_not_buy_permission(self):
        # The rule is about which file, not about how the caller spelled it.
        # "an explicit root is allowed" would have permitted every one of the
        # 3,222 rows, because run_apply passes no root at all.
        reason = provider_usage.refusal_reason(self.pretend_live / "usage.jsonl")
        self.assertIsNotNone(reason)
        self.assertIn("temporary directory", reason)

    def test_a_temporary_root_is_written_normally(self):
        row = self.record(self.root / "elsewhere")
        self.assertIsNotNone(row, "the guard must not cost a test its own ledger")
        self.assertEqual(row.applicationId, "acme-pm")
        rows = provider_usage.read_ledger(self.root / "elsewhere")
        self.assertEqual(len(rows), 1)

    def test_a_redirected_environment_is_written_normally(self):
        # How store_isolation moves the ledger, so refusing this would break
        # every module that adopts it.
        with mock.patch.dict(
                os.environ, {"JOB_APPLICATION_DIR": str(self.root / "viaenv")}):
            self.assertIsNotNone(self.record(None))
        self.assertEqual(len(provider_usage.read_ledger(self.root / "viaenv")), 1)

    def test_the_refusal_is_announced_rather_than_swallowed(self):
        stream = tempfile.TemporaryFile("w+", encoding="utf-8")
        self.addCleanup(stream.close)
        with mock.patch.object(sys, "stderr", stream):
            provider_usage.record("acme-pm", "openai", "gpt-5.6-luna", "form",
                                  None, root=self.pretend_live, prices=None)
        stream.seek(0)
        said = stream.read()
        self.assertIn("refusing to write", said)
        self.assertIn(str(self.pretend_live / "usage.jsonl"), said)

    def test_it_is_still_announced_when_the_caller_has_patched_print(self):
        """The runs that trigger this are the runs that silence print().

        `tests/test_provider_runners.py` wraps every `run_apply.main()` call in
        `mock.patch("builtins.print")`, so the first version of this guard
        refused all ten rows and reported it zero times. Measured that way
        round: 10 rows written without the guard, 0 with it, 0 diagnostics
        either way. A backstop nobody can see fire is one nobody can debug.
        """
        stream = tempfile.TemporaryFile("w+", encoding="utf-8")
        self.addCleanup(stream.close)
        with mock.patch("builtins.print") as printed, \
                mock.patch.object(sys, "stderr", stream):
            provider_usage.record("acme-pm", "openai", "gpt-5.6-luna", "form",
                                  None, root=self.pretend_live, prices=None)
        stream.seek(0)
        self.assertIn("refusing to write", stream.read())
        printed.assert_not_called()


class TheGuardedPathIsTheRealOneTests(unittest.TestCase):
    def test_live_ledger_is_the_device_store(self):
        self.assertEqual(
            provider_usage.live_ledger(),
            pathlib.Path("~/.job-application/usage.jsonl").expanduser())

    def test_it_ignores_a_redirected_environment(self):
        # live_ledger answers "which file is the real measurement", so it must
        # not move when JOB_APPLICATION_DIR does. If it did, the guard would
        # follow the redirect and stop protecting anything.
        with mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": "/tmp/nowhere"}):
            self.assertEqual(
                provider_usage.live_ledger(),
                pathlib.Path("~/.job-application/usage.jsonl").expanduser())


class TheGuardCannotFireInProductionTests(unittest.TestCase):
    """The one way this backstop could do harm, measured instead of assumed.

    A guard that misfires on the apply path costs the cost report a real row and
    says so only on a stderr nobody is reading. The claim it rests on is that
    `unittest` and `pytest` are never in sys.modules during a real run, and that
    claim is checked here by importing the apply path in a fresh interpreter
    rather than by re-reading the docstring that makes it.
    """

    APPLY_PATH = ("run_apply", "document_worker", "watch", "providers",
                  "provider_usage", "publish", "ingest")

    def test_importing_the_apply_path_leaves_no_test_runner_behind(self):
        program = (
            "import sys; sys.path.insert(0, %r)\n"
            "import %s\n"
            "print(sorted(n for n in ('unittest', 'pytest') if n in sys.modules))\n"
            % (str(ROOT / "scripts"), ", ".join(self.APPLY_PATH))
        )
        done = subprocess.run([sys.executable, "-c", program],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "[]", done.stderr)

    def test_no_module_under_scripts_imports_a_test_runner(self):
        offenders = []
        for source in sorted((ROOT / "scripts").glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    if name.split(".")[0] in ("unittest", "pytest"):
                        offenders.append(f"{source.name}:{node.lineno} {name}")
        self.assertEqual(offenders, [], f"the guard would misfire: {offenders}")

    def test_the_guard_is_transparent_when_no_runner_is_present(self):
        with mock.patch.object(provider_usage, "_under_test_runner",
                               return_value=False):
            self.assertIsNone(
                provider_usage.refusal_reason(provider_usage.live_ledger()))


class RedirectingTheStoreTests(unittest.TestCase):
    def test_it_moves_the_environment_and_puts_it_back(self):
        before = os.environ.get("JOB_APPLICATION_DIR")
        root = store_isolation.begin()
        try:
            self.assertEqual(os.environ["JOB_APPLICATION_DIR"], str(root))
        finally:
            store_isolation.end()
        self.assertEqual(os.environ.get("JOB_APPLICATION_DIR"), before)

    def test_it_moves_module_constants_it_was_never_told_about(self):
        # Discovery is the whole point: a constant added to scripts/ tomorrow
        # has to be covered without anyone editing store_isolation.
        original = providers.STATE_FILE
        root = store_isolation.begin()
        try:
            self.assertEqual(providers.STATE_FILE, root / "providers.json")
            self.assertEqual(providers.STATE_DIR, root)
        finally:
            store_isolation.end()
        self.assertEqual(providers.STATE_FILE, original)

    def test_a_registry_built_with_no_arguments_follows_the_redirect(self):
        # The measured hole. ProviderRegistry's state_file default was bound at
        # import, so `ProviderRegistry()` wrote the device's own providers.json
        # while `_default_registry()`, which passes STATE_FILE, honoured the
        # redirect. Two behaviours from one constructor.
        root = store_isolation.begin()
        try:
            self.assertEqual(providers.ProviderRegistry().state_file,
                             root / "providers.json")
        finally:
            store_isolation.end()

    def test_redirects_nest(self):
        outer = store_isolation.begin()
        inner = store_isolation.begin()
        self.assertNotEqual(outer, inner)
        self.assertEqual(providers.STATE_DIR, inner)
        store_isolation.end()
        self.assertEqual(providers.STATE_DIR, outer)
        store_isolation.end()

    def test_ending_without_beginning_is_an_error_rather_than_a_shrug(self):
        with self.assertRaises(RuntimeError):
            store_isolation.end()

    def test_the_ledger_a_redirected_run_writes_is_the_temporary_one(self):
        root = store_isolation.begin()
        try:
            row = provider_usage.record("acme-pm", "openai", "gpt-5.6-luna",
                                        "form", None)
            self.assertIsNotNone(row)
            self.assertTrue((root / "usage.jsonl").exists())
        finally:
            store_isolation.end()

    def test_a_module_outside_sys_modules_is_redirected_when_named(self):
        # test_watch builds its modules with spec_from_file_location, so they
        # are unreachable from sys.modules and have to be handed over.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_for_isolation_test", ROOT / "scripts" / "watch.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertNotIn("watch_for_isolation_test", sys.modules)

        original = module.STATE_DIR
        root = store_isolation.begin(module)
        try:
            self.assertEqual(module.STATE_DIR, root)
        finally:
            store_isolation.end()
        self.assertEqual(module.STATE_DIR, original)

    def test_the_module_a_swapped_sys_modules_entry_orphaned_is_still_redirected(self):
        """The one that only shows up when the whole suite runs.

        test_providers, test_provider_login, test_cover_letter and others
        execute a scripts module by hand and assign it over its sys.modules
        entry. Every module that imported the original keeps pointing at the
        original, so scanning sys.modules redirects the copy and leaves the
        live object naming the device store. Measured: run_apply's
        providers.work_order() wrote the real providers.json from inside a
        module whose setUpModule had called begin().
        """
        import importlib.util
        import run_apply

        # Whatever run_apply actually calls, which in a full suite run may
        # already be an orphan: test_providers swaps the entry at import time,
        # so this must not assume it is still the one in sys.modules.
        held = run_apply.providers
        before = held.STATE_FILE
        previous = sys.modules.get("providers")
        self.addCleanup(sys.modules.__setitem__, "providers", previous)

        spec = importlib.util.spec_from_file_location(
            "providers", ROOT / "scripts" / "providers.py")
        replacement = importlib.util.module_from_spec(spec)
        sys.modules["providers"] = replacement
        spec.loader.exec_module(replacement)

        self.assertIs(run_apply.providers, held)
        self.assertIsNot(sys.modules["providers"], held)

        root = store_isolation.begin()
        try:
            self.assertEqual(held.STATE_FILE, root / "providers.json",
                             "the module run_apply calls was left behind")
            self.assertEqual(replacement.STATE_FILE, root / "providers.json")
        finally:
            store_isolation.end()
        self.assertEqual(held.STATE_FILE, before)

    def test_a_module_outside_sys_modules_is_missed_when_it_is_not(self):
        # Stated as a test rather than only in a docstring, because it is the
        # limit someone will walk into: begin() cannot find what nothing holds.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "watch_unnamed_isolation_test", ROOT / "scripts" / "watch.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        original = module.STATE_DIR
        store_isolation.begin()
        try:
            self.assertEqual(module.STATE_DIR, original)
        finally:
            store_isolation.end()


class EveryModuleThatDrivesTheStoreIsolatesItTests(unittest.TestCase):
    """The five modules measured writing to the device store, kept isolated.

    This catches the isolation being removed. It does not catch a sixth module
    being added, and saying so is the point: the only thing that catches that is
    running the suite against a redirected HOME and looking at what appears,
    which is how all five of these were found. `store_isolation` names that
    procedure in full.
    """

    MEASURED = {
        "test_apply_capability_contract": "rewrote browser-mcp-apply-1.json",
        "test_browser_slot_affinity": "filed acme-pm in sessions/ and roles.json",
        "test_document_worker": "five usage.jsonl rows a run",
        "test_provider_runners": "ten usage.jsonl rows a run",
        "test_watch": "wrote providers.json",
    }

    def test_each_one_still_calls_begin(self):
        missing = []
        for name, why in sorted(self.MEASURED.items()):
            source = (pathlib.Path(__file__).parent / f"{name}.py").read_text(
                encoding="utf-8")
            if "store_isolation.begin(" not in source:
                missing.append(f"{name} ({why})")
        self.assertEqual(missing, [], f"isolation removed from: {missing}")

    def test_each_one_puts_it_back(self):
        missing = [
            name for name in sorted(self.MEASURED)
            if "store_isolation.end()" not in
            (pathlib.Path(__file__).parent / f"{name}.py").read_text(encoding="utf-8")
        ]
        self.assertEqual(missing, [], f"redirect never undone in: {missing}")


class NoStorePathIsAFrozenDefaultTests(unittest.TestCase):
    """A store path may not be a default argument anywhere under scripts/.

    Python binds a default once, when the def executes, so `state_file=STATE_FILE`
    keeps naming the path the module held at import however the constant is set
    afterwards. That is invisible in review -- the signature reads correctly --
    and it is what let the provider registry ignore a redirect that every other
    caller respected. Five sites had it.
    """

    def test_no_default_argument_names_a_store_constant(self):
        offenders = []
        for source in sorted((ROOT / "scripts").glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            constants = set()
            for _ in range(3):
                for node in tree.body:
                    if not isinstance(node, ast.Assign):
                        continue
                    text = ast.unparse(node.value)
                    if (".job-application" in text
                            or any(name in text for name in constants)):
                        constants.update(
                            t.id for t in node.targets if isinstance(t, ast.Name))
            if not constants:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                defaults = list(node.args.defaults) + [
                    d for d in node.args.kw_defaults if d is not None]
                for default in defaults:
                    text = ast.unparse(default)
                    if any(text == name or text.startswith(f"{name}.")
                           or text.startswith(f"{name} /") for name in constants):
                        offenders.append(
                            f"{source.name}:{node.lineno} {node.name}(...={text})")
        self.assertEqual(
            offenders, [],
            "a store path bound at import time, which no redirect can move: "
            f"{offenders}")


class TheRecordIsUnchangedTests(unittest.TestCase):
    """The guard sits in front of the write and changes nothing behind it."""

    def test_a_recorded_row_still_carries_everything_it_did(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = provider_usage.record(
                "app9", "openai", "m", "documents",
                provider_usage.parse('{"usage":{"input_tokens":10,'
                                     '"output_tokens":2}}'),
                root=tmp, prices=None)
            self.assertTrue(row.measured)
            stored = json.loads(
                (pathlib.Path(tmp) / "usage.jsonl").read_text().strip())
            self.assertEqual(stored["applicationId"], "app9")
            self.assertEqual(stored["usage"]["input_tokens"], 10)

    def test_an_unwritable_root_still_returns_none_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            wall = pathlib.Path(tmp) / "wall"
            wall.write_text("not a directory")
            self.assertIsNone(provider_usage.record(
                "app10", "openai", "m", "form", None,
                root=wall / "under", prices=None))

    def test_a_root_that_is_not_a_path_at_all_returns_none(self):
        self.assertIsNone(provider_usage.record(
            "app11", "openai", "m", "form", None, root=object(), prices=None))
