
from dataclasses import dataclass
from .models import Decision
from .intelligence import regime, split_pair
from .instruments import instrument_meta, is_forex
from .rsi_intelligence import analyze_snapshot
from .context_edge import session_policy, entry_location_context, context_penalties, synthetic_market_mode


def clamp(v,a,b): return max(a,min(b,v))

@dataclass
class Vote:
    name:str
    direction:float
    quality:float
    note:str


class MultiMarketResearchBrain:
    """One explainable Champion AI with asset/regime-aware internal specialists.

    v2.6.0 makes RSI a structural context engine instead of a 30/70 reversal switch.
    RSI can confirm trend continuation, warn about exhaustion, identify divergence,
    detect failure swings and dynamic range shifts, and is always combined with
    price structure / momentum / regime.
    """
    def __init__(self,name="Multi-Market Research Brain v3",threshold=63,cfg=None):
        self.name=name
        self.threshold=float(threshold)
        self.cfg=cfg or {}

    def apply_config(self,cfg):
        self.cfg=cfg
        self.threshold=float(cfg.get("min_signal_score",self.threshold))

    def _trend(self,s):
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        ema=clamp(gap*68000,-100,100)
        mom=clamp(s.momentum*34,-100,100)
        aligned=1.0 if ema*mom>=0 else .42
        direction=clamp((ema*.58+mom*.42)*aligned,-100,100)
        return Vote("Trend",direction,clamp(42+abs(direction)*.54,0,100),
                    f"EMA {ema:+.0f}, momentum {mom:+.0f}")

    def _rsi_intelligence(self,s):
        a=analyze_snapshot(s)
        direction=a.directional_score
        quality=clamp(36+abs(direction)*.45+a.confidence*.28,0,100)
        return Vote("RSIIntel",direction,quality,a.summary())

    def _mean_reversion(self,s):
        r=regime(s)
        a=analyze_snapshot(s)
        dev=(s.mid-s.ema_fast)/max(abs(s.ema_fast),1e-9)
        stretch=clamp(-dev*90000,-100,100)
        mom=clamp(s.momentum*34,-100,100)

        # No "RSI < 30 => BUY" or "RSI > 70 => SELL".
        # Reversion only receives meaningful weight after RSI structure confirms
        # exhaustion (regular divergence / failure swing) or range behavior agrees.
        rsi_dir=0.0
        structural=False
        if a.failure_swing=="BULLISH":
            rsi_dir=78; structural=True
        elif a.failure_swing=="BEARISH":
            rsi_dir=-78; structural=True
        elif a.regular_divergence=="BULLISH":
            rsi_dir=58; structural=True
        elif a.regular_divergence=="BEARISH":
            rsi_dir=-58; structural=True
        elif r=="RANGE":
            # In a proven range, extremes may assist reversion but only with slope
            # turning back toward the centerline.
            if a.value<=38 and a.slope>0:
                rsi_dir=38; structural=True
            elif a.value>=62 and a.slope<0:
                rsi_dir=-38; structural=True

        raw=rsi_dir*.70+stretch*.30
        confirmation=1.0 if structural and (raw*mom>=0 or abs(mom)<8) else .28
        mult={"RANGE":.82,"REVERSAL":1.0,"MIXED":.22,"TREND":.03,"HIGH VOL":.02,"ABNORMAL":0}.get(r,.18)
        direction=clamp(raw*mult*confirmation,-100,100)
        quality=clamp(20+abs(direction)*.70+(20 if structural else 0),0,100)
        return Vote("MeanRev",direction,quality,
                    f"{a.interpretation}, regular={a.regular_divergence}, failure={a.failure_swing}")

    def _breakout(self,s):
        r=regime(s)
        mom=clamp(s.momentum*44,-100,100)
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        ema=clamp(gap*52000,-100,100)
        vol=clamp((s.atr_pips-7)*4.5,0,100)
        direction=clamp(mom*.72+ema*.28,-100,100)
        mult={"TREND":1.0,"HIGH VOL":.82,"MIXED":.62,"REVERSAL":.18,"RANGE":.10,"ABNORMAL":0}.get(r,.45)
        direction*=mult
        return Vote("Breakout",direction,
                    clamp(28+vol*.32+abs(direction)*.43,0,100),
                    f"ATR {s.atr_pips:.1f}, impulse {direction:+.0f}")

    def _weights(self,asset,r):
        # Trend, MeanRev, Breakout, RSIIntel
        base={
            "TREND":(.48,.02,.27,.23),
            "HIGH VOL":(.34,.01,.42,.23),
            "REVERSAL":(.15,.39,.08,.38),
            "RANGE":(.13,.42,.08,.37),
            "MIXED":(.40,.08,.22,.30),
            "ABNORMAL":(0,0,0,0),
        }.get(r,(.36,.10,.22,.32))
        t,m,b,ri=base

        if asset=="CRYPTO":
            t+=.04; b+=.07; m-=.08; ri-=.03
        elif asset=="ENERGY":
            b+=.06; m-=.03; t-=.02; ri-=.01
        elif asset=="INDICES":
            t+=.04; ri+=.02; m-=.04; b-=.02
        elif asset=="METALS":
            ri+=.04; t+=.02; b-=.03; m-=.03

        total=max(.01,t+m+b+ri)
        return {"Trend":t/total,"MeanRev":m/total,"Breakout":b/total,"RSIIntel":ri/total}

    def decide(self,s,currency_strength=None,liveness_relax=0.0):
        meta=instrument_meta(s.symbol)
        asset=meta["asset_class"]
        r=regime(s)
        rsi_a=analyze_snapshot(s)

        synthetic_market=synthetic_market_mode(self.cfg)

        votes=[self._trend(s),self._mean_reversion(s),self._breakout(s),self._rsi_intelligence(s)]
        enabled={
            "Trend":self.cfg.get("use_trend_specialist",True),
            "MeanRev":self.cfg.get("use_mean_reversion_specialist",True),
            "Breakout":self.cfg.get("use_breakout_specialist",True),
            "RSIIntel":self.cfg.get("use_rsi_intelligence",True),
        }
        weights=self._weights(asset,r)
        for k in weights:
            if not enabled.get(k,True):
                weights[k]=0
        total=sum(weights.values()) or 1
        weights={k:v/total for k,v in weights.items()}

        direction=sum(v.direction*weights[v.name] for v in votes)
        quality=sum(v.quality*weights[v.name] for v in votes)

        directional=[v.direction for v in votes if enabled.get(v.name,True) and abs(v.direction)>=12]
        pos=sum(x>0 for x in directional)
        neg=sum(x<0 for x in directional)
        agreement=max(pos,neg)/max(1,len(directional)) if directional else 0.0

        strength_edge=0.0
        if is_forex(s.symbol) and currency_strength and self.cfg.get("use_currency_strength",True):
            b,q=split_pair(s.symbol)
            strength_edge=clamp(currency_strength.get(b,0)-currency_strength.get(q,0),-100,100)
            direction=clamp(direction*.84+strength_edge*.16,-100,100)

        # RSI conflict penalty: a strong opposing RSI structure is not an automatic
        # reversal, but production confidence is reduced until price confirms.
        rsi_conflict=False
        if direction>0 and rsi_a.supports("BUY",r)<-20:
            rsi_conflict=True
        elif direction<0 and rsi_a.supports("SELL",r)<-20:
            rsi_conflict=True

        # v2.9.1 Context Edge: time + market + location are part of the signal,
        # not decorations after a BUY/SELL has already been chosen.
        session_ctx=session_policy(s.symbol,s.session,self.cfg)
        if synthetic_market:
            # Synthetic random walks are not session-structured, so session gating
            # is disabled only when the *market data* itself is synthetic.
            session_ctx={"allowed":True,"code":"SYNTHETIC_SESSION_FREE","profile":"SYNTHETIC","quality":72.0}
        proposed_side="BUY" if direction>=0 else "SELL"
        location_ctx=entry_location_context(s,proposed_side,r,self.cfg)
        ctx_score_penalty,ctx_conf_penalty,ctx_codes=context_penalties(location_ctx,self.cfg)
        session_q=float(session_ctx.get("quality",55.0))

        spread_q=clamp(100-s.spread_pips*28,0,100)
        data_q=clamp(s.quality,0,100)
        edge=abs(direction)
        disagreement=(1-agreement)*24
        category_penalty={"FOREX":0,"METALS":1,"INDICES":1,"ENERGY":3,"CRYPTO":5}.get(asset,3)
        conflict_penalty=float(self.cfg.get("rsi_conflict_score_penalty",8.0)) if rsi_conflict else 0.0

        # Extreme trend/momentum used to push raw confidence almost monotonically
        # toward 95%, even though the forward log showed the highest-confidence
        # bucket performed worst. Saturate signal-strength credit and let location
        # penalise chasing instead of treating more momentum as always better.
        sat=float(self.cfg.get("confidence_signal_saturation_start",72.0))
        tail=float(self.cfg.get("confidence_signal_tail_weight",0.18))
        effective_edge=min(edge,sat)+max(0.0,edge-sat)*tail
        effective_quality=min(quality,88.0)+max(0.0,quality-88.0)*0.25

        score=clamp(
            38+effective_edge*.38+effective_quality*.15+session_q*.07+spread_q*.05+data_q*.04
            -disagreement-category_penalty-conflict_penalty-ctx_score_penalty,0,100
        )
        confidence=clamp(
            31+effective_edge*.31+effective_quality*.16+agreement*16-disagreement*.45-category_penalty
            -(10 if rsi_conflict else 0)-ctx_conf_penalty,0,float(self.cfg.get("raw_confidence_cap",90.0))
        )

        threshold=float(
            self.cfg.get("manual_min_signal_score",self.threshold)
            if self.cfg.get("trading_profile")=="MANUAL"
            else self.cfg.get("min_signal_score",self.threshold)
        )
        if r=="HIGH VOL": threshold+=7
        elif r=="MIXED": threshold+=3
        if s.session=="Rollover / Thin" and asset!="CRYPTO":
            threshold+=10
        if asset=="CRYPTO": threshold+=3

        min_cons=float(self.cfg.get("min_consensus_pct",66))/100
        min_conf=float(self.cfg.get("min_confidence",56))
        min_edge=float(self.cfg.get("min_edge",20))

        liveness_relax=clamp(float(liveness_relax),0.0,1.0) if synthetic_market else 0.0
        if liveness_relax>0:
            threshold=max(58.0,threshold-3.0*liveness_relax)
            min_cons=max(.55,min_cons-.08*liveness_relax)
            min_conf=max(52.0,min_conf-3.0*liveness_relax)
            min_edge=max(16.0,min_edge-3.0*liveness_relax)

        after_cost_edge=max(
            0.0,
            (score-50.0)*(confidence/100.0)
            -s.spread_pips*float(self.cfg.get("edge_spread_penalty",1.6))
        )
        min_after_cost=float(self.cfg.get("min_after_cost_edge",8.0))
        if liveness_relax>0:
            min_after_cost=max(5.5,min_after_cost-1.8*liveness_relax)

        hard=None
        if r=="ABNORMAL": hard="abnormal market state"
        elif s.quality<float(self.cfg.get("min_data_quality",75)): hard="low data quality"
        elif s.spread_pips>float(self.cfg.get("max_spread_pips",2.5)): hard="spread safety limit"
        elif not synthetic_market and not session_ctx.get("allowed",True):
            hard=f"{session_ctx.get('code','WRONG_SESSION')} / {session_ctx.get('profile','context policy')}"
        if r=="HIGH VOL" and self.cfg.get("pause_on_high_volatility",False):
            hard="high-volatility pause"

        # Strong contrary failure swings can veto immediate production entry.
        structural_veto=False
        if direction>0 and rsi_a.failure_swing=="BEARISH": structural_veto=True
        if direction<0 and rsi_a.failure_swing=="BULLISH": structural_veto=True

        if hard:
            action="BLOCK"
        elif structural_veto:
            action="WAIT"
        elif agreement<min_cons and edge<42:
            action="WAIT"
        elif score<threshold or confidence<min_conf or edge<min_edge or after_cost_edge<min_after_cost:
            action="WAIT"
        else:
            action="BUY" if direction>0 else "SELL"

        # RR starts higher than old range/reversal assumptions because the previous
        # logs showed winners too small versus full stop-outs.
        if r=="RANGE":
            stop=max(5.5,s.atr_pips*1.12); rr=1.55
        elif r=="REVERSAL":
            stop=max(6.0,s.atr_pips*1.18); rr=1.70
        elif r=="HIGH VOL":
            stop=max(8.0,s.atr_pips*1.60); rr=1.70
        else:
            stop=max(6.5,s.atr_pips*1.38); rr=1.85
        if asset=="CRYPTO":
            stop*=1.15
        target=stop*rr

        summary=" · ".join(
            f"{v.name} {v.direction:+.0f}"
            for v in votes if enabled.get(v.name,True)
        )
        strength=f" · FX strength {strength_edge:+.0f}" if is_forex(s.symbol) and currency_strength else ""
        rsi_text=rsi_a.summary()
        reason=(
            f"{asset} / {r} | {summary}{strength} | consensus {agreement*100:.0f}% | "
            f"confidence {confidence:.0f}% | after-cost edge {after_cost_edge:.1f} | "
            f"RSI Intelligence: {rsi_text} | RSI bias {rsi_a.bias} {rsi_a.confidence:.0f}% | "
            f"ATR {s.atr_pips:.1f} | spread {s.spread_pips:.1f} | "
            f"CTX {session_ctx.get('profile','?')} / {location_ctx.get('location','?')} / "
            f"ext {location_ctx.get('side_extension_atr',0.0):+.2f}ATR"
        )
        if ctx_codes:
            reason+=f" | CTX FLAGS {','.join(ctx_codes)}"
        if rsi_conflict:
            reason+=" | RSI STRUCTURE CONFLICT"
        if liveness_relax>0:
            reason+=" | OPPORTUNITY RECOVERY"
        if action=="WAIT":
            reason="WAIT — insufficient confirmed edge / structure. "+reason
        elif action=="BLOCK":
            reason=f"BLOCK — {hard}. "+reason

        return Decision(
            s.symbol,action,round(score,1),round(confidence,1),reason,
            round(stop,1),round(target,1),s.timestamp,self.name
        )


def build_currency_strength(snapshots):
    raw={}; counts={}
    for symbol,s in snapshots.items():
        if not is_forex(symbol): continue
        b,q=split_pair(symbol)
        gap=(s.ema_fast-s.ema_slow)/max(abs(s.ema_slow),1e-9)
        signal=clamp(gap*45000+s.momentum*20,-100,100)
        raw[b]=raw.get(b,0)+signal
        raw[q]=raw.get(q,0)-signal
        counts[b]=counts.get(b,0)+1
        counts[q]=counts.get(q,0)+1
    avg={c:raw[c]/max(1,counts[c]) for c in raw}
    mx=max([abs(v) for v in avg.values()] or [1])
    return {c:clamp(v/mx*100,-100,100) for c,v in avg.items()}
