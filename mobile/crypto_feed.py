"""Mobile-only 24/7 PAPER crypto candle fallback, no broker orders.

Mobile's small Render instance uses the PC AI strategy unchanged. For BTC/ETH,
the public Kraken and Coinbase candle APIs provide alternative 1m LIVE data if
Binance/Yahoo are unreachable. This layer NEVER substitutes stale or synthetic
prices, and it is enabled only on the mobile service.
"""
from __future__ import annotations

import math
import time
import urllib.parse
from datetime import datetime, timezone

from forex_app.instruments import instrument_meta
from forex_app.live_market import LiveMarketFeed

_KRAKEN_PAIRS = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
_COINBASE_PAIRS = {"BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}


def _validated_ohlc(rows, provider, *, source_order):
    """Produce existing strategy's OHLC tuples in ascending UTC seconds."""
    parsed = []
    for raw in rows:
        try:
            if source_order == "kraken":
                # Kraken: [time, open, high, low, close, vwap, volume, count]
                if not isinstance(raw, (list, tuple)) or len(raw) < 5:
                    continue
                t, o, h, l, c = (
                    float(raw[0]), float(raw[1]), float(raw[2]),
                    float(raw[3]), float(raw[4])
                )
            elif source_order == "coinbase":
                # Coinbase Exchange: [time, low, high, open, close, volume]
                if not isinstance(raw, (list, tuple)) or len(raw) < 5:
                    continue
                t, l, h, o, c = (
                    float(raw[0]), float(raw[1]), float(raw[2]),
                    float(raw[3]), float(raw[4])
                )
            else:
                raise ValueError("Unsupported candle source")
            if not all(math.isfinite(v) for v in (t, o, h, l, c)):
                continue
            if not (t > 0 and l > 0 and l <= min(o, c) <= max(o, c) <= h):
                continue
            parsed.append((t, o, h, l, c))
        except (TypeError, ValueError, IndexError):
            continue
    parsed.sort(key=lambda x: x[0])
    # Discard duplicate minute buckets rather than reproducing old candles.
    dedup = {}
    for row in parsed:
        dedup[row[0]] = row
    ordered = [dedup[k] for k in sorted(dedup)]
    if not ordered:
        return None
    return (
        [row[4] for row in ordered],
        [row[1] for row in ordered],
        [row[2] for row in ordered],
        [row[3] for row in ordered],
        [row[0] for row in ordered],
        provider,
    )


class MobileLiveMarketFeed(LiveMarketFeed):
    """Retain original FX/metals/indices logic; improve mobile BTC/ETH only."""

    def _fetch_kraken(self, symbol):
        pair = _KRAKEN_PAIRS.get(symbol)
        if not pair:
            return None
        query = urllib.parse.urlencode({"pair": pair, "interval": 1})
        response = self._get_json("https://api.kraken.com/0/public/OHLC?" + query)
        if not isinstance(response, dict) or response.get("error"):
            raise RuntimeError("Kraken public OHLC request unavailable")
        data = response.get("result")
        if not isinstance(data, dict):
            raise RuntimeError("Kraken public OHLC format unavailable")
        candidates = [v for k, v in data.items() if k != "last" and isinstance(v, list)]
        if len(candidates) != 1:
            raise RuntimeError("Kraken public OHLC pair missing")
        return _validated_ohlc(candidates[0], "KRAKEN PUBLIC 1M", source_order="kraken")

    def _fetch_coinbase(self, symbol):
        pair = _COINBASE_PAIRS.get(symbol)
        if not pair:
            return None
        # Coinbase Exchange limits one request to 300 buckets. Keep a fixed
        # 200-minute window; no user credentials or account permissions.
        end = (int(time.time()) // 60 + 1) * 60
        start = end - 200 * 60
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
        query = urllib.parse.urlencode({
            "granularity": 60,
            "start": iso(start),
            "end": iso(end),
        })
        response = self._get_json(
            f"https://api.exchange.coinbase.com/products/{pair}/candles?" + query
        )
        if not isinstance(response, list):
            raise RuntimeError("Coinbase public candles unavailable")
        return _validated_ohlc(response, "COINBASE PUBLIC 1M", source_order="coinbase")

    def _refresh_symbol(self, symbol, force=False):
        if symbol not in _KRAKEN_PAIRS:
            return super()._refresh_symbol(symbol, force=force)
        now = time.time()
        if (not force and now - self._last_fetch.get(symbol, 0)
                < self.refresh_seconds and self.history[symbol]):
            return
        self._last_fetch[symbol] = now
        bootstrap = not bool(self.history[symbol])
        # The last known working source goes first to reduce avoidable HTTP
        # failures and to keep alternate public providers truly independent.
        methods = [
            ("kraken", self._fetch_kraken),
            ("binance", self._fetch_binance if bootstrap else self._fetch_binance_incremental),
            ("coinbase", self._fetch_coinbase),
            ("yahoo", self._fetch_yahoo if bootstrap else self._fetch_yahoo_incremental),
        ]
        current = self._provider.get(symbol, "").upper()
        preferred = next(
            (name for name, _ in methods if current.startswith(name.upper())), None
        )
        if preferred:
            methods.sort(key=lambda item: 0 if item[0] == preferred else 1)
        failures = []
        for name, fetch in methods:
            try:
                data = fetch(symbol)
                if data is None:
                    raise RuntimeError("No 1-minute candles")
                close, opens, highs, lows, times, provider = self._normalize_result(data)
                if len(times) < (30 if bootstrap else 1):
                    raise RuntimeError("Insufficient candles")
                newest = float(times[-1])
                if not math.isfinite(newest) or now - newest > self.stale_seconds or newest - now > 75:
                    raise RuntimeError("Public candles not fresh")
                self._publish_result(symbol, (close, opens, highs, lows, times, provider), bootstrap)
                return
            except Exception as exc:
                failures.append(f"{name}: {str(exc)[:80]}")
        self._last_error[symbol] = "All public crypto sources unavailable: " + "; ".join(failures)[:260]
