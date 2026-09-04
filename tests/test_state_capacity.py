"""publish.state_capacity: the reading Guard 3's headroom alarm divides.

The interface exists so the alarm never learns where the state lives. It asks
for a limit and a size, prints the store name it is handed, and keeps working
when the state moves to R2 behind a pointer.

Both properties below were asked for by the alarm's author and both are the
difference between an alarm and a decoration.
"""

import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("publish", ROOT / "scripts" / "publish.py")
publish = importlib.util.module_from_spec(spec)
sys.modules["publish"] = publish
spec.loader.exec_module(publish)


def a_state(jobs=3, filler=200):
    return {"profile": {"firstName": "A"}, "sessions": [], "history": [],
            "roles": {}, "meta": {}, "searchConfig": None, "providers": {},
            "jobs": {f"j{n}": {"id": f"j{n}", "pad": "x" * filler} for n in range(jobs)}}


class ShapeTest(unittest.TestCase):
    def setUp(self):
        self.reading = publish.state_capacity(state=a_state())

    def test_it_answers_the_three_keys_the_alarm_divides(self):
        for key in ("store", "limit", "size"):
            self.assertIn(key, self.reading)
        self.assertIsInstance(self.reading["limit"], int)
        self.assertIsInstance(self.reading["size"], int)

    def test_the_store_is_a_name_to_print_not_an_enum_to_keep_in_step(self):
        self.assertIsInstance(self.reading["store"], str)
        self.assertTrue(self.reading["store"].strip())

    def test_the_ratio_is_computable_and_sane(self):
        ratio = self.reading["size"] / self.reading["limit"]
        self.assertGreater(ratio, 0)
        self.assertLess(ratio, 1)


class ItMeasuresRatherThanEstimatesTest(unittest.TestCase):
    """The exact defect the old size test had: a count standing in for bytes."""

    def test_size_is_the_serializer_the_guard_weighs(self):
        state = a_state(jobs=40)
        reading = publish.state_capacity(state=state)
        self.assertEqual(reading["size"], len(publish.serialize_state(state)))

    def test_a_bigger_state_reads_bigger(self):
        small = publish.state_capacity(state=a_state(jobs=3))["size"]
        large = publish.state_capacity(state=a_state(jobs=300))["size"]
        self.assertGreater(large, small * 10)

    def test_it_reports_the_nearer_of_the_two_ceilings(self):
        # api/page.py caps the KV body before decompressing and the state
        # after. Either can bind, so the alarm gets whichever is closer.
        reading = publish.state_capacity(state=a_state(jobs=3))
        self.assertIn(reading["constraint"], ("decompressed state", "KV envelope"))
        self.assertIn(reading["limit"],
                      (publish.MAX_STATE_BYTES, publish.MAX_ENVELOPE_BYTES))

    def test_incompressible_content_moves_the_binding_ceiling(self):
        """A ratio nobody controls decides which cap binds, so prove it moves.

        Highly compressible padding keeps the envelope small and the
        decompressed state binds. Random-looking padding does not compress, so
        the envelope grows toward its own, tighter, 16 MB cap.
        """
        import hashlib
        blob = "".join(hashlib.sha256(str(n).encode()).hexdigest() for n in range(400))
        compressible = publish.state_capacity(state=a_state(jobs=200, filler=8000))
        self.assertEqual(compressible["constraint"], "decompressed state")
        noisy = {"profile": {"firstName": "A"}, "sessions": [], "history": [],
                 "roles": {}, "meta": {}, "searchConfig": None, "providers": {},
                 "jobs": {f"j{n}": {"id": f"j{n}", "pad": blob} for n in range(200)}}
        reading = publish.state_capacity(state=noisy)
        # Envelope/16MB against state/20MB: incompressible content narrows the
        # gap even when it does not yet cross it.
        envelope_share = publish.envelope_bytes(publish.serialize_state(noisy)) / publish.MAX_ENVELOPE_BYTES
        state_share = len(publish.serialize_state(noisy)) / publish.MAX_STATE_BYTES
        self.assertGreater(envelope_share / state_share,
                           (publish.envelope_bytes(publish.serialize_state(a_state(jobs=200, filler=8000)))
                            / publish.MAX_ENVELOPE_BYTES)
                           / (len(publish.serialize_state(a_state(jobs=200, filler=8000)))
                              / publish.MAX_STATE_BYTES))
        self.assertIn(reading["constraint"], ("decompressed state", "KV envelope"))


class ItRaisesRatherThanReturningASentinelTest(unittest.TestCase):
    """A zero reaching the alarm reads as no headroom used and pages nobody."""

    def test_an_unmeasurable_state_raises(self):
        class Hostile:
            def __repr__(self):
                return "<unserializable>"
        with self.assertRaises(publish.CapacityError):
            publish.state_capacity(state={"jobs": {"j": Hostile()}})

    def test_it_never_answers_zero(self):
        with self.assertRaises(publish.CapacityError):
            publish.state_capacity(state=None, root="/nonexistent-store-path")

    def test_the_error_says_what_could_not_be_read(self):
        try:
            publish.state_capacity(state={"jobs": {"j": object()}})
        except publish.CapacityError as exc:
            self.assertIn("could not measure", str(exc))
        else:
            self.fail("expected CapacityError")
