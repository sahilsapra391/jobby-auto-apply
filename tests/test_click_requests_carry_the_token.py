"""Every board mutation must carry the x-jobby-request token.

The deployed API refuses any POST without the token as a cross-site request
(rejectCrossSiteMutation in deploy/api/board.js), so an embedded script whose
fetch omits it is a button that silently stopped working. That is not a
hypothetical: found_lane.py grew its own post() helper on 2026-08-19, the
token check landed server-side on 2026-08-23, and every Watch, Pass and Apply
click from the Found lane 403ed for the five days until 2026-08-28. The toast
said "That did not reach the queue" and named no reason, so fifteen sessions
of bug-fixing walked straight past it.

The scan is textual because the scripts are JavaScript embedded in Python
strings, some under f-string brace doubling, and running each one against a
DOM stub proves parsing rather than headers. A window around each POST is
enough: the headers object sits inside the same fetch call, always within a
few lines. The test counts what it inspected and holds a floor, so a matcher
that quietly stops matching fails instead of passing over nothing — the same
rule test_diagnostics_are_kept.py follows.
"""

import pathlib
import re
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

POST_MARKER = re.compile(r"method:\s*['\"]POST['\"]")
TOKEN = "x-jobby-request"

# Raise this when a new mutation is added; lowering it means a click surface
# disappeared and somebody should know whether that was on purpose.
KNOWN_POST_CALLS = 7


class ClickRequestsCarryTheToken(unittest.TestCase):
    def collect(self):
        """(file, offset, window) for every embedded POST fetch."""
        found = []
        for path in sorted(SCRIPTS.glob("*.py")):
            if path.name.startswith("_"):
                continue
            text = path.read_text(encoding="utf-8")
            for match in POST_MARKER.finditer(text):
                start = text.rfind("fetch(", 0, match.start())
                if start < 0 or match.start() - start > 300:
                    # A POST literal with no fetch beside it is config or
                    # server-side text, not a browser mutation.
                    continue
                found.append((path.name, start, text[start:match.end() + 600]))
        return found

    def test_every_embedded_post_sends_the_token(self):
        calls = self.collect()
        missing = [(name, at) for name, at, window in calls if TOKEN not in window]
        self.assertEqual(
            missing, [],
            "these fetch POSTs omit the %s header, so the deployed API will "
            "refuse them as cross-site and the click will die with a toast: %r"
            % (TOKEN, missing),
        )

    def test_the_scan_still_sees_the_posts_it_was_written_against(self):
        calls = self.collect()
        self.assertGreaterEqual(
            len(calls), KNOWN_POST_CALLS,
            "the scan found %d POST fetches where %d were known; either a "
            "click surface was removed or the matcher stopped matching, and "
            "both deserve a person looking" % (len(calls), KNOWN_POST_CALLS),
        )
