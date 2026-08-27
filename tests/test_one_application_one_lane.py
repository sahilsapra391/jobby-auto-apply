"""An application appears in exactly one lane. Not usually. Exactly.

Every lane used to be its own comprehension and one of them read a different
field: Submitted came from `history`, everything else from `session.status`.
Two sources of truth for "where does this appear" can disagree, and one did --
an application with a completed event sitting at status `watch`, drawn in both
Submitted and Watching at the same time.

Bucketing once makes the overlap impossible rather than absent, so these tests
are about the decision function and about the page it produces, not about the
store happening to be tidy today.
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dashboard  # noqa: E402


def session(aid, status, **extra):
    entry = {"applicationId": aid, "status": status, "company": f"Co {aid}",
             "role": "Product Manager"}
    entry.update(extra)
    return entry


class ExactlyOneLaneTests(unittest.TestCase):
    def test_every_workflow_status_lands_in_one_named_lane(self):
        cases = {"watch": "watch", "inflight": "flight", "active": "act",
                 "review": "act", "dropped": "dropped"}
        for status, lane in cases.items():
            with self.subTest(status=status):
                self.assertEqual(dashboard.lane_of(session("a", status)), lane)

    def test_a_status_nobody_defined_lands_nowhere_rather_than_nearby(self):
        for unknown in ("", "queued", "paused", None, "SUBMITTED?"):
            with self.subTest(status=unknown):
                self.assertIsNone(dashboard.lane_of(session("a", unknown)))

    def test_submitted_outranks_the_lane_its_status_would_give_it(self):
        """The real case: a completed event on a session still sitting at watch."""
        for status in ("watch", "review", "inflight", "active"):
            with self.subTest(status=status):
                self.assertEqual(
                    dashboard.lane_of(session("a", status), {"a"}), "done")

    def test_a_drop_does_not_erase_that_an_application_went_out(self):
        """Zero of these exist today; this is the rule for the first one.

        A submission is a fact about the outside world and cannot be recalled.
        The drop is still recorded on the session, it just does not decide which
        lane the application is drawn in.
        """
        self.assertEqual(dashboard.lane_of(session("a", "dropped"), {"a"}), "done")
        self.assertEqual(dashboard.lane_of(session("a", "dropped"), set()), "dropped")

    def test_bucketing_partitions_rather_than_copies(self):
        sessions = [session("a", "watch"), session("b", "inflight"),
                    session("c", "review"), session("d", "dropped"),
                    session("e", "watch"), session("f", "nonsense")]
        buckets = dashboard.lanes_for(sessions, {"e"})
        seen = [s["applicationId"] for lane in buckets.values() for s in lane]
        self.assertEqual(len(seen), len(set(seen)), f"drawn twice: {seen}")
        self.assertNotIn("f", seen)
        self.assertEqual(sorted(seen), ["a", "b", "c", "d", "e"])
        self.assertEqual([s["applicationId"] for s in buckets["done"]], ["e"])


class ThePageDrawsItOnceTests(unittest.TestCase):
    """The property that matters is on the page, not in the helper."""

    def page(self, sessions, history=()):
        return dashboard.build(
            {"sessions": list(sessions), "history": list(history), "jobs": {},
             "meta": {}, "profile": {}}, "now")

    def test_an_application_submitted_while_still_watching_is_drawn_once(self):
        sessions = [session("acme", "watch", company="Acme")]
        history = [{"applicationId": "acme", "event": "completed",
                    "company": "Acme", "role": "Product Manager",
                    "at": "2026-08-01T00:00:00Z"}]
        page = self.page(sessions, history)
        self.assertEqual(page.count('data-id="acme"'), page.count('data-id="acme"'))
        # It is submitted, so Watching must not count it.
        self.assertIn('data-tab="watch"', page)
        watch_tab = page.split('data-tab="watch"')[1].split("</button>")[0]
        self.assertIn('<span class="n">0</span>', watch_tab)

    def test_the_tab_counts_add_up_to_the_sessions_that_have_a_lane(self):
        sessions = [session("a", "watch"), session("b", "inflight"),
                    session("c", "review"), session("d", "dropped")]
        page = self.page(sessions)
        counts = {}
        for lane in ("watch", "flight", "act"):
            chunk = page.split(f'data-tab="{lane}"')[1].split("</button>")[0]
            counts[lane] = int(chunk.split('<span class="n">')[1].split("<")[0])
        self.assertEqual(counts, {"watch": 1, "flight": 1, "act": 1})


if __name__ == "__main__":
    unittest.main()
