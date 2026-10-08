from collections import defaultdict
from .instruments import instrument_meta, is_forex
from .rsi_intelligence import analyze_snapshot


def split_pair(symbol):
    if not is_forex(symbol):return "",""
    return symbol[:3],symbol[3:6]


def regime(s):
    gap=abs(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
    if s.spread_pips>3.2 or s.quality<70:return "ABNORMAL"
    if s.atr_pips>28:return "HIGH VOL"
    rsi=analyze_snapshot(s)

    # Trend is price-structure first. A high/low RSI does NOT automatically imply reversal.
    if gap>0.00055 and abs(s.momentum)>0.28:
        return "TREND"

    # Reversal requires RSI structure (failure swing / regular divergence) plus
    # price momentum that is no longer strongly accelerating in the old direction.
    reversal_pattern=(rsi.failure_swing!="NONE" or rsi.regular_divergence!="NONE")
    if reversal_pattern and abs(s.momentum)<0.32:
        return "REVERSAL"

    if abs(s.momentum)<0.12:
        return "RANGE"
    return "MIXED"


def market_rank(s,d):
    meta=instrument_meta(s.symbol); asset=meta["asset_class"]
    liq={"FOREX":9,"METALS":8,"INDICES":8,"ENERGY":6,"CRYPTO":5}.get(asset,4)
    if meta["category"]=="Forex Major":liq+=3
    spread=max(0,12-s.spread_pips*4.5); quality=(s.quality-70)*.35
    rb={"TREND":10,"REVERSAL":6,"RANGE":3,"MIXED":3,"HIGH VOL":-3,"ABNORMAL":-20}[regime(s)]
    return round(max(0,min(100,d.score*.62+liq+spread+quality+rb)),1)


def exposure_summary(positions):
    if not positions:return "No active market concentration"
    fx=defaultdict(float); classes=defaultdict(float)
    for p in positions:
        meta=instrument_meta(p.symbol); w=max(.01,p.risk_amount); classes[meta["asset_class"]]+=w
        if meta["asset_class"]=="FOREX":
            b,q=split_pair(p.symbol); dr=1 if p.side=="BUY" else -1
            fx[b]+=dr*w; fx[q]-=dr*w
    parts=[]
    if fx:
        top=sorted(fx.items(),key=lambda x:abs(x[1]),reverse=True)[:2]
        parts.extend(f"{c} {'LONG' if v>0 else 'SHORT'} ${abs(v):.0f}" for c,v in top)
    topc=sorted(classes.items(),key=lambda x:x[1],reverse=True)[:2]
    parts.extend(f"{c.title()} ${v:.0f} risk" for c,v in topc)
    return "  •  ".join(parts[:4])
