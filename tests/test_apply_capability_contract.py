"""The apply path's capability fence, and the seam where it stopped binding.

On 2026-08-27 a run held a browser server that had answered a preflight ninety
seconds earlier and was dead by the time a provider built its session. Codex
named it and refused to start. Claude started anyway, ran two turns, and parked
the application on a blocker addressed to a person who could do nothing with it.
Three separate things had to be true for that to be invisible, and each has a
test here: the fence ran once for a whole loop, the required list and the
advertised list were maintained by hand in two files, and a park told you
nothing about whether the agent ever had a browser.

What this cannot do is drive a real browser; that is test_apply_end_to_end,
which needs Chrome and npx. Unlike the PDF render tests it is not excluded from
CI, so it runs there and skips, which comes to the same thing: this module is
the half of the contract a green check actually proves.
"""

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browser  # noqa: E402
import browser_mcp_proxy  # noqa: E402
import mcp_preflight  # noqa: E402
import providers  # noqa: E402
import store_isolation  # noqa: E402


def setUpModule():
    # Building a real invocation writes the slot's firewall config. Unisolated,
    # asking for apply-1 rewrote ~/.job-application/browser-mcp-apply-1.json,
    # the file a live Chrome slot's fence is configured from, and created a
    # browser-profile for a browser this module never opens.
    store_isolation.begin()


def tearDownModule():
    store_isolation.end()


class FenceSurfaceContractTests(unittest.TestCase):
    """One contract in two files, so compare them rather than restate them.

    mcp_preflight.REQUIRED is what a run refuses to start without.
    _tool_definitions is what the firewall offers. A tool added to one and not
    the other is either unguarded or impossible, and both fail quietly.
    """

    def advertised(self):
        return {
            tool["name"]
            for tool in browser_mcp_proxy._tool_definitions(("/a.pdf", "/b.pdf"))
        }

    def test_every_advertised_browser_tool_is_required_by_the_preflight(self):
        missing = self.advertised() - set(mcp_preflight.REQUIRED["browser"])
        self.assertEqual(missing, set(), f"advertised but never checked for: {missing}")

    def test_every_required_browser_tool_is_one_the_firewall_advertises(self):
        missing = set(mcp_preflight.REQUIRED["browser"]) - self.advertised()
        self.assertEqual(missing, set(), f"required but never offered: {missing}")

    def test_the_upload_that_needs_no_chooser_is_among_them(self):
        self.assertIn("browser_attach_file", self.advertised())
        self.assertIn("browser_attach_file", mcp_preflight.REQUIRED["browser"])

    def test_the_arbitrary_javascript_tool_is_in_neither(self):
        self.assertNotIn("browser_run_code_unsafe", self.advertised())
        self.assertNotIn("browser_run_code_unsafe", mcp_preflight.REQUIRED["browser"])


class ProviderCarriesTheBrowserTests(unittest.TestCase):
    """Every provider cleared for apply has to actually be handed the servers.

    The binary is stubbed. The first version let `build_run_invocation` resolve
    the real CLI, which passes on a laptop that has Claude and Codex installed
    and dies on a runner that has neither, and a test that only holds where the
    thing is already working is the shape of bug this whole change is about.
    What is under test is the argv, not the install.
    """

    BINARY = staticmethod(lambda provider, required: f"/nonexistent/{provider}")

    def context(self, root):
        for name in ("resume.pdf", "cover.pdf"):
            (root / name).write_bytes(b"%PDF-1.4\n")
        return {
            "applicationId": "a1b2c3d4",
            "resume": str(root / "resume.pdf"),
            "cover": str(root / "cover.pdf"),
            "uploadResume": str(root / "resume.pdf"),
            "uploadCover": str(root / "cover.pdf"),
        }

    def test_each_apply_provider_receives_both_servers(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name).resolve()
            context = self.context(root)
            cleared = [
                p for p in ("openai", "anthropic", "gemini")
                if providers.supports(p, "apply")
            ]
            self.assertTrue(cleared, "no provider is cleared for form filling")
            for provider in cleared:
                with self.subTest(provider=provider):
                    invocation = providers.build_run_invocation(
                        provider, "apply", "prompt",
                        workspace=root, browser_slot="apply-1",
                        browser_adapter=browser, application_context=context,
                        binary_resolver=self.BINARY,
                    )
                    flat = " ".join(invocation.argv)
                    self.assertIn("browser", flat)
                    self.assertIn("jobby", flat)


class ActivityTellsParksApartTests(unittest.TestCase):
    def test_the_activity_path_lives_with_the_uploads_it_describes(self):
        context = {"applicationId": "a1b2c3d4", "resume": "/r.pdf", "cover": "/c.pdf",
                   "uploadResume": "/r.pdf", "uploadCover": "/c.pdf"}
        path = browser.activity_path("apply-1", context)
        self.assertEqual(path.name, "activity.json")
        self.assertEqual(path.parent.name, "a1b2c3d4")
        self.assertEqual(path.parent.parent.name, "uploads")

    def test_an_application_id_that_would_escape_the_tree_is_refused(self):
        for unsafe in ("../../etc", "a/b", "..", "x" * 129):
            with self.subTest(id=unsafe):
                with self.assertRaises(ValueError):
                    browser.activity_path("apply-1", {
                        "applicationId": unsafe, "resume": "/r.pdf", "cover": "/c.pdf",
                        "uploadResume": "/r.pdf", "uploadCover": "/c.pdf",
                    })


class AbsentEvidenceIsNotEvidenceTests(unittest.TestCase):
    """A missing activity record must not read as "made zero browser calls".

    The classification turns on `totalCalls == 0`. A run killed on timeout may
    take the firewall down with it before it writes anything, and calling that a
    capability failure would blame the provider for a fault it did not have.
    """

    def test_a_missing_record_reads_as_nothing_known(self):
        import run_apply
        context = {"applicationId": "nosuchrun", "resume": "/r.pdf", "cover": "/c.pdf",
                   "uploadResume": "/r.pdf", "uploadCover": "/c.pdf"}
        activity = run_apply.read_browser_activity("apply-1", context)
        self.assertEqual(activity, {})
        self.assertIsNone(activity.get("totalCalls"))
        self.assertNotEqual(activity.get("totalCalls"), 0)

    def test_an_unreadable_record_reads_the_same_way(self):
        import run_apply
        with tempfile.TemporaryDirectory() as name:
            broken = pathlib.Path(name) / "activity.json"
            broken.write_text("{not json")
            with mock.patch.object(run_apply.browser, "activity_path",
                                   return_value=broken):
                self.assertEqual(run_apply.read_browser_activity("apply-1", {}), {})


class CapacityReachesTheBoardTests(unittest.TestCase):
    """The phone alarm says something is wrong; the board has to say what.

    Written to meta every tick rather than only when it appears, so it clears
    itself when a slot frees. A warning that has to be cleared by the code that
    raised it is a warning that outlives its cause.
    """

    def test_the_mirror_carries_it_to_the_board(self):
        import class_b
        row = class_b.meta_row({"slotsParked": "5 of 5 browser slots are parked"})
        self.assertEqual(row.get("slots_parked"), "5 of 5 browser slots are parked")

    def test_a_working_fleet_mirrors_nothing(self):
        import class_b
        self.assertIsNone(class_b.meta_row({}).get("slots_parked"))

    def test_the_board_draws_it_in_the_in_flight_lane(self):
        """Where the question gets asked, and not as the stopped alarm."""
        page = (ROOT / "apps" / "web" / "app" / "page.tsx").read_text()
        at = page.index("meta?.slotsParked")
        self.assertIn('active === "flight"', page[at - 60:at])
        self.assertIn('className="capacity"', page[at:at + 200])
        self.assertNotIn("Automation is stopped", page[at - 200:at + 200])

    def test_the_watcher_writes_it_every_tick_so_it_clears_itself(self):
        source = (ROOT / "scripts" / "watch.py").read_text()
        self.assertIn('"--key", "slotsParked"', source)
        block = source[source.index("parked_now = parked_slot_warning()"):][:400]
        self.assertIn('meta_value("slotsParked")', block)


class PreflightRunsPerAttemptTests(unittest.TestCase):
    """Checked once at the door, used for the whole loop, was the fault.

    The window between check and use was ninety seconds on the run that failed.
    This reads the source because the loop it guards cannot be entered without
    a provider CLI, and the property is structural: the call sits inside the
    per-candidate body, not before it.
    """

    def source(self):
        return (ROOT / "scripts" / "run_apply.py").read_text()

    def test_the_check_is_inside_the_candidate_loop(self):
        text = self.source()
        loop = text.index("for index, candidate in enumerate(candidates):")
        checks = [
            index for index in range(len(text))
            if text.startswith("mcp_preflight.check(", index)
        ]
        self.assertTrue(any(index > loop for index in checks),
                        "the fence must be re-checked for each provider attempt")

    def test_a_failed_check_skips_that_provider_rather_than_starting_it(self):
        text = self.source()
        after = text[text.index("for index, candidate in enumerate(candidates):"):]
        block = after[after.index("mcp_preflight.check("):]
        self.assertIn("capabilityFailure", block[:1400])
        self.assertIn("continue", block[:1400])

    def test_a_park_with_no_browser_calls_is_recorded_as_a_capability_failure(self):
        text = self.source()
        self.assertIn('outcome.get("terminal") == "parked"', text)
        self.assertIn('activity.get("totalCalls") == 0', text)
        self.assertIn('"parkClass"', text)


class TheBoardShowsTheDifferenceTests(unittest.TestCase):
    """The tag has to survive the mirror, or it is a local variable.

    `class_b.session_row` names the columns it writes. A field the runner
    writes and the row does not carry is dropped between the store and the
    board, so the card renders exactly as it did before and nothing says the
    fix is inert.
    """

    def session(self, **extra):
        base = {
            "applicationId": "a1b2c3d4", "company": "Initech", "role": "PM",
            "status": "active", "pendingFields": [],
        }
        base.update(extra)
        return base

    def test_the_tag_survives_the_mirror(self):
        import class_b
        row = class_b.session_row(self.session(parkClass="capability"))
        self.assertEqual(row.get("park_class"), "capability")

    def test_a_capability_park_does_not_read_as_work_for_the_user(self):
        rows = (ROOT / "apps" / "web" / "app" / "_ui" / "Rows.tsx").read_text()
        self.assertIn('const fault = row.park_class === "capability";', rows)
        self.assertIn('<span className="chip fault">system fault</span>', rows)
        self.assertIn("not a step for", rows)

    def test_a_real_blocker_still_reads_as_work_for_the_user(self):
        import class_b
        row = class_b.session_row(self.session(
            pendingFields=[{"question": "Do you need sponsorship?"}]))
        self.assertIn(row.get("park_class"), (None, ""))
        rows = (ROOT / "apps" / "web" / "app" / "_ui" / "Rows.tsx").read_text()
        self.assertIn("{pending.length} to do", rows)
