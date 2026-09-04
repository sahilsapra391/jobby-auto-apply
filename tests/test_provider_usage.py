"""Token capture and costing.

The tests that matter here are the ones about refusing to answer. A cost
instrument that guesses is worse than none, because the guess gets quoted.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import provider_usage
import providers  # noqa: E402


OPENAI = ('{"type":"turn.completed","usage":{"input_tokens":76582,'
          '"cached_input_tokens":59136,"cache_write_input_tokens":0,'
          '"output_tokens":435,"reasoning_output_tokens":115}}')
ANTHROPIC = ('{"type":"result","usage":{"input_tokens":1200,"output_tokens":300,'
             '"cache_read_input_tokens":900,"cache_creation_input_tokens":100}}')
GEMINI = ('{"response":{"usageMetadata":{"promptTokenCount":5000,'
          '"candidatesTokenCount":800,"cachedContentTokenCount":4000,'
          '"thoughtsTokenCount":250}}}')

TABLE = {
    "asOf": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    "models": {
        "m": {"inputPerMTok": 10.0, "cachedInputPerMTok": 1.0,
              "cacheWritePerMTok": 12.5, "outputPerMTok": 50.0},
    },
}


class ParseTests(unittest.TestCase):
    def test_openai_shape(self):
        usage = provider_usage.parse(OPENAI)
        self.assertEqual(usage.shape, "openai")
        self.assertEqual(usage.input_tokens, 76582)
        self.assertEqual(usage.cached_input_tokens, 59136)
        self.assertEqual(usage.output_tokens, 435)
        self.assertEqual(usage.reasoning_output_tokens, 115)

    def test_anthropic_shape_is_told_apart_by_its_cache_fields(self):
        usage = provider_usage.parse(ANTHROPIC)
        self.assertEqual(usage.shape, "anthropic")
        self.assertEqual(usage.cached_input_tokens, 900)
        self.assertEqual(usage.cache_write_input_tokens, 100)

    def test_gemini_shape(self):
        usage = provider_usage.parse(GEMINI)
        self.assertEqual(usage.shape, "gemini")
        self.assertEqual(usage.input_tokens, 5000)
        self.assertEqual(usage.output_tokens, 800)

    def test_cached_input_is_a_subset_of_input_not_an_addition(self):
        # Getting this backwards would overstate the largest number in the
        # record. On the real run above it is the difference between 17,446
        # tokens billed at full rate and 76,582.
        usage = provider_usage.parse(OPENAI)
        self.assertEqual(usage.fresh_input_tokens, 76582 - 59136)

    def test_the_last_block_wins_because_the_clis_report_cumulatively(self):
        stream = ('{"usage":{"input_tokens":10,"output_tokens":1}}\n'
                  '{"usage":{"input_tokens":90,"output_tokens":9}}')
        usage = provider_usage.parse(stream)
        self.assertEqual(usage.input_tokens, 90)
        self.assertEqual(usage.blocks_seen, 2)

    def test_nothing_parseable_is_none_never_an_exception(self):
        for payload in ("", None, "plain text", '{"usage": {broken',
                        '{"usage":{}}', '{"usage":{"input_tokens":0}}'):
            self.assertIsNone(provider_usage.parse(payload))

    def test_a_zero_token_block_is_not_a_measured_zero(self):
        # A measured zero would be averaged in as a real cheap run.
        self.assertIsNone(provider_usage.parse('{"usage":{"input_tokens":0,'
                                               '"output_tokens":0}}'))


class CostTests(unittest.TestCase):
    def test_fresh_cached_write_and_output_are_billed_at_their_own_rates(self):
        usage = provider_usage.Usage(input_tokens=1_000_000,
                                     cached_input_tokens=400_000,
                                     cache_write_input_tokens=100_000,
                                     output_tokens=200_000)
        # 600k fresh @10 + 400k cached @1 + 100k write @12.5 + 200k out @50
        expected = (0.6 * 10) + (0.4 * 1) + (0.1 * 12.5) + (0.2 * 50)
        self.assertAlmostEqual(
            provider_usage.estimate_usd(usage, "m", TABLE), expected, places=6)

    def test_an_expired_row_refuses_even_when_the_table_is_fresh(self):
        table = json.loads(json.dumps(TABLE))
        table["models"]["m"]["ratesExpireOn"] = "2020-01-01"
        usage = provider_usage.parse(OPENAI)
        self.assertIsNone(provider_usage.estimate_usd(usage, "m", table))

    def test_an_unexpired_row_still_prices(self):
        table = json.loads(json.dumps(TABLE))
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        table["models"]["m"]["ratesExpireOn"] = future
        self.assertIsNotNone(
            provider_usage.estimate_usd(provider_usage.parse(OPENAI), "m", table))

    def test_unknown_model_and_missing_usage_are_none_not_zero(self):
        usage = provider_usage.parse(OPENAI)
        self.assertIsNone(provider_usage.estimate_usd(usage, "absent", TABLE))
        self.assertIsNone(provider_usage.estimate_usd(None, "m", TABLE))
        self.assertIsNone(provider_usage.estimate_usd(usage, "m", None))


class PriceTableTests(unittest.TestCase):
    def test_a_stale_table_is_refused(self):
        old = (datetime.now(timezone.utc)
               - timedelta(days=provider_usage.PRICE_MAX_AGE_DAYS + 1))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps(
                {"asOf": old.strftime("%Y-%m-%d"), "models": {"m": {}}}))
            self.assertIsNone(provider_usage.load_prices(path))

    def test_a_table_with_no_date_or_bad_json_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            undated = Path(tmp) / "u.json"
            undated.write_text(json.dumps({"models": {}}))
            self.assertIsNone(provider_usage.load_prices(undated))
            broken = Path(tmp) / "b.json"
            broken.write_text("{not json")
            self.assertIsNone(provider_usage.load_prices(broken))
            self.assertIsNone(provider_usage.load_prices(Path(tmp) / "absent.json"))

    def test_the_shipped_table_prices_every_model_jobby_pins(self):
        """Catches a typo, a lapsed introductory rate, and a renamed pin.

        The model list used to be three literals here, and one of them was
        gemini-3.7-flash while providers.MODEL_IDS pinned
        gemini-3.7-flash-medium. So this test priced a model nobody ran, passed,
        and Gemini's real cost recorded as null for as long as that was true.
        Derived from the pins now, so the two cannot drift apart again.
        """
        prices = provider_usage.load_prices()
        self.assertIsNotNone(prices, "references/model-prices.json is unusable")
        usage = provider_usage.parse(OPENAI)
        unpriced = [
            model for model in sorted(set(providers.MODEL_IDS.values()))
            if provider_usage.estimate_usd(usage, model, prices) is None
        ]
        self.assertEqual(unpriced, [], f"expired or missing rates: {unpriced}")


class LedgerTests(unittest.TestCase):
    def test_a_row_is_written_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = provider_usage.record("app1", "openai", "m", "form",
                                        provider_usage.parse(OPENAI),
                                        root=tmp, prices=TABLE)
            self.assertTrue(row.measured)
            rows = provider_usage.read_ledger(tmp)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["applicationId"], "app1")
            self.assertEqual(rows[0]["usage"]["input_tokens"], 76582)

    def test_an_unmeasured_run_is_recorded_rather_than_skipped(self):
        # Coverage has to be visible. Dropping these would make every average
        # a statement about which paths happen to report usage.
        with tempfile.TemporaryDirectory() as tmp:
            row = provider_usage.record("app2", "anthropic", "m", "form", None,
                                        root=tmp, prices=TABLE)
            self.assertFalse(row.measured)
            self.assertIsNone(row.costUsd)
            self.assertEqual(len(provider_usage.read_ledger(tmp)), 1)

    def test_recording_never_raises_when_it_cannot_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "wall"
            blocker.write_text("not a directory")
            self.assertIsNone(provider_usage.record(
                "app3", "openai", "m", "form", provider_usage.parse(OPENAI),
                root=blocker / "under", prices=TABLE))

    def test_summary_marks_an_application_unknown_when_any_run_is_unmeasured(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider_usage.record("app4", "openai", "m", "form",
                                  provider_usage.parse(OPENAI),
                                  root=tmp, prices=TABLE)
            provider_usage.record("app4", "openai", "m", "documents", None,
                                  root=tmp, prices=TABLE)
            summary = provider_usage.summarise(provider_usage.read_ledger(tmp))
            self.assertEqual(summary["app4"]["invocations"], 2)
            self.assertEqual(summary["app4"]["measured"], 1)
            self.assertFalse(summary["app4"]["costKnown"])


class PricedModelTests(unittest.TestCase):
    """Every pinned model must have a price, and a miss must say so.

    The table carried gemini-3.7-flash while providers.MODEL_IDS pinned
    gemini-3.7-flash-medium. Every Gemini row costed as null, nothing anywhere
    said why, and cost tracking was off for a whole provider without a single
    failure. A rename does that quietly unless something checks.
    """

    def setUp(self):
        self.prices = provider_usage.load_prices()

    def test_the_price_table_loads(self):
        self.assertIsNotNone(
            self.prices, "the price table is missing, malformed or stale")

    def test_every_pinned_model_has_a_price_entry(self):
        missing = [
            model for model in providers.MODEL_IDS.values()
            if model not in (self.prices.get("models") or {})
        ]
        self.assertEqual(
            missing, [],
            "these pinned models have no price entry, so their cost silently "
            "records as null: " + ", ".join(missing))

    def test_every_pinned_model_actually_costs_a_run(self):
        # An entry that exists but has lapsed is the same silence.
        usage = provider_usage.Usage(
            input_tokens=1000, output_tokens=100, cached_input_tokens=0,
            cache_write_input_tokens=0, reasoning_output_tokens=0,
            blocks_seen=1, shape="test",
        ) if hasattr(provider_usage, "Usage") else None
        if usage is None:
            self.skipTest("no Usage constructor exposed")
        for model in sorted(set(providers.MODEL_IDS.values())):
            with self.subTest(model=model):
                self.assertIsNone(
                    provider_usage.unpriced_reason(usage, model, self.prices))
                self.assertIsNotNone(
                    provider_usage.estimate_usd(usage, model, self.prices))

    def test_a_missing_entry_is_named_not_silent(self):
        usage = provider_usage.parse('{"usage":{"input_tokens":10,"output_tokens":2}}')
        reason = provider_usage.unpriced_reason(usage, "not-a-model", self.prices)
        self.assertIn("no price entry", reason or "")
        self.assertIn("not-a-model", reason or "")

    def test_no_price_table_is_named_too(self):
        usage = provider_usage.parse('{"usage":{"input_tokens":10,"output_tokens":2}}')
        self.assertEqual(
            provider_usage.unpriced_reason(usage, "claude-sonnet-5", None),
            "no price table")
