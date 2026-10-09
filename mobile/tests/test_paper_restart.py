"""PAPER state survives an application restart on an attached durable disk."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forex_app.market import SyntheticFeed
from forex_app.models import Position
from mobile.server import MobileRuntime


class PaperRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.symbols = ["EURUSD", "GBPUSD", "USDJPY"]
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({
            "symbols": self.symbols, "paper_trading_capital": 20000.0,
            "starting_balance": 20000.0, "market_data_mode": "SYNTHETIC",
            "selection_mode": "AUTO TOP 10", "market_scan_top_n": 10,
            "risk_per_trade_pct": .4, "max_total_risk_pct": 1.6,
            "max_open_positions": 5, "max_category_positions": 2,
            "trading_profile": "AI TRADING", "min_signal_score": 63,
            "category_risk_forex": 1.0, "max_spread_pips": 2.5,
            "max_consecutive_losses": 3, "daily_loss_limit_pct": 2.0
        }), encoding="utf-8")
        self.dbpath = self.root / "paper.sqlite"
        self.env = patch.dict(os.environ, {
            "FX_MOBILE_STORAGE_DIR": str(self.root),
            "FX_MOBILE_DB": str(self.dbpath),
            "FX_MOBILE_SETTINGS": str(self.root / "settings.json")
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def runtime(self):
        return MobileRuntime(config_path=self.config, db_path=self.dbpath,
                             feed=SyntheticFeed(self.symbols), start_worker=False)

    def test_restore_equity_positions_risk_and_resume_intent(self):
        first = self.runtime()
        try:
            e = first.engine
            e.session_started_at = "2026-10-09T10:00:00+00:00"
            e.balance, e.equity = 20750., 20810.
            e.risk.high_water_balance = 20900.
            e.risk.consecutive_losses = 2
            e.scan_count, e.decision_cycle_count = 80, 4
            p = Position("abcd1234", "EURUSD", "BUY", .03, 1.12, 1.11, 1.14, 100.,
                         "2026-10-09T10:02:00+00:00", unrealized=60.)
            e.positions.append(p)
            e.entry_context[p.id] = {"entry_reason": "test entry"}
            first.db.save_positions(e.positions)
            e.enabled = True
            first._persist_checkpoint()
        finally:
            first.close()

        second = self.runtime()
        try:
            e = second.engine
            self.assertTrue(second.durable)
            self.assertEqual(e.balance, 20750.)
            self.assertEqual(e.equity, 20810.)
            self.assertEqual(e.session_started_at, "2026-10-09T10:00:00+00:00")
            self.assertEqual(len(e.positions), 1)
            self.assertEqual(e.positions[0].id, "abcd1234")
            self.assertEqual(e.risk.high_water_balance, 20900.)
            self.assertEqual(e.risk.consecutive_losses, 2)
            self.assertEqual(e.scan_count, 80)
            self.assertEqual(e.entry_context["abcd1234"]["entry_reason"], "test entry")
            self.assertTrue(second.auto_resume_pending)
            self.assertFalse(e.enabled, "No automatic execution before live quote preflight")
            self.assertEqual(len(second.db.load_positions()), 1)
            # A valid feed permits the same no-reset resume performed by the scanner.
            self.assertTrue(second.trading_readiness_after_scan())
            e.set_enabled(True, reset_on_start=False)
            second.auto_resume_pending = False
            second._persist_checkpoint()
        finally:
            second.close()

        third = self.runtime()
        try:
            self.assertTrue(third.auto_resume_pending)
            self.assertEqual(third.engine.balance, 20750.)
            self.assertEqual(len(third.engine.positions), 1)
        finally:
            third.close()

    def test_pause_persists_and_disables_resume(self):
        first = self.runtime()
        first.command("start", {})
        first.command("pause", {})
        first.close()
        second = self.runtime()
        try:
            self.assertFalse(second.auto_resume_pending)
            self.assertFalse(second.engine.enabled)
        finally:
            second.close()

    def test_invalid_checkpoint_blocks_new_entries_and_preserves_existing_rows(self):
        first = self.runtime()
        first.close()
        import sqlite3
        conn = sqlite3.connect(self.dbpath)
        conn.execute("UPDATE mobile_paper_checkpoint SET state_json='{}'")
        conn.commit()
        conn.close()
        second = self.runtime()
        try:
            self.assertIsNotNone(second.persistence_error)
            self.assertFalse(second.trading_readiness()["ready"])
            with self.assertRaisesRegex(ValueError, "Recovery requires review"):
                second.command("start", {})
        finally:
            # intentionally invalid checkpoint: closing must not silently overwrite it
            second.shutdown.set()
            second.db.conn.close()


if __name__ == "__main__":
    unittest.main()
