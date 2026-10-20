"""What v2's upload writes is what the device takes.

A base resume upload crosses three files that have never been run against
each other: `planResumeUpload` and `planResumeEdit` in `deploy/profile_edits.mjs`
(which v2 runs through its generated copy), `queue.mjs upload-take`, and
`watch._resume_command` / `install_uploaded_resume`. Each is correct on its own
and the property only exists between them, which is the shape CLAUDE.md keeps
finding: two correct halves and a composition nobody checked.

So the JavaScript planners are run in node on a real body, and what they
produce is handed to the real Python that consumes it.
"""

import base64
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


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


def _node():
    import toolpath
    return toolpath.find("node", required=False)


def run_js(expression, body):
    node = _node()
    if node is None:
        raise unittest.SkipTest("node is not installed")
    edits = json.dumps(str(REPO / "apps" / "web" / "app" / "_ui" / "profileEdits.generated.mjs"))
    keys = json.dumps(str(REPO / "apps" / "web" / "app" / "_ui" / "deviceKeys.generated.mjs"))
    script = (f"import * as edits from {edits}; import * as keys from {keys};"
              "let raw='';process.stdin.on('data',c=>{raw+=c;}).on('end',()=>{"
              "const body=JSON.parse(raw);"
              f"console.log(JSON.stringify({expression}));}});")
    out = subprocess.run([node, "--input-type=module", "-e", script],
                         input=json.dumps(body), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError(f"node failed: {out.stderr[:400]}")
    return json.loads(out.stdout)


PDF = b"%PDF-1.7\n" + b"0" * 4096


class TheUploadReachesTheDevice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_ceiling_and_the_magic_agree_on_both_sides(self):
        said = run_js("({max: edits.MAX_UPLOAD_BYTES, magic: edits.PDF_MAGIC,"
                      " prefix: keys.UPLOAD_KEY_PREFIX})", {})
        self.assertEqual(said["max"], watch.MAX_UPLOAD_BYTES)
        self.assertEqual(said["magic"].encode("latin1"), b"%PDF-")
        self.assertEqual(said["prefix"], "jobby:upload:")

    def test_what_v2_parks_and_queues_is_what_the_watcher_installs(self):
        upload_id = "doc-0123456789abcdef"
        encoded = base64.b64encode(PDF).decode("ascii")
        parked = run_js("edits.planResumeUpload(body)",
                        {"uploadId": upload_id, "pdf": encoded})
        self.assertEqual(parked["uploadId"], upload_id)

        # The resume edit v2 sends next, carrying only the upload's name.
        planned = run_js("edits.planResumeEdit(body)", {
            "editId": "v2-abc", "action": "add", "key": "eu", "label": "Europe",
            "countries": ["de", "FR"], "copyFrom": "us", "uploadId": upload_id})
        self.assertEqual(planned["queue"], "resumeEdits")
        # The queue entry is `{applicationId: editId, ...extra}` (kvqueue.enqueue).
        entry = {"applicationId": planned["editId"], **planned["extra"]}
        self.assertNotIn("pdf", entry, "the document rode the shared queue key")

        # The device: `upload-take` hands back exactly what was parked.
        took = []

        def fake_run(argv, **kw):
            took.append(argv)
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"uploadId": argv[-1], "pdf": parked["encoded"]}), "")

        base_dir = pathlib.Path(self.tmp.name) / "base-resumes"
        with mock.patch.object(watch.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(watch.toolpath, "queue_env", return_value={}), \
             mock.patch.object(watch.toolpath, "node", return_value="node"), \
             mock.patch.object(watch, "BASE_RESUME_DIR", base_dir):
            argv, stdin = watch._resume_command(entry)

        self.assertEqual(took[0][1:], ["queue.mjs", "upload-take", upload_id])
        self.assertEqual(argv, ["resume-add", "--input", "-"])
        payload = json.loads(stdin)
        self.assertEqual(payload["key"], "eu")
        self.assertEqual(payload["countries"], ["DE", "FR"])
        self.assertEqual(payload["copyFrom"], "us")
        installed = pathlib.Path(payload["documentPath"])
        self.assertEqual(installed, base_dir / "eu.pdf")
        self.assertEqual(installed.read_bytes(), PDF)

    def test_an_add_without_an_upload_takes_nothing(self):
        planned = run_js("edits.planResumeEdit(body)", {
            "editId": "v2-abd", "action": "add", "key": "eu", "countries": ["DE"],
            "copyFrom": "us"})
        entry = {"applicationId": planned["editId"], **planned["extra"]}
        with mock.patch.object(watch.subprocess, "run") as spawned:
            argv, stdin = watch._resume_command(entry)
        spawned.assert_not_called()
        self.assertNotIn("documentPath", json.loads(stdin))

    def test_queue_mjs_reads_the_shared_prefix(self):
        source = (REPO / "deploy" / "queue.mjs").read_text()
        self.assertIn("import { UPLOAD_KEY_PREFIX } from './device_keys.mjs';", source)
        self.assertIn("kvGet(env, `${UPLOAD_KEY_PREFIX}${prefix}`)", source)
        self.assertIn("kvDelete(env, `${UPLOAD_KEY_PREFIX}${prefix}`)", source)
