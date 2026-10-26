"""Regression: mobile PAPER honors Adaptive RSI's SHADOW-only configuration.

Do not alter desktop Forex engine or enable broker trading.
"""
import json
import tempfile
import unittest
from pathlib import Path

from forex_app.market import SyntheticFeed
from forex_app.models import Decision
from forex_app.rsi_intelligence import analyze_snapshot
from mobile.server import MobileRuntime


class MobileAdaptiveRsiShadowContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_runtime(self, shadow_only):
        cfg = self.root / ("shadow.json" if shadow_only else "executing.json")
        values = {
            "symbols": ["EURUSD", "BTCUSD", "XAUUSD"],
            "starting_balance": 20000, "paper_trading_capital": 20000,
            "market_data_mode": "LIVE", "selection_mode": "AUTO TOP 10",
            "trading_profile": "AI TRADING",
            "min_signal_score": 63, "min_confidence": 56,
            "risk_per_trade_pct": 0.4, "max_total_risk_pct": 1.6,
            "adaptive_rsi_shadow_only": shadow_only,
            "adaptive_rsi_execution_gate_enabled": True,
            "forex_probation_enabled": True,
            "context_forex_new_york_enabled": False,
            "self_learning_enabled": True,
        }
        cfg.write_text(json.dumps(values))
        runtime = MobileRuntime(
            config_path=cfg,
            db_path=self.root / ("shadow.sqlite" if shadow_only else "executing.sqlite"),
            feed=SyntheticFeed(["EURUSD","BTCUSD","XAUUSD"]),
            start_worker=False,
        )
        return runtime, cfg, values

    def test_explicit_shadow_only_prevents_unvalidated_rsi_entry_veto(self):
        runtime, path, original = self.make_runtime(True)
        try:
            self.assertTrue(runtime.staged_paper_test)
            self.assertFalse(runtime.cfg["adaptive_rsi_execution_gate_enabled"])
            self.assertFalse(runtime.engine.cfg["adaptive_rsi_execution_gate_enabled"])
            self.assertTrue(runtime.cfg["forex_probation_enabled"])
            self.assertTrue(runtime.cfg["self_learning_enabled"])
            self.assertEqual(runtime._signal_diagnostics()["adaptive_rsi_paper_mode"], "SHADOW ONLY")
            self.assertEqual(runtime.db.lifetime_trade_summary()["trades"], 0)

            s = runtime.feed.snapshot("EURUSD")
            d = Decision("EURUSD","SELL",80,80,"test signal",8,16,s.timestamp)
            research = runtime.engine.adaptive_rsi.propose(s,d,"REVERSAL",analyze_snapshot(s))
            self.assertEqual(research["mode"],"SHADOW")
            self.assertEqual(runtime.engine.adaptive_rsi.summary()["execution_influence"],False)
            self.assertEqual(json.loads(path.read_text()), original,
                             "Mobile runtime overrides must not rewrite shared config")
        finally:
            runtime.close()

    def test_explicit_executing_setting_is_not_silently_changed(self):
        runtime, path, original = self.make_runtime(False)
        try:
            self.assertTrue(runtime.cfg["adaptive_rsi_execution_gate_enabled"])
            self.assertEqual(runtime._signal_diagnostics()["adaptive_rsi_paper_mode"],"EXECUTION GATE")
            self.assertEqual(json.loads(path.read_text()),original)
        finally:
            runtime.close()

    def test_shadow_contract_does_not_change_paper_ledger_or_balance(self):
        runtime, _, original = self.make_runtime(True)
        try:
            before=runtime.db.lifetime_trade_summary()
            balance=runtime.engine.balance
            runtime._signal_diagnostics()
            runtime.state()
            self.assertEqual(runtime.db.lifetime_trade_summary(),before)
            self.assertEqual(runtime.engine.balance,balance)
            self.assertEqual(len(runtime.engine.positions),0)
            self.assertEqual(original["starting_balance"],20000)
        finally:
            runtime.close()


if __name__=="__main__":
    unittest.main()
