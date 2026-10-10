"""Mobile crypto 24/7 PAPER readiness and public candle source regressions.

Fixtures are local. No IG, Binance, Yahoo, Kraken or Coinbase network calls.
PC TradingEngine and desktop PC configuration remain unchanged.
"""
import json
import math
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forex_app.engine import TradingEngine
from forex_app.live_market import LiveMarketFeed
from mobile.crypto_feed import MobileLiveMarketFeed, _validated_ohlc
from mobile.server import MobileRuntime


def snapshot(symbol, *, seconds_ago=45, reported_age=8, live=True):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    return SimpleNamespace(
        symbol=symbol, feed_status="LIVE" if live else "STALE",
        data_age_seconds=reported_age, timestamp=stamp,
    )


class MobileWeekendCryptoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        c = json.loads((Path(__file__).resolve().parents[2] / "config.json").read_text())
        c["market_data_mode"] = "LIVE"
        c["symbols"] = ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "ETHUSD"]
        self.config = root / "cfg.json"
        self.config.write_text(json.dumps(c), encoding="utf-8")
        self.runtime = MobileRuntime(
            config_path=self.config, db_path=root / "crypto.sqlite",
            feed=SimpleNamespace(), start_worker=False
        )
        self.addCleanup(self.runtime.close)

    def completed_scan(self):
        self.runtime.last_scan_completed_at = time.monotonic()

    def test_fresh_btc_only_allows_crypto_not_stale_forex(self):
        self.completed_scan()
        self.runtime.engine.snapshots = {
            "EURUSD": snapshot("EURUSD", seconds_ago=2 * 86400, reported_age=3),
            "GBPUSD": snapshot("GBPUSD", seconds_ago=2 * 86400, reported_age=3),
            "BTCUSD": snapshot("BTCUSD"),
        }
        ready = self.runtime.trading_readiness()
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["scope"], "CRYPTO_ONLY")
        self.assertEqual(ready["fresh"], 1)
        self.assertEqual(ready["fresh_crypto"], 1)
        self.assertEqual(ready["required"], 1)
        self.assertEqual(ready["crypto_symbols"], ["BTCUSD"])
        response = self.runtime.command("start", {})
        self.assertTrue(response["running"])
        self.assertEqual(self.runtime.engine.entry_asset_scope, "CRYPTO_ONLY")
        with patch.object(TradingEngine, "_open", return_value="mocked PAPER entry") as paper_open:
            self.assertIsNone(self.runtime.engine._open(SimpleNamespace(), SimpleNamespace(symbol="EURUSD")))
            paper_open.assert_not_called()
            self.assertEqual(
                self.runtime.engine._open(SimpleNamespace(), SimpleNamespace(symbol="BTCUSD")),
                "mocked PAPER entry"
            )
            paper_open.assert_called_once()

    def test_fresh_btc_and_eth_allow_crypto_weekends(self):
        self.completed_scan()
        self.runtime.engine.snapshots = {
            "BTCUSD": snapshot("BTCUSD"), "ETHUSD": snapshot("ETHUSD")
        }
        result = self.runtime.trading_readiness()
        self.assertTrue(result["ready"])
        self.assertEqual(result["scope"], "CRYPTO_ONLY")
        self.assertEqual(result["fresh_crypto"], 2)

    def test_three_fresh_markets_use_original_multi_market_policy(self):
        self.completed_scan()
        self.runtime.engine.snapshots = {
            "EURUSD": snapshot("EURUSD"),
            "GBPUSD": snapshot("GBPUSD"),
            "BTCUSD": snapshot("BTCUSD"),
        }
        result = self.runtime.trading_readiness()
        self.assertTrue(result["ready"])
        self.assertEqual(result["scope"], "MULTI_MARKET")
        self.assertEqual(result["required"], 3)

    def test_stale_candle_timestamp_blocks_despite_cached_age(self):
        self.completed_scan()
        self.runtime.engine.snapshots = {
            "BTCUSD": snapshot("BTCUSD", seconds_ago=18000, reported_age=1),
            "ETHUSD": snapshot("ETHUSD", seconds_ago=18000, reported_age=1),
        }
        result = self.runtime.trading_readiness()
        self.assertFalse(result["ready"])
        self.assertEqual(result["fresh"], 0)
        with self.assertRaisesRegex(ValueError, "PAPER start blocked"):
            self.runtime.command("start", {})

    def test_paused_scanner_and_missing_storage_still_fail_closed(self):
        self.runtime.engine.snapshots = {"BTCUSD": snapshot("BTCUSD")}
        self.assertFalse(self.runtime.trading_readiness()["ready"])
        self.completed_scan()
        self.runtime.persistence_error = "Persistent PAPER disk error"
        result = self.runtime.trading_readiness()
        self.assertFalse(result["ready"])
        self.assertEqual(result["scope"], "BLOCKED")

    def test_mobile_core_gate_rejects_even_crypto_when_stale(self):
        engine = self.runtime.engine
        engine.entry_asset_scope = "BLOCKED"
        with patch.object(TradingEngine, "_open") as real_open:
            self.assertIsNone(engine._open(SimpleNamespace(), SimpleNamespace(symbol="BTCUSD")))
            real_open.assert_not_called()

    def test_staged_universe_keeps_btc_and_eth_but_not_all_pc_symbols(self):
        self.assertTrue(self.runtime.staged_paper_test)
        self.assertEqual(self.runtime.full_symbol_count, 5)  # isolated fixture
        self.assertIn("BTCUSD", self.runtime.cfg["symbols"])
        self.assertIn("ETHUSD", self.runtime.cfg["symbols"])


class PublicCryptoFallbackTests(unittest.TestCase):
    def make_feed(self):
        f = MobileLiveMarketFeed(["BTCUSD", "ETHUSD"])
        self.addCleanup(f._executor.shutdown, wait=False)
        return f

    @staticmethod
    def bars(count=80, start=None):
        end = int(time.time()) // 60 * 60
        if start is None:
            start = end - (count - 1) * 60
        return [[start + i * 60, "100.0", "105.0", "95.0", "101.0", "100", "8", 8]
                for i in range(count)]

    def test_kraken_public_ohlc_parses_prices_in_correct_order(self):
        f = self.make_feed()
        def get_json(url):
            self.assertIn("api.kraken.com/0/public/OHLC?", url)
            self.assertIn("pair=XBTUSD", url)
            return {"error": [], "result": {"XXBTZUSD": self.bars(70), "last": 123}}
        f._get_json = get_json
        result = f._fetch_kraken("BTCUSD")
        self.assertEqual(len(result[0]), 70)
        self.assertEqual(result[0][-1], 101.0)
        self.assertEqual(result[1][-1], 100.0)
        self.assertEqual(result[2][-1], 105.0)
        self.assertEqual(result[3][-1], 95.0)
        self.assertEqual(result[5], "KRAKEN PUBLIC 1M")

    def test_coinbase_exchange_public_ohlc_uses_200_minute_window(self):
        f = self.make_feed()
        def get_json(url):
            self.assertIn("api.exchange.coinbase.com/products/ETH-USD/candles?", url)
            self.assertIn("granularity=60", url)
            return [[a[0], a[3], a[2], a[1], a[4], 1]
                    for a in reversed(self.bars(75))]
        f._get_json = get_json
        result = f._fetch_coinbase("ETHUSD")
        self.assertEqual(len(result[0]), 75)
        self.assertEqual(result[3][-1], 95.0)
        self.assertLess(result[4][0], result[4][-1])
        self.assertEqual(result[5], "COINBASE PUBLIC 1M")

    def test_kraken_failure_uses_coinbase_when_binance_unavailable(self):
        f = self.make_feed()
        def fail(*a, **kw):
            raise RuntimeError("provider unavailable")
        f._fetch_kraken = fail
        f._fetch_binance = fail
        f._fetch_coinbase = lambda sym: _validated_ohlc(
            [[bar[0], bar[3], bar[2], bar[1], bar[4], 1] for bar in self.bars(80)],
            "COINBASE PUBLIC 1M", source_order="coinbase"
        )
        f._fetch_yahoo = fail
        f._refresh_symbol("BTCUSD", force=True)
        self.assertEqual(f._provider["BTCUSD"], "COINBASE PUBLIC 1M")
        snap = f.snapshot("BTCUSD")
        self.assertEqual(snap.feed_status, "LIVE")

    def test_stale_provider_quotes_are_rejected_even_if_public_api_succeeds(self):
        f = self.make_feed()
        stale = self.bars(80, start=int(time.time()) - 12_000)
        r = _validated_ohlc(stale, "KRAKEN PUBLIC 1M", source_order="kraken")
        f._fetch_kraken = lambda sym: r
        f._fetch_binance = lambda sym: r
        f._fetch_coinbase = lambda sym: r
        f._fetch_yahoo = lambda sym: r
        f._refresh_symbol("BTCUSD", force=True)
        self.assertNotIn("BTCUSD", f._provider)
        self.assertIn("All public crypto sources unavailable", f._last_error["BTCUSD"])
        with self.assertRaisesRegex(RuntimeError, "All public crypto sources unavailable"):
            f.snapshot("BTCUSD")

    def test_malformed_ohlc_rejected(self):
        self.assertIsNone(_validated_ohlc([
            [time.time(), -1, 200, 100, 100, 1],
            [time.time(), 2, 3, 4, math.nan, 1],
        ], "KRAKEN PUBLIC 1M", source_order="kraken"))

    def test_noncrypto_source_untouched(self):
        f = self.make_feed()
        f.symbols.append("EURUSD")
        f.history["EURUSD"] = []
        with patch.object(LiveMarketFeed, "_refresh_symbol") as parent:
            f._refresh_symbol("EURUSD", force=True)
            parent.assert_called_once_with("EURUSD", force=True)


if __name__ == "__main__":
    unittest.main()
