import os
import time
import unittest
from unittest.mock import patch

from mobile.crypto_feed import MobileLiveMarketFeed
from mobile.futures_feed import FuturesMinuteFeed


class FuturesFeedTests(unittest.TestCase):
    def setUp(self):
        self.end = int(time.time() // 60) * 60
        self.rows = [{"ticker": "CLZ6", "window_start": (self.end - 60 * i) * 10**9,
                      "open": 70, "close": 70.1, "high": 70.2, "low": 69.9}
                     for i in range(31)]

    def test_publish_closed_minutes_under_verified_symbol(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test",
                        "FX_MOBILE_FUTURES_CONTRACTS": '{"WTIUSD":"CLZ6"}'}):
            feed = MobileLiveMarketFeed(["WTIUSD"])
        try:
            with patch("mobile.futures_feed.build_opener") as opener:
                import json
                from io import BytesIO
                opener.return_value.open.return_value = BytesIO(json.dumps({"results": self.rows}).encode())
                feed._refresh_symbol("WTIUSD", force=True)
                request = opener.return_value.open.call_args.args[0]
                self.assertIn("api.massive.com/futures/v1/aggs/CLZ6?", request.full_url)
                self.assertNotIn("test", request.full_url)
            snap = feed.snapshot("WTIUSD")
            self.assertEqual(snap.feed_provider, "MASSIVE FUTURES 1M CLZ6")
            self.assertEqual(feed.candle_times["WTIUSD"][-1], self.end - 60)
            self.assertEqual(len(feed.history["WTIUSD"]), 30)
        finally:
            feed.close()

    def test_delayed_wrong_contract_and_invalid_bar_are_rejected(self):
        for rows in (self.rows[5:], [dict(self.rows[1], ticker="GCZ6")],
                     [dict(self.rows[1], low=90)]):
            with self.subTest(row=rows[0]), self.assertRaises(ValueError):
                FuturesMinuteFeed.parse({"results": rows}, "CLZ6", False, self.end + 3, 180)

    def test_prevent_substitution_of_other_products(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test",
                        "FX_MOBILE_FUTURES_CONTRACTS": '{"BRENTUSD":"CLZ6"}'}):
            with self.assertRaises(ValueError):
                FuturesMinuteFeed()
