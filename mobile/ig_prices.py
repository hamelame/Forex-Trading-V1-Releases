"""Market data only, restricted to two pinned IG demo contracts."""
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Stable PAPER identities; never mix positions or candles with Yahoo contracts.
CONTRACTS = {
    "XAUIGDEC26": ("MT.D.GC.FWS3.IP", "Gold", "DEC-26", "Gold ($100)"),
    "WTIIGUSD": ("CC.D.CL.UEE.IP", "US Crude", "-", "Oil - US Crude ($1)"),
}
REPLACEMENTS = {"XAUUSD": "XAUIGDEC26", "WTIUSD": "WTIIGUSD"}
ENV = ("IG_DEMO_API_KEY", "IG_DEMO_USERNAME", "IG_DEMO_PASSWORD")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def midpoint(pair):
    bid, ask = pair["bid"], pair["ask"]
    if not all(type(x) in (float, int) and math.isfinite(x) for x in (bid, ask)) or not 0 < bid <= ask:
        raise ValueError("Invalid IG bid/ask")
    return (bid + ask) / 2


def quote_time(clock, now):
    parsed = datetime.strptime(clock, "%H:%M:%S").time()
    today = datetime.fromtimestamp(now, timezone.utc).date()
    ts = datetime.combine(today, parsed, timezone.utc).timestamp()
    if ts > now + 43200:
        ts -= 86400
    if not 0 <= now - ts <= 180:
        raise ValueError("IG quote outside freshness window")
    return ts


def historical_bars(prices, now):
    bars = {}
    for p in prices:
        stamp = datetime.fromisoformat(p["snapshotTimeUTC"].replace("/", "-").replace("Z", "+00:00"))
        ts = (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()
        if ts % 60 or ts + 60 > now:
            continue
        o, h, l, c = (midpoint(p[k]) for k in ("openPrice", "highPrice", "lowPrice", "closePrice"))
        if l <= min(o, c) <= max(o, c) <= h:
            bars[ts] = (c, o, h, l)
    return bars


class DemoPrices:
    def __init__(self):
        self.headers = None
        self.login_lock = threading.Lock()
        self.retry_login = 0
        self.seed_attempt = {}
        self.quotes = {}
        self.logged = {}

    def _request(self, path, version, headers, body=None):
        req = Request("https://demo-api.ig.com/gateway/deal" + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={**headers, "Accept":"application/json", "Content-Type":"application/json", "Version":str(version)})
        with build_opener(NoRedirect()).open(req, timeout=5) as res:
            return json.loads(res.read(1000000)), res.headers

    def session(self):
        with self.login_lock:
            if self.headers is not None:
                return self.headers
            if time.monotonic() < self.retry_login:
                raise ValueError("IG login cooldown")
            self.retry_login = time.monotonic() + 300
            key, user, password = (os.environ[k].strip() for k in ENV)
            headers = {"X-IG-API-KEY":key}
            session, tokens = self._request("/session", 2, headers, {"identifier":user, "password":password})
            if session.get("reroutingEnvironment") not in (None, "DEMO") or not all(tokens.get(k) for k in ("CST","X-SECURITY-TOKEN")):
                raise ValueError("Invalid IG demo session")
            self.headers = {**headers, "CST":tokens["CST"], "X-SECURITY-TOKEN":tokens["X-SECURITY-TOKEN"]}
            return self.headers

    def get(self, path, version):
        headers = self.session()
        try:
            return self._request(path, version, headers)[0]
        except HTTPError as exc:
            if exc.code == 401:
                with self.login_lock:
                    if self.headers is headers:
                        self.headers = None
            raise ValueError(f"IG market data HTTP {exc.code}") from None

    def refresh(self, feed, symbol):
        epic, term, expiry, name = CONTRACTS[symbol]
        try:
            detail = self.get("/markets/" + epic, 3)
            snap = detail["snapshot"]
            rows = self.get("/markets?searchTerm=" + quote(term), 1)["markets"]
            row = next((r for r in rows if r.get("epic") == epic), None)
            if (not row or detail["instrument"].get("epic") != epic or row.get("expiry") != expiry
                or row.get("instrumentName") != name or row.get("instrumentType") != "COMMODITIES"
                or row.get("marketStatus") != "TRADEABLE" or snap.get("marketStatus") != "TRADEABLE"
                or type(snap.get("delayTime")) not in (int, float) or snap["delayTime"] != 0):
                raise ValueError("IG contract, session or delay check failed")
            now = time.time()
            ts = quote_time(row["updateTimeUTC"], now)
            mid = midpoint({"bid": row["bid"], "ask": row["offer"]})
            bars = {}
            if len(feed.history[symbol]) < 30 and time.monotonic() >= self.seed_attempt.get(symbol, 0):
                self.seed_attempt[symbol] = time.monotonic() + 1800
                try:
                    prices = self.get(f"/prices/{epic}?resolution=MINUTE&max=90", 3)
                    bars = historical_bars(prices["prices"], now)
                except (KeyError, TypeError, ValueError, OSError) as exc:
                    logging.warning("IG price history unavailable for %s (%s); collecting quote bars", symbol, type(exc).__name__)
            bucket = ts // 60 * 60
            with feed._data_locks[symbol]:
                times = feed.candle_times[symbol]
                if times and bucket < times[-1]:
                    raise ValueError("IG quote precedes latest chart bar")
                if times and bucket == times[-1]:
                    observed = (mid, feed.open_history[symbol][-1], max(mid,feed.high_history[symbol][-1]), min(mid,feed.low_history[symbol][-1]))
                else:
                    observed = (mid, mid, mid, mid)
            bars[bucket] = observed
            times = sorted(bars)
            c,o,h,l = zip(*(bars[t] for t in times))
            feed._publish_result(symbol,(c,o,h,l,times,"IG DEMO · " + epic),not feed.history[symbol])
            self.quotes[symbol] = (ts,row["bid"],row["offer"])
            if time.monotonic() >= self.logged.get(symbol,0):
                logging.warning("IG_PAPER_FEED %s epic=%s bars=%d latest_minute=%d quote_age=%.1fs",
                                symbol, epic, len(feed.history[symbol]), bucket, now-ts)
                self.logged[symbol] = time.monotonic()+60
        except Exception as exc:
            self.quotes.pop(symbol,None)
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            feed._last_error[symbol] = reason[:120]
            if time.monotonic() >= self.logged.get(symbol,0):
                logging.warning("IG_PAPER_FEED_WAIT %s %s",symbol,reason[:120])
                self.logged[symbol] = time.monotonic()+60
