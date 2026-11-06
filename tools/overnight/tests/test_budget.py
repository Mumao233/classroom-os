import json
from pathlib import Path
import tempfile
import unittest

from tools.overnight.budget import LimitExceeded, SubscriptionLimits
from tools.overnight.state import DuplicateEvent, RevisionConflict, StateError


ROOT = Path(__file__).resolve().parents[3]


class SubscriptionLimitTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "work/overnight/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="subscription-", dir=root)
        self.path = Path(self.tmp.name) / "limits.json"
        self.now = 1000
        self.ledger = SubscriptionLimits(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extra_api_spend_cannot_be_authorized_and_settings_are_strict(self):
        for config in ({"extra_api_spend_usd": 50}, {"extra_api_spend_usd": True},
                       {"batch_max_calls": -1}, {"max_repairs": 4}, {"model_rate": 5},
                       {"batch_wall_seconds": float("nan")}, {"task_max_turns": 1.5}):
            with self.subTest(config=config), self.assertRaises(StateError):
                self.ledger.initialize(config)
        state = self.ledger.initialize()
        self.assertEqual(state["limits"]["extra_api_spend_usd"], 0)

    def test_reinitialize_cannot_increase_limits_or_reset_usage(self):
        self.ledger.initialize({"batch_max_calls": 1})
        self.ledger.begin_call("first", "06a", expected_revision=0)
        self.now += 2
        state = self.ledger.finish_call("first", {"turns": 1, "input_tokens": 20, "output_tokens": 10},
                                       expected_revision=1)
        resumed = SubscriptionLimits(self.path, clock=lambda: self.now)
        with self.assertRaises(StateError):
            resumed.initialize({"batch_max_calls": 10})
        result = resumed.begin_call("second", "05a", expected_revision=state["revision"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertNotIn("second", result["calls"])
        self.assertEqual(resumed.usage_totals(result)["calls"], 1)

    def test_crash_preserves_running_call_and_prevents_duplicate_spend(self):
        self.ledger.initialize()
        self.ledger.begin_call("may-have-run", "06a", expected_revision=0)
        resumed = SubscriptionLimits(self.path, clock=lambda: self.now)
        with self.assertRaises(StateError):
            resumed.begin_call("new-call", "06a", expected_revision=1)
        with self.assertRaises(DuplicateEvent):
            resumed.begin_call("may-have-run", "06a", expected_revision=0)
        stopped = resumed.block("may-have-run", "interrupted", expected_revision=1)
        self.assertTrue(resumed.usage_totals(stopped)["usage_unknown"])
        self.assertEqual(resumed.usage_totals(stopped)["turns"], 1)
        with self.assertRaises(LimitExceeded):
            resumed.begin_call("fallback", "06a", expected_revision=2)

    def test_quota_and_rate_limit_stop_without_switching_billing(self):
        for reason in ("quota_exhausted", "rate_limit"):
            ledger = SubscriptionLimits(Path(self.tmp.name) / (reason + ".json"), clock=lambda: self.now)
            ledger.initialize()
            ledger.begin_call("one", "06a", expected_revision=0)
            result = ledger.block("one", reason, expected_revision=1)
            self.assertEqual(result["block_reason"], reason)
            self.assertEqual(result["limits"]["extra_api_spend_usd"], 0)
            with self.assertRaises(LimitExceeded):
                ledger.begin_call("retry", "06a", expected_revision=2)

    def test_block_preserves_observed_usage_and_reserved_turns_after_restart(self):
        self.ledger.initialize()
        self.ledger.begin_call("one", "05a", expected_revision=0)
        self.ledger.finish_call("one", {"turns": 1, "input_tokens": 20, "output_tokens": 10},
                                expected_revision=1)
        self.ledger.begin_call("two", "06a", reserved_turns=3, expected_revision=2)
        self.now += 7
        usage = {"turns": 2, "input_tokens": 100, "output_tokens": 30}
        result = self.ledger.block("two", "timeout", usage=usage, elapsed_seconds=7,
                                   expected_revision=3)
        resumed = SubscriptionLimits(self.path, clock=lambda: self.now)
        self.assertEqual(resumed.snapshot(), result)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["block_reason"], "timeout")
        self.assertEqual(result["calls"]["two"]["status"], "BLOCKED")
        self.assertEqual(result["calls"]["two"]["usage"], usage)
        self.assertEqual(result["calls"]["two"]["elapsed_seconds_reported"], 7)
        self.assertEqual(result["calls"]["two"]["reserved_turns"], 3)
        self.assertEqual(resumed.usage_totals(result), {"calls": 2, "turns": 4,
            "input_tokens": 120, "output_tokens": 40, "usage_unknown": False})
        self.assertEqual(resumed.usage_totals(result, "06a"), {"calls": 1, "turns": 3,
            "input_tokens": 100, "output_tokens": 30, "usage_unknown": False})
        with self.assertRaises(LimitExceeded):
            resumed.begin_call("retry", "06a", expected_revision=4)
        self.assertEqual(resumed.snapshot(), result)

    def test_block_rejects_invalid_usage_and_elapsed_without_mutating_ledger(self):
        self.ledger.initialize()
        initial = self.ledger.begin_call("one", "06a", expected_revision=0)
        for kwargs in (
                {"usage": {"turns": True, "input_tokens": 1, "output_tokens": 2}},
                {"usage": {"turns": 1, "input_tokens": -1, "output_tokens": 2}},
                {"usage": {"turns": 1, "input_tokens": 1, "output_tokens": 2, "extra": 0}},
                {"elapsed_seconds": True}, {"elapsed_seconds": -1}, {"elapsed_seconds": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(StateError):
                self.ledger.block("one", "timeout", expected_revision=1, **kwargs)
            self.assertEqual(self.ledger.snapshot(), initial)

    def test_deadline_uses_persisted_clock_not_reported_elapsed(self):
        self.ledger.initialize({"batch_wall_seconds": 60, "task_wall_seconds": 10})
        result = self.ledger.begin_call("one", "06a", expected_revision=0)
        self.assertEqual(result["calls"]["one"]["deadline_ms"], 1010000)
        self.now += 11
        result = self.ledger.finish_call("one", {"turns": 1, "input_tokens": 1, "output_tokens": 1},
                                        elapsed_seconds=0, expected_revision=1)
        self.assertEqual(result["block_reason"], "timeout")

    def test_clock_rollback_is_not_extra_runtime(self):
        self.ledger.initialize()
        self.ledger.begin_call("one", "06a", expected_revision=0)
        self.now -= 1
        result = self.ledger.finish_call("one", {"turns": 1, "input_tokens": 1, "output_tokens": 1}, expected_revision=1)
        self.assertEqual(result["block_reason"], "clock_rollback")

    def test_observed_tokens_are_cumulative_and_unknown_usage_cannot_fake_zero(self):
        self.ledger.initialize({"batch_max_input_tokens": 100})
        self.ledger.begin_call("one", "06a", expected_revision=0)
        result = self.ledger.finish_call("one", {"turns": 1, "input_tokens": 75, "output_tokens": 5}, expected_revision=1)
        self.ledger.begin_call("two", "05a", expected_revision=result["revision"])
        result = self.ledger.finish_call("two", None, expected_revision=3)
        self.assertEqual(result["block_reason"], "usage_unknown")
        self.assertEqual(self.ledger.usage_totals(result)["input_tokens"], 75)
        self.assertTrue(self.ledger.usage_totals(result)["usage_unknown"])

    def test_unknown_tokens_are_disclosed_when_only_reliable_limits_are_enabled(self):
        self.ledger.initialize()
        self.ledger.begin_call("one", "06a", expected_revision=0)
        result = self.ledger.finish_call("one", {"turns": 1, "input_tokens": None, "output_tokens": None}, expected_revision=1)
        self.assertEqual(result["status"], "READY")
        self.assertTrue(self.ledger.usage_totals(result)["usage_unknown"])
        self.assertEqual(self.ledger.usage_totals(result)["calls"], 1)

    def test_turn_excess_duplicate_settlement_and_stale_revision(self):
        self.ledger.initialize()
        self.ledger.begin_call("one", "06a", expected_revision=0)
        with self.assertRaises(RevisionConflict):
            self.ledger.finish_call("one", None, expected_revision=0)
        with self.assertRaises(StateError):
            self.ledger.finish_call("one", {"turns": True, "input_tokens": 1, "output_tokens": 2}, expected_revision=1)
        result = self.ledger.finish_call("one", {"turns": 2, "input_tokens": 1, "output_tokens": 2}, expected_revision=1)
        self.assertEqual(result["block_reason"], "limit_exceeded")
        with self.assertRaises(DuplicateEvent):
            self.ledger.finish_call("one", None, expected_revision=1)

    def test_repair_limit_survives_restart(self):
        self.ledger.initialize()
        for index in range(3):
            self.ledger.record_repair("06a", expected_revision=index, event_id=f"repair-{index}")
        resumed = SubscriptionLimits(self.path)
        with self.assertRaises(LimitExceeded):
            resumed.record_repair("06a", expected_revision=3, event_id="repair-4")
        self.assertEqual(resumed.snapshot()["repairs"]["06a"], 3)

    def test_corrupt_or_unknown_persisted_fields_are_rejected(self):
        result = self.ledger.initialize()
        result["limits"]["unknown"] = "unsafe"
        self.path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(StateError):
            self.ledger.snapshot()


if __name__ == "__main__":
    unittest.main()
