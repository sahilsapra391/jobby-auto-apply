"""A stale board has to set off an alarm that does not live on the board.

On 2026-08-27 publishing failed for about 22 hours and nothing rang. The
existing alarm watches whether a publish *attempt* failed, and two conditions
stacked that produce no failed attempt at all: the Vercel quota cooldown makes
publish() return early without raising, and under it the state had outgrown its
size ceiling. Throughout, the board served a real and complete snapshot from the
previous evening, so every surface anyone might check looked healthy.

The lesson is the one this project keeps relearning in new clothes: measure the
outcome, not the attempt.
"""

import importlib.util
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("watch", ROOT / "scripts" / "watch.py")
watch = importlib.util.module_from_spec(spec)
sys.modules["watch"] = watch
spec.loader.exec_module(watch)


class BoardStalenessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.original = watch.LAST_DEPLOY
        watch.LAST_DEPLOY = self.tmp / "last-deploy"

    def tearDown(self):
        watch.LAST_DEPLOY = self.original

    def publish_was(self, seconds_ago):
        watch.LAST_DEPLOY.write_text("x")
        when = time.time() - seconds_ago
        import os
        os.utime(watch.LAST_DEPLOY, (when, when))

    def test_a_board_published_moments_ago_is_not_stale(self):
        self.publish_was(30)
        self.assertEqual(watch.stale_board_warning(), "")
        self.assertLess(watch.board_staleness(), 60)

    def test_a_board_inside_the_window_is_not_stale(self):
        self.publish_was(watch.PUBLISH_STALE_SECONDS - 60)
        self.assertEqual(watch.stale_board_warning(), "")

    def test_a_board_past_the_window_raises_the_alarm(self):
        self.publish_was(watch.PUBLISH_STALE_SECONDS + 60)
        self.assertTrue(watch.stale_board_warning())

    def test_the_real_outage_would_have_rung(self):
        # 22 hours, which is what actually happened.
        self.publish_was(22 * 3600)
        warning = watch.stale_board_warning()
        self.assertIn("22.0 hours", warning)
        # It has to say the board looks fine, because that is the trap.
        self.assertIn("looks fine and is not", warning)
        self.assertIn("publish.py", warning)

    def test_a_board_that_has_never_published_is_not_an_alarm(self):
        # A fresh machine has nothing to be stale about, and crying wolf on
        # first run is how an alarm gets ignored later.
        self.assertIsNone(watch.board_staleness())
        self.assertEqual(watch.stale_board_warning(), "")

    def test_the_window_is_twice_the_publish_floor(self):
        self.assertEqual(watch.PUBLISH_STALE_SECONDS, 2 * watch.PUBLISH_FLOOR_SECONDS)

    def test_status_reports_the_age_rather_than_only_whether_it_is_configured(self):
        # `health` only ever meant "healthchecks has a URL", which read as
        # reassurance for a day while the board rotted.
        self.publish_was(22 * 3600)
        self.assertTrue(watch.stale_board_warning())
        self.assertGreater(watch.board_staleness(), 79000)


class CanonicalUrlTest(unittest.TestCase):
    """Never emit the host that answers a redirect."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("publish", ROOT / "scripts" / "publish.py")
        self.publish = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.publish)

    def test_the_apex_becomes_www(self):
        self.assertEqual(self.publish.canonical_url("https://jobby.fyi"),
                         "https://www.jobby.fyi")
        self.assertEqual(self.publish.canonical_url("https://jobby.fyi/settings"),
                         "https://www.jobby.fyi/settings")

    def test_a_host_that_already_answers_is_left_alone(self):
        for url in ("https://www.jobby.fyi", "https://jobby-board.vercel.app",
                    "https://jobby-board-abc-sahilsapra.vercel.app/x"):
            self.assertEqual(self.publish.canonical_url(url), url)

    def test_a_query_and_fragment_survive(self):
        self.assertEqual(self.publish.canonical_url("https://jobby.fyi/a?b=c#d"),
                         "https://www.jobby.fyi/a?b=c#d")

    def test_nonsense_is_returned_untouched(self):
        for value in ("", None, "not a url", "javascript:alert(1)", "ftp://jobby.fyi"):
            self.assertEqual(self.publish.canonical_url(value), value)


if __name__ == "__main__":
    unittest.main()
