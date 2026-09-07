"""What may reach a builder, and what deploys may create.

**`applications/` reached a remote builder.** The image was clean -- the
Dockerfile copies three paths and nothing else -- but the *context* is uploaded
whole, and on 2026-09-07 that was 118 MB across 11,704 files: `.git` 49 MB,
`.claude` 33 MB, and `applications/` 9.4 MB of real resumes and cover letters
carrying a real name, address, phone number and email. None of it belonged on a
builder disk, and no line of the Dockerfile said otherwise, because the
Dockerfile is not what decides.

**And `fly deploy` created two machines against a config with no service
block.** `--ha` defaults to true -- "Create spare machines that increases app
availability" -- so HA is a deploy-time flag rather than a consequence of
`[[services]]`. A standby for this app is a second sweeper running in parallel
with the first.

Both checks below read the *effective* file rather than a paraphrase of it, and
the workflow one strips comments and error strings, because a throwaway version
of it reported "creates nothing: False" on the sentence telling a reader how to
create one.
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class DockerignoreTests(unittest.TestCase):
    """The allowlist, and the three directories it exists for."""

    def patterns(self):
        text = (REPO / ".dockerignore").read_text()
        return [line.strip() for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    def test_it_denies_everything_before_allowing_anything(self):
        # Deny-then-allow, not allow-then-deny. A denylist is a list somebody
        # has to remember to extend, and what it forgets is by definition what
        # nobody thought of -- which is how `applications/` unanchored in
        # .gitignore once swallowed an API route.
        self.assertEqual(self.patterns()[0], "*",
                         "the first pattern is not a total deny, so anything "
                         "unnamed travels")

    def test_the_three_that_leaked_are_not_reachable(self):
        # None of these is named in the file, and that is the point: the deny
        # is total and they were never allowed back. Asserting they are absent
        # from the allowlist is the check, not asserting they are listed.
        allowed = {p.lstrip("!").rstrip("/*").split("/")[0]
                   for p in self.patterns() if p.startswith("!")}
        for leaked in ("applications", ".git", ".claude"):
            with self.subTest(directory=leaked):
                self.assertNotIn(leaked, allowed)

    def test_it_allows_exactly_what_the_dockerfile_copies(self):
        # Derived from the Dockerfile rather than restated, so a fourth COPY
        # fails this instead of silently shipping an empty directory.
        docker = (REPO / "deploy" / "sourcing" / "Dockerfile").read_text()
        copied = {line.split()[1].rstrip("/").split("/")[0]
                  for line in docker.splitlines()
                  if line.startswith("COPY ")}
        allowed = {p.lstrip("!").rstrip("/*").split("/")[0]
                   for p in self.patterns() if p.startswith("!")}
        missing = sorted(copied - allowed)
        self.assertEqual(missing, [],
                         f"the Dockerfile copies {missing} and the context "
                         f"excludes it, so the build fails on a path that is "
                         f"there")

    def test_it_re_denies_the_working_files_inside_what_it_allows(self):
        # `scripts/_*` is a real pattern of untracked diagnostics in this repo,
        # and allowing `scripts/**` would otherwise carry them.
        self.assertIn("scripts/_*", self.patterns())

    def test_only_one_file_from_docs_is_allowed(self):
        patterns = self.patterns()
        self.assertIn("docs/*", patterns, "docs is allowed wholesale")
        self.assertIn("!docs/write-paths.md", patterns)


class DeployWorkflowTests(unittest.TestCase):
    """The workflow, read as commands rather than as text.

    **Stripped of comments and of quoted strings**, because a first version of
    this check reported that the workflow creates no machines as False, on the
    error message telling a reader to create one. Third instance in one session
    of a scanner reading text about a thing as the thing.
    """

    def commands(self):
        text = (REPO / ".github" / "workflows" / "sourcing.yml").read_text()
        out = []
        for line in text.splitlines():
            bare = line.split("#", 1)[0]
            # Drop anything inside quotes: an error message naming a command is
            # not the workflow running it.
            bare = re.sub(r'"[^"]*"', '""', bare)
            bare = re.sub(r"'[^']*'", "''", bare)
            out.append(bare)
        return "\n".join(out)

    def raw(self):
        return (REPO / ".github" / "workflows" / "sourcing.yml").read_text()

    def test_it_builds_and_pushes_without_deploying(self):
        self.assertIn("--build-only", self.commands())
        self.assertIn("--push", self.commands())

    def test_it_creates_no_machines(self):
        # A standby for this app is a second sweeper running in parallel.
        self.assertNotIn("machine run", self.commands())
        self.assertNotIn("machines create", self.commands())

    def test_the_strip_does_not_hide_a_real_creation(self):
        # The control for the strip. If someone adds a real `machine run` step,
        # stripping comments and strings must not make it invisible.
        sample = '# use machine run to create one\n  run: flyctl machine run x\n'
        stripped = "\n".join(re.sub(r'"[^"]*"', '""', l.split("#", 1)[0])
                             for l in sample.splitlines())
        self.assertIn("machine run", stripped)

    def test_it_labels_the_image_with_the_commit(self):
        # flyctl's default is `deployment-{timestamp}`, which cannot be traced
        # back to a merge.
        self.assertIn("--image-label", self.commands())
        self.assertIn("GITHUB_SHA", self.raw())

    def test_it_reads_back_every_machine_and_fails_on_a_stale_one(self):
        raw = self.raw()
        self.assertIn("flyctl machines list", raw)
        self.assertIn("::error::machine running", raw)

    def test_it_refuses_when_no_machine_exists(self):
        # Zero machines satisfies a loop over every machine, which is the
        # anti-vacuity hole this repository keeps meeting.
        self.assertIn("no machines exist", self.raw())

    def test_deploys_do_not_overlap(self):
        self.assertIn("concurrency:", self.raw())

    def test_the_workflow_reruns_when_the_ignore_file_changes(self):
        # The `.dockerignore` decides what reaches the builder. A change to it
        # that does not trigger a build is a change nobody has tested.
        self.assertIn(".dockerignore", self.raw())
