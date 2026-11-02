"""Pre-IG CRYPTO PAPER: validate Shadow-only RSI and observational diagnostics.

No external price requests, broker orders or new PAPER sessions.
"""
import json
import tempfile
import unittest
from pathlib import Path

from forex_app.market import SyntheticFeed
from forex_app.models import Decision
from mobile.server import MobileRuntime


class CryptoShadowDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=Path(self.temp.name)
        self.cfg_path=self.home/"config.json"
        self.config={
            "symbols":["BTCUSD","ETHUSD","EURUSD"],
            "starting_balance":20000,
            "paper_trading_capital":20000,
            "market_data_mode":"LIVE",
            "min_signal_score":63,
            "min_confidence":56,
            "trading_profile":"AI TRADING",
            "selection_mode":"AUTO TOP 5",
            "risk_per_trade_pct":0.4,
            "max_total_risk_pct":1.6,
            "adaptive_rsi_shadow_only":True,
            "adaptive_rsi_execution_gate_enabled":True,
            "high_vol_require_positive_edge":True,
        }
        self.cfg_path.write_text(json.dumps(self.config))

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self):
        return MobileRuntime(
            config_path=self.cfg_path,
            db_path=self.home/"paper.sqlite",
            feed=SyntheticFeed(self.config["symbols"]),
            start_worker=False,
        )

    def test_shadow_only_contract_prevents_unintended_hard_execution_gate(self):
        r=self.runtime()
        try:
            self.assertFalse(r.cfg["adaptive_rsi_execution_gate_enabled"])
            self.assertTrue(r.cfg["adaptive_rsi_shadow_only"])
            self.assertFalse(r.engine.cfg["adaptive_rsi_execution_gate_enabled"])
            self.assertEqual(r._crypto_diagnostics()["adaptive_rsi_mode"],"SHADOW ONLY")
            self.assertTrue(r.cfg["high_vol_require_positive_edge"],
                            "Other trading gates must not be relaxed")
            self.assertEqual(r.cfg["enabled_asset_classes"],["CRYPTO"])
            self.assertEqual(json.loads(self.cfg_path.read_text()),self.config,
                             "Shared PC config must remain unchanged")
        finally:
            r.close()

    def test_explicit_nonshadow_execution_setting_remains_unchanged(self):
        cfg=dict(self.config,adaptive_rsi_shadow_only=False)
        self.cfg_path.write_text(json.dumps(cfg))
        r=self.runtime()
        try:
            self.assertTrue(r.cfg["adaptive_rsi_execution_gate_enabled"])
            self.assertEqual(r._crypto_diagnostics()["adaptive_rsi_mode"],"EXECUTION GATE")
            self.assertEqual(json.loads(self.cfg_path.read_text()),cfg)
        finally:
            r.close()

    def test_diagnostics_count_each_closed_candle_once_and_do_not_trade(self):
        r=self.runtime()
        try:
            e=r.engine
            e.enabled=True
            old_balance=e.balance
            old_trades=r.db.lifetime_trade_summary()
            d=Decision("BTCUSD","BUY",81,76,"Champion test",11,23,"2026-11-02T09:00:00+00:00")
            e.decisions={"BTCUSD":d,"ETHUSD":Decision(
                "ETHUSD","WAIT",60,55,"WAIT - insufficient edge",11,23,d.timestamp)}
            e.decision_cycle_count+=1
            e.last_gate_reasons={
                "HIGH_VOL_RESEARCH_ONLY · empirical edge UNPROVEN":2,
                "Adaptive RSI gate · RSI outside band":1,
            }
            r._record_crypto_diagnostics()
            self.assertEqual(r._diag_actions["BUY"],1)
            self.assertEqual(r._diag_actions["WAIT"],1)
            self.assertEqual(r._diag_gates["HIGH_VOL_HISTORY"],2)
            self.assertEqual(r._diag_gates["ADAPTIVE_RSI"],1)
            r._record_crypto_diagnostics()
            self.assertEqual(r._diag_actions["BUY"],1)
            self.assertEqual(r._diag_gates["HIGH_VOL_HISTORY"],2)
            e.decision_cycle_count+=1
            e.last_gate_reasons={"HIGH_VOL_RESEARCH_ONLY":1}
            r._record_crypto_diagnostics()
            self.assertEqual(r._diag_actions["BUY"],2)
            self.assertEqual(r._diag_gates["HIGH_VOL_HISTORY"],3)
            self.assertEqual(r.db.lifetime_trade_summary(),old_trades)
            self.assertEqual(e.balance,old_balance)
            self.assertEqual(e.positions,[])
        finally:
            r.close()

    def test_read_only_api_diagnostics_does_not_mutate_trade_history(self):
        r=self.runtime()
        try:
            before=r.db.lifetime_trade_summary()
            ledger=r.db.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            for _ in range(3):
                d=r._crypto_diagnostics()
                self.assertEqual(d["selection"]["scanned"],0)
                self.assertEqual(d["market_data_mode"],"LIVE")
                self.assertEqual(d["actions_since_start"],{})
                self.assertEqual(d["readiness_scope"],"BLOCKED")
            snapshot=r.state()
            self.assertIn("crypto_diagnostics",snapshot)
            self.assertFalse(snapshot["crypto_diagnostics"]["active"])
            self.assertEqual(before,r.db.lifetime_trade_summary())
            self.assertEqual(ledger,r.db.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
        finally:
            r.close()


if __name__=="__main__":
    unittest.main()
