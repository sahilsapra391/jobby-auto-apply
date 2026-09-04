"""Nothing tracked in this repository carries Sahil's personal data.

This exists because 39 screenshots of filled application forms were tracked at
the repo root and pushed, 7.8MB of them, including a completed EEO section with
race and gender selected and a phone number in the clear. The rule that was
supposed to prevent it only looked at tests/fixtures/, which is the one place
the leak was not.

Two rules, deliberately different in kind:

  Images fail by rule. A screenshot of an application form cannot be shown to be
  safe by inspecting it, and nobody is going to OCR a PNG in a unit test. So no
  image is tracked outside the brand assets and the deploy favicons, full stop.

  Text is scanned for markers taken from the live profile at run time, rather
  than written into this file. Putting the real phone number into a test that
  exists to keep the real phone number out of the repository would be an odd way
  to go about it.

Run from the repo root: python3 -m unittest discover -s tests
"""

import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORE = pathlib.Path.home() / ".job-application" / "profile.json"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic"}

# The only images that belong in the tree: the brand kit, the favicons the
# deployed board serves, and the brand-pack copy sync_vercel_renderer.py writes
# into the function bundle. Everything else is a screenshot until proven
# otherwise.
IMAGE_ALLOWED = ("assets/", "deploy/assets/", "deploy/public/",
                 "apps/web/public/")

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".html", ".js", ".mjs", ".css",
                 ".yml", ".yaml", ".sh", ".plist", ".toml", ".jsonl"}


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [p for p in out.stdout.splitlines() if p.strip()]


def markers():
    """PII to search for, taken from the profile rather than written down here.

    Returns (marker, label) pairs. Short or ambiguous values are skipped: a
    first name or a city matches too much ordinary prose to be a signal.
    """
    if not STORE.exists():
        return []
    try:
        profile = json.loads(STORE.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    found = []
    for entry in profile.get("resumes") or []:
        identity = entry.get("identity") or {}
        phone = re.sub(r"\D", "", str(identity.get("phone") or ""))
        if len(phone) >= 10:
            found.append((phone[-10:], "phone number"))
        address = identity.get("fullAddress") or {}
        street = str(address.get("street") or "").strip()
        if len(street) > 8:
            found.append((street.lower(), "street address"))
        postal = str(address.get("postalCode") or "").strip()
        if len(postal) >= 5:
            found.append((postal, "postal code"))
    email = str(profile.get("email") or "").strip().lower()
    if "@" in email:
        found.append((email, "personal email"))
    return found


def digits_only(text):
    return re.sub(r"\D", "", text)


class TrackedImageTests(unittest.TestCase):
    def test_no_screenshots_are_tracked(self):
        offenders = [
            p for p in tracked()
            if pathlib.Path(p).suffix.lower() in IMAGE_SUFFIXES
            and not p.startswith(IMAGE_ALLOWED)
        ]
        self.assertEqual(
            offenders, [],
            "Images outside the brand assets are agent screenshots of filled "
            "application forms. They cannot be shown to be safe, so none are "
            "tracked. Remove with: git rm --cached <path>\n" + "\n".join(offenders))


class TrackedTextTests(unittest.TestCase):
    def setUp(self):
        self.markers = markers()

    def test_the_scan_has_something_to_look_for(self):
        # Without the profile this suite would pass by finding nothing, which
        # reads identical to finding nothing wrong. Say which it is.
        if not self.markers:
            self.skipTest(f"no profile at {STORE}; the text scan cannot run here")
        self.assertGreater(len(self.markers), 1)

    def test_no_tracked_text_file_carries_profile_data(self):
        if not self.markers:
            self.skipTest("no profile on this machine")
        hits = []
        for rel in tracked():
            path = ROOT / rel
            if path.suffix.lower() not in TEXT_SUFFIXES or not path.exists():
                continue
            try:
                body = path.read_text(errors="replace")
            except OSError:
                continue
            low = body.lower()
            packed = digits_only(body)
            for marker, label in self.markers:
                present = (marker in packed) if marker.isdigit() else (marker in low)
                if present:
                    hits.append(f"{rel}: {label}")
        self.assertEqual(hits, [], "tracked files carrying profile data:\n"
                         + "\n".join(sorted(set(hits))))


class TheApplicationsRuleIsAnchored(unittest.TestCase):
    """`/applications/` protects the delivered resumes, and only those.

    **Unanchored it matched any directory of that name at any depth**, which
    silently swallowed `apps/web/app/api/applications/`. The write endpoint
    built and answered 503 locally, where the file is on disk, and 404'd from
    the traced bundle in CI because it had never been committed. A rule
    protecting real personal data eating source code is the worst way round for
    both, and the local check could not see it: it read the working tree.

    Both directions, because either alone is half a check. Dropping the anchor
    passes the first assertion and fails the second; deleting the rule passes
    the second and fails the first.
    """

    def ignored(self, path):
        out = subprocess.run(["git", "check-ignore", "-q", path],
                             cwd=ROOT, capture_output=True)
        return out.returncode == 0

    def test_the_delivered_documents_are_still_ignored(self):
        self.assertTrue(
            self.ignored("applications"),
            "applications/ at the repo root holds resumes carrying a real "
            "name, phone and street address, and must never be tracked")

    def test_a_nested_directory_of_that_name_is_not(self):
        self.assertFalse(
            self.ignored("apps/web/app/api/applications/route.ts"),
            "the rule is anchored to the repo root; unanchored it eats source "
            "code that happens to share the name")

    def test_the_endpoint_is_actually_tracked(self):
        """The assertion above is about the rule. This one is about the file,
        because a route git ignores and a route nobody wrote look identical
        from CI."""
        out = subprocess.run(["git", "ls-files",
                              "apps/web/app/api/applications/route.ts"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertTrue(out.stdout.strip(),
                        "the write endpoint is not tracked, so it cannot be "
                        "in any build but this working tree")
