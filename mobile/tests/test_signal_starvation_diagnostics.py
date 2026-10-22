"""Mobile-only, no-orders signal starvation reporting and Shadow observations."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from forex_app.models import Decision
from forex_app.market import SyntheticFeed
from mobile.server import MobileRuntime, MobileTradingEngine


class SignalStarvationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.cfg = folder / "config.json"
        self.cfg.write_text(json.dumps({
            "symbols": ["EURUSD", "XAUUSD", "BTCUSD"],
            "starting_balance": 20000.0, "paper_trading_capital": 20000.0,
            "market_data_mode": "SYNTHETIC",
            "min_signal_score": 63, "min_confidence": 56,
            "max_open_positions": 5, "risk_per_trade_pct": 0.4,
            "max_total_risk_pct": 1.6, "selection_mode": "AUTO TOP 10",
            "trading_profile": "AI TRADING",
            "counterfactual_learning_enabled": True,
            "counterfactual_horizon_bars": 1,
        }))
        self.runtime = MobileRuntime(config_path=self.cfg, db_path=folder / "db.sqlite",
                                     feed=SyntheticFeed(["EURUSD", "XAUUSD", "BTCUSD"]),
                                     start_worker=False)
        self.e = self.runtime.engine
        self.e.currency_strength = {}

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def test_diagnostics_are_read_only_for_paper_ledger(self):
        initial = self.runtime.db.lifetime_trade_summary()
        before_balance = self.e.balance
        before_count = self.e.scan_count
        first = self.runtime._signal_diagnostics()
        second = self.runtime._signal_diagnostics()
        self.assertEqual(first, second)
        self.assertEqual(first["current_reasons"], {})
        self.assertIsNone(first["last_closed_at"])
        self.assertIsNone(first["last_opened_at"])
        self.assertFalse(first["no_entry_48h"])
        self.assertEqual(self.e.balance, before_balance)
        self.assertEqual(self.e.scan_count, before_count)
        self.assertEqual(self.runtime.db.lifetime_trade_summary(), initial)
        response = self.runtime.state()
        self.assertIn("signal_diagnostics", response)
        self.assertEqual(response["signal_diagnostics"]["shadow"]["sampled_rows"], 0)

    def test_session_and_quality_categories_do_not_change_decisions(self):
        d = Decision("EURUSD", "BLOCK", 50, 42, "BLOCK — WRONG_SESSION / FX_NY_PROBATION",
                     10, 20, "2026-10-22T10:00:00+00:00")
        self.assertEqual(MobileTradingEngine._signal_gate(d, d), "SESSION_POLICY")
        w = replace(d, action="WAIT", reason="WAIT — insufficient confirmed edge / structure")
        self.assertEqual(MobileTradingEngine._signal_gate(w, w), "SIGNAL_QUALITY")
        self.assertEqual(MobileTradingEngine._signal_gate(replace(d,action="BUY"),
                                                         replace(d,action="BUY")), "EXECUTION_CANDIDATE")

    def test_learning_veto_observed_in_shadow_but_never_executed(self):
        snap = self.runtime.feed.snapshot("EURUSD")
        original = Decision("EURUSD", "BUY", 79, 75, "Champion BUY edge",
                            9, 18, snap.timestamp)
        vetoed = replace(original, action="WAIT",
                         reason="Champion BUY edge | GOV RESEARCH-ONLY risk=0.00x · GOVERNOR RESEARCH-ONLY")
        self.e.enabled = True
        self.e.scan_count = 8
        initial_count = self.runtime.db.lifetime_trade_summary()["trades"]
        with patch.object(self.e.champion, "decide", return_value=original) as champion, \
             patch.object(self.e.learning, "adjust_decision", return_value=vetoed), \
             patch.object(self.e, "_open", side_effect=AssertionError("No execution permitted")):
            decision, _ = self.e._research_decision(snap)
        self.assertIs(decision, vetoed)
        champion.assert_called_once()
        self.assertEqual(self.e.signal_gate_counts["LEARNING_GOVERNOR"], 1)
        self.assertEqual(self.e.signal_gate_observations, 1)
        self.assertEqual(len(self.e.learning.pending_counterfactuals), 1)
        self.assertEqual(self.runtime.db.lifetime_trade_summary()["trades"], initial_count)

        # The existing Shadow resolver measures only the later price change.
        # No PAPER position or broker order is ever created.
        future = replace(snap, timestamp="2026-10-22T22:00:00+00:00",
                         bid=snap.bid+0.0010, ask=snap.ask+0.0010)
        self.e.learning.resolve_counterfactuals({"EURUSD":future}, 9)
        result = self.runtime._signal_diagnostics()
        self.assertEqual(len(self.e.positions), 0)
        self.assertEqual(self.runtime.db.lifetime_trade_summary()["trades"], initial_count)
        groups = result["shadow"]["groups"]
        self.assertTrue(any(x["reason"] == "LEARNING_VETO" and x["samples"] == 1 for x in groups))
        self.assertEqual(result["signal_reasons_since_boot"]["LEARNING_GOVERNOR"], 1)

    def test_paused_telemetry_does_not_start_shadow_or_execution(self):
        snap = self.runtime.feed.snapshot("EURUSD")
        orig = Decision("EURUSD", "BUY", 80, 70, "Original", 8, 16, snap.timestamp)
        veto = replace(orig, action="WAIT", reason="GOVERNOR RESEARCH-ONLY")
        with patch.object(self.e.champion, "decide", return_value=orig), \
             patch.object(self.e.learning, "adjust_decision", return_value=veto):
            decision, _ = self.e._research_decision(snap)
        self.assertEqual(decision.action, "WAIT")
        self.assertEqual(self.e.signal_gate_observations, 0)
        self.assertEqual(len(self.e.learning.pending_counterfactuals), 0)

    def test_no_trade_alert_requires_actual_elapsed_time(self):
        self.e.enabled = True
        before = self.runtime._signal_diagnostics()
        self.assertFalse(before["no_entry_48h"], "Never traded is not an inferred historical lock")
        self.runtime.db.conn.execute(
            """INSERT INTO trades(id,symbol,side,lots,entry,exit,pnl,r_multiple,
                    opened_at,closed_at,reason,brain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("old", "EURUSD", "BUY", 0.01, 1.1, 1.11, 0.5, 0.1,
             "2026-10-01T00:00:00+00:00", "2026-10-01T01:00:00+00:00",
             "TP", "Champion"))
        self.runtime.db.conn.commit()
        after = self.runtime._signal_diagnostics()
        self.assertTrue(after["no_entry_48h"])
        self.assertGreaterEqual(after["last_opened_hours_ago"], 48)
        self.assertEqual(self.runtime.db.lifetime_trade_summary()["trades"], 1)


if __name__ == "__main__":
    unittest.main()
