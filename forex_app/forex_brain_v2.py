from dataclasses import dataclass
from .models import Decision
from .intelligence import regime, split_pair

def clamp(v,a,b): return max(a,min(b,v))

@dataclass
class Vote:
    name: str
    direction: float
    quality: float
    note: str

class ForexResearchBrainV2:
    """
    PAPER-ONLY research brain.

    Design goals:
    - combine multiple independent FX-style signals instead of one monolithic score
    - abstain when specialists disagree
    - demand stronger evidence in high-volatility / thin-session states
    - use cross-pair currency strength as confirmation, not as a standalone trigger
    - keep risk sizing separate from signal generation
    """
    def __init__(self,name="FX Research Brain v2",threshold=63):
        self.name=name
        self.threshold=float(threshold)

    def _trend(self,s):
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        ema=clamp(gap*70000,-100,100)
        mom=clamp(s.momentum*35,-100,100)
        aligned=1.0 if ema*mom>=0 else .42
        direction=clamp((ema*.58+mom*.42)*aligned,-100,100)
        quality=clamp(42+abs(direction)*.54,0,100)
        return Vote("Trend",direction,quality,f"EMA {ema:+.0f}, momentum {mom:+.0f}")

    def _mean_reversion(self,s):
        r=regime(s)
        rsi_edge=clamp((50-s.rsi)*3.35,-100,100)
        dev=(s.mid-s.ema_fast)/max(abs(s.ema_fast),1e-9)
        dev_edge=clamp(-dev*100000,-100,100)
        raw=rsi_edge*.68+dev_edge*.32
        mult={"RANGE":1.0,"REVERSAL":.95,"MIXED":.45,"TREND":.10,"HIGH VOL":.08,"ABNORMAL":0}.get(r,.3)
        direction=clamp(raw*mult,-100,100)
        quality=clamp(30+abs(direction)*.62,0,100)
        return Vote("MeanRev",direction,quality,f"RSI {s.rsi:.0f}, stretch {direction:+.0f}")

    def _breakout(self,s):
        r=regime(s)
        mom=clamp(s.momentum*44,-100,100)
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        ema=clamp(gap*52000,-100,100)
        vol=clamp((s.atr_pips-7)*5.5,0,100)
        direction=clamp(mom*.72+ema*.28,-100,100)
        mult={"TREND":1.0,"HIGH VOL":.82,"MIXED":.62,"REVERSAL":.22,"RANGE":.12,"ABNORMAL":0}.get(r,.45)
        direction*=mult
        quality=clamp(28+vol*.32+abs(direction)*.43,0,100)
        return Vote("Breakout",direction,quality,f"ATR {s.atr_pips:.1f}, impulse {direction:+.0f}")

    def _session_quality(self,session):
        return {
            "London + New York":100,
            "London":92,
            "New York":88,
            "Asia":68,
            "Rollover / Thin":18,
        }.get(session,55)

    def decide(self,s,currency_strength=None):
        r=regime(s)
        votes=[self._trend(s),self._mean_reversion(s),self._breakout(s)]

        weights={
            "TREND":{"Trend":.58,"MeanRev":.06,"Breakout":.36},
            "HIGH VOL":{"Trend":.36,"MeanRev":.04,"Breakout":.60},
            "REVERSAL":{"Trend":.10,"MeanRev":.78,"Breakout":.12},
            "RANGE":{"Trend":.08,"MeanRev":.82,"Breakout":.10},
            "MIXED":{"Trend":.46,"MeanRev":.24,"Breakout":.30},
            "ABNORMAL":{"Trend":0,"MeanRev":0,"Breakout":0},
        }.get(r,{"Trend":.4,"MeanRev":.3,"Breakout":.3})

        direction=sum(v.direction*weights[v.name] for v in votes)
        specialist_quality=sum(v.quality*weights[v.name] for v in votes)

        # Agreement: strong signals should not come from one specialist fighting the others.
        directional=[v.direction for v in votes if abs(v.direction)>=12]
        pos=sum(1 for x in directional if x>0); neg=sum(1 for x in directional if x<0)
        agreement=max(pos,neg)/max(1,len(directional)) if directional else 0.0
        disagreement_penalty=(1-agreement)*24

        strength_edge=0.0
        if currency_strength:
            b,q=split_pair(s.symbol)
            strength_edge=clamp(currency_strength.get(b,0)-currency_strength.get(q,0),-100,100)
            # confirmation only
            direction=clamp(direction*.82+strength_edge*.18,-100,100)

        session_q=self._session_quality(s.session)
        spread_q=clamp(100-s.spread_pips*28,0,100)
        data_q=clamp(s.quality,0,100)
        edge=abs(direction)

        # High-vol / thin sessions require more evidence.
        regime_threshold=self.threshold
        if r=="HIGH VOL": regime_threshold+=7
        elif r=="MIXED": regime_threshold+=3
        if s.session=="Rollover / Thin": regime_threshold+=10

        score=clamp(
            38
            + edge*.38
            + specialist_quality*.15
            + session_q*.07
            + spread_q*.05
            + data_q*.04
            - disagreement_penalty,
            0,100
        )
        confidence=clamp(
            32 + edge*.34 + specialist_quality*.18 + agreement*18 - disagreement_penalty*.45,
            0,95
        )

        hard_block=None
        if r=="ABNORMAL": hard_block="abnormal market state"
        elif s.quality<75: hard_block="low data quality"
        elif s.spread_pips>2.5: hard_block="spread safety limit"
        elif s.session=="Rollover / Thin" and s.spread_pips>1.4: hard_block="thin-session liquidity"

        if hard_block:
            action="BLOCK"
        elif agreement<.66 and edge<42:
            action="WAIT"
        elif score<regime_threshold or confidence<55 or edge<20:
            action="WAIT"
        else:
            action="BUY" if direction>0 else "SELL"

        # Extra regime confirmation
        trend_vote=next(v for v in votes if v.name=="Trend")
        mean_vote=next(v for v in votes if v.name=="MeanRev")
        breakout_vote=next(v for v in votes if v.name=="Breakout")
        if action in ("BUY","SELL"):
            if r=="TREND" and abs(trend_vote.direction)<25:
                action="WAIT"
            elif r in ("RANGE","REVERSAL") and abs(mean_vote.direction)<28:
                action="WAIT"
            elif r=="HIGH VOL" and abs(breakout_vote.direction)<34:
                action="WAIT"

        # Regime-aware stop / target. No optimization to historical P/L yet.
        if r in ("RANGE","REVERSAL"):
            stop=max(6.0,s.atr_pips*1.25); rr=1.45
        elif r=="HIGH VOL":
            stop=max(8.0,s.atr_pips*1.65); rr=1.65
        else:
            stop=max(6.5,s.atr_pips*1.42); rr=1.85
        target=stop*rr

        vote_summary=" · ".join(f"{v.name} {v.direction:+.0f}" for v in votes)
        strength_txt=f" · FX strength {strength_edge:+.0f}" if currency_strength else ""
        reason=(
            f"{r} | {vote_summary}{strength_txt} | agreement {agreement*100:.0f}% | "
            f"session {s.session} | RSI {s.rsi:.0f} | ATR {s.atr_pips:.1f} | spread {s.spread_pips:.1f}p"
        )
        if action=="WAIT":
            reason="WAIT — insufficient consensus/edge. "+reason
        elif action=="BLOCK":
            reason=f"BLOCK — {hard_block}. "+reason

        return Decision(
            s.symbol,action,round(score,1),round(confidence,1),reason,
            round(stop,1),round(target,1),s.timestamp,self.name
        )

def build_currency_strength_v2(snapshots):
    raw={}; counts={}
    for symbol,s in snapshots.items():
        if len(symbol)<6: continue
        b,q=split_pair(symbol)
        if len(b)!=3 or len(q)!=3: continue
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        signal=clamp(gap*45000+s.momentum*20,-100,100)
        raw[b]=raw.get(b,0)+signal
        raw[q]=raw.get(q,0)-signal
        counts[b]=counts.get(b,0)+1
        counts[q]=counts.get(q,0)+1
    avg={c:raw[c]/max(1,counts[c]) for c in raw}
    mx=max([abs(v) for v in avg.values()] or [1])
    return {c:clamp(v/mx*100,-100,100) for c,v in avg.items()}
