""""Open & finish" holds a slot, and the hold ends by itself.

**The hold this replaces is the one that caused a twenty-hour outage.**
`reconcile_browser_slots` used to keep a slot while its application was parked,
so a Retry could reuse the filled tab. Five parked applications held all five
slots, four queued retries could never get one, and three Drops drained into a
fleet with nothing to give them. The fix made the rule "held while inflight and
at no other time", which is right and which closes a parked application's
browser within a tick.

That closure is what made "Open & finish" useless: it opens a window on a form
the reconciler removed seconds earlier. Measured 2026-09-07 on a real
run -- parked 02:50:55Z, released 02:54:29Z, `tabs` 0.

**So the hold comes back with the property the old one lacked: an end.** It is
caused by a person pressing a button rather than by an application's state, it
carries a deadline in the lock, and an unreadable deadline counts as expired.
Five slots held at once costs fifteen minutes of capacity rather than a day.

The tests below are about that difference and not about the convenience.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts"))

import store_isolation  # noqa: E402


def setUpModule():
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


import watch  # noqa: E402


def stamp(delta_seconds):
    return (datetime.now(timezone.utc)
            + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TheHoldEnds(unittest.TestCase):
    def test_a_live_hold_is_not_expired(self):
        self.assertFalse(watch.hold_expired({"heldUntil": stamp(60)}))

    def test_a_passed_deadline_is_expired(self):
        self.assertTrue(watch.hold_expired({"heldUntil": stamp(-1)}))

    def test_the_deadline_is_the_moment_not_a_window(self):
        """Exactly at the deadline the hold is over, so a slot cannot linger on
        a rounding difference."""
        self.assertTrue(watch.hold_expired({"heldUntil": stamp(0)}))

    def test_a_lock_with_no_hold_is_expired(self):
        """**A parked lock is not a held one.** Every lock written before this
        existed has no `heldUntil`, and reading those as held would pin every
        parked slot in the store the moment this deploys -- which is the
        outage, arriving as an upgrade."""
        self.assertTrue(watch.hold_expired({"parked": True, "note": "x"}))
        self.assertTrue(watch.hold_expired({}))
        self.assertTrue(watch.hold_expired(None))

    def test_an_unreadable_deadline_is_expired_not_held(self):
        """The default has to be release. A hold nobody can read is a hold
        nobody can end, and that is the failure being fixed rather than a
        conservative choice."""
        for bad in ("", "soon", "2026-13-45T99:99:99Z", 17, [], {"a": 1}):
            with self.subTest(value=bad):
                self.assertTrue(watch.hold_expired({"heldUntil": bad}))


class TheHoldIsBoundedByConstruction(unittest.TestCase):
    def test_the_ceiling_is_short_enough_to_be_survivable(self):
        """Five slots held at once must cost minutes, not a day.

        The number is not the point; the bound is. This asserts an order of
        magnitude rather than fifteen minutes exactly, so tuning it does not
        need a test edit and doubling it twice does.
        """
        self.assertLessEqual(watch.HOLD_SECONDS, 30 * 60,
                             "a hold this long is closer to the unbounded one "
                             "it replaces than to a person walking to a laptop")
        self.assertGreaterEqual(watch.HOLD_SECONDS, 5 * 60,
                                "shorter than the time to reach the laptop and "
                                "type a code makes the button useless again")

    def test_a_hold_writes_a_deadline_into_the_lock(self):
        """In the lock, not in memory: the watcher restarts, and a hold a
        restart forgets releases early while one a restart re-reads from its
        own clock would never end."""
        with tempfile.TemporaryDirectory() as root:
            watch.LOCK_DIR = Path(root)
            watch.hold_slot("apply-1", "app-1", seconds=120)
            held = json.loads((Path(root) / "apply-1.lock").read_text())
        self.assertEqual(held["note"], "app-1")
        self.assertTrue(held["parked"],
                        "every existing reader of these locks decides "
                        "availability on `parked`, and a held slot is not "
                        "available")
        self.assertEqual(held["heldFor"], "open-and-finish")
        self.assertFalse(watch.hold_expired(held))

    def test_the_hold_is_a_third_state_and_parked_is_untouched(self):
        """`park_slot` must not start writing deadlines. A parked application
        is released within a tick and that is the fix for the outage; only an
        explicit press earns a hold."""
        with tempfile.TemporaryDirectory() as root:
            watch.LOCK_DIR = Path(root)
            watch.park_slot("apply-2", "app-2")
            parked = json.loads((Path(root) / "apply-2.lock").read_text())
        self.assertNotIn("heldUntil", parked)
        self.assertTrue(watch.hold_expired(parked),
                        "parking a slot now holds it, which is the twenty-hour "
                        "outage arriving through the door built to avoid it")
