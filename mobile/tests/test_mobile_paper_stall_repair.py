"""Guardrails for mobile-only PAPER entry stall repair.

The PC strategy remains unchanged. No test can send IG orders.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forex_app.context_edge import session_policy
from forex_app.engine import TradingEngine
from forex_app.market import SyntheticFeed
from forex_app.models import Decision
from mobile.server import MobileRuntime


class MobilePaperStallRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = root / "config.json"
        self.config.write_text(json.dumps({
            "symbols": ["EURUSD", "BTCUSD", "ETHUSD"],
            "market_data_mode": "LIVE",
            "paper_trading_capital": 20000.0,
            "starting_balance": 20000.0,
            "min_signal_score": 63,
            "selection_mode": "AUTO TOP 10",
            "trading_profile": "AI TRADING",
            "risk_per_trade_pct": 0.4,
            "max_total_risk_pct": 1.6,
            "max_open_positions": 4,
            "context_session_gate_enabled": True,
            "context_forex_new_york_enabled": False,
            "context_unvalidated_asia_enabled": False,
            "forex_probation_enabled": True,
            "high_vol_require_positive_edge": True,
            "high_vol_min_signal_score": 78,
            "high_vol_min_confidence": 76,
            "allow_rollover_session": False,
            "allow_asia_session": True,
        }))
        self.runtime = MobileRuntime(
            config_path=self.config, db_path=root / "paper.sqlite",
            feed=SyntheticFeed(["EURUSD", "BTCUSD", "ETHUSD"]), start_worker=False
        )
        self.e = self.runtime.engine
        self.snap = self.runtime.feed.snapshot("BTCUSD")
        self.decision = Decision(
            "BTCUSD", "BUY", 88, 86, "test edge", 12, 26, self.snap.timestamp
        )
        self.e.regimes["BTCUSD"] = "HIGH VOL"

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def test_new_york_forex_is_paper_probation_only(self):
        self.assertTrue(self.runtime.staged_paper_test)
        self.assertTrue(self.e.cfg["paper_only_build"])
        self.assertTrue(self.e.cfg["context_forex_new_york_enabled"])
        self.assertTrue(self.e.cfg["forex_probation_enabled"])
        self.assertTrue(session_policy("EURUSD", "New York", self.e.cfg)["allowed"])
        self.assertTrue(session_policy("EURUSD", "London", self.e.cfg)["allowed"])
        self.assertFalse(session_policy("EURUSD", "Asia", self.e.cfg)["allowed"])
        self.assertFalse(session_policy("EURUSD", "Rollover / Thin", self.e.cfg)["allowed"])
        # The shared checked-in PC defaults are not modified.
        self.assertFalse(json.loads(self.config.read_text())["context_forex_new_york_enabled"])

    def test_bootstrap_one_virgin_high_vol_crypto_candidate(self):
        self.assertTrue(self.e.cfg["mobile_crypto_highvol_bootstrap"])
        self.assertTrue(self.e.cfg["high_vol_require_positive_edge"])
        with patch.object(self.e.learning, "execution_edge_score",
                          return_value={"label": "UNPROVEN", "samples": 0}), \
             patch.object(TradingEngine, "_open", autospec=True) as core_open:
            self.e._open(self.decision, self.snap)
            core_open.assert_called_once()
            self.assertFalse(core_open.call_args.args[0].cfg["high_vol_require_positive_edge"] is False,
                "temporary override must be restored after returning")
            # The temporary flag is FALSE only while calling core implementation;
            # verify with a side effect, not retained state.
        self.assertTrue(self.e.cfg["high_vol_require_positive_edge"])
        self.assertEqual(len(self.e.positions), 0)
        self.assertEqual(self.runtime.db.lifetime_trade_summary()["trades"], 0)

    def test_core_sees_temporary_disabled_positive_edge_gate(self):
        seen = []
        def inspect(instance, d, s):
            seen.append(instance.cfg["high_vol_require_positive_edge"])
        with patch.object(self.e.learning, "execution_edge_score",
                          return_value={"label": "UNPROVEN", "samples": 0}), \
             patch.object(TradingEngine, "_open", autospec=True, side_effect=inspect):
            self.e._open(self.decision, self.snap)
        self.assertEqual(seen, [False])
        self.assertTrue(self.e.cfg["high_vol_require_positive_edge"])

    def test_negative_or_insufficient_confidence_cannot_bypass_gate(self):
        for edge, decision in (
            ({"label": "NEGATIVE", "samples": 1}, self.decision),
            ({"label": "MIXED", "samples": 1}, self.decision),
            ({"label": "UNPROVEN", "samples": 0},
                Decision("BTCUSD", "BUY", 80, 86, "not enough score", 12, 26,
                         self.snap.timestamp)),
            ({"label": "UNPROVEN", "samples": 0},
                Decision("BTCUSD", "BUY", 88, 79, "not enough confidence", 12, 26,
                         self.snap.timestamp)),
        ):
            observed = []
            with self.subTest(label=edge["label"], score=decision.score, confidence=decision.confidence), \
                 patch.object(self.e.learning, "execution_edge_score", return_value=edge), \
                 patch.object(TradingEngine, "_open", autospec=True,
                              side_effect=lambda engine, d, s: observed.append(
                                  engine.cfg["high_vol_require_positive_edge"])):
                self.e._open(decision, self.snap)
            self.assertEqual(observed, [True])
            self.assertTrue(self.e.cfg["high_vol_require_positive_edge"])

    def test_prior_same_regime_outcome_keeps_positive_edge_gate(self):
        # A prior actual PAPER learning experience is stronger than synthetic
        # shadow evidence; it disqualifies the special first-trial pathway.
        self.runtime.db.conn.execute(
            "INSERT INTO learning_experiences(symbol,side,regime) VALUES (?,?,?)",
            ("BTCUSD", "BUY", "HIGH VOL")
        )
        self.runtime.db.conn.commit()
        observed = []
        with patch.object(self.e.learning, "execution_edge_score",
                          return_value={"label": "UNPROVEN", "samples": 0}), \
             patch.object(TradingEngine, "_open", autospec=True,
                          side_effect=lambda engine, d, s: observed.append(
                              engine.cfg["high_vol_require_positive_edge"])):
            self.e._open(self.decision, self.snap)
        self.assertEqual(observed, [True])
        self.assertEqual(self.runtime.db.lifetime_trade_summary()["trades"], 0)

    def test_never_apply_bootstrap_to_fx_or_paused_ig(self):
        snap = self.runtime.feed.snapshot("EURUSD")
        d = Decision("EURUSD", "BUY", 95, 95, "test", 8, 16, snap.timestamp)
        self.e.regimes["EURUSD"] = "HIGH VOL"
        seen = []
        with patch("mobile.server._non_crypto_session_open", return_value=True), \
             patch.object(TradingEngine, "_open", autospec=True,
                          side_effect=lambda engine, dec, s: seen.append(
                              engine.cfg["high_vol_require_positive_edge"])):
            self.e._open(d, snap)
        self.assertEqual(seen, [True])
        self.assertEqual(len(self.e.positions), 0)

if __name__ == "__main__":
    unittest.main()
