"""Occupied slots equal In flight rows. That is the whole rule.

The pool used to hold a slot for a parked application so a Retry could reuse the
filled tab. On 2026-08-27 five parked applications held all five slots, four
queued retries could never get one, and three Drops drained into a fleet with
nothing to give them. Twenty hours, every application looking healthy.

The bug was not duration. `lock_holder` returns a `parked: true` record before it
ever reaches the liveness check, so the lock is immortal by construction, and a
test asserting "slots free up eventually" passes against a timeout-based fix that
still deadlocks under load. So this pins the property the user stated instead:
the number of occupied slots equals the number of rows in the In flight lane,
which is a thing he can check by looking at the board.
"""

import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import browser  # noqa: E402
import dashboard  # noqa: E402
import watch  # noqa: E402


def session(aid, status, slot=None, **extra):
    entry = {"applicationId": aid, "status": status,
             "company": f"Co {aid}", "role": "PM"}
    if slot:
        entry["browserSlot"] = slot
    entry.update(extra)
    return entry


class TheRuleHoldsTests(unittest.TestCase):
    """reconcile leaves exactly the in-flight applications holding slots."""

    def reconcile(self, sessions, locks):
        """Run one reconcile over a fake fleet; return the locks it leaves."""
        state = dict(locks)

        def holder(name):
            return state.get(name)

        def park(name, aid):
            state[name] = {"pid": 0, "at": "2026-08-27T00:00:00Z",
                           "note": aid, "parked": True}

        def release(aid, slot=None, entry=None):
            slot = slot or (entry or {}).get("browserSlot")
            if slot in state:
                del state[slot]
                return True
            return False

        with mock.patch.object(watch, "_sessions", return_value=sessions), \
                mock.patch.object(watch, "_latest_history", return_value={}), \
                mock.patch.object(watch, "lock_holder", side_effect=holder), \
                mock.patch.object(watch, "park_slot", side_effect=park), \
                mock.patch.object(watch, "release_browser_slot", side_effect=release):
            watch.reconcile_browser_slots()
        return state

    def parked(self, aid, at="2026-08-27T00:00:00Z"):
        return {"pid": 0, "at": at, "note": aid, "parked": True}

    def test_nothing_in_flight_leaves_every_slot_open(self):
        sessions = [
            session("a", "review", "apply-1", pendingFields=[{"question": "?"}]),
            session("b", "dropped", "apply-2"),
            session("c", "watch", "apply-3"),
            session("d", "completed", "apply-4"),
        ]
        locks = {f"apply-{i}": self.parked(x)
                 for i, x in zip(range(1, 5), "abcd")}
        left = self.reconcile(sessions, locks)
        self.assertEqual(left, {}, f"slots still held: {sorted(left)}")

    def test_three_in_flight_leaves_exactly_three_held(self):
        sessions = [session(x, "inflight", f"apply-{i}")
                    for i, x in enumerate("abc", start=1)]
        sessions += [session("d", "review", "apply-4",
                             pendingFields=[{"question": "?"}]),
                     session("e", "dropped", "apply-5")]
        locks = {f"apply-{i}": self.parked(x)
                 for i, x in zip(range(1, 6), "abcde")}
        left = self.reconcile(sessions, locks)
        self.assertEqual(sorted(left), ["apply-1", "apply-2", "apply-3"])
        self.assertEqual(len(left), 3)

    def test_moving_from_in_flight_to_needs_you_frees_the_slot(self):
        """The park that used to keep its slot. This is the behaviour change."""
        locks = {"apply-1": self.parked("a")}
        still = self.reconcile([session("a", "inflight", "apply-1")], locks)
        self.assertEqual(sorted(still), ["apply-1"])
        # finish.park moves it to review and records what the user must do.
        freed = self.reconcile(
            [session("a", "review", "apply-1",
                     pendingFields=[{"question": "Do you need sponsorship?"}])],
            locks,
        )
        self.assertEqual(freed, {})

    def test_dropping_frees_the_slot(self):
        locks = {"apply-2": self.parked("b")}
        self.assertEqual(
            self.reconcile([session("b", "dropped", "apply-2")], locks), {}
        )

    def test_a_lock_whose_session_forgot_it_is_still_released(self):
        """The shape that took the pool to zero: nobody left to release it by name."""
        locks = {"apply-3": self.parked("gone")}
        self.assertEqual(self.reconcile([], locks), {})
        # And an in-flight application that dropped its own browserSlot record
        # does not get to keep the lock on the strength of its status alone.
        self.assertEqual(
            self.reconcile([session("gone", "inflight")], locks), {}
        )

    def test_the_count_matches_what_the_board_shows(self):
        """The user checks this by looking, so it has to be the same number."""
        sessions = [session("a", "inflight", "apply-1"),
                    session("b", "inflight", "apply-2"),
                    session("c", "review", "apply-3",
                            pendingFields=[{"question": "?"}])]
        locks = {f"apply-{i}": self.parked(x)
                 for i, x in zip(range(1, 4), "abc")}
        held = len(self.reconcile(sessions, locks))
        shown = len([s for s in sessions if s.get("status") == "inflight"])
        self.assertEqual(held, shown)
        page = dashboard.build(
            {"sessions": sessions, "jobs": [], "history": [], "meta": {},
             "profile": {}}, "now")
        self.assertIn(f'data-tab="flight" role="tab" aria-selected="false" '
                      f'aria-controls="p-flight">In flight<span class="n">{shown}</span>',
                      page.replace("\n", " "))


class ThePoolIsEightTests(unittest.TestCase):
    def test_one_definition_and_watch_follows_it(self):
        self.assertEqual(browser.SLOT_COUNT, 8)
        self.assertEqual(len(browser.SLOTS), 8)
        self.assertEqual(watch.APPLY_SLOTS, 8)
        self.assertEqual(watch.APPLY_LOCKS, browser.SLOTS)

    def test_every_slot_gets_its_own_port(self):
        ports = [browser.port_for(s) for s in browser.SLOTS]
        self.assertEqual(len(set(ports)), 8)
        self.assertEqual(ports, list(range(browser.BASE_PORT, browser.BASE_PORT + 8)))

    def test_a_slot_that_has_never_run_is_simply_empty(self):
        """apply-6..8 have no lock, no profile and no browser. That is correct."""
        for name in ("apply-6", "apply-7", "apply-8"):
            self.assertIn(name, watch.APPLY_LOCKS)


class ADropIsNotUndoneTests(unittest.TestCase):
    """Drop looked like it did nothing, and the slot pool was only half of why.

    watch.log, 2026-08-27T22:53:44, one tick and one line:

        {"dropped": ["a052ab8427c12dfb", "7923ca442c573811"],
         "queued":  ["a052ab8427c12dfb", "7923ca442c573811"]}

    Drops are processed first, then the tick walks the apply queue and sets a
    phase on each tail item, and setting a phase set the status to inflight. So
    a stale apply resurrected an application ninety seconds after the user
    dropped it. Freeing every slot in the pool does not touch this.
    """

    def run_phase(self, session_status):
        """Drive scripts/phase.py against a throwaway store."""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            store = str(ROOT / "scripts" / "store.py")
            entry = {"applicationId": "acme-pm", "company": "Acme", "role": "PM",
                     "status": session_status}
            written = pathlib.Path(root) / "in.json"
            written.write_text(json.dumps(entry))
            subprocess.run([sys.executable, store, "--root", root, "session-save",
                            "--id", "acme-pm", "--input", str(written)],
                           capture_output=True, text=True, check=True)
            out = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "phase.py"), "--root", root,
                 "--id", "acme-pm", "--phase", "Queued",
                 "--detail", "Waiting for an earlier application"],
                capture_output=True, text=True,
            )
            loaded = subprocess.run([sys.executable, store, "--root", root,
                                     "session-load", "--id", "acme-pm"],
                                    capture_output=True, text=True)
            return out, json.loads(loaded.stdout or "{}")

    def test_a_phase_update_cannot_put_a_dropped_application_back_in_flight(self):
        out, session = self.run_phase("dropped")
        self.assertEqual(session.get("status"), "dropped", out.stdout)
        self.assertIn("refused", out.stdout)

    def test_a_live_application_still_takes_its_phase(self):
        """The refusal must not be a blanket one; this is the case it protects."""
        out, session = self.run_phase("watch")
        self.assertEqual(session.get("status"), "inflight", out.stdout)
        self.assertEqual(session.get("previousStatus"), "watch")

    def test_the_tick_does_not_queue_what_it_just_dropped(self):
        """Belt and braces above the refusal, so no slot is spent on refused work."""
        source = (ROOT / "scripts" / "watch.py").read_text()
        block = source[source.index("apply_items = q.get(\"apply\") or []"):][:900]
        self.assertIn("just_dropped", block)
        self.assertIn('changed.get("dropped")', block)
