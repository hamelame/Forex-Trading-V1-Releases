"""Display-only PC/mobile data quality labels: never affect order permission."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forex_app.feed_display import display_feed_status, weekend_closed
from forex_app.instruments import instrument_meta
from mobile.server import MobileRuntime


def clock(day, hour, month=10):
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


class FeedDisplayTests(unittest.TestCase):
    def test_weekend_close_and_reopen_uses_new_york_tz(self):
        for sym in ("EURUSD", "USDNOK", "XAUUSD", "US500", "WTIUSD"):
            with self.subTest(symbol=sym):
                self.assertFalse(weekend_closed(sym, clock(9, 20)))  # Fri 16 NY
                self.assertTrue(weekend_closed(sym, clock(9, 21)))   # Fri 17 NY
                self.assertTrue(weekend_closed(sym, clock(10, 18)))  # Sat
                self.assertTrue(weekend_closed(sym, clock(11, 20)))  # Sun 16 NY
                self.assertFalse(weekend_closed(sym, clock(11, 21))) # Sun 17 NY
        # March DST starts: NY Sunday 17 = UTC 21, not UTC 22.
        self.assertTrue(weekend_closed("EURUSD", clock(8, 20, 3)))
        self.assertFalse(weekend_closed("EURUSD", clock(8, 21, 3)))

    def test_crypto_never_marked_weekend_closed(self):
        for symbol in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LINKUSD", "AVAXUSD"):
            self.assertEqual(instrument_meta(symbol)["asset_class"], "CRYPTO")
            self.assertFalse(weekend_closed(symbol, clock(10, 18)))
            self.assertEqual(display_feed_status(symbol, "LIVE", 40, when=clock(10,18))["status"], "LIVE")
            self.assertEqual(display_feed_status(symbol, "STALE", 450, when=clock(10,18))["status"], "STALE DATA")
            self.assertEqual(display_feed_status(symbol, "NO DATA", None, "provider down", clock(10,18))["status"], "FEED ERROR")

    def test_stale_friday_forex_is_closed_not_broken_provider(self):
        self.assertEqual(display_feed_status("EURUSD", "STALE", 80000, when=clock(10, 18))["status"], "MARKET CLOSED")
        self.assertEqual(display_feed_status("XAGUSD", "NO DATA", error="429", when=clock(10, 18))["status"], "MARKET CLOSED")
        self.assertEqual(display_feed_status("EURUSD", "STALE", 360, when=clock(12, 18))["status"], "STALE DATA")
        self.assertEqual(display_feed_status("EURUSD", "STALE", 360, "provider down", clock(12, 18))["status"], "FEED ERROR")

    def test_a_genuinely_live_quote_is_not_marked_stale(self):
        self.assertEqual(display_feed_status("ETHUSD", "LIVE", 10, error="transient", when=clock(10, 18))["status"], "LIVE")
        # Display labels do not erase the underlying provider error.
        self.assertEqual(display_feed_status("EURUSD", "STALE", 900, "timeout", clock(12,18))["note"],
                         "Price provider unavailable or invalid")

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            weekend_closed("EURUSD", datetime(2026, 10, 10, 14))


class MobileDisplayIsolationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config = json.loads((Path(__file__).resolve().parents[2] / "config.json").read_text())
        config["symbols"] = ["EURUSD", "BTCUSD", "ETHUSD"]
        config["market_data_mode"] = "LIVE"
        p = root / "settings.json"
        p.write_text(json.dumps(config), encoding="utf-8")
        feed = SimpleNamespace(
            feed_status=lambda s: {
                "EURUSD": {"status": "STALE", "age_seconds": 900, "error": "timeout"},
                "BTCUSD": {"status": "LIVE", "age_seconds": 20},
                "ETHUSD": {"status": "STALE", "age_seconds": 450},
            }[s]
        )
        self.runtime = MobileRuntime(config_path=p, db_path=root/"test.sqlite",
                                     feed=feed, start_worker=False)
        self.addCleanup(self.runtime.close)

    def test_all_symbols_present_and_scope_unchanged(self):
        with patch("forex_app.feed_display.datetime") as dt:
            dt.now.return_value = clock(10, 18)
            labels = self.runtime._feed_display()
        self.assertEqual(list(labels), ["EURUSD", "BTCUSD", "ETHUSD"])
        self.assertEqual(labels["EURUSD"]["status"], "MARKET CLOSED")
        self.assertEqual(labels["BTCUSD"]["status"], "LIVE")
        self.assertEqual(labels["ETHUSD"]["status"], "STALE DATA")
        state = self.runtime.state()
        self.assertIn("feed_display", state)
        self.assertEqual(state["symbols"], ["EURUSD", "BTCUSD", "ETHUSD"])
        self.assertFalse(state["running"])
        self.assertEqual(len(self.runtime.engine.positions), 0)


if __name__ == "__main__":
    unittest.main()
