from .models import Decision

def clamp(v,a,b): return max(a,min(b,v))

class Brain:
    def __init__(self, name="Champion", weights=None, threshold=64):
        self.name=name
        self.w=weights or {"trend_weight":.32,"momentum_weight":.22,"mean_reversion_weight":.12,"volatility_weight":.18,"session_weight":.16}
        self.threshold=threshold

    def decide(self, s):
        trend_raw=(s.ema_fast-s.ema_slow) / max(abs(s.ema_slow),1e-9)
        trend=clamp(trend_raw*60000,-100,100)
        momentum=clamp(s.momentum*28,-100,100)
        meanrev=clamp((50-s.rsi)*2.0,-100,100)
        vol=clamp(100-abs(s.atr_pips-14)*4,-40,100)
        sess={"London":85,"London + New York":100,"New York":80,"Asia":55,"Rollover / Thin":-45}.get(s.session,40)

        directional = (self.w["trend_weight"]*trend +
                       self.w["momentum_weight"]*momentum +
                       self.w["mean_reversion_weight"]*meanrev)
        quality = self.w["volatility_weight"]*vol + self.w["session_weight"]*sess
        long_score=50 + directional*0.42 + quality*0.18
        short_score=50 - directional*0.42 + quality*0.18

        if s.spread_pips > 2.5 or s.quality < 70:
            action="BLOCK"
            score=max(long_score,short_score)
            reason=f"Blocked: spread {s.spread_pips:.1f} pips or data quality below threshold."
        elif max(long_score, short_score) < self.threshold:
            action="WAIT"; score=max(long_score,short_score)
            reason=f"No edge above threshold. Trend {trend:+.0f}, momentum {momentum:+.0f}, RSI {s.rsi:.0f}, session {s.session}."
        elif long_score >= short_score:
            action="BUY"; score=long_score
            reason=f"Long edge: trend {trend:+.0f}, momentum {momentum:+.0f}, RSI {s.rsi:.0f}, {s.session}, spread {s.spread_pips:.1f}p."
        else:
            action="SELL"; score=short_score
            reason=f"Short edge: trend {trend:+.0f}, momentum {momentum:+.0f}, RSI {s.rsi:.0f}, {s.session}, spread {s.spread_pips:.1f}p."

        score=clamp(score,0,100)
        confidence=clamp(45+abs(score-50)*1.1,0,95)
        stop=max(6.0,s.atr_pips*1.35)
        target=stop*1.65
        return Decision(s.symbol,action,round(score,1),round(confidence,1),reason,round(stop,1),round(target,1),s.timestamp,self.name)
