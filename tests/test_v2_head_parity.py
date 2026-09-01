"""v2's head is the board's head, and the check compares the two rather than
restating either.

The favicon is the reason this file exists. `deploy/public/` is Vercel's static
output directory and is not in the serverless bundle, so a renderer that reads
a path under it works on every laptop and 404s deployed; the board already paid
for that once. v2 inherits the resolution by carrying the *rendered* links, and
this test fails if it ever carries fewer than the board emits.

**Asserted against `dashboard.build`, never against a list written here.** A
test that spells out "there are three icons" drifts the moment the board grows
a fourth, and it drifts silently, in the passing direction.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

CHROME = REPO / "apps" / "web" / "app" / "chrome.generated.ts"
PUBLIC = REPO / "apps" / "web" / "public"


def head_tags(markup):
    """Every meta and link in a head, as comparable tuples.

    Attribute *values* for the icons are compared by their first 40 characters:
    a data URI is tens of kilobytes and comparing it whole turns a readable
    failure into an unreadable one, while the prefix still distinguishes an
    SVG from an ICO from a PNG.
    """
    out = set()
    for kind, attrs in re.findall(r"<(meta|link)\b([^>]*)>", markup, re.I):
        pairs = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', attrs))
        if "href" in pairs:
            pairs["href"] = pairs["href"][:40]
        out.add((kind.lower(), tuple(sorted(pairs.items()))))
    return out


def generated(name):
    """One export out of the generated module, decoded as the JSON it is."""
    source = CHROME.read_text(encoding="utf-8")
    match = re.search(rf"^export const {name} = (.*);$", source, re.M)
    if not match:
        raise AssertionError(f"{name} is not exported from chrome.generated.ts")
    return json.loads(match.group(1))


class HeadParityTests(unittest.TestCase):
    def setUp(self):
        import dashboard
        import sync_v2_styles
        self.board = dashboard.build(sync_v2_styles.SYNTHETIC_STATE,
                                     "2026-01-01T00:00:00Z")

    def test_every_head_tag_the_board_emits_is_carried(self):
        board_head = self.board[:self.board.index("<style")]
        want = head_tags(board_head)
        got = head_tags(generated("HEAD"))
        self.assertTrue(want, "the board rendered no head tags; the check is vacuous")
        missing = want - got
        self.assertFalse(missing, f"the board's head carries tags v2 does not: {missing}")

    def test_the_three_icons_and_the_manifest_are_all_present(self):
        """The count comes from the board, not from this file."""
        board_head = self.board[:self.board.index("<style")]
        board_icons = re.findall(r'<link[^>]*rel="(icon|apple-touch-icon|manifest)"',
                                 board_head)
        carried = re.findall(r'<link[^>]*rel="(icon|apple-touch-icon|manifest)"',
                             generated("HEAD"))
        self.assertEqual(sorted(board_icons), sorted(carried))

    def test_the_theme_key_is_the_boards_own(self):
        """v2 had written `jobby-theme` while the board writes `jobby.theme`, so a
        theme chosen on one board was invisible to the other."""
        for name in ("THEME_BOOT", "THEME_WIRE"):
            self.assertIn("jobby.theme", generated(name),
                          f"{name} no longer names the board's storage key")

    def test_the_carried_scripts_parse_as_javascript(self):
        """**Present and parseable are different claims**, and the first version
        of this file only made the first one.

        The theme boot was extracted as a span beginning inside a `try {`, so
        it carried `} catch (e) {}` with no opener. Every assertion here passed,
        because the substrings it looked for were all there; the browser said
        `Unexpected token '}'` and both theme scripts died. A test that greps
        for a function name cannot tell a program from a fragment of one.
        """
        for name in ("THEME_BOOT", "THEME_WIRE", "SELECT_SCRIPT"):
            js = generated(name)
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
                handle.write(js)
                path = handle.name
            try:
                done = subprocess.run(["node", "--check", path],
                                      capture_output=True, text=True)
            finally:
                Path(path).unlink(missing_ok=True)
            self.assertEqual(done.returncode, 0,
                             f"{name} is not a parseable program:\n{done.stderr}")

    def test_the_wiring_actually_binds_the_controls(self):
        """A boot without a wiring is a control that renders and does nothing,
        which is what v2 shipped."""
        wire = generated("THEME_WIRE")
        self.assertIn("addEventListener", wire)
        self.assertIn("themebtn", wire)

    def test_every_file_the_manifest_names_is_in_the_static_directory(self):
        """The manifest is served from `public/`, and so are the icons it names.
        A manifest pointing at a 404 is the favicon outage in a second costume."""
        manifest = json.loads((PUBLIC / "site.webmanifest").read_text())
        for icon in manifest.get("icons", []):
            src = icon["src"].lstrip("/")
            self.assertTrue((PUBLIC / src).is_file(),
                            f"site.webmanifest names {icon['src']}, which is not in public/")


class GeneratorRefusesToEmitLessTests(unittest.TestCase):
    """The extractor raises on a moved marker rather than emitting a short head.

    Verified by breaking it, not by watching it work.
    """

    def test_a_missing_marker_is_fatal(self):
        import sync_v2_styles
        with self.assertRaises(SystemExit):
            sync_v2_styles._span("<head>nothing here</head>",
                                 "<meta charset=", "</head>", "the head")

    def test_a_start_without_an_end_is_fatal(self):
        import sync_v2_styles
        with self.assertRaises(SystemExit):
            sync_v2_styles._span("<meta charset=utf-8> and then nothing",
                                 "<meta charset=", "<link rel=\"manifest\"", "the head")


if __name__ == "__main__":
    unittest.main()
