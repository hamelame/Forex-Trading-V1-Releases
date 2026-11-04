import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from forex_app.instruments import underlying_key
from mobile.crypto_feed import MobileLiveMarketFeed
from mobile.ig_prices import CONTRACTS


class FeedTests(unittest.TestCase):
    def test_normal_scan_and_chart_for_the_pinned_contracts(self):
        end = int(time.time() // 60) * 60
        feed = MobileLiveMarketFeed(list(CONTRACTS))
        self.addCleanup(feed.close)
        for symbol, (epic, term, expiry, name) in CONTRACTS.items():
            level = 8802 if symbol == "WTIIGUSD" else 4075
            stamp = lambda t: datetime.fromtimestamp(t, timezone.utc)
            row = dict(epic=epic, expiry=expiry, instrumentName=name, instrumentType="COMMODITIES",
                       marketStatus="TRADEABLE", updateTimeUTC=stamp(time.time()).strftime("%H:%M:%S"),
                       bid=level, offer=level+1)
            detail = dict(instrument={"epic":epic},snapshot={"marketStatus":"TRADEABLE","delayTime":0})
            pair = {"bid":level,"ask":level+1}
            history = [{"snapshotTimeUTC":stamp(end-i*60).strftime("%Y-%m-%dT%H:%M:%S"),
                        **{k:pair for k in ("openPrice","highPrice","lowPrice","closePrice")}}
                       for i in range(40,0,-1)]
            with patch.object(feed.ig_prices, "get", side_effect=[detail, {"markets":[row]}, {"prices":history}]):
                feed._refresh_symbol(symbol, True)
            s = feed.snapshot(symbol)
            self.assertEqual((s.bid, s.ask), (level, level+1))
            self.assertIn(epic,s.feed_provider)
            self.assertEqual(len(feed.candle_window(symbol)),41)
            self.assertLess(datetime.fromisoformat(s.timestamp).timestamp(),end)
            detail["snapshot"]["delayTime"] = 10
            with patch.object(feed.ig_prices, "get", side_effect=[detail,{"markets":[row]}]):
                feed._refresh_symbol(symbol, True)
            with self.assertRaises(RuntimeError):
                feed.snapshot(symbol)
        self.assertEqual(underlying_key("WTIIGUSD"), underlying_key("WTIUSD"))
        self.assertEqual(underlying_key("XAUIGDEC26"), underlying_key("XAUUSD"))
