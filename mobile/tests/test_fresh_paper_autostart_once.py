"""The user-authorized fresh PAPER engine auto-starts ONCE, never after Pause.

Prior ledgers and all real broker connections remain untouched.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forex_app.market import SyntheticFeed
from mobile.server import MobileRuntime

SYMBOLS=["BTCUSD","ETHUSD","EURUSD","XAUUSD","XAGUSD","WTIUSD"]


class FreshPaperAutoStartOnceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.cfg=self.root/"cfg.json"
        self.cfg.write_text(json.dumps({
            "symbols":SYMBOLS,
            "market_data_mode":"LIVE",
            "starting_balance":20000,
            "paper_trading_capital":20000,
            "min_signal_score":63,
            "min_confidence":56,
            "selection_mode":"AUTO TOP 10",
            "trading_profile":"AI TRADING",
            "risk_per_trade_pct":0.4,
        }))
        self.db="fx_mobile_fresh_multimarket_20261103.sqlite"
        self.marker=self.root/".fx_mobile_fresh_multimarket_first_start_20261103"

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self,db=None):
        with patch.dict(os.environ,{
            "FX_MOBILE_STORAGE_DIR":str(self.root),
            "FX_MOBILE_SETTINGS":str(self.root/"settings.json"),
        },clear=False):
            return MobileRuntime(
                config_path=self.cfg,
                db_path=self.root/(db or self.db),
                feed=SyntheticFeed(SYMBOLS),
                start_worker=False)

    def test_first_boot_queues_auto_start_without_opening_unverified_trade(self):
        r=self.runtime()
        try:
            self.assertTrue(self.marker.exists())
            self.assertTrue(r.auto_resume_pending)
            self.assertFalse(r.engine.enabled)
            self.assertEqual(r.engine.positions,[])
            self.assertEqual(r.db.lifetime_trade_summary()["trades"],0)
            self.assertEqual(r.checkpoint.load()["enabled"],True)
            self.assertIsNone(r.persistence_error)
        finally:
            r.close()

    def test_explicit_pause_survives_all_later_restarts(self):
        r=self.runtime()
        try:
            self.assertTrue(r.auto_resume_pending)
            status=r.command("pause",{})
            self.assertIn("paused",status["message"])
            self.assertFalse(r.auto_resume_pending)
            self.assertFalse(r.engine.enabled)
            self.assertTrue(self.marker.exists())
        finally:
            r.close()
        again=self.runtime()
        try:
            self.assertFalse(again.auto_resume_pending)
            self.assertFalse(again.engine.enabled)
            self.assertTrue(self.marker.exists())
        finally:
            again.close()

    def test_existing_nonfresh_db_never_autostarts(self):
        legacy=self.runtime(db="fx_mobile.sqlite")
        try:
            self.assertFalse(legacy.auto_resume_pending)
            self.assertFalse(legacy.engine.enabled)
            self.assertFalse(self.marker.exists())
        finally:
            legacy.close()


if __name__=="__main__":
    unittest.main()
