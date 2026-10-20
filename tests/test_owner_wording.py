"""The owner-only wording copy: what the device publishes, when, and what it
keeps out of everywhere else.

v2's resume wording editor reads one KV key, `jobby:owner:resume-wording`,
behind an owner check. These pin the device half: `wording_publish.py` builds
the payload `owner-wording.mjs` accepts, `watch.refresh_owner_wording` runs it
when an input moves and not otherwise, a failure is reported rather than
swallowed and retried on a backoff, a test run cannot reach the real KV, and a
wording edit's outcome no longer carries the wording into `edit_outcomes`,
which the public Profile page reads.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import store_isolation  # noqa: E402
import watch  # noqa: E402
import wording_publish  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


def _node():
    import toolpath
    return toolpath.find("node", required=False)


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class ThePayload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        # No profile on purpose: the render needs one and fails, which is the
        # case where the editor says "could not render" instead of drawing an
        # empty page. The wording itself still travels.
        self.content = json.loads(
            (REPO / "templates" / "resume-content.json").read_text())
        (self.root / "resume-content.json").write_text(json.dumps(self.content))

    def publish(self, runner):
        return wording_publish.publish(self.root, runner=runner, node="node", env={})

    def test_it_hands_the_store_copy_to_the_node_writer(self):
        seen = {}

        def runner(argv, **kw):
            seen["argv"] = argv
            seen["cwd"] = kw.get("cwd")
            seen["payload"] = json.loads(pathlib.Path(argv[-1]).read_text())
            return Completed(stdout=json.dumps({"state": "published", "digest": "d"}))

        verdict = self.publish(runner)
        self.assertEqual(verdict["state"], "published")
        self.assertEqual(seen["argv"][1:3], ["owner-wording.mjs", "write"])
        self.assertEqual(pathlib.Path(seen["cwd"]), REPO / "deploy")
        self.assertEqual(seen["payload"]["content"], self.content)
        self.assertRegex(seen["payload"]["digest"], r"^[0-9a-f]{64}$")
        # The temporary file holding the wording does not outlive the call.
        self.assertFalse(pathlib.Path(seen["argv"][-1]).exists())

    def test_the_digest_follows_the_wording_and_nothing_else(self):
        first = wording_publish.snapshot(self.root)["digest"]
        self.assertEqual(wording_publish.snapshot(self.root)["digest"], first)
        changed = dict(self.content, banner=self.content["banner"] + " edited")
        (self.root / "resume-content.json").write_text(json.dumps(changed))
        self.assertNotEqual(wording_publish.snapshot(self.root)["digest"], first)

    def test_a_writer_that_fails_is_a_failure_with_its_reason(self):
        with self.assertRaises(RuntimeError) as caught:
            self.publish(lambda argv, **kw: Completed(1, stderr="KV write failed: 403"))
        self.assertIn("KV write failed: 403", str(caught.exception))

    @unittest.skipUnless(_node(), "node is not installed")
    def test_the_payload_is_the_shape_the_node_writer_accepts(self):
        """Python builds it and JavaScript guards the key, so they are run
        against each other rather than each against a restatement."""
        snap = wording_publish.snapshot(self.root)
        script = ("import { shapeOwnerWording } from "
                  + json.dumps(str(REPO / "deploy" / "device_keys.mjs")) + ";"
                  "let raw='';process.stdin.on('data',c=>{raw+=c;}).on('end',()=>{"
                  "console.log(JSON.stringify(shapeOwnerWording(JSON.parse(raw))));});")
        out = subprocess.run([_node(), "--input-type=module", "-e", script],
                             input=json.dumps(snap), capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        shaped = json.loads(out.stdout)
        self.assertEqual(shaped["content"], snap["content"])
        self.assertEqual(shaped["digest"], snap["digest"])
        self.assertEqual(set(shaped), {"schemaVersion", "publishedAt", "digest",
                                       "content", "render"})


class WhenTheWatcherPublishes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        patcher = mock.patch.dict("os.environ", {"JOB_APPLICATION_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        watch._owner_wording_signature = None
        watch._owner_wording_retry_at = 0.0
        self.calls = []

    def runner(self, result):
        def run(argv, **kw):
            self.calls.append(argv)
            return result
        return run

    def test_the_watcher_and_the_publisher_watch_the_same_files(self):
        self.assertEqual([str(p) for p in watch.owner_wording_inputs(self.root)],
                         [str(p) for p in wording_publish.inputs(self.root)])

    def test_the_first_tick_publishes_and_the_next_does_not(self):
        ok = self.runner(Completed(stdout=json.dumps({"state": "published", "digest": "d1"})))
        self.assertEqual(watch.refresh_owner_wording(now=0, runner=ok),
                         {"ownerWording": {"published": "d1"}})
        self.assertEqual(watch.refresh_owner_wording(now=5, runner=ok), {})
        self.assertEqual(len(self.calls), 1)

    def test_a_moved_input_publishes_again(self):
        ok = self.runner(Completed(stdout=json.dumps({"state": "unchanged"})))
        watch.refresh_owner_wording(now=0, runner=ok)
        (self.root / "resume-content.json").write_text('{"banner": "x"}')
        watch.refresh_owner_wording(now=5, runner=ok)
        self.assertEqual(len(self.calls), 2)

    def test_a_forced_refresh_runs_even_when_nothing_moved(self):
        ok = self.runner(Completed(stdout=json.dumps({"state": "unchanged"})))
        watch.refresh_owner_wording(now=0, runner=ok)
        watch.refresh_owner_wording(force=True, now=1, runner=ok)
        self.assertEqual(len(self.calls), 2)

    def test_a_failure_is_reported_and_retried_on_a_backoff(self):
        bad = self.runner(Completed(1, stderr="owner-wording.mjs failed: 403"))
        said = watch.refresh_owner_wording(now=0, runner=bad)
        self.assertIn("403", said["ownerWording"]["failed"])
        self.assertEqual(watch.refresh_owner_wording(now=60, runner=bad), {})
        self.assertEqual(len(self.calls), 1)
        watch.refresh_owner_wording(now=watch.OWNER_WORDING_RETRY_SECONDS + 1, runner=bad)
        self.assertEqual(len(self.calls), 2)

    def test_a_test_run_never_spawns_the_real_publisher(self):
        """This suite is a test run, so with no runner the function must refuse
        before reaching `subprocess.run`, which would otherwise write the real
        KV namespace from a fixture."""
        with mock.patch.object(watch.subprocess, "run") as spawned:
            self.assertEqual(watch.refresh_owner_wording(force=True), {})
        spawned.assert_not_called()

    def test_tick_calls_it(self):
        """A function nothing calls is the `activity.json` counter again."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(watch.tick))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn("refresh_owner_wording", called)


class TheWordingStaysOutOfOutcomes(unittest.TestCase):
    def test_a_wording_edit_records_no_submission(self):
        content = {"banner": "my employment history in my own words"}
        target, submitted = watch._edit_subject(
            "resumeEdits", {"action": "content", "content": content})
        self.assertIsNone(target)
        self.assertIsNone(submitted)

    def test_other_edits_still_record_what_was_sent(self):
        self.assertEqual(watch._edit_subject("answerEdits", {"key": "k", "value": "v"}),
                         ("k", "v"))
        self.assertEqual(
            watch._edit_subject("resumeEdits", {"action": "add", "key": "eu",
                                                "countries": ["DE"]}),
            ("eu", {"countries": ["DE"]}))

    def test_an_accepted_wording_edit_republishes_at_once(self):
        """The editor shows the KV copy, so an accepted edit has to reach it
        without waiting for a modification stamp that may not have moved."""
        item = {"applicationId": "wrd-1", "action": "content",
                "content": {"banner": "b"}}
        forced = []
        with mock.patch.object(watch, "claim_all", return_value=[item]), \
             mock.patch.object(watch, "_record_outcome") as recorded, \
             mock.patch.object(watch.subprocess, "run",
                               return_value=Completed(stdout='{"saved": true}')), \
             mock.patch.object(watch, "refresh_owner_wording",
                               side_effect=lambda **kw: forced.append(kw) or {}):
            watch.apply_profile_edits({"resumeEdits": [item]})
        self.assertEqual(forced, [{"force": True}])
        pending = recorded.call_args_list[0].args[0]
        self.assertEqual(pending["state"], "pending")
        self.assertIsNone(pending["submitted"])
