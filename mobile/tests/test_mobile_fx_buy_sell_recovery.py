"""Mobile PAPER only: bounded, two-sided FX recovery / zero IG execution.

Never change TradingEngine or PC config; live feed and every non-probation
technical / risk veto remain binding during a test position.
"""
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from forex_app.engine import TradingEngine
from forex_app.market import SyntheticFeed
from forex_app.models import Decision
from mobile.server import MobileRuntime


class MobileFxBuySellPaperRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        home=Path(self.tmp.name)
        self.cfg_path=home/"config.json"
        self.base={
            "symbols":["EURUSD","GBPUSD","EURGBP","USDJPY","BTCUSD"],
            "market_data_mode":"LIVE",
            "starting_balance":20000.0,"paper_trading_capital":20000.0,
            "selection_mode":"AUTO TOP 10","trading_profile":"AI TRADING",
            "min_signal_score":63,"min_confidence":56,
            "risk_per_trade_pct":0.4,"max_total_risk_pct":1.6,
            "equity_sizing_max_trade_risk_pct":0.18,
            "max_open_positions":5,"forex_probation_enabled":True,
            "context_session_gate_enabled":True,
            "allow_london_session":True,"allow_overlap_session":True,
            "context_forex_new_york_enabled":False,
        }
        self.cfg_path.write_text(json.dumps(self.base))
        feed=SyntheticFeed(self.base["symbols"])
        feed.requires_fresh_live_data=True
        self.runtime=MobileRuntime(config_path=self.cfg_path,
                   db_path=home/"paper.sqlite",feed=feed,start_worker=False)
        self.e=self.runtime.engine
        self.e.enabled=True
        self.e.entry_asset_scope="MULTI_MARKET"
        self.s=replace(feed.snapshot("GBPUSD"),
            session="London",feed_status="LIVE",
            timestamp=datetime.now(timezone.utc).isoformat(),
            data_age_seconds=5.0,quality=98.0,spread_pips=0.8)
        self.e.regimes["GBPUSD"]="TREND"
        self.d=Decision("GBPUSD","BUY",82,78,"Champion BUY",
                         9,22,self.s.timestamp)

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def mocked_research(self, edge=None,stats=None,risk_state=("NORMAL",0)):
        stack=ExitStack()
        stack.enter_context(patch("mobile.server._non_crypto_session_open",return_value=True))
        stack.enter_context(patch.object(self.e.risk,"recovery_state",return_value=risk_state))
        stack.enter_context(patch.object(self.e.learning,"execution_edge_score",
            return_value=edge or {"label":"UNPROVEN","score":0.0,"samples":0}))
        stack.enter_context(patch.object(self.e.learning,"symbol_stats_for",return_value=stats))
        stack.enter_context(patch.object(self.e.learning,"context_health",
            return_value={"allow":True,"score":0.0,"state":"LEARNING"}))
        stack.enter_context(patch.object(self.e.learning,"runtime_context_guard",
            return_value={"allow":True,"score":0.0,"state":"CLEAR"}))
        stack.enter_context(patch.object(self.e.learning,"directional_health",
            return_value={"state":"LEARNING","score":0.0}))
        return stack

    def test_both_buy_and_sell_paper_signals_get_equal_research_access(self):
        with self.mocked_research():
            self.assertTrue(self.e._fx_paper_research_allowed(self.d,self.s))
            sell=replace(self.d,action="SELL",reason="Champion SELL")
            self.assertTrue(self.e._fx_paper_research_allowed(sell,self.s))
        self.assertTrue(self.runtime.staged_paper_test)
        self.assertTrue(self.e.cfg["forex_probation_enabled"])
        self.assertTrue(self.e.cfg["mobile_fx_paper_research_enabled"])
        self.assertEqual(self.runtime.db.lifetime_trade_summary()["trades"],0)
        self.assertEqual(json.loads(self.cfg_path.read_text()),self.base)

    def test_no_real_money_ig_or_cross_asset_research(self):
        with self.mocked_research():
            for side in ("BUY","SELL"):
                eurusd=replace(self.s,symbol="EURUSD")
                self.assertFalse(self.e._fx_paper_research_allowed(
                    replace(self.d,symbol="EURUSD",action=side),eurusd))
            self.assertFalse(self.e._fx_paper_research_allowed(
                replace(self.d,symbol="BTCUSD"),replace(self.s,symbol="BTCUSD")))
            self.assertFalse(self.e._fx_paper_research_allowed(
                replace(self.d,action="WAIT"),self.s))
            self.assertFalse(self.e._fx_paper_research_allowed(
                replace(self.d,action="BLOCK"),self.s))
            self.assertFalse(self.e._fx_paper_research_allowed(
                replace(self.d,score=68),self.s))
            self.assertFalse(self.e._fx_paper_research_allowed(
                replace(self.d,confidence=64),self.s))
            self.assertFalse(self.e._fx_paper_research_allowed(
                self.d,replace(self.s,session="Asia")))
            self.assertFalse(self.e._fx_paper_research_allowed(
                self.d,replace(self.s,session="New York")))
            self.assertFalse(self.e._fx_paper_research_allowed(
                self.d,replace(self.s,session="Rollover / Thin")))
            self.assertFalse(self.e._fx_paper_research_allowed(
                self.d,replace(self.s,data_age_seconds=121)))
            self.assertFalse(self.e._fx_paper_research_allowed(
                self.d,replace(self.s,feed_status="STALE")))
            self.assertFalse(self.e._fx_paper_research_allowed(
                self.d,replace(self.s,quality=80)))
            self.assertFalse(self.e._fx_paper_research_allowed(
                self.d,replace(self.s,spread_pips=3)))
        self.assertFalse(self.e.cfg["mobile_fx_paper_research_enabled"] is False)

    def test_excludes_empirically_negative_and_emergency_contexts(self):
        with self.mocked_research(edge={"label":"NEGATIVE","score":-0.4,"samples":6}):
            self.assertFalse(self.e._fx_paper_research_allowed(self.d,self.s))
        with self.mocked_research(stats={"recent_samples":8,
                          "expectancy_r":-0.24,"pf":0.5}):
            self.assertFalse(self.e._fx_paper_research_allowed(self.d,self.s))
        with self.mocked_research(risk_state=("EMERGENCY",0)):
            self.assertFalse(self.e._fx_paper_research_allowed(self.d,self.s))
        with self.mocked_research(risk_state=("COOLDOWN",6)):
            self.assertFalse(self.e._fx_paper_research_allowed(self.d,self.s))
        with self.mocked_research(risk_state=("RECOVERY",0)):
            self.assertTrue(self.e._fx_paper_research_allowed(self.d,self.s))
        self.e.enabled=False
        with self.mocked_research():
            self.assertFalse(self.e._fx_paper_research_allowed(self.d,self.s))

    def test_both_sides_use_original_parent_technical_gate_and_tiny_risk_cap(self):
        observed=[]
        def original(engine,d,s):
            observed.append({
                "side":d.action,"symbol":s.symbol,"brain":d.brain,
                "probation":engine.cfg["forex_probation_enabled"],
                "risk_pct":engine.cfg["risk_per_trade_pct"],
                "risk_cap_pct":engine.cfg["equity_sizing_max_trade_risk_pct"],
                "reason":d.reason,
            })
        before=self.runtime.db.lifetime_trade_summary()
        with patch("mobile.server._non_crypto_session_open",return_value=True):
            with patch.object(self.e,"_fx_paper_research_allowed",return_value=True):
                with patch.object(TradingEngine,"_open",autospec=True,side_effect=original):
                    for side in ("BUY","SELL"):
                        self.e._open(replace(self.d,action=side),self.s)
        self.assertEqual([o["side"] for o in observed],["BUY","SELL"])
        for obs in observed:
            self.assertEqual(obs["symbol"],"GBPUSD")
            self.assertEqual(obs["brain"],"Mobile FX PAPER Research")
            self.assertFalse(obs["probation"])
            self.assertEqual(obs["risk_pct"],0.06)
            self.assertEqual(obs["risk_cap_pct"],0.05)
            self.assertIn("NO IG",obs["reason"])
        self.assertEqual(self.e.cfg["risk_per_trade_pct"],0.4)
        self.assertEqual(self.e.cfg["equity_sizing_max_trade_risk_pct"],0.18)
        self.assertTrue(self.e.cfg["forex_probation_enabled"])
        self.assertEqual(self.runtime.db.lifetime_trade_summary(),before)
        self.assertEqual(self.e.positions,[])

    def test_restores_cfg_even_if_original_parent_raises(self):
        with patch("mobile.server._non_crypto_session_open",return_value=True):
            with patch.object(self.e,"_fx_paper_research_allowed",return_value=True):
                with patch.object(TradingEngine,"_open",autospec=True,
                            side_effect=RuntimeError("execution veto")):
                    with self.assertRaisesRegex(RuntimeError,"execution veto"):
                        self.e._open(self.d,self.s)
        self.assertEqual(self.e.cfg["risk_per_trade_pct"],0.4)
        self.assertEqual(self.e.cfg["equity_sizing_max_trade_risk_pct"],0.18)
        self.assertTrue(self.e.cfg["forex_probation_enabled"])

    def test_research_daily_cap_and_direction_repeat_and_open_position(self):
        midnight=datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00+00:00")
        with self.mocked_research():
            self.assertTrue(self.e._fx_paper_research_allowed(self.d,self.s))
            self.runtime.db.conn.execute(
                """INSERT INTO trades(id,symbol,side,lots,entry,exit,pnl,r_multiple,
                opened_at,closed_at,reason,brain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()),"GBPUSD","BUY",0.001,1.2,1.3,1.0,0.2,
                midnight,midnight,"TP","Mobile FX PAPER Research"))
            self.runtime.db.conn.commit()
            self.assertFalse(self.e._fx_paper_research_allowed(self.d,self.s))
            self.assertTrue(self.e._fx_paper_research_allowed(
                replace(self.d,action="SELL"),self.s))
            self.runtime.db.conn.execute(
                """INSERT INTO trades(id,symbol,side,lots,entry,exit,pnl,r_multiple,
                opened_at,closed_at,reason,brain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()),"GBPUSD","SELL",0.001,1.2,1.3,1.0,0.2,
                midnight,midnight,"TP","Mobile FX PAPER Research"))
            self.runtime.db.conn.commit()
            self.assertFalse(self.e._fx_paper_research_allowed(
                replace(self.d,action="SELL"),self.s))
            self.assertFalse(self.e._fx_paper_research_allowed(self.d,self.s))
        self.assertEqual(self.runtime.db.lifetime_trade_summary()["trades"],2)

    def test_post_signal_logs_group_numeric_gate_details(self):
        e=self.e
        e.last_gate_reasons={
            "FOREX probation · score 68/79 · effective confidence 55/75":2,
            "FOREX probation · score 74/79 · effective confidence 60/75":3,
            "RSI/price gate · RANGE requires RSI turn away":1}
        e._post_signal_audit()
        self.assertEqual(e.post_signal_counts["ORDER_GATE:FOREX probation"],5)
        self.assertEqual(e.post_signal_counts["ORDER_GATE:RSI/price gate"],1)
        self.assertEqual(len(e.post_signal_counts),2)
        diag=self.runtime._signal_diagnostics()
        self.assertTrue(diag["fx_buy_sell_research_paper"])
        self.assertEqual(diag["fx_research_max_risk_pct"],0.05)


if __name__=="__main__":
    unittest.main()
