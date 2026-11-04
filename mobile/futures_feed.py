"""Read-only minute bars for explicitly selected exchange futures contracts."""
import json
import math
import os
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOTS = {"XAUUSD": "GC", "XAGUSD": "SI", "XPTUSD": "PL", "XPDUSD": "PA",
         "WTIUSD": "CL", "BRENTUSD": "BZ", "HEATOILUSD": "HO", "RBOBUSD": "RB"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class FuturesMinuteFeed:
    def __init__(self):
        self.key = os.getenv("MASSIVE_API_KEY", "").strip()
        self.contracts = json.loads(os.getenv("FX_MOBILE_FUTURES_CONTRACTS", "{}"))
        if not isinstance(self.contracts, dict):
            raise ValueError("Expected a JSON object mapping mobile symbols to futures contracts")
        for symbol, ticker in self.contracts.items():
            root = ROOTS.get(symbol)
            if not root or not isinstance(ticker, str) or not re.fullmatch(root + r"[FGHJKMNQUVXZ]\d", ticker):
                raise ValueError(f"Invalid outright futures contract for {symbol}")
        if self.contracts and not self.key:
            raise ValueError("Configured futures contracts require MASSIVE_API_KEY")

    def fetch(self, symbol, bootstrap, max_age, timeout):
        ticker = self.contracts[symbol]
        now = time.time()
        minutes = 1440 if bootstrap else 25
        params = urlencode({"resolution": "1min", "window_start.gte": int(now - minutes * 60) * 10**9,
                            "window_start.lt": int(now // 60) * 60 * 10**9,
                            "sort": "window_start.desc", "limit": 1440 if bootstrap else 25})
        request = Request(f"https://api.massive.com/futures/v1/aggs/{ticker}?{params}",
                          headers={"Authorization": "Bearer " + self.key, "Accept": "application/json"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise ValueError(f"Futures data HTTP {exc.code} for {ticker}") from None
        return self.parse(payload, ticker, bootstrap, time.time(), max_age)

    @staticmethod
    def parse(payload, ticker, bootstrap, now, max_age):
        bars = {}
        for row in payload.get("results") or []:
            if row.get("ticker") != ticker:
                raise ValueError("Unexpected futures contract in response")
            stamp = row.get("window_start")
            if type(stamp) is not int or stamp <= 0 or stamp % (60 * 10**9):
                raise ValueError("Invalid futures minute timestamp")
            ts = stamp // 10**9
            if ts + 60 > now:
                continue
            o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(x) for x in (o,h,l,c)) or not 0 < l <= min(o,c) <= max(o,c) <= h:
                raise ValueError("Invalid futures OHLC")
            if ts in bars and bars[ts] != (c,o,h,l):
                raise ValueError("Conflicting bars for a futures minute")
            bars[ts] = (c,o,h,l)
        times = sorted(bars)
        if len(times) < (30 if bootstrap else 1):
            raise ValueError(f"Insufficient closed minute bars for {ticker}")
        if now - times[-1] > max_age:
            raise ValueError(f"Delayed futures data for {ticker} ({int(now - times[-1])}s)")
        closes, opens, highs, lows = zip(*(bars[t] for t in times))
        return closes, opens, highs, lows, times, f"MASSIVE FUTURES 1M {ticker}"
