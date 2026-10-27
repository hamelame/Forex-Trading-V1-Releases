"""CRYPTO ONLY rollback smoke & durable-regression tests.

Uses a pre-IG WSGI bridge and the reliable Oct 10 mobile crypto provider.
Absolutely no broker connections or network requests.
"""
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from forex_app.engine import TradingEngine
from forex_app.market import SyntheticFeed
from forex_app.models import Decision, Position
from mobile.server import MobileRuntime

CRYPTO = ["BTCUSD","ETHUSD","SOLUSD","XRPUSD","LINKUSD","ADAUSD","LTCUSD","AVAXUSD"]
FOREX = ["EURUSD","GBPUSD","XAUUSD"]


class CryptoPreIGRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.cfg_path = self.dir / "cfg.json"
        self.base={
            "symbols":FOREX+CRYPTO,
            "market_data_mode":"LIVE",
            "starting_balance":20000.0,
            "paper_trading_capital":20000.0,
            "min_signal_score":63,
            "min_confidence":56,
            "selection_mode":"MANUAL",
            "market_scan_top_n":20,
            "trading_profile":"AI TRADING",
            "risk_per_trade_pct":0.4,
            "max_total_risk_pct":1.6,
            "max_open_positions":5,
        }
        self.cfg_path.write_text(json.dumps(self.base))

    def tearDown(self):
        self.temp.cleanup()

    def make_runtime(self, durable=False, db_name="db.sqlite"):
        feed = SyntheticFeed(self.base["symbols"])
        path = self.dir / db_name
        if durable:
            environ=patch.dict(os.environ,{
                "FX_MOBILE_STORAGE_DIR":str(self.dir),
                "FX_MOBILE_SETTINGS":str(self.dir / "settings.json"),
            },clear=False)
        else:
            environ=patch.dict(os.environ,{
                "FX_MOBILE_STORAGE_DIR":"",
                "FX_MOBILE_SETTINGS":str(self.dir / "settings.json"),
            },clear=False)
        with environ:
            return MobileRuntime(config_path=self.cfg_path,
                      db_path=path,feed=feed,start_worker=False)

    def test_only_supported_crypto_are_in_universe_auto_top5(self):
        r=self.make_runtime()
        try:
            self.assertEqual(r.cfg["symbols"],CRYPTO)
            self.assertEqual(r.cfg["enabled_asset_classes"],["CRYPTO"])
            self.assertEqual(r.cfg["selection_mode"],"AUTO TOP 5")
            self.assertEqual(r.cfg["market_scan_top_n"],5)
            self.assertEqual(r.cfg["selected_chart_symbol"],"BTCUSD")
            self.assertEqual(r.cfg["broker"],"synthetic")
            self.assertEqual(r.cfg["mode"],"PAPER")
            self.assertEqual(r.cfg["manual_selected_symbols"],[])
            self.assertEqual(r.full_symbol_count,len(FOREX+CRYPTO))
            self.assertEqual(json.loads(self.cfg_path.read_text()),self.base)
            self.assertEqual(r.db.lifetime_trade_summary()["trades"],0)
        finally:
            r.close()

    def test_saved_manual_forex_watchlist_cannot_reenable_fx(self):
        saved=self.dir / "settings.json"
        saved.write_text(json.dumps({
            "selection_mode":"MANUAL",
            "manual_selected_symbols":["EURUSD","GBPUSD"],
            "selected_chart_symbol":"EURUSD",
        }))
        r=self.make_runtime()
        try:
            self.assertEqual(r.cfg["selection_mode"],"AUTO TOP 5")
            self.assertEqual(r.cfg["selected_chart_symbol"],"BTCUSD")
            self.assertEqual(r.cfg["manual_selected_symbols"],[])
            self.assertEqual(json.loads(saved.read_text())["manual_selected_symbols"],
                             ["EURUSD","GBPUSD"])
        finally:
            r.close()

    def test_rejects_fx_orders_even_when_scope_or_selector_says_ready(self):
        r=self.make_runtime()
        try:
            fx=r.feed.snapshot("EURUSD")
            d=Decision("EURUSD","BUY",90,91,"test",8,15,fx.timestamp)
            r.engine.entry_asset_scope="MULTI_MARKET"
            with patch.object(TradingEngine,"_open",autospec=True) as parent:
                r.engine._open(d,fx)
                parent.assert_not_called()
            self.assertIn("CRYPTO ONLY",r.engine.last_open_block_reason)
            self.assertEqual(r.engine.positions,[])
        finally:
            r.close()

    def test_24_7_ready_from_fresh_crypto_when_forex_is_missing(self):
        r=self.make_runtime()
        try:
            r.last_scan_completed_at=time.monotonic()
            now=datetime.now(timezone.utc).isoformat()
            s=r.feed.snapshot("BTCUSD")
            r.engine.snapshots["BTCUSD"]=replace(
                s,timestamp=now,feed_status="LIVE",data_age_seconds=1.0)
            v=r.trading_readiness()
            self.assertTrue(v["ready"])
            self.assertEqual(v["scope"],"CRYPTO_ONLY")
            self.assertEqual(v["fresh_crypto"],1)
            self.assertIn("CRYPTO 24/7",v["reason"])
            r.engine.entry_asset_scope="CRYPTO_ONLY"
            with patch.object(TradingEngine,"_open",autospec=True) as parent:
                d=Decision("BTCUSD","BUY",90,91,"test",8,15,now)
                r.engine._open(d,r.engine.snapshots["BTCUSD"])
                parent.assert_called_once()
            # Order execution is mocked; no forced PAPER trade.
            self.assertEqual(r.db.lifetime_trade_summary()["trades"],0)
        finally:
            r.close()

    def test_126_closed_trades_and_balance_survive_rollback_restart(self):
        r=self.make_runtime(durable=True)
        try:
            r.engine.balance=19986.86
            r.engine.equity=19986.86
            stamp="2026-10-14T12:00:00+00:00"
            rows=[(f"prior_{i}","BTCUSD","BUY",0.001,65000,65050,
                0.1,0.01,stamp,stamp,"TP","Champion")
                for i in range(126)]
            r.db.conn.executemany(
                "INSERT INTO trades(id,symbol,side,lots,entry,exit,pnl,r_multiple,"
                "opened_at,closed_at,reason,brain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows)
            r.db.conn.commit()
            r._persist_checkpoint()
            self.assertEqual(r.db.lifetime_trade_summary()["trades"],126)
        finally:
            r.close()

        restored=self.make_runtime(durable=True)
        try:
            self.assertIsNone(restored.persistence_error)
            self.assertAlmostEqual(restored.engine.balance,19986.86,2)
            self.assertEqual(restored.db.lifetime_trade_summary()["trades"],126)
            self.assertEqual(restored.cfg["symbols"],CRYPTO)
            self.assertEqual(restored.engine.positions,[])
        finally:
            restored.close()

    def test_forex_position_in_durable_checkpoint_fails_closed_not_erased(self):
        from forex_app.models import Position
        r=self.make_runtime(durable=True)
        try:
            p=Position("oldfx","EURUSD","BUY",0.01,1.12,1.11,
                       1.14,15.0,datetime.now(timezone.utc).isoformat())
            r.engine.positions=[p]
            r.db.save_positions([p])
            r._persist_checkpoint()
        finally:
            r.close()
        restored=self.make_runtime(durable=True)
        try:
            self.assertIsNotNone(restored.persistence_error)
            self.assertFalse(restored.engine.enabled)
            self.assertEqual([x["id"] for x in restored.db.load_positions()],["oldfx"])
            self.assertEqual(restored.db.lifetime_trade_summary()["trades"],0)
        finally:
            restored.close()

    def test_wsgi_has_no_ig_routes_or_background_broker_worker(self):
        with patch.dict(os.environ,{"FX_MOBILE_TOKEN":"rollback-test-token-1234567890"}):
            import importlib
            cloud=importlib.reload(importlib.import_module("mobile_cloud.app"))
            paths={rule.rule for rule in cloud.app.url_map.iter_rules()}
            self.assertFalse(any(p.startswith("/api/ig") for p in paths))
            self.assertNotIn("ig_auto_worker",vars(cloud))
            with cloud.app.test_client() as client:
                status=client.get("/health")
                self.assertEqual(status.status_code,200)
                data=status.get_json()
                self.assertTrue(data["paper_only"])
                self.assertTrue(data["crypto_only"])
                self.assertFalse(data["ig_integration"])
                self.assertEqual(client.post("/api/ig-demo/auto/start").status_code,404)
                self.assertEqual(client.post("/api/ig-demo/trial/open").status_code,404)


if __name__=="__main__":
    unittest.main()
