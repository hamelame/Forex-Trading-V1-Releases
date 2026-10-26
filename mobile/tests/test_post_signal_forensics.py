"""Read-only diagnostics of PAPER entry candidates after the Champion signal."""
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from forex_app.engine import TradingEngine
from forex_app.market import SyntheticFeed
from forex_app.models import Decision
from mobile.server import MobileRuntime


class PostSignalForensicsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        cfg = root / "config.json"
        cfg.write_text(json.dumps({
            "symbols": ["EURUSD", "BTCUSD"],
            "starting_balance": 20000, "paper_trading_capital": 20000,
            "market_data_mode": "SYNTHETIC", "selection_mode": "AUTO TOP 10",
            "trading_profile": "AI TRADING", "min_signal_score": 63,
            "min_confidence": 56, "selection_min_ai_score": 63,
            "selection_min_confidence": 56, "selection_min_market_score": 58,
            "selection_min_data_quality": 75, "selection_max_spread": 2.5,
        }))
        self.runtime = MobileRuntime(
            config_path=cfg, db_path=root / "db.sqlite",
            feed=SyntheticFeed(["EURUSD", "BTCUSD"]), start_worker=False)
        self.e = self.runtime.engine
        self.snap = self.runtime.feed.snapshot("BTCUSD")
        self.good = Decision("BTCUSD","BUY",87,82,"Signal",10,20,self.snap.timestamp)

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_selection_reason_follows_real_gate_threshold_order(self):
        explain = self.e._post_signal_eligibility_reason
        self.assertEqual(explain("BTCUSD",self.snap,self.good,57,"TREND"),"MARKET_RANK")
        self.assertEqual(explain("BTCUSD",self.snap,replace(self.good,score=62),80,"TREND"),"AI_SCORE")
        self.assertEqual(explain("BTCUSD",self.snap,replace(self.good,confidence=55),80,"TREND"),"AI_CONFIDENCE")
        self.assertEqual(explain("BTCUSD",replace(self.snap,quality=74),self.good,80,"TREND"),"DATA_QUALITY")
        self.assertEqual(explain("BTCUSD",replace(self.snap,spread_pips=3),self.good,80,"TREND"),"SPREAD")
        self.assertEqual(explain("BTCUSD",self.snap,self.good,80,"TREND"),"UNEXPLAINED_SELECTOR_REFUSAL")

    def test_post_signal_research_never_touches_ledger_or_trade_readiness(self):
        e = self.e
        e.snapshots["BTCUSD"] = self.snap
        e.decisions["BTCUSD"] = self.good
        e.rankings["BTCUSD"] = 57.0
        e.regimes["BTCUSD"] = "TREND"
        initial = self.runtime.db.lifetime_trade_summary()
        e._post_signal_audit()
        self.assertEqual(e.post_signal_counts["MARKET_RANK"], 1)
        self.assertEqual(e.post_signal_latest["examples"]["MARKET_RANK"]["symbol"],"BTCUSD")
        self.assertEqual(self.runtime.db.lifetime_trade_summary(),initial)
        self.assertEqual(len(e.positions),0)
        self.assertEqual(e.decisions["BTCUSD"],self.good)
        e.selector.shortlist=["BTCUSD"]
        e.selector.eligible=["BTCUSD"]
        e._post_signal_audit()
        self.assertEqual(e.post_signal_counts["SELECTED_BUY_SELL"],1)
        self.assertEqual(e.post_signal_counts["MARKET_RANK"],1)

    def test_observation_only_once_per_new_closed_candle(self):
        e=self.e
        e.enabled=True
        with patch.object(TradingEngine,"scan",autospec=True) as underlying:
            e.scan()
            e.scan()
            underlying.assert_called()
        self.assertEqual(e.post_signal_counts, {})
        with patch.object(TradingEngine,"scan",autospec=True,
                          side_effect=lambda engine:setattr(engine,"decision_cycle_count",1)):
            e.scan()
        self.assertEqual(e.post_signal_last_cycle,-1)
        self.assertEqual(e.post_signal_counts,{})
        self.assertIsInstance(self.runtime._signal_diagnostics()["post_signal_totals_since_boot"],dict)


if __name__=="__main__":
    unittest.main()
