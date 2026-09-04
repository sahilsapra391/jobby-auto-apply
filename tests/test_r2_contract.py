"""scripts/r2.py and deploy/r2.mjs sign the same request. Prove it, don't assume.

The device uploads and the deployed API mints the links, so SigV4 is written
twice, once in Python and once in JavaScript. That is the same shape as the
company-logo derivation, where a first version of the contract test covered
only the plans and passed happily with a legal suffix deleted from the script's
copy. So this compares complete URLs, character for character, over the cases
the two implementations are most likely to disagree on: URI encoding, the
canonical query sort, and the extra response-header parameters.

A disagreement here is not cosmetic. The device would stamp an object the API
could not sign a link for, and every document on the board would 403 from R2
while both halves passed their own tests.
"""

import datetime
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("r2", ROOT / "scripts" / "r2.py")
r2 = importlib.util.module_from_spec(spec)
sys.modules["r2"] = r2
spec.loader.exec_module(r2)

ACCOUNT = "0123456789abcdef0123456789abcdef"
BUCKET = "jobby-artifacts"
ACCESS_KEY = "fedcba9876543210fedcba9876543210"
SECRET = "a" * 64
WHEN = datetime.datetime(2026, 8, 25, 12, 34, 56, tzinfo=datetime.timezone.utc)
WHEN_MS = int(WHEN.timestamp() * 1000)

# Every case that has ever been a difference between two URI encoders, plus the
# real key shapes: an application id, a company folder with a space, brackets,
# an ampersand, and a name long enough to be nothing special.
CASES = [
    {"key": "u/account-1/app/bc839847c93d9e46/resume.pdf", "expires": 300, "headers": None},
    {"key": "u/account-1/app/bc839847c93d9e46/cover.html", "expires": 60, "headers": None},
    {"key": "u/account-1/app/bc839847c93d9e46/confirmation.png", "expires": 3600, "headers": None},
    {"key": "u/account-1/unfiled/Keka HR/Sahil_Sapra_KekaHR_Resume.pdf",
     "expires": 300, "headers": None},
    {"key": "u/account-1/unfiled/Leucine (AI for Pharma)/CV.pdf", "expires": 300, "headers": None},
    {"key": "u/account-1/unfiled/Cognite - AI for Industry/a&b.pdf", "expires": 300, "headers": None},
    {"key": "u/account-1/unfiled/Tilde~Co/it's here!.pdf", "expires": 300, "headers": None},
    {"key": "u/account-1/unfiled/Ünïcode/résumé.pdf", "expires": 300, "headers": None},
    {"key": "u/account-1/unfiled/Plus+Co/a+b=c.pdf", "expires": 300, "headers": None},
    {"key": "u/account-1/unfiled/Star*Co/100%.pdf", "expires": 300, "headers": None},
    {"key": "u/account-1/app/aa/resume.pdf", "expires": 300, "headers": {
        "response-content-type": "application/pdf",
        "response-content-disposition": 'inline; filename="Sahil Sapra (Resume).pdf"'}},
    {"key": "u/account-1/app/aa/cover.html", "expires": 300, "headers": {
        "response-content-type": "text/html; charset=utf-8",
        "response-content-disposition": 'inline; filename="a&b.html"'}},
]

NODE_SCRIPT = """
import { presignGet } from './r2.mjs';
const env = {
  R2_ACCOUNT_ID: process.env.R2_ACCOUNT_ID,
  R2_BUCKET: process.env.R2_BUCKET,
  R2_ACCESS_KEY_ID: process.env.R2_ACCESS_KEY_ID,
  R2_SECRET_ACCESS_KEY: process.env.R2_SECRET_ACCESS_KEY,
};
const cases = JSON.parse(process.env.CASES);
const when = Number(process.env.WHEN_MS);
const out = [];
for (const c of cases) out.push(await presignGet(env, c.key, c.expires, when, c.headers));
console.log(JSON.stringify(out));
"""


def node_urls():
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT],
        cwd=ROOT / "deploy", capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
             "R2_ACCOUNT_ID": ACCOUNT, "R2_BUCKET": BUCKET,
             "R2_ACCESS_KEY_ID": ACCESS_KEY, "R2_SECRET_ACCESS_KEY": SECRET,
             "CASES": json.dumps(CASES), "WHEN_MS": str(WHEN_MS)})
    if proc.returncode != 0:
        raise AssertionError(f"the deployed signer would not run: {proc.stderr[:500]}")
    return json.loads(proc.stdout)


class SigningContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = r2.Client(ACCOUNT, BUCKET, ACCESS_KEY, SECRET)
        cls.node = node_urls()

    def test_every_case_signs_identically_in_both_languages(self):
        self.assertEqual(len(self.node), len(CASES))
        for case, from_node in zip(CASES, self.node):
            with self.subTest(key=case["key"]):
                from_python = self.client.presign_get(
                    case["key"], case["expires"], now=WHEN,
                    response_headers=case["headers"])
                self.assertEqual(from_python, from_node)

    def test_the_cases_actually_produce_different_signatures(self):
        # A contract test that compares two constants passes forever. This is
        # the check that the matrix is exercising the signer at all.
        signatures = {url.rsplit("X-Amz-Signature=", 1)[1] for url in self.node}
        self.assertEqual(len(signatures), len(CASES))

    def test_both_halves_default_the_bucket_and_the_expiry_the_same_way(self):
        python_default = r2.DEFAULT_EXPIRY_SECONDS
        source = (ROOT / "deploy" / "r2.mjs").read_text(encoding="utf-8")
        self.assertIn(f"DEFAULT_EXPIRY_SECONDS = {python_default}", source)
        self.assertIn(f"DEFAULT_BUCKET = '{r2.DEFAULT_BUCKET}'", source)
