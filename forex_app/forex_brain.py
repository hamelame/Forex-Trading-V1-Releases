from dataclasses import dataclass
from .models import Decision
from .intelligence import regime, split_pair

def clamp(v,a,b): return max(a,min(b,v))

@dataclass
class SpecialistVote:
    name: str
    direction: float   # -100 short ... +100 long
    quality: float     # 0..100
    note: str

class ForexCouncil:
    """
    Forex-specific paper/research brain.

    The Council intentionally separates:
      - trend / momentum continuation
      - range / mean reversion
      - volatility / breakout
      - cross-pair currency-strength confirmation
      - session, spread and data-quality gates

    It does not use carry/macro inputs until real rate/macro data is available.
    """
    def __init__(self,name="FX Council v1",threshold=61):
        self.name=name
        self.threshold=float(threshold)

    def _trend_vote(self,s):
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        trend=clamp(gap*65000,-100,100)
        mom=clamp(s.momentum*32,-100,100)
        alignment=1.0 if trend*mom>=0 else 0.48
        direction=clamp((trend*.62+mom*.38)*alignment,-100,100)
        quality=clamp(48+abs(direction)*.48,0,100)
        return SpecialistVote("Trend",direction,quality,f"EMA {trend:+.0f} / momentum {mom:+.0f}")

    def _reversal_vote(self,s):
        # Strong only in RANGE/REVERSAL states and at meaningful RSI extremes.
        r=regime(s)
        stretch=clamp((50-s.rsi)*3.1,-100,100)
        mid_gap=(s.mid-s.ema_fast)/max(abs(s.ema_fast),1e-9)
        dev=clamp(-mid_gap*90000,-100,100)
        direction=clamp(stretch*.7+dev*.3,-100,100)
        regime_mult={"RANGE":1.0,"REVERSAL":1.0,"MIXED":.55,"TREND":.18,"HIGH VOL":.18,"ABNORMAL":0}.get(r,.4)
        direction*=regime_mult
        quality=clamp(35+abs(direction)*.58,0,100)
        return SpecialistVote("Reversal",direction,quality,f"RSI {s.rsi:.0f} / stretch {direction:+.0f}")

    def _breakout_vote(self,s):
        r=regime(s)
        mom=clamp(s.momentum*40,-100,100)
        trend=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        trend=clamp(trend*55000,-100,100)
        vol=clamp((s.atr_pips-8)*5,0,100)
        direction=clamp(mom*.68+trend*.32,-100,100)
        regime_mult={"TREND":1.0,"HIGH VOL":.9,"MIXED":.65,"REVERSAL":.28,"RANGE":.18,"ABNORMAL":0}.get(r,.5)
        direction*=regime_mult
        quality=clamp(30+vol*.30+abs(direction)*.42,0,100)
        return SpecialistVote("Breakout",direction,quality,f"ATR {s.atr_pips:.1f} / impulse {direction:+.0f}")

    def decide(self,s,currency_strength=None):
        r=regime(s)
        votes=[self._trend_vote(s),self._reversal_vote(s),self._breakout_vote(s)]

        weights={
            "TREND":{"Trend":.55,"Reversal":.08,"Breakout":.37},
            "HIGH VOL":{"Trend":.33,"Reversal":.07,"Breakout":.60},
            "REVERSAL":{"Trend":.14,"Reversal":.70,"Breakout":.16},
            "RANGE":{"Trend":.12,"Reversal":.76,"Breakout":.12},
            "MIXED":{"Trend":.44,"Reversal":.28,"Breakout":.28},
            "ABNORMAL":{"Trend":0,"Reversal":0,"Breakout":0},
        }.get(r,{"Trend":.4,"Reversal":.3,"Breakout":.3})

        direction=sum(v.direction*weights[v.name] for v in votes)
        specialist_quality=sum(v.quality*weights[v.name] for v in votes)

        # Cross-pair currency-strength confirmation.
        strength_edge=0.0
        if currency_strength:
            b,q=split_pair(s.symbol)
            strength_edge=clamp(currency_strength.get(b,0)-currency_strength.get(q,0),-100,100)
            direction=clamp(direction*.78+strength_edge*.22,-100,100)

        session_q={"London":92,"London + New York":100,"New York":88,"Asia":68,"Rollover / Thin":20}.get(s.session,55)
        spread_q=clamp(100-s.spread_pips*25,0,100)
        data_q=clamp(s.quality,0,100)

        edge=abs(direction)
        score=clamp(
            42
            + edge*.36
            + specialist_quality*.12
            + session_q*.07
            + spread_q*.05
            + data_q*.04,
            0,100
        )
        confidence=clamp(40+edge*.36+specialist_quality*.18,0,95)

        # Hard safety gates first.
        if r=="ABNORMAL" or s.quality<70:
            action="BLOCK"
        elif s.spread_pips>2.5:
            action="BLOCK"
        elif score<self.threshold or edge<18:
            action="WAIT"
        else:
            action="BUY" if direction>0 else "SELL"

        # Avoid weak reversal entries in trending regimes and weak breakout entries in ranges.
        if action in ("BUY","SELL") and r=="TREND" and abs(votes[0].direction)<22:
            action="WAIT"
        if action in ("BUY","SELL") and r=="RANGE" and abs(votes[1].direction)<24:
            action="WAIT"

        stop=max(6.0,s.atr_pips*(1.30 if r in ("RANGE","REVERSAL") else 1.48))
        rr=1.55 if r in ("RANGE","REVERSAL") else 1.85
        target=stop*rr

        vote_summary=" · ".join(f"{v.name} {v.direction:+.0f}" for v in votes)
        strength_txt=f" · FX strength {strength_edge:+.0f}" if currency_strength else ""
        reason=(
            f"{r} council: {vote_summary}{strength_txt}. "
            f"Session {s.session}, RSI {s.rsi:.0f}, ATR {s.atr_pips:.1f}, spread {s.spread_pips:.1f}p."
        )
        if action=="WAIT":
            reason="WAIT — edge/confirmation below threshold. "+reason
        elif action=="BLOCK":
            reason="BLOCK — safety/data/spread gate. "+reason

        return Decision(
            s.symbol,action,round(score,1),round(confidence,1),reason,
            round(stop,1),round(target,1),s.timestamp,self.name
        )

def build_currency_strength(snapshots):
    """
    Cross-sectional currency-strength proxy from current trend + momentum.
    Values are normalized to roughly -100..100.
    """
    raw={}
    counts={}
    for symbol,s in snapshots.items():
        if len(symbol)<6: continue
        b,q=split_pair(symbol)
        if len(b)!=3 or len(q)!=3: continue
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        signal=clamp(gap*42000+s.momentum*18,-100,100)
        raw[b]=raw.get(b,0)+signal
        raw[q]=raw.get(q,0)-signal
        counts[b]=counts.get(b,0)+1
        counts[q]=counts.get(q,0)+1
    avg={c:raw[c]/max(1,counts[c]) for c in raw}
    mx=max([abs(v) for v in avg.values()] or [1])
    return {c:clamp(v/mx*100,-100,100) for c,v in avg.items()}
