"""Fresh PAPER profile: separate learning ledger and strictly live multi-market entries.

All tests are synthetic and NEVER place an IG or exchange broker order.
"""
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from forex_app.context_edge import session_policy
from forex_app.engine import TradingEngine
from forex_app.live_market import _yahoo_symbol
from forex_app.market import SyntheticFeed
from forex_app.models import Decision
from mobile.server import MobileRuntime

COINS=["BTCUSD","ETHUSD","SOLUSD","XRPUSD","LINKUSD","ADAUSD","LTCUSD","AVAXUSD","BCHUSD","DOTUSD"]
FX=["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD"]
METALS=["XAUUSD","XAGUSD","XPTUSD","XPDUSD"]
ENERGY=["WTIUSD","BRENTUSD","HEATOILUSD","RBOBUSD"]
SYMBOLS=COINS+FX+METALS+ENERGY


class FreshMultiMarketPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.config=self.home/"config.json"
        self.settings=self.home/"settings.json"
        self.original={
            "symbols":SYMBOLS, "market_data_mode":"LIVE",
            "starting_balance":20000, "paper_trading_capital":20000,
            "min_signal_score":63,"min_confidence":56,
            "selection_mode":"AUTO TOP 5", "market_scan_top_n":5,
            "trading_profile":"AI TRADING",
            "risk_per_trade_pct":0.4,"max_total_risk_pct":1.6,
            "max_open_positions":5,
            "high_vol_require_positive_edge":True,
            "adaptive_rsi_shadow_only":True,
            "adaptive_rsi_execution_gate_enabled":True,
        }
        self.config.write_text(json.dumps(self.original))
        self.feed=SyntheticFeed(SYMBOLS)

    def tearDown(self):
        self.tmp.cleanup()

    def runtime(self, db="paper.db", durable=False):
        env={"FX_MOBILE_SETTINGS":str(self.settings),
             "FX_MOBILE_STORAGE_DIR":str(self.home) if durable else ""}
        with patch.dict(os.environ,env,clear=False):
            return MobileRuntime(config_path=self.config,
                db_path=self.home/db,feed=SyntheticFeed(SYMBOLS),
                start_worker=False)

    def test_profile_live_markets_and_cold_start_risk(self):
        r=self.runtime()
        try:
            self.assertEqual(r.cfg["symbols"],SYMBOLS)
            self.assertEqual(r.cfg["enabled_asset_classes"],
                ["CRYPTO","FOREX","METALS","ENERGY"])
            self.assertEqual(r.cfg["selection_mode"],"AUTO TOP 10")
            self.assertEqual(r.cfg["market_scan_top_n"],10)
            self.assertFalse(r.cfg["high_vol_require_positive_edge"])
            self.assertEqual(r.cfg["high_vol_min_confidence"],68)
            self.assertEqual(r.cfg["high_vol_min_signal_score"],74)
            self.assertEqual(r.cfg["market_data_mode"],"LIVE")
            self.assertEqual(r.cfg["broker"],"synthetic")
            self.assertEqual(r.cfg["mode"],"PAPER")
            self.assertEqual(r.cfg["risk_per_trade_pct"],0.10)
            self.assertEqual(r.cfg["equity_sizing_max_trade_risk_pct"],0.10)
            self.assertEqual(r.cfg["max_total_risk_pct"],0.50)
            self.assertEqual(r.cfg["max_open_positions"],3)
            self.assertEqual(json.loads(self.config.read_text()),self.original)
        finally:
            r.close()

    def test_all_market_session_policies_and_source_mappings(self):
        r=self.runtime()
        try:
            for sess in ("Asia","London","New York","London + New York",
                         "Rollover / Thin"):
                self.assertTrue(session_policy("BTCUSD",sess,r.cfg)["allowed"])
            self.assertTrue(session_policy("EURUSD","London",r.cfg)["allowed"])
            self.assertTrue(session_policy("EURUSD","New York",r.cfg)["allowed"])
            self.assertTrue(session_policy("XAUUSD","New York",r.cfg)["allowed"])
            self.assertTrue(session_policy("XAGUSD","London + New York",r.cfg)["allowed"])
            self.assertTrue(session_policy("WTIUSD","New York",r.cfg)["allowed"])
            self.assertFalse(session_policy("WTIUSD","Rollover / Thin",r.cfg)["allowed"])
            self.assertFalse(session_policy("EURUSD","Rollover / Thin",r.cfg)["allowed"])
            self.assertFalse(session_policy("XAUUSD","Asia",r.cfg)["allowed"])
            self.assertEqual(_yahoo_symbol("EURUSD"),"EURUSD=X")
            self.assertEqual(_yahoo_symbol("XAUUSD"),"GC=F")
            self.assertEqual(_yahoo_symbol("XAGUSD"),"SI=F")
            self.assertEqual(_yahoo_symbol("WTIUSD"),"CL=F")
            self.assertEqual(_yahoo_symbol("BRENTUSD"),"BZ=F")
            self.assertEqual([_yahoo_symbol(s) for s in METALS[2:] + ENERGY[2:]],
                             ["PL=F", "PA=F", "HO=F", "RB=F"])
        finally:
            r.close()

    def test_delayed_gold_does_not_unlock_multimarket(self):
        r=self.runtime()
        try:
            r.last_scan_completed_at=time.monotonic()
            now=datetime.now(timezone.utc).isoformat()
            r.engine.snapshots["BTCUSD"]=replace(
                r.feed.snapshot("BTCUSD"),timestamp=now,
                feed_status="LIVE",data_age_seconds=8)
            r.engine.snapshots["ETHUSD"]=replace(
                r.feed.snapshot("ETHUSD"),timestamp=now,
                feed_status="LIVE",data_age_seconds=8)
            old=(datetime.now(timezone.utc).timestamp()-840)
            from datetime import datetime as _dt
            stamp=_dt.fromtimestamp(old,timezone.utc).isoformat()
            r.engine.snapshots["XAUUSD"]=replace(
                r.feed.snapshot("XAUUSD"),timestamp=stamp,
                feed_status="LIVE",data_age_seconds=840)
            with patch("mobile.server._non_crypto_session_open",return_value=True):
                d=r.trading_readiness()
                self.assertEqual(d["scope"],"CRYPTO_ONLY")
                self.assertEqual(d["fresh_non_crypto"],0)
                r.engine.snapshots["EURUSD"]=replace(
                    r.feed.snapshot("EURUSD"),timestamp=now,
                    feed_status="LIVE",data_age_seconds=8)
                ready=r.trading_readiness()
                self.assertEqual(ready["scope"],"MULTI_MARKET")
                self.assertEqual(ready["fresh_non_crypto"],1)
        finally:
            r.close()

    def test_noncrypto_stale_market_and_weekend_never_reach_parent(self):
        r=self.runtime()
        try:
            r.engine.entry_asset_scope="MULTI_MARKET"
            now=datetime.now(timezone.utc).isoformat()
            gold=r.feed.snapshot("XAUUSD")
            live=replace(gold,timestamp=now,feed_status="LIVE",data_age_seconds=12)
            delayed=replace(live,data_age_seconds=900)
            d=Decision("XAUUSD","BUY",85,85,"paper",10,20,now)
            with patch("mobile.server._non_crypto_session_open",return_value=True), \
                 patch.object(TradingEngine,"_open",autospec=True) as parent:
                r.engine._open(d,delayed)
                parent.assert_not_called()
                r.engine._open(d,live)
                parent.assert_called_once()
            with patch("mobile.server._non_crypto_session_open",return_value=False), \
                 patch.object(TradingEngine,"_open",autospec=True) as parent:
                r.engine._open(d,live)
                parent.assert_not_called()
            r.engine.entry_asset_scope="CRYPTO_ONLY"
            with patch.object(TradingEngine,"_open",autospec=True) as parent:
                r.engine._open(d,live)
                parent.assert_not_called()
            self.assertEqual(r.engine.positions,[])
        finally:
            r.close()

    def test_new_ledger_starts_clean_and_leaves_old_closed_trade_history_unchanged(self):
        old=self.runtime(db="legacy.db",durable=True)
        try:
            date="2026-10-14T12:00:00+00:00"
            rows=[(f"old_{n}","BTCUSD","SELL",0.001,65000,65010,
                -0.1,-0.1,date,date,"SL","Champion")
                for n in range(126)]
            old.db.conn.executemany(
                "INSERT INTO trades(id,symbol,side,lots,entry,exit,pnl,r_multiple,"
                "opened_at,closed_at,reason,brain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",rows)
            old.db.conn.commit()
            old._persist_checkpoint()
            self.assertEqual(old.db.lifetime_trade_summary()["trades"],126)
        finally:
            old.close()
        fresh=self.runtime(db="fresh.db",durable=True)
        try:
            self.assertIsNone(fresh.persistence_error)
            self.assertEqual(fresh.db.lifetime_trade_summary()["trades"],0)
            self.assertEqual(fresh.engine.positions,[])
            self.assertEqual(fresh.engine.learning.execution_edge_score(
                "BTCUSD","HIGH VOL","SELL","New York")["label"],"UNPROVEN")
            self.assertFalse(fresh.cfg["high_vol_require_positive_edge"])
            self.assertTrue((self.home/"legacy.db").exists())
        finally:
            fresh.close()
        again=self.runtime(db="legacy.db",durable=True)
        try:
            self.assertEqual(again.db.lifetime_trade_summary()["trades"],126)
            self.assertEqual(again.engine.positions,[])
        finally:
            again.close()


if __name__=="__main__":
    unittest.main()
