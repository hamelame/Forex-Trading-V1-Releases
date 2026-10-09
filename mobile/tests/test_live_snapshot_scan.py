"""Reproduce paused LIVE snapshot and AI analysis without external network calls."""
import json
import math
import tempfile
import time
import threading
import unittest
from pathlib import Path

from mobile.server import MobileRuntime
from forex_app.live_market import LiveMarketFeed

MARKETS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD"
]


class RealCandleScanTests(unittest.TestCase):
    def test_real_candle_indicators_do_not_block_paused_scanner(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            cfg = json.loads((Path(__file__).resolve().parents[2] / "config.json").read_text())
            cfg["symbols"] = MARKETS
            cfg["market_data_mode"] = "LIVE"
            file = path / "cfg.json"
            file.write_text(json.dumps(cfg))
            feed = LiveMarketFeed(MARKETS, cfg)
            feed._executor.shutdown(wait=False)
            feed.advance = lambda: None  # Real candles but no public HTTP requests.
            now = (int(time.time()) // 60) * 60
            for index, sym in enumerate(MARKETS):
                step = 0.00025 if "JPY" not in sym else 0.015
                base = (1.1 + index * 0.01) if sym not in ["BTCUSD", "ETHUSD", "XAUUSD", "XAGUSD"] else (2000.0 + index * 10)
                bars = [base + j * step * 0.018 + step * math.sin(j / 8.0) * 2.5 for j in range(320)]
                times = [float(now - 60 * (319 - j)) for j in range(320)]
                opens = bars[:]
                highs = [v + step for v in bars]
                lows = [v - step for v in bars]
                feed._publish_result(sym, (bars, opens, highs, lows, times, "OFFLINE FIXTURE"), True)
            runtime = MobileRuntime(config_path=file, db_path=path / "data.sqlite", feed=feed, start_worker=False)
            failures = []
            completed = threading.Event()

            def scan():
                try:
                    runtime.engine.scan()
                except BaseException as e:
                    failures.append(repr(e))
                finally:
                    completed.set()

            thread = threading.Thread(target=scan, daemon=True)
            thread.start()
            try:
                self.assertTrue(completed.wait(15), "Market indicators or strategy code stalled for >15s on 14 live-formatted symbols")
                self.assertFalse(failures, repr(failures))
                self.assertGreaterEqual(len(runtime.engine.snapshots), 3)
                print("PAUSED LIVE-FORMAT SCAN FINISHED", len(runtime.engine.snapshots), "snapshots")
            finally:
                # Never close the sqlite DB while the isolated test scanner is still running.
                if completed.is_set():
                    runtime.close()


if __name__ == "__main__":
    unittest.main()
