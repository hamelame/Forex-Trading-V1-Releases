
from dataclasses import dataclass
from statistics import median
from typing import Tuple

def clamp(v,a,b): return max(a,min(b,v))

@dataclass(frozen=True)
class RSIAnalysis:
    value: float
    slope: float
    acceleration: float
    centerline_state: str
    operating_range: str
    range_shift: str
    regular_divergence: str
    hidden_divergence: str
    failure_swing: str
    momentum_state: str
    bullish_score: float
    bearish_score: float
    confidence: float
    interpretation: str
    tags: Tuple[str,...]

    @property
    def bias(self):
        d=self.bullish_score-self.bearish_score
        if d>=12:return "BULLISH"
        if d<=-12:return "BEARISH"
        return "NEUTRAL"

    @property
    def directional_score(self):
        return clamp(self.bullish_score-self.bearish_score,-100,100)

    def supports(self, side, regime="MIXED"):
        score=self.directional_score if side=="BUY" else -self.directional_score
        # Divergence against an otherwise strong trend is a warning, not an automatic reversal.
        if side=="BUY" and self.regular_divergence=="BEARISH":
            score-=18
        elif side=="SELL" and self.regular_divergence=="BULLISH":
            score-=18
        # Failure swing is strong confirmation because it contains a break of RSI structure.
        if side=="BUY" and self.failure_swing=="BULLISH":
            score+=24
        elif side=="SELL" and self.failure_swing=="BEARISH":
            score+=24
        # Hidden divergence is treated as continuation evidence.
        if regime=="TREND":
            if side=="BUY" and self.hidden_divergence=="BULLISH": score+=18
            if side=="SELL" and self.hidden_divergence=="BEARISH": score+=18
        return clamp(score,-100,100)

    def summary(self):
        parts=[
            f"RSI {self.value:.0f}",
            f"slope {self.slope:+.1f}",
            self.centerline_state,
            f"{self.operating_range} range",
        ]
        if self.range_shift!="NONE": parts.append(self.range_shift)
        if self.regular_divergence!="NONE": parts.append(f"regular {self.regular_divergence.lower()} div")
        if self.hidden_divergence!="NONE": parts.append(f"hidden {self.hidden_divergence.lower()} div")
        if self.failure_swing!="NONE": parts.append(f"{self.failure_swing.lower()} failure swing")
        return " · ".join(parts)

def _pivots(vals, kind="low", left=2, right=2):
    out=[]
    n=len(vals)
    for i in range(left,n-right):
        w=vals[i-left:i+right+1]
        if kind=="low":
            if vals[i]==min(w) and w.count(vals[i])==1: out.append(i)
        else:
            if vals[i]==max(w) and w.count(vals[i])==1: out.append(i)
    return out

def _operating_range(rsis):
    recent=list(rsis[-40:])
    if len(recent)<14:return "NEUTRAL"
    lo=min(recent); hi=max(recent); med=median(recent)
    # Brown-style dynamic interpretation: bullish RSI often finds support above the
    # classic oversold region, while bearish RSI often fails below classic overbought.
    if lo>=38 and med>=52 and hi>=64:return "BULLISH"
    if hi<=62 and med<=48 and lo<=36:return "BEARISH"
    return "NEUTRAL"

def _range_shift(rsis):
    if len(rsis)<60:return "NONE"
    old=_operating_range(rsis[-80:-40] if len(rsis)>=80 else rsis[:-30])
    new=_operating_range(rsis[-40:])
    if new!=old and new in ("BULLISH","BEARISH"):
        return f"{new}_SHIFT"
    return "NONE"

def _centerline(rsis):
    if len(rsis)<4:
        return "ABOVE_50" if rsis and rsis[-1]>=50 else "BELOW_50"
    cur=rsis[-1]; prev=rsis[-2]
    prior=rsis[-4:-1]
    if cur>=50 and prev<50:return "50_RECLAIM"
    if cur<50 and prev>=50:return "50_LOSS"
    if cur>=50 and min(prior)>=48:return "ABOVE_50_HOLD"
    if cur<50 and max(prior)<=52:return "BELOW_50_HOLD"
    return "ABOVE_50" if cur>=50 else "BELOW_50"

def _divergence(prices,rsis):
    if len(prices)<8 or len(rsis)!=len(prices):
        return "NONE","NONE"
    lows=_pivots(prices,"low")
    highs=_pivots(prices,"high")
    candidates_regular=[]
    candidates_hidden=[]
    min_rsi_delta=2.0

    if len(lows)>=2:
        a,b=lows[-2],lows[-1]
        if prices[b]<prices[a] and rsis[b]>rsis[a]+min_rsi_delta:
            candidates_regular.append((b,"BULLISH"))
        elif prices[b]>prices[a] and rsis[b]<rsis[a]-min_rsi_delta:
            candidates_hidden.append((b,"BULLISH"))

    if len(highs)>=2:
        a,b=highs[-2],highs[-1]
        if prices[b]>prices[a] and rsis[b]<rsis[a]-min_rsi_delta:
            candidates_regular.append((b,"BEARISH"))
        elif prices[b]<prices[a] and rsis[b]>rsis[a]+min_rsi_delta:
            candidates_hidden.append((b,"BEARISH"))

    regular=max(candidates_regular,key=lambda x:x[0])[1] if candidates_regular else "NONE"
    hidden=max(candidates_hidden,key=lambda x:x[0])[1] if candidates_hidden else "NONE"
    return regular,hidden

def _failure_swing(rsis):
    if len(rsis)<12:return "NONE"
    x=list(rsis[-28:])
    lows=_pivots(x,"low",1,1); highs=_pivots(x,"high",1,1)
    # Bullish: oversold low -> reaction high -> higher low -> break reaction high.
    if len(lows)>=2 and highs:
        l1,l2=lows[-2],lows[-1]
        hs=[h for h in highs if l1<h<l2]
        if hs:
            h=hs[-1]
            if x[l1]<30 and x[l2]>x[l1]+2 and x[-1]>x[h]:
                return "BULLISH"
    # Bearish mirror.
    if len(highs)>=2 and lows:
        h1,h2=highs[-2],highs[-1]
        ls=[l for l in lows if h1<l<h2]
        if ls:
            l=ls[-1]
            if x[h1]>70 and x[h2]<x[h1]-2 and x[-1]<x[l]:
                return "BEARISH"
    return "NONE"

def analyze_snapshot(s):
    rsis=list(getattr(s,"rsi_history",()) or ())
    prices=list(getattr(s,"price_history",()) or ())
    if not rsis:
        rsis=[float(s.rsi)]
    if not prices:
        prices=[float(s.mid)]*len(rsis)
    n=min(len(prices),len(rsis))
    prices=prices[-n:]; rsis=rsis[-n:]

    value=float(rsis[-1])
    slope=(value-rsis[-4])/3.0 if len(rsis)>=4 else 0.0
    prev_slope=(rsis[-4]-rsis[-7])/3.0 if len(rsis)>=7 else slope
    acceleration=slope-prev_slope
    center=_centerline(rsis)
    op_range=_operating_range(rsis)
    shift=_range_shift(rsis)
    regular,hidden=_divergence(prices,rsis)
    failure=_failure_swing(rsis)

    bull=0.0; bear=0.0; tags=[]
    if value>50:
        bull += min(18,(value-50)*0.75)
    elif value<50:
        bear += min(18,(50-value)*0.75)
    if slope>0:
        bull += min(18,abs(slope)*5); tags.append("RSI_RISING")
    elif slope<0:
        bear += min(18,abs(slope)*5); tags.append("RSI_FALLING")

    if center in ("50_RECLAIM","ABOVE_50_HOLD"):
        bull+=16; tags.append(center)
    elif center in ("50_LOSS","BELOW_50_HOLD"):
        bear+=16; tags.append(center)

    if op_range=="BULLISH":
        bull+=18; tags.append("BULLISH_RANGE")
    elif op_range=="BEARISH":
        bear+=18; tags.append("BEARISH_RANGE")
    if shift=="BULLISH_SHIFT":
        bull+=18; tags.append(shift)
    elif shift=="BEARISH_SHIFT":
        bear+=18; tags.append(shift)

    if regular=="BULLISH":
        bull+=18; tags.append("REGULAR_BULLISH_DIVERGENCE")
    elif regular=="BEARISH":
        bear+=18; tags.append("REGULAR_BEARISH_DIVERGENCE")
    if hidden=="BULLISH":
        bull+=20; tags.append("HIDDEN_BULLISH_DIVERGENCE")
    elif hidden=="BEARISH":
        bear+=20; tags.append("HIDDEN_BEARISH_DIVERGENCE")
    if failure=="BULLISH":
        bull+=28; tags.append("BULLISH_FAILURE_SWING")
    elif failure=="BEARISH":
        bear+=28; tags.append("BEARISH_FAILURE_SWING")

    # Extremes are not reversal orders. They identify strong state; reversal points
    # only gain extra weight when structure (divergence/failure swing) confirms.
    if value>=70:
        tags.append("OVERBOUGHT")
        if slope>0 and op_range=="BULLISH": bull+=8
        if regular=="BEARISH" or failure=="BEARISH": bear+=10
    elif value<=30:
        tags.append("OVERSOLD")
        if slope<0 and op_range=="BEARISH": bear+=8
        if regular=="BULLISH" or failure=="BULLISH": bull+=10

    bull=clamp(bull,0,100); bear=clamp(bear,0,100)
    delta=bull-bear
    if delta>=22:
        interpretation="BULLISH_CONTINUATION" if hidden=="BULLISH" or op_range=="BULLISH" else "BULLISH_CONFIRMATION"
    elif delta<=-22:
        interpretation="BEARISH_CONTINUATION" if hidden=="BEARISH" or op_range=="BEARISH" else "BEARISH_CONFIRMATION"
    elif regular!="NONE" or failure!="NONE":
        interpretation="REVERSAL_WATCH"
    else:
        interpretation="NEUTRAL_CONTEXT"
    confidence=clamp(42+abs(delta)*.48+min(len(tags),5)*3,0,95)
    return RSIAnalysis(value,slope,acceleration,center,op_range,shift,regular,hidden,failure,
                       "EXPANDING" if abs(slope)>1.4 else ("WEAKENING" if abs(slope)<.35 else "STABLE"),
                       bull,bear,confidence,interpretation,tuple(tags))
