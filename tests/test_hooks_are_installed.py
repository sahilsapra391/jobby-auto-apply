"""The pre-commit guards must actually be bound in the checkout doing the work.

**A guard that has never bound is worse than no guard, because it is counted as
coverage.** `.githooks/pre-commit` is the third enforcement point for the image
allowlist named in CLAUDE.md and the only one for the `JOBBY_ADD` staging rule.
It runs through `core.hooksPath`, which is git config and therefore per clone,
and a fresh `git clone` has it unset. Nothing in a repository can set it during
a clone; git runs no repository-supplied code there, deliberately.

Measured 2026-09-04:

    production checkout   core.hooksPath = .githooks    hook active
    ~/dev clone           core.hooksPath = (unset)      hook inert

So in that clone `git add -A` had never been gated, and nothing said so,
because a hook that is not installed does not fail.

**The gap is clones and not worktrees, which the first write-up got wrong.** A
linked worktree shares the repository's config, so `git worktree add` from a
configured checkout inherits `core.hooksPath`. Verified by creating one and
reading the value back, after asserting the opposite from the fact that one
clone had it unset -- the observation was right and the mechanism inferred from
it was not.

**Why this skips under CI, and why that is not the usual excuse.** The property
is "the person committing from this checkout is gated". CI does not commit, so
there is nothing there for the hook to gate and no fresh clone to fix; asserting
it would fail every run and teach everyone to ignore the file. That is different
from the skips that hide things, where the subject exists and the check declines
to look. Here the subject does not exist.

What keeps this honest is that it runs in every developer checkout, which is
exactly where the gap was.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import install_hooks  # noqa: E402

IN_CI = os.environ.get("CI", "").lower() == "true"


@unittest.skipIf(IN_CI, "CI never commits, so it has no hook to bind and no "
                        "fresh clone to fix; the subject does not exist here")
class TheHooksAreBoundInThisCheckout(unittest.TestCase):
    def test_core_hookspath_points_at_the_repository_hooks(self):
        self.assertEqual(
            install_hooks.configured(), install_hooks.EXPECTED,
            "the pre-commit guards are inert in this checkout: the image "
            "allowlist loses its third enforcement point and JOBBY_ADD loses "
            "its only one. Fix it with:\n"
            "    python3 scripts/install_hooks.py")

    def test_every_hook_is_executable(self):
        """git skips a non-executable hook without a word, which is the same
        silence as not being bound at all."""
        limp = [p.name for p in install_hooks.hooks()
                if not install_hooks.executable(p)]
        self.assertEqual(limp, [],
                         f"git will skip these silently: {limp}. "
                         f"python3 scripts/install_hooks.py fixes the mode.")


class TheInstallerAnswersHonestly(unittest.TestCase):
    """These run everywhere, CI included: they are about the script, not about
    this machine's configuration."""

    def test_check_fails_in_a_checkout_with_no_binding(self):
        """The half that matters. Verified against a real throwaway repository
        rather than a mock, because the thing under test is what `git config`
        says in a clone nobody has configured."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, timeout=30)
            self.assertEqual(install_hooks.configured(tmp), "",
                             "a fresh repository should have no hooksPath, "
                             "which is the whole reason this exists")

    def test_it_reports_a_binding_when_there_is_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, timeout=30)
            subprocess.run(["git", "config", "core.hooksPath", ".githooks"],
                           cwd=tmp, check=True, timeout=30)
            self.assertEqual(install_hooks.configured(tmp), ".githooks")

    def test_an_unreadable_config_reads_as_unset_rather_than_raising(self):
        """`configured` is called from a test that must fail loudly, so it may
        not raise on its own account and turn a real answer into an error."""
        self.assertEqual(install_hooks.configured("/nonexistent-checkout"), "")

    def test_the_refusal_names_the_command_that_fixes_it(self):
        """**Asserted from the message a person actually sees**, not from the
        source that produces it. A first version of this read this very file
        and checked the string appeared in it, which is the scanner shape this
        repository has now removed five times: it would have passed with the
        command spelled only in a comment.

        So it runs `--check` in a real unbound repository and reads stderr.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, timeout=30)
            done = subprocess.run(
                [sys.executable, str(REPO / "scripts" / "install_hooks.py"),
                 "--check", "--repo", tmp],
                capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1,
                         "an unbound checkout must exit non-zero; a check that "
                         "cannot fail is the thing being fixed here")
        self.assertIn("python3 scripts/install_hooks.py", done.stderr)
        self.assertIn("inert", done.stderr)
