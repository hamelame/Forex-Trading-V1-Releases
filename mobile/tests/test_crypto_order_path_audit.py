"""Mobile CRYPTO PAPER: prove 'trade-ready' != an actual new-candle order.

Observational only. Mocks the common parent scanner and never opens an order.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forex_app.engine import TradingEngine
from forex_app.market import SyntheticFeed
from forex_app.models import Decision
from mobile.server import MobileRuntime


class CryptoOrderPathAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        conf = self.home / "settings.json"
        conf.write_text(json.dumps({
            "symbols":["BTCUSD","ETHUSD","SOLUSD","EURUSD"],
            "market_data_mode":"LIVE",
            "starting_balance":20000,
            "paper_trading_capital":20000,
            "min_signal_score":63,
            "min_confidence":56,
            "selection_mode":"AUTO TOP 5",
            "trading_profile":"AI TRADING",
            "risk_per_trade_pct":0.4,
        }))
        self.runtime=MobileRuntime(config_path=conf,
                db_path=self.home/"paper.sqlite",
                feed=SyntheticFeed(["BTCUSD","ETHUSD","SOLUSD","EURUSD"]),
                start_worker=False)
        self.e=self.runtime.engine
        self.e.enabled=True
        self.e.last_candle_ts["BTCUSD"]="2026-11-02T09:40:00Z"
        self.e.last_candle_ts["ETHUSD"]="2026-11-02T09:40:00Z"
        self.e.decisions["BTCUSD"]=Decision(
            "BTCUSD","SELL",81,79,"test signal",8,15,
            "2026-11-02T09:40:00Z")
        self.e.selector.shortlist=["BTCUSD"]
        self.e.regimes["BTCUSD"]="TREND"
        self.e.last_gate_reasons={}
        self.e.last_status="AI started new PAPER session"

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def test_selected_old_candle_is_not_new_order_attempt(self):
        e=self.e
        before=self.runtime.db.lifetime_trade_summary()
        def other_candle(engine):
            engine.decision_cycle_count+=1
            engine.last_candle_ts["ETHUSD"]="2026-11-02T09:41:00Z"
        with patch.object(TradingEngine,"scan",autospec=True,side_effect=other_candle):
            e.scan()
        a=e.mobile_order_audit
        self.assertEqual(a["outcome"],"SELECTED_SIGNAL_FROM_PRIOR_CANDLE")
        self.assertEqual(a["fresh_selected_buy_sell"],[])
        self.assertEqual(a["selected_buy_sell"][0]["side"],"SELL")
        self.assertEqual(a["recent_opened_symbols"],[])
        self.assertEqual(self.runtime.db.lifetime_trade_summary(),before)
        self.runtime._record_crypto_diagnostics()
        d=self.runtime._crypto_diagnostics()
        self.assertEqual(d["last_block_category"],"NONE")
        self.assertEqual(d["order_path_counts"]["SELECTED_SIGNAL_FROM_PRIOR_CANDLE"],1)
        self.assertIn("PAPER session",a["engine_status"])

    def test_verified_real_crypto_veto_without_fake_session_reason(self):
        e=self.e
        def new_btc(engine):
            engine.decision_cycle_count+=1
            engine.last_candle_ts["BTCUSD"]="2026-11-02T09:41:00Z"
            engine.last_gate_reasons={
              "HIGH_VOL_RESEARCH_ONLY · empirical edge UNPROVEN":1
            }
        with patch.object(TradingEngine,"scan",autospec=True,side_effect=new_btc):
            e.scan()
        a=e.mobile_order_audit
        self.assertEqual(a["outcome"],"EXECUTION_REJECTED")
        self.assertEqual(a["fresh_selected_buy_sell"],["BTCUSD"])
        self.assertIn("HIGH_VOL_RESEARCH_ONLY",a["real_execution_rejections"])
        self.runtime._record_crypto_diagnostics()
        d=self.runtime._crypto_diagnostics()
        self.assertEqual(d["last_block_category"],"HIGH_VOL_HISTORY")
        self.assertEqual(d["last_order_gate"]["HIGH_VOL_HISTORY"],1)
        self.assertEqual(d["order_path_counts"]["EXECUTION_REJECTED"],1)
        self.assertEqual(self.e.positions,[])

    def test_fresh_buy_sell_without_reported_gate_is_surfaced(self):
        e=self.e
        def new_btc(engine):
            engine.decision_cycle_count+=1
            engine.last_candle_ts["BTCUSD"]="2026-11-02T09:41:00Z"
            engine.last_gate_reasons={}
        with patch.object(TradingEngine,"scan",autospec=True,side_effect=new_btc):
            e.scan()
        self.assertEqual(e.mobile_order_audit["outcome"],
                         "FRESH_SIGNAL_WITHOUT_RECORDED_GATE")
        self.assertEqual(self.runtime._crypto_diagnostics()["last_block_category"],"NONE")

    def test_paused_scanner_does_not_invent_new_order_attempt(self):
        self.e.enabled=False
        with patch.object(TradingEngine,"scan",autospec=True):
            self.e.scan()
        self.assertFalse(hasattr(self.e,"mobile_order_audit"))
        self.assertEqual(self.runtime._crypto_diagnostics()["last_block_category"],"NONE")


if __name__=="__main__":
    unittest.main()
