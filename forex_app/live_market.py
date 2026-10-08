"""v2.7.1 live public market-data layer.

PAPER execution only. This module never sends orders.
Primary public feed: Yahoo Finance chart endpoint for FX, metals, energy and indices.
Crypto uses Binance public klines where possible, then Yahoo as fallback.
No synthetic price fallback is used in LIVE mode: stale/missing data stays unavailable.
"""
from __future__ import annotations
import json, math, time, urllib.parse, urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from threading import Lock
from dataclasses import replace
from .instruments import instrument_spec, instrument_meta
from .market import session_name
from .models import MarketSnapshot

YAHOO_MAP={
    "XAUUSD":"GC=F","XAGUSD":"SI=F","XPTUSD":"PL=F","XPDUSD":"PA=F",
    "WTIUSD":"CL=F","BRENTUSD":"BZ=F","NATGASUSD":"NG=F","GASOILUSD":"HO=F","HEATOILUSD":"HO=F","RBOBUSD":"RB=F",
    "US500":"^GSPC","US100":"^NDX","US30":"^DJI","US2000":"^RUT","UK100":"^FTSE","DE40":"^GDAXI","FR40":"^FCHI",
    "EU50":"^STOXX50E","ES35":"^IBEX","IT40":"FTSEMIB.MI","CH20":"^SSMI","NL25":"^AEX","JP225":"^N225","HK50":"^HSI",
    "AUS200":"^AXJO","IN50":"^NSEI","KR200":"^KS200","CA60":"^GSPTSE",
}

def _yahoo_symbol(symbol):
    s=symbol.upper(); meta=instrument_meta(s)
    if meta["asset_class"]=="FOREX": return f"{s}=X"
    if meta["asset_class"]=="CRYPTO" and s.endswith("USD"): return f"{s[:-3]}-USD"
    return YAHOO_MAP.get(s)

def _binance_symbol(symbol):
    s=symbol.upper(); meta=instrument_meta(s)
    if meta["asset_class"]=="CRYPTO" and s.endswith("USD"):
        return f"{s[:-3]}USDT"
    return None

class LiveMarketFeed:
    def __init__(self,symbols,cfg=None):
        self.requires_fresh_live_data=True
        self.symbols=list(symbols); self.cfg=cfg or {}
        # Keep enough point-in-time history to describe current-session structure
        # and, when the provider has it, the prior New-York trading day.
        self.history={s:deque(maxlen=3200) for s in self.symbols}
        # v2.9.2.3 replay support: preserve provider OHLC without adding any
        # new network requests. The replay UI reads these local buffers only.
        self.open_history={s:deque(maxlen=3200) for s in self.symbols}
        self.high_history={s:deque(maxlen=3200) for s in self.symbols}
        self.low_history={s:deque(maxlen=3200) for s in self.symbols}
        self.candle_times={s:deque(maxlen=3200) for s in self.symbols}
        self._last_fetch={}; self._last_error={}; self._provider={}
        self.refresh_seconds=max(5.0,float(self.cfg.get("live_data_refresh_seconds",20.0)))
        self.stale_seconds=max(30.0,float(self.cfg.get("live_data_stale_seconds",180.0)))
        self.delayed_stale_seconds=max(self.stale_seconds,float(self.cfg.get("live_data_delayed_stale_seconds",1200.0)))
        self.timeout=max(2.0,float(self.cfg.get("live_data_timeout_seconds",8.0)))
        self.user_agent="Mozilla/5.0 FX-AI-Paper/2.7"
        # Network I/O must never run on Tk's UI thread. Keep one bounded pool for
        # the lifetime of the feed and only publish completed candle batches.
        # PERFORMANCE: a large pool parsing 140 full 1-minute histories in parallel
        # can starve Tk's UI thread through Python's GIL even though HTTP itself is
        # asynchronous. Keep the pool deliberately bounded and stagger submissions.
        workers=max(2,min(int(self.cfg.get("live_data_workers",6)),12,max(2,len(self.symbols))))
        self.submit_batch=max(2,min(int(self.cfg.get("live_data_submit_batch",8)),24))
        self.incremental_minutes=max(5,min(int(self.cfg.get("live_data_incremental_minutes",20)),120))
        self._executor=ThreadPoolExecutor(max_workers=workers,thread_name_prefix="fx-live")
        self._inflight=set()
        self._inflight_lock=Lock()
        self._data_locks={s:Lock() for s in self.symbols}
        # Expensive RSI/EMA/ATR/session-structure calculations are valid for the
        # whole closed 1-minute candle. Cache them until that candle changes.
        self._snapshot_cache={}

    def _get_json(self,url):
        req=urllib.request.Request(url,headers={"User-Agent":self.user_agent,"Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _fetch_binance(self,symbol):
        """Bootstrap enough history for indicators/session context."""
        bs=_binance_symbol(symbol)
        if not bs: return None
        q=urllib.parse.urlencode({"symbol":bs,"interval":"1m","limit":1000})
        data=self._get_json("https://api.binance.com/api/v3/klines?"+q)
        if not isinstance(data,list) or len(data)<30: return None
        vals=[]; opens=[]; highs=[]; lows=[]; times=[]
        for row in data:
            opens.append(float(row[1])); vals.append(float(row[4])); highs.append(float(row[2])); lows.append(float(row[3])); times.append(float(row[0])/1000.0)
        return vals,opens,highs,lows,times,"BINANCE PUBLIC 1M"

    def _fetch_binance_incremental(self,symbol):
        """Fetch only a small recent tail after bootstrap.

        The previous build downloaded and parsed ~1000 crypto candles every 20s
        per symbol. That was unnecessary because the strategy advances on closed
        1-minute candles and was a major source of whole-app sluggishness.
        """
        bs=_binance_symbol(symbol)
        if not bs: return None
        limit=max(10,min(120,self.incremental_minutes+5))
        q=urllib.parse.urlencode({"symbol":bs,"interval":"1m","limit":limit})
        data=self._get_json("https://api.binance.com/api/v3/klines?"+q)
        if not isinstance(data,list) or not data: return None
        vals=[]; opens=[]; highs=[]; lows=[]; times=[]
        for row in data:
            opens.append(float(row[1])); vals.append(float(row[4])); highs.append(float(row[2])); lows.append(float(row[3])); times.append(float(row[0])/1000.0)
        return vals,opens,highs,lows,times,"BINANCE PUBLIC 1M"

    def _parse_yahoo(self,data,min_rows=1):
        result=((data.get("chart") or {}).get("result") or [None])[0]
        if not result: return None
        ts=result.get("timestamp") or []; quote=(((result.get("indicators") or {}).get("quote") or [{}])[0])
        closes=quote.get("close") or []; opens=quote.get("open") or []; highs=quote.get("high") or []; lows=quote.get("low") or []
        rows=[]; prev_close=None
        for i,(t,c,h,l) in enumerate(zip(ts,closes,highs,lows)):
            if c is None or h is None or l is None: continue
            c=float(c); h=float(h); l=float(l)
            oval=opens[i] if i<len(opens) else None
            try:o=float(oval) if oval is not None else float(prev_close if prev_close is not None else c)
            except Exception:o=float(prev_close if prev_close is not None else c)
            if not (math.isfinite(o) and math.isfinite(c) and math.isfinite(h) and math.isfinite(l)): continue
            rows.append((float(t),o,c,h,l)); prev_close=c
        if len(rows)<min_rows: return None
        rows=rows[-3000:]
        return ([r[2] for r in rows],[r[1] for r in rows],[r[3] for r in rows],[r[4] for r in rows],
                [r[0] for r in rows],"YAHOO PUBLIC 1M")

    def _fetch_yahoo(self,symbol):
        """Bootstrap history once; subsequent refreshes use a small time slice."""
        ys=_yahoo_symbol(symbol)
        if not ys: return None
        enc=urllib.parse.quote(ys,safe="")
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?range=2d&interval=1m&includePrePost=true"
        return self._parse_yahoo(self._get_json(url),min_rows=30)

    def _fetch_yahoo_incremental(self,symbol):
        ys=_yahoo_symbol(symbol)
        if not ys: return None
        enc=urllib.parse.quote(ys,safe="")
        now=int(time.time())
        start=now-(self.incremental_minutes+5)*60
        q=urllib.parse.urlencode({"period1":start,"period2":now+60,"interval":"1m","includePrePost":"true"})
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?{q}"
        return self._parse_yahoo(self._get_json(url),min_rows=1)

    @staticmethod
    def _normalize_result(result):
        if result is None:
            return None
        if len(result)==3:
            vals,times,provider=result
            vals=list(vals); opens=[vals[0]]+vals[:-1] if vals else []
            highs=list(vals); lows=list(vals)
            return vals,opens,highs,lows,list(times),provider
        if len(result)==5:
            # Backward-compatible path for tests/providers returning close/high/low only.
            vals,highs,lows,times,provider=result
            vals=list(vals); opens=[vals[0]]+vals[:-1] if vals else []
            return vals,opens,list(highs),list(lows),list(times),provider
        vals,opens,highs,lows,times,provider=result
        return list(vals),list(opens),list(highs),list(lows),list(times),provider

    def _publish_result(self,symbol,result,bootstrap):
        normalized=self._normalize_result(result)
        if normalized is None:
            raise RuntimeError("No supported live provider mapping")
        vals,opens,highs,lows,times,provider=normalized
        if not vals or not times:
            raise RuntimeError("Empty live candle payload")
        lock=self._data_locks[symbol]
        with lock:
            if bootstrap or not self.history[symbol] or not self.candle_times[symbol]:
                self.history[symbol]=deque(vals,maxlen=3200)
                self.open_history[symbol]=deque(opens,maxlen=3200)
                self.high_history[symbol]=deque(highs,maxlen=3200)
                self.low_history[symbol]=deque(lows,maxlen=3200)
                self.candle_times[symbol]=deque(times,maxlen=3200)
            else:
                # Incremental merge: revise the latest forming candle in place and
                # append only genuinely new minute bars. No 3000-row rebuild.
                hv=self.history[symbol]; ho=self.open_history[symbol]; hh=self.high_history[symbol]; hl=self.low_history[symbol]; ht=self.candle_times[symbol]
                if len(ho)!=len(hv):
                    derived=[float(hv[0])]+[float(x) for x in list(hv)[:-1]] if hv else []
                    self.open_history[symbol]=deque(derived,maxlen=3200); ho=self.open_history[symbol]
                last_t=float(ht[-1]) if ht else -1.0
                for c,o,h,l,t in zip(vals,opens,highs,lows,times):
                    t=float(t)
                    if t < last_t:
                        continue
                    if ht and abs(t-float(ht[-1]))<0.5:
                        hv[-1]=float(c); ho[-1]=float(o); hh[-1]=float(h); hl[-1]=float(l)
                    else:
                        hv.append(float(c)); ho.append(float(o)); hh.append(float(h)); hl.append(float(l)); ht.append(t)
                        last_t=t
            self._provider[symbol]=provider
        self._last_error.pop(symbol,None)

    def _refresh_symbol(self,symbol,force=False):
        now=time.time()
        if not force and now-self._last_fetch.get(symbol,0)<self.refresh_seconds and self.history[symbol]: return
        self._last_fetch[symbol]=now
        try:
            bootstrap=not bool(self.history[symbol])
            result=None
            if instrument_meta(symbol)["asset_class"]=="CRYPTO":
                try:
                    result=self._fetch_binance(symbol) if bootstrap else self._fetch_binance_incremental(symbol)
                except Exception as exc:
                    self._last_error[symbol]=f"Binance: {exc}"
            if result is None:
                result=self._fetch_yahoo(symbol) if bootstrap else self._fetch_yahoo_incremental(symbol)
            self._publish_result(symbol,result,bootstrap)
        except Exception as exc:
            self._last_error[symbol]=str(exc)

    def advance(self):
        """Queue a small bounded batch of due downloads and return immediately.

        Staggering prevents 140 large HTTP/JSON parsing jobs from competing with
        Tk at the same instant. Newly launched installs load markets progressively
        while the interface remains responsive.
        """
        now=time.time()
        with self._inflight_lock:
            due=[s for s in self.symbols
                 if s not in self._inflight and now-self._last_fetch.get(s,0)>=self.refresh_seconds]
            # Bootstrap empty symbols first, then the stalest cached symbols.
            due.sort(key=lambda s:(1 if self.history[s] else 0,self._last_fetch.get(s,0)))
            for symbol in due[:self.submit_batch]:
                self._inflight.add(symbol)
                self._last_fetch[symbol]=now
                future=self._executor.submit(self._refresh_symbol,symbol,True)
                future.add_done_callback(lambda _f,s=symbol:self._download_finished(s))

    def _download_finished(self,symbol):
        with self._inflight_lock:
            self._inflight.discard(symbol)

    @staticmethod
    def _ema(vals,n):
        a=2/(n+1); e=vals[0]
        for v in vals[1:]: e=a*v+(1-a)*e
        return e

    @staticmethod
    def _rsi_series(vals,n=14):
        vals=list(vals)
        if len(vals)<n+1:return [50.0]*len(vals)
        diffs=[vals[i]-vals[i-1] for i in range(1,len(vals))]
        avg_gain=sum(max(x,0) for x in diffs[:n])/n; avg_loss=sum(max(-x,0) for x in diffs[:n])/n
        out=[50.0]*n
        def calc(g,l):
            if l<=1e-12:return 100.0 if g>0 else 50.0
            rs=g/l; return 100-(100/(1+rs))
        out.append(calc(avg_gain,avg_loss))
        for d in diffs[n:]:
            avg_gain=(avg_gain*(n-1)+max(d,0))/n; avg_loss=(avg_loss*(n-1)+max(-d,0))/n; out.append(calc(avg_gain,avg_loss))
        if len(out)<len(vals): out=[50.0]*(len(vals)-len(out))+out
        return out[-len(vals):]

    @staticmethod
    def _trading_day_key(epoch):
        """FX-style trading day anchored at 17:00 New York local time."""
        from zoneinfo import ZoneInfo
        dt=datetime.fromtimestamp(float(epoch),timezone.utc).astimezone(ZoneInfo("America/New_York"))
        day=dt.date()
        if (dt.hour,dt.minute)<(17,0):
            day=day-timedelta(days=1)
        return day.isoformat()

    @staticmethod
    def _session_structure(times,highs,lows,signal_idx,atr_price,step):
        if signal_idx<0 or not times: return {}
        current_dt=datetime.fromtimestamp(float(times[signal_idx]),timezone.utc)
        current_session=session_name(current_dt)
        start=signal_idx
        while start>0:
            prev_dt=datetime.fromtimestamp(float(times[start-1]),timezone.utc)
            if session_name(prev_dt)!=current_session: break
            start-=1
        sh=max(highs[start:signal_idx+1]); sl=min(lows[start:signal_idx+1])
        current_key=LiveMarketFeed._trading_day_key(times[signal_idx])
        prior_keys=[]
        for t in reversed(times[:signal_idx+1]):
            k=LiveMarketFeed._trading_day_key(t)
            if k!=current_key and k not in prior_keys:
                prior_keys.append(k)
            if prior_keys: break
        pdh=pdl=None; complete=False
        if prior_keys:
            pk=prior_keys[0]
            ix=[i for i,t in enumerate(times[:signal_idx+1]) if LiveMarketFeed._trading_day_key(t)==pk]
            if ix:
                pdh=max(highs[i] for i in ix); pdl=min(lows[i] for i in ix)
                # A full FX day has ~1440 1m observations, while exchange-traded
                # proxies have fewer.  300+ candles is enough to mark the level as
                # reasonably representative; otherwise it stays telemetry-only.
                complete=len(ix)>=300
        rng=max(sh-sl,step)
        return {
            "session_high":float(sh),"session_low":float(sl),
            "session_bars":int(signal_idx-start+1),
            "session_range_atr":float(rng/max(atr_price,step)),
            "previous_day_high":None if pdh is None else float(pdh),
            "previous_day_low":None if pdl is None else float(pdl),
            "previous_day_complete":bool(complete),
        }

    def snapshot(self,symbol):
        # Deliberately never fetch here: snapshot is consumed by the engine timer
        # and must remain a fast local-cache read. Expensive indicators are cached
        # by closed-candle identity so a 2-second UI/quote scan does not recompute
        # 100-3000 candles for all 140 symbols.
        lock=self._data_locks[symbol]
        with lock:
            vals_dq=self.history[symbol]; highs_dq=self.high_history[symbol]; lows_dq=self.low_history[symbol]; times_dq=self.candle_times[symbol]
            if len(vals_dq)<30 or not times_dq:
                raise RuntimeError(self._last_error.get(symbol,"No live candles yet"))
            if len(highs_dq)!=len(vals_dq) or len(lows_dq)!=len(vals_dq):
                # Backward-compatible cache/test path.
                if not highs_dq:self.high_history[symbol]=deque(vals_dq,maxlen=3200); highs_dq=self.high_history[symbol]
                if not lows_dq:self.low_history[symbol]=deque(vals_dq,maxlen=3200); lows_dq=self.low_history[symbol]
                if len(highs_dq)!=len(vals_dq) or len(lows_dq)!=len(vals_dq):
                    raise RuntimeError("Malformed live candle buffers")

            newest_t=float(times_dq[-1]); now=time.time()
            age=max(0.0,now-newest_t)
            asset=instrument_meta(symbol)["asset_class"]
            freshness_limit=self.stale_seconds if asset in ("FOREX","CRYPTO") else self.delayed_stale_seconds
            if age>freshness_limit:
                raise RuntimeError(f"STALE DATA ({age:.0f}s old)")
            signal_offset=-1
            if len(vals_dq)>=31 and now < newest_t+60.0:
                signal_offset=-2
            signal_t=float(times_dq[signal_offset]); signal_close=float(vals_dq[signal_offset])
            cache_key=(signal_t,signal_close)
            cached=self._snapshot_cache.get(symbol)
            mid=float(vals_dq[-1])
            spec=instrument_spec(symbol); step=max(float(spec["tick_size"]),1e-12)
            spread=max(0.2,float(spec.get("spread_base",0.9)))
            bid=mid-spread*step/2; ask=mid+spread*step/2
            quality=max(75.0,min(100.0,100.0-age/max(freshness_limit,1.0)*15.0))
            if cached and cached[0]==cache_key:
                base=cached[1]
                return replace(base,bid=bid,ask=ask,quality=quality,data_age_seconds=age,feed_provider=self._provider.get(symbol,"PUBLIC"))

            # One full copy/calculation per NEW closed candle, not per 2-second scan.
            vals=list(vals_dq); highs=list(highs_dq); lows=list(lows_dq); times=list(times_dq)

        signal_idx=len(vals)-1 if signal_offset==-1 else len(vals)-2
        signal_vals=vals[:signal_idx+1]; signal_times=times[:signal_idx+1]
        if len(signal_vals)<30:
            raise RuntimeError("Not enough closed live candles yet")
        ema_fast=self._ema(signal_vals[-70:],20); ema_slow=self._ema(signal_vals[-120:],50)
        hist=signal_vals[-100:]; rsis=self._rsi_series(hist,14); rsi=rsis[-1]
        look=min(14,len(signal_vals)-1); diffs=[abs(signal_vals[-i]-signal_vals[-i-1])/step for i in range(1,look+1)]
        atr=max(2.0,(sum(diffs)/max(len(diffs),1))*2.5)
        atr_price=atr*step
        structure=self._session_structure(times,highs,lows,signal_idx,atr_price,step)
        momentum=(signal_vals[-1]-signal_vals[-min(8,len(signal_vals))])/(step*8)
        signal_dt=datetime.fromtimestamp(signal_times[-1],timezone.utc)
        snap=MarketSnapshot(symbol,bid,ask,spread,rsi,ema_fast,ema_slow,atr,momentum,session_name(signal_dt),
                            signal_dt.isoformat(),quality,tuple(rsis),tuple(hist),"LIVE",
                            self._provider.get(symbol,"PUBLIC"),age,
                            structure.get("session_high"),structure.get("session_low"),
                            int(structure.get("session_bars",0)),structure.get("session_range_atr"),
                            structure.get("previous_day_high"),structure.get("previous_day_low"),
                            bool(structure.get("previous_day_complete",False)))
        self._snapshot_cache[symbol]=(cache_key,snap)
        return snap

    def candle_window(self,symbol,start_iso=None,end_iso=None,pre_bars=30,max_bars=300):
        """Return a lightweight point-in-time OHLC window from local cached candles.

        Replay rendering never performs HTTP. This method only snapshots the feed's
        already-loaded buffers under the per-symbol lock, keeping the zero-lag UI design.
        """
        if symbol not in self.history:
            return []
        def _epoch(value):
            if value is None:return None
            if isinstance(value,(int,float)):return float(value)
            txt=str(value).replace("Z","+00:00")
            try:
                dt=datetime.fromisoformat(txt)
                if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:return None
        start=_epoch(start_iso); end=_epoch(end_iso)
        lock=self._data_locks[symbol]
        with lock:
            closes=list(self.history[symbol]); opens=list(self.open_history[symbol]);
            highs=list(self.high_history[symbol]); lows=list(self.low_history[symbol]); times=list(self.candle_times[symbol])
        n=min(len(closes),len(times),len(highs),len(lows))
        if n<=0:return []
        closes=closes[-n:]; highs=highs[-n:]; lows=lows[-n:]; times=times[-n:]
        if len(opens)<n:
            derived=[closes[0]]+closes[:-1]
            opens=derived
        else:
            opens=opens[-n:]
        indices=list(range(n))
        if start is not None:
            first=next((i for i,t in enumerate(times) if float(t)>=start),max(0,n-1))
            first=max(0,first-max(0,int(pre_bars)))
        else:
            first=max(0,n-int(max_bars))
        if end is not None:
            last=max((i for i,t in enumerate(times) if float(t)<=end+60.0),default=n-1)+1
        else:
            last=n
        first=max(0,min(first,n)); last=max(first,min(last,n))
        if last-first>int(max_bars):
            first=last-int(max_bars)
        out=[]
        for i in range(first,last):
            out.append({"ts":float(times[i]),"open":float(opens[i]),"high":float(highs[i]),"low":float(lows[i]),"close":float(closes[i])})
        return out

    def feed_status(self,symbol):
        if not self.history.get(symbol): return {"status":"NO DATA","provider":self._provider.get(symbol,""),"error":self._last_error.get(symbol,"")}
        age=time.time()-(self.candle_times[symbol][-1] if self.candle_times[symbol] else 0)
        asset=instrument_meta(symbol)["asset_class"]
        limit=self.stale_seconds if asset in ("FOREX","CRYPTO") else self.delayed_stale_seconds
        return {"status":"LIVE" if age<=limit else "STALE","provider":self._provider.get(symbol,""),"age_seconds":age,"error":self._last_error.get(symbol,"")}

    def close(self):
        """Stop accepting downloads without making window shutdown wait for them."""
        self._executor.shutdown(wait=False,cancel_futures=True)
