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
import inspect
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
        self.original = (watch.LAST_DEPLOY, watch.LAST_PUBLISH)
        watch.LAST_DEPLOY = self.tmp / "last-deploy"
        watch.LAST_PUBLISH = self.tmp / "last-publish"

    def tearDown(self):
        watch.LAST_DEPLOY, watch.LAST_PUBLISH = self.original

    def _touch(self, path, seconds_ago):
        import os
        path.write_text("x")
        when = time.time() - seconds_ago
        os.utime(path, (when, when))

    def publish_was(self, seconds_ago):
        self._touch(watch.LAST_DEPLOY, seconds_ago)

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
        self.assertIn("sync_board.py --no-publish", warning)

    def test_a_board_that_has_never_published_is_not_an_alarm(self):
        # A fresh machine has nothing to be stale about, and crying wolf on
        # first run is how an alarm gets ignored later.
        self.assertIsNone(watch.board_staleness())
        self.assertEqual(watch.stale_board_warning(), "")


    def test_a_publish_run_by_hand_clears_the_alarm(self):
        """The defect this caught: the board was 29 seconds old and still stale.

        LAST_DEPLOY is written only by the watcher's own publish path, so a
        publish by any other route left the alarm ringing over a fresh board.
        publish.py writes LAST_PUBLISH however it was invoked, and the newer of
        the two is what counts.
        """
        self._touch(watch.LAST_DEPLOY, 22 * 3600)
        self.assertTrue(watch.stale_board_warning())
        self._touch(watch.LAST_PUBLISH, 29)
        self.assertEqual(watch.stale_board_warning(), "")
        self.assertLess(watch.board_staleness(), 60)

    def test_an_old_hand_publish_does_not_mask_a_stale_watcher(self):
        # The newer wins in both directions, so a stale hand-publish beside a
        # fresh watcher publish is not an alarm either.
        self._touch(watch.LAST_PUBLISH, 22 * 3600)
        self._touch(watch.LAST_DEPLOY, 29)
        self.assertEqual(watch.stale_board_warning(), "")

    def test_both_stale_is_still_an_alarm(self):
        self._touch(watch.LAST_DEPLOY, 22 * 3600)
        self._touch(watch.LAST_PUBLISH, 20 * 3600)
        self.assertTrue(watch.stale_board_warning())

    def test_publish_records_the_marker_where_the_watcher_reads_it(self):
        # One path writes it, another reads it; they have to agree on where.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "publish_mod", ROOT / "scripts" / "publish.py")
        publish = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publish)
        self.assertEqual(publish.LAST_PUBLISH.name, self.original[1].name)
        self.assertEqual(publish.LAST_PUBLISH, self.original[1])

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



class RenderTimeFreshnessTests(unittest.TestCase):
    """The board says how old it is, measured when it is served.

    The frozen board's status line read "Search v21 is live. Every 4 hours,
    next run 19:31" -- true when that snapshot was built and meaningless
    nineteen hours later, because it had frozen with everything else. An
    indicator that can freeze along with the page is theatre, so this one is
    computed per request from the clock rather than carried in the state.
    """

    def setUp(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "page_module", root / "deploy" / "api" / "page.py")
        self.page = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(self.page)
        except Exception as exc:                      # pragma: no cover
            self.skipTest(f"page.py needs its deployment context: {exc}")

    def stamp(self, hours_ago):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc)
                - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_a_fresh_board_says_only_when_it_was_built(self):
        label, stale = self.page._freshness(self.stamp(0.2))
        self.assertFalse(stale)
        self.assertNotIn("stopped updating", label)

    def test_a_board_older_than_the_threshold_says_so_in_words(self):
        label, stale = self.page._freshness(self.stamp(23))
        self.assertTrue(stale)
        self.assertIn("23 hours old", label)
        self.assertIn("stopped updating", label)

    def test_a_board_days_old_is_counted_in_days(self):
        label, _ = self.page._freshness(self.stamp(72))
        self.assertIn("3 days old", label)

    def test_the_age_is_measured_at_render_and_not_carried_in_the_state(self):
        """The property that makes it useful. The same stored timestamp reads
        differently as time passes, which is exactly what a frozen board needs
        and what a value baked into the snapshot cannot do."""
        source = inspect.getsource(self.page._freshness)
        self.assertIn("datetime.now", source)

    def test_an_unreadable_timestamp_does_not_take_the_board_down(self):
        label, stale = self.page._freshness("not a date")
        self.assertFalse(stale)
        self.assertTrue(label)
