"""Fresh multi-market PAPER smoke and durable-regression tests.

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
FOREX = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD"]
METALS_ENERGY = ["XAUUSD","XAGUSD","WTIUSD","BRENTUSD"]
ASSETS = CRYPTO + FOREX + METALS_ENERGY


class CryptoPreIGRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.cfg_path = self.dir / "cfg.json"
        self.base={
            "symbols":ASSETS,
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

    def test_selected_crypto_forex_metals_energy_and_bounded_paper_risk(self):
        r=self.make_runtime()
        try:
            self.assertEqual(r.cfg["symbols"],ASSETS)
            self.assertEqual(r.cfg["enabled_asset_classes"],["CRYPTO","FOREX","METALS","ENERGY"])
            self.assertEqual(r.cfg["selection_mode"],"AUTO TOP 10")
            self.assertEqual(r.cfg["market_scan_top_n"],10)
            self.assertFalse(r.cfg["high_vol_require_positive_edge"])
            self.assertEqual(r.cfg["high_vol_min_signal_score"],74)
            self.assertEqual(r.cfg["high_vol_min_confidence"],68)
            self.assertAlmostEqual(r.cfg["risk_per_trade_pct"],0.10)
            self.assertAlmostEqual(r.cfg["max_total_risk_pct"],0.50)
            self.assertEqual(r.cfg["max_open_positions"],3)
            self.assertEqual(r.cfg["selected_chart_symbol"],"BTCUSD")
            self.assertEqual(r.cfg["broker"],"synthetic")
            self.assertEqual(r.cfg["mode"],"PAPER")
            self.assertEqual(r.cfg["manual_selected_symbols"],[])
            self.assertEqual(r.full_symbol_count,len(ASSETS))
            self.assertEqual(json.loads(self.cfg_path.read_text()),self.base)
            self.assertEqual(r.db.lifetime_trade_summary()["trades"],0)
        finally:
            r.close()

    def test_saved_manual_selection_cannot_override_auto_top10(self):
        saved=self.dir / "settings.json"
        saved.write_text(json.dumps({
            "selection_mode":"MANUAL",
            "manual_selected_symbols":["EURUSD","GBPUSD"],
            "selected_chart_symbol":"EURUSD",
        }))
        r=self.make_runtime()
        try:
            self.assertEqual(r.cfg["selection_mode"],"AUTO TOP 10")
            self.assertEqual(r.cfg["selected_chart_symbol"],"EURUSD")
            self.assertEqual(r.cfg["manual_selected_symbols"],[])
            self.assertEqual(json.loads(saved.read_text())["manual_selected_symbols"],
                             ["EURUSD","GBPUSD"])
        finally:
            r.close()

    def test_noncrypto_freshness_and_market_weekend_block_before_parent_order(self):
        r=self.make_runtime()
        try:
            fx=r.feed.snapshot("EURUSD")
            now=datetime.now(timezone.utc).isoformat()
            fresh=replace(fx,timestamp=now,feed_status="LIVE",data_age_seconds=10.0)
            old=replace(fresh,data_age_seconds=900.0)
            d=Decision("EURUSD","BUY",90,91,"test",8,15,now)
            r.engine.entry_asset_scope="MULTI_MARKET"
            with patch("mobile.server._non_crypto_session_open",return_value=True), \
                 patch.object(TradingEngine,"_open",autospec=True) as parent:
                r.engine._open(d,old)
                parent.assert_not_called()
                self.assertIn("STALE",r.engine.last_open_block_reason)
                r.engine._open(d,fresh)
                parent.assert_called_once()
            r.engine.entry_asset_scope="CRYPTO_ONLY"
            with patch.object(TradingEngine,"_open",autospec=True) as parent:
                r.engine._open(d,fresh)
                parent.assert_not_called()
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
            self.assertEqual(restored.cfg["symbols"],ASSETS)
            self.assertEqual(restored.engine.positions,[])
        finally:
            restored.close()

    def test_unsupported_prior_position_in_checkpoint_fails_closed_not_erased(self):
        from forex_app.models import Position
        r=self.make_runtime(durable=True)
        try:
            p=Position("oldfx","US500","BUY",0.01,1.12,1.11,
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

    def test_crypto_trades_may_be_considered_in_every_session_including_rollover(self):
        from forex_app.context_edge import session_policy
        r=self.make_runtime()
        try:
            for session in ("Asia","London","London + New York",
                            "New York","Rollover / Thin"):
                with self.subTest(session=session):
                    policy=session_policy("BTCUSD",session,r.cfg)
                    self.assertTrue(policy["allowed"])
                    self.assertEqual(policy["code"],"CRYPTO_24_7")
            self.assertTrue(r.cfg["allow_rollover_session"])
            self.assertTrue(r.cfg["context_crypto_24_7"])
            self.assertEqual(r.cfg["symbols"],ASSETS)
            self.assertNotIn("allow_rollover_session",self.base,
                             "Shared PC config must never be rewritten")
        finally:
            r.close()

    def test_settings_save_preserves_multimarket_policy_and_crypto_rollover(self):
        r=self.make_runtime()
        try:
            result=r.command("settings",{"values":{
                "allow_rollover_session":False,
                "selection_mode":"MANUAL",
                "market_scan_top_n":15}})
            self.assertIn("saved",result["message"].lower())
            self.assertEqual(r.cfg["selection_mode"],"AUTO TOP 10")
            self.assertEqual(r.cfg["market_scan_top_n"],10)
            self.assertTrue(r.cfg["allow_rollover_session"])
            self.assertEqual(r.cfg["enabled_asset_classes"],["CRYPTO","FOREX","METALS","ENERGY"])
            from forex_app.context_edge import session_policy
            self.assertTrue(session_policy("ETHUSD","Rollover / Thin",r.cfg)["allowed"])
            self.assertEqual(json.loads(self.cfg_path.read_text()),self.base)
        finally:
            r.close()

    def test_wsgi_has_no_ig_routes_or_background_broker_worker(self):
        with patch.dict(os.environ,{"FX_MOBILE_TOKEN":"rollback-test-token-1234567890"}):
            import importlib
            cloud=importlib.reload(importlib.import_module("mobile_cloud.app"))
            paths={rule.rule for rule in cloud.app.url_map.iter_rules()}
            self.assertFalse(any(p.startswith("/api/ig") for p in paths))
            self.assertNotIn("ig_auto_worker",vars(cloud))
            # /health now starts the AFTER-FORK PAPER runtime in production.
            # Use a fake instance here; this unit test must never start a LIVE
            # provider thread or modify the global on-disk PAPER database.
            with patch.object(cloud,"get_runtime",return_value=object()):
              with cloud.app.test_client() as client:
                status=client.get("/health")
                self.assertEqual(status.status_code,200)
                data=status.get_json()
                self.assertTrue(data["paper_only"])
                self.assertFalse(data["crypto_only"])
                self.assertTrue(data["multi_market_paper"])
                self.assertFalse(data["ig_integration"])
                self.assertIn(client.post("/api/ig-demo/auto/start").status_code,(404,405))
                self.assertIn(client.post("/api/ig-demo/trial/open").status_code,(404,405))


if __name__=="__main__":
    unittest.main()
