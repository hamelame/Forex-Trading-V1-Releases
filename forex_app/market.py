import random
from collections import deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .instruments import instrument_spec


def pip_size(symbol):
    """Backward-compatible name: returns the synthetic price step for any instrument."""
    return float(instrument_spec(symbol).get("tick_size",0.0001))


def session_name(now=None):
    """DST-aware broad trading session label.

    The old fixed UTC cutoffs drifted by an hour through daylight-saving
    changes and treated several late-US hours as rollover. This version anchors
    London and New York to their local clocks. The intentionally narrow
    17:00-New-York rollover window is kept separate from normal NY hours.
    """
    now=(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now=now.replace(tzinfo=timezone.utc)
    else:
        now=now.astimezone(timezone.utc)
    london=now.astimezone(ZoneInfo("Europe/London"))
    ny=now.astimezone(ZoneInfo("America/New_York"))
    tokyo=now.astimezone(ZoneInfo("Asia/Tokyo"))

    # Retail FX rollover is centred on 17:00 New York. Keep a narrow safety
    # window around it instead of labelling the entire late-US evening rollover.
    ny_minutes=ny.hour*60+ny.minute
    if 16*60+45 <= ny_minutes < 17*60+30:
        return "Rollover / Thin"

    london_open=8 <= london.hour < 17
    new_york_open=8 <= ny.hour < 17
    asia_open=9 <= tokyo.hour < 18
    if london_open and new_york_open:return "London + New York"
    if london_open:return "London"
    if new_york_open:return "New York"
    if asia_open:return "Asia"
    return "Rollover / Thin"


class SyntheticFeed:
    """Multi-market synthetic feed for software / paper-trading research only."""
    def __init__(self,symbols):
        self.symbols=list(symbols)
        self.history={s:deque(maxlen=420) for s in self.symbols}
        self.price={s:float(instrument_spec(s)["base_price"]) for s in self.symbols}
        self.regime={s:random.choice([-1,0,1]) for s in self.symbols}
        self.vol_state={s:1.0 for s in self.symbols}
        for _ in range(260):self._step_all()

    def _step_all(self):
        for s in self.symbols:
            spec=instrument_spec(s); p=self.price[s]; step=float(spec["tick_size"]); vol=float(spec.get("volatility",1.0))
            if random.random()<0.012:self.regime[s]=random.choice([-1,0,1])
            if random.random()<0.008:self.vol_state[s]=random.choice([0.65,1.0,1.5,2.0])
            # Percent-aware scaling prevents BTC/index prices from moving unrealistically slowly.
            relative_step=max(step,abs(p)*0.000035*vol)
            drift=self.regime[s]*relative_step*0.07
            shock=random.gauss(0,relative_step*0.52*self.vol_state[s])
            p=max(step*20,p+drift+shock)
            self.price[s]=p; self.history[s].append(p)

    def _ema(self,vals,n):
        a=2/(n+1); e=vals[0]
        for v in vals[1:]:e=a*v+(1-a)*e
        return e

    def _rsi_series(self,vals,n=14):
        """Wilder RSI series aligned to the supplied price history."""
        vals=list(vals)
        if len(vals)<n+1:
            return [50.0]*len(vals)
        diffs=[vals[i]-vals[i-1] for i in range(1,len(vals))]
        seed=diffs[:n]
        avg_gain=sum(max(x,0.0) for x in seed)/n
        avg_loss=sum(max(-x,0.0) for x in seed)/n
        out=[50.0]*n
        def calc(g,l):
            if l<=1e-12:return 100.0 if g>0 else 50.0
            rs=g/l
            return 100.0-(100.0/(1.0+rs))
        out.append(calc(avg_gain,avg_loss))
        for d in diffs[n:]:
            gain=max(d,0.0); loss=max(-d,0.0)
            avg_gain=(avg_gain*(n-1)+gain)/n
            avg_loss=(avg_loss*(n-1)+loss)/n
            out.append(calc(avg_gain,avg_loss))
        # out corresponds one-to-one with vals
        if len(out)<len(vals): out=[50.0]*(len(vals)-len(out))+out
        return out[-len(vals):]

    def _rsi(self,vals,n=14):
        return self._rsi_series(vals,n)[-1]

    def advance(self):self._step_all()

    def snapshot(self,symbol):
        from .models import MarketSnapshot
        vals=list(self.history[symbol]); spec=instrument_spec(symbol); step=float(spec["tick_size"]); mid=vals[-1]
        sess=session_name()
        sess_mult={"London":.78,"London + New York":.82,"New York":.88,"Asia":.92,"Rollover / Thin":1.55}[sess]
        spread=max(.2,random.gauss(float(spec.get("spread_base",.9))*sess_mult,.12))
        bid=mid-(spread*step/2); ask=mid+(spread*step/2)
        ema_fast=self._ema(vals[-70:],20); ema_slow=self._ema(vals[-120:],50)
        hist_vals=vals[-100:]
        rsi_series=self._rsi_series(hist_vals,14); rsi=rsi_series[-1]
        diffs=[abs(vals[i]-vals[i-1])/max(step,1e-12) for i in range(-14,0)]
        atr=max(2.0,sum(diffs)/len(diffs)*2.5)
        momentum=(vals[-1]-vals[-8])/(max(step,1e-12)*8)
        quality=max(78.0,min(100.0,100-random.random()*3))
        return MarketSnapshot(symbol,bid,ask,spread,rsi,ema_fast,ema_slow,atr,momentum,sess,
                              datetime.now(timezone.utc).isoformat(),quality,
                              tuple(rsi_series),tuple(hist_vals))


class MT5Feed:
    """Reserved for a future live-data build. V2.1 remains hard PAPER/SYNTHETIC."""
    def __init__(self,symbols):
        raise RuntimeError("MT5 / live broker feed is disabled in this PAPER-ONLY research build.")
