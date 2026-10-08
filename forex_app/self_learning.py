from dataclasses import replace
from collections import defaultdict
from datetime import datetime, timezone
import math, re

from .instruments import instrument_meta

def clamp(v,a,b):
    return max(a,min(b,v))

class SelfLearningEngine:
    """Evidence-gated PAPER learning layer.

    The learner never rewrites the Champion model after a few trades.
    It records detailed outcomes, builds regime/context statistics, evaluates
    blocked trades counterfactually, and only applies small bounded adjustments
    after a context passes a minimum-sample promotion gate.
    """
    def __init__(self,cfg,db):
        self.cfg=cfg
        self.db=db
        self.pending_counterfactuals={}
        self._cache={}
        self._symbol_cache={}
        self._fast_context_cache={}
        self._regime_cache={}
        self._side_cache={}
        self._global_regime_cache={}
        self._global_side_cache={}
        self._confidence_cache={}
        self._asset_confidence_cache={}
        self._effective_confidence_cache={}
        self._asset_effective_confidence_cache={}
        self._rsi_pattern_cache={}
        # v2.9.2 hierarchical Context Health caches.  Exact buckets fall back to
        # broader parents so tiny samples cannot masquerade as an edge.
        self._health_exact_cache={}
        self._health_symbol_session_cache={}
        self._health_asset_session_regime_cache={}
        self._health_asset_session_cache={}
        self._health_session_cache={}
        # v2.9.2.6 directional health: side-specific evidence is tracked
        # separately from broad context health so BUY/SELL asymmetry can adapt
        # without hard-coding a permanent market direction.
        self._direction_asset_regime_location_cache={}
        self._direction_side_location_cache={}
        self._direction_asset_side_cache={}
        self._direction_global_side_cache={}
        self.runtime_health=defaultdict(list)
        self.runtime_circuit_until={}
        self._global_expectancy_cache={"samples":0,"expectancy_r":0.0,"pf":1.0}
        self._cache_trade_count=-1
        self.promoted_contexts=set()
        self.last_note="Learning Lab collecting evidence"

    def apply_config(self,cfg):
        self.cfg=cfg
        self._cache={}
        self._symbol_cache={}
        self._fast_context_cache={}
        self._regime_cache={}
        self._side_cache={}
        self._global_regime_cache={}
        self._global_side_cache={}
        self._confidence_cache={}
        self._asset_confidence_cache={}
        self._effective_confidence_cache={}
        self._asset_effective_confidence_cache={}
        self._rsi_pattern_cache={}
        self._health_exact_cache={}
        self._health_symbol_session_cache={}
        self._health_asset_session_regime_cache={}
        self._health_asset_session_cache={}
        self._health_session_cache={}
        self._direction_asset_regime_location_cache={}
        self._direction_side_location_cache={}
        self._direction_asset_side_cache={}
        self._direction_global_side_cache={}
        self._global_expectancy_cache={"samples":0,"expectancy_r":0.0,"pf":1.0}
        self._cache_trade_count=-1

    def reset_runtime_state(self):
        self.runtime_health=defaultdict(list)
        self.runtime_circuit_until={}

    def context_key(self,symbol,regime,side,session):
        meta=instrument_meta(symbol)
        # Keep learning broad enough to gather evidence, but regime-aware.
        return f"{meta['asset_class']}|{regime}|{side}|{session}"

    def _parse_factor(self,reason,name):
        pats={
            "trend":r"Trend\s*([+-]?\d+(?:\.\d+)?)",
            "meanrev":r"MeanRev\s*([+-]?\d+(?:\.\d+)?)",
            "breakout":r"Breakout\s*([+-]?\d+(?:\.\d+)?)",
            "consensus":r"consensus\s*(\d+(?:\.\d+)?)%",
        }
        m=re.search(pats[name],reason or "",re.I)
        return float(m.group(1)) if m else 0.0

    def record_trade(self,position,decision_context,pnl,r_multiple,exit_reason,bars_open,mfe_r,mae_r,cycle=0):
        ctx=decision_context or {}
        symbol=position.symbol
        regime=ctx.get("regime","MIXED")
        session=ctx.get("session","Unknown")
        side=position.side
        key=self.context_key(symbol,regime,side,session)
        self.db.learning_experience({
            "trade_id":position.id,
            "ts":datetime.now(timezone.utc).isoformat(),
            "symbol":symbol,
            "asset_class":instrument_meta(symbol)["asset_class"],
            "regime":regime,
            "session":session,
            "side":side,
            "context_key":key,
            "score":float(ctx.get("score",position.entry_score or 0)),
            "confidence":float(ctx.get("confidence",0)),
            "spread":float(ctx.get("spread",0)),
            "rsi":float(ctx.get("rsi",0)),
            "atr":float(ctx.get("atr",0)),
            "trend":float(ctx.get("trend",0)),
            "meanrev":float(ctx.get("meanrev",0)),
            "breakout":float(ctx.get("breakout",0)),
            "consensus":float(ctx.get("consensus",0)),
            "rsi_state":str(ctx.get("rsi_state","UNKNOWN")),
            "rsi_pattern":str(ctx.get("rsi_pattern","")),
            "rsi_bias":str(ctx.get("rsi_bias","NEUTRAL")),
            "rsi_slope":float(ctx.get("rsi_slope",0)),
            "rsi_support":float(ctx.get("rsi_support",0)),
            "quality_gate_state":str(ctx.get("quality_gate_state","UNKNOWN")),
            "entry_candle_ts":str(ctx.get("entry_candle_ts","")),
            "entry_candle_seq":int(ctx.get("entry_candle_seq",0)),
            "calibrated_win_probability":ctx.get("calibrated_win_probability"),
            "confidence_calibration_samples":int(ctx.get("confidence_calibration_samples",0)),
            "confidence_overconfidence_gap":ctx.get("confidence_overconfidence_gap"),
            "economic_edge_shadow_r":ctx.get("economic_edge_shadow_r"),
            "neural_win_probability_entry":ctx.get("neural_win_probability"),
            "model_probability_disagreement":ctx.get("model_probability_disagreement"),
            "session_profile":str(ctx.get("session_profile","UNKNOWN")),
            "location_state":str(ctx.get("location_state","UNKNOWN")),
            "range_position":float(ctx.get("range_position",0.5)),
            "extension_atr":float(ctx.get("extension_atr",0.0)),
            "side_extension_atr":float(ctx.get("side_extension_atr",0.0)),
            "breakout_extension_atr":float(ctx.get("breakout_extension_atr",0.0)),
            "late_entry":int(ctx.get("late_entry",0)),
            "poor_location":int(ctx.get("poor_location",0)),
            "fresh_breakout":int(ctx.get("fresh_breakout",0)),
            "session_range_position":float(ctx.get("session_range_position",0.5)),
            "session_range_atr":ctx.get("session_range_atr"),
            "session_bars":int(ctx.get("session_bars",0)),
            "distance_session_high_atr":float(ctx.get("distance_session_high_atr",99.0)),
            "distance_session_low_atr":float(ctx.get("distance_session_low_atr",99.0)),
            "distance_pdh_atr":float(ctx.get("distance_pdh_atr",99.0)),
            "distance_pdl_atr":float(ctx.get("distance_pdl_atr",99.0)),
            "previous_day_complete":int(ctx.get("previous_day_complete",0)),
            "structure_barrier":int(ctx.get("structure_barrier",0)),
            "context_health_state":str(ctx.get("context_health_state","LEARNING")),
            "context_health_score":float(ctx.get("context_health_score",0.0)),
            "context_health_samples":int(ctx.get("context_health_samples",0)),
            "context_health_source":str(ctx.get("context_health_source","HIERARCHY")),
            "runtime_context_state":str(ctx.get("runtime_context_state","CLEAR")),
            "effective_confidence":float(ctx.get("effective_confidence",ctx.get("confidence",0.0))),
            "trend_pullback_retest":int(ctx.get("trend_pullback_retest",0)),
            "trend_chase":int(ctx.get("trend_chase",0)),
            "session_health_state":str(ctx.get("session_health_state","LEARNING")),
            "session_health_score":float(ctx.get("session_health_score",0.0)),
            "session_health_samples":int(ctx.get("session_health_samples",0)),
            "session_health_source":str(ctx.get("session_health_source","HIERARCHY")),
            "directional_health_state":str(ctx.get("directional_health_state","LEARNING")),
            "directional_health_score":float(ctx.get("directional_health_score",0.0)),
            "directional_health_samples":int(ctx.get("directional_health_samples",0)),
            "directional_health_source":str(ctx.get("directional_health_source","HIERARCHY")),
            "sizing_state":str(ctx.get("sizing_state","LEGACY")),
            "sizing_floor_pct":float(ctx.get("sizing_floor_pct",0.0)),
            "sizing_cap_pct":float(ctx.get("sizing_cap_pct",0.0)),
            "sizing_capital_basis":float(ctx.get("sizing_capital_basis",0.0)),
            "pnl":float(pnl),
            "r_multiple":float(r_multiple),
            "mfe_r":float(mfe_r),
            "mae_r":float(mae_r),
            "bars_open":int(bars_open),
            "exit_reason":str(exit_reason),
        })
        self._cache_trade_count=-1
        self._record_runtime_health(symbol,regime,side,session,float(r_multiple),int(cycle or 0))

    def _health_stats(self,items,window=80):
        """Chronological health metrics with recency weighting.

        v2.9.2.5 keeps the old walk-forward-style validation slice, but adds an
        exponentially decayed view so an early hot streak cannot dominate the
        current strategy-health label for days.  The decay is evidence only:
        it never creates a signal.
        """
        ordered=list(reversed(items))[-max(4,int(window)):]
        rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in ordered]
        wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
        gp=sum(wins); gl=abs(sum(losses)); n=len(rs)
        recent_n=max(4,min(int(self.cfg.get("context_health_recent_window",16)),n))
        recent=rs[-recent_n:] if rs else []

        # Chronological validation slice: HEALTHY must still work in the later
        # sample, not merely look good because of an early hot streak.
        split=max(1,int(n*0.65))
        validation=rs[split:] if n-split>=4 else []
        vw=[x for x in validation if x>0]; vl=[x for x in validation if x<0]
        vgp=sum(vw); vgl=abs(sum(vl))
        validation_pf=(vgp/vgl if vgl>1e-12 else (9.99 if vgp>0 else 0.0))

        # Exponential recency weighting.  Newer trades receive more weight while
        # all observations remain visible to the long-run statistics.
        half_life=max(4.0,float(self.cfg.get("context_health_half_life_trades",18.0)))
        if n:
            decay=0.5**(1.0/half_life)
            weights=[decay**(n-1-i) for i in range(n)]
            wsum=sum(weights) or 1.0
            weighted_e=sum(w*r for w,r in zip(weights,rs))/wsum
            wgp=sum(w*r for w,r in zip(weights,rs) if r>0)
            wgl=abs(sum(w*r for w,r in zip(weights,rs) if r<0))
            weighted_pf=wgp/wgl if wgl>1e-12 else (9.99 if wgp>0 else 0.0)
        else:
            weighted_e=0.0; weighted_pf=0.0

        rw=[x for x in recent if x>0]; rl=[x for x in recent if x<0]
        rgp=sum(rw); rgl=abs(sum(rl))
        recent_pf=rgp/rgl if rgl>1e-12 else (9.99 if rgp>0 else 0.0)

        # Peak-to-trough drawdown in R over the context sample.
        eq=0.0; peak=0.0; max_dd=0.0
        for r in rs:
            eq+=r; peak=max(peak,eq); max_dd=max(max_dd,peak-eq)

        older=rs[:-recent_n] if len(rs)>recent_n else []
        old_e=(sum(older)/len(older)) if older else 0.0
        recent_e=(sum(recent)/len(recent)) if recent else 0.0
        drift_delta=recent_e-old_e if older else 0.0
        return {
            "samples":n,
            "expectancy_r":sum(rs)/n if n else 0.0,
            "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
            "win_rate":100.0*len(wins)/n if n else 0.0,
            "recent_expectancy_r":recent_e,
            "recent_pf":recent_pf,
            "weighted_expectancy_r":weighted_e,
            "weighted_pf":weighted_pf,
            "validation_expectancy_r":sum(validation)/len(validation) if validation else 0.0,
            "validation_pf":validation_pf,
            "max_drawdown_r":max_dd,
            "drift_delta_r":drift_delta,
            "loss_rate":len(losses)/n if n else 0.0,
        }

    def _runtime_keys(self,symbol,regime,side,session):
        asset=instrument_meta(symbol)["asset_class"]
        return [
            f"SESSION|{session}",
            f"ASSET_SESSION|{asset}|{session}",
            f"ASSET_SESSION_REGIME|{asset}|{session}|{regime}",
        ]

    def _record_runtime_health(self,symbol,regime,side,session,r_multiple,cycle):
        max_window=max(8,int(self.cfg.get("context_circuit_window",10)))
        for key in self._runtime_keys(symbol,regime,side,session):
            arr=self.runtime_health[key]
            arr.append(clamp(float(r_multiple),-2.0,2.0))
            if len(arr)>max_window*3:
                del arr[:-max_window*3]
            recent=arr[-max_window:]
            if len(recent)<int(self.cfg.get("context_circuit_min_samples",8)):
                continue
            wins=[x for x in recent if x>0]; losses=[x for x in recent if x<0]
            gp=sum(wins); gl=abs(sum(losses)); pf=gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0)
            e=sum(recent)/len(recent); lr=len(losses)/len(recent)
            if (e<=float(self.cfg.get("context_circuit_block_expectancy_r",-0.35))
                    and pf<=float(self.cfg.get("context_circuit_block_pf",0.50))
                    and lr>=float(self.cfg.get("context_circuit_block_loss_rate",0.70))):
                self.runtime_circuit_until[key]=max(
                    int(self.runtime_circuit_until.get(key,0)),
                    int(cycle)+int(self.cfg.get("context_circuit_cooldown_bars",30))
                )

    def runtime_context_guard(self,symbol,regime,side,session,cycle):
        """Intraday circuit breaker.  Bad streaks reduce risk first, then pause.

        A pause expires automatically and permits a new probe trade, avoiding the
        classic deadlock where a blocked context can never demonstrate recovery.
        """
        if not self.cfg.get("context_runtime_circuit_enabled",True):
            return {"state":"DISABLED","allow":True,"risk_mult":1.0,"reason":"runtime circuit disabled"}
        worst=None; notes=[]; risk=1.0
        min_n=int(self.cfg.get("context_circuit_watch_min_samples",6))
        window=max(8,int(self.cfg.get("context_circuit_window",10)))
        for key in self._runtime_keys(symbol,regime,side,session):
            until=int(self.runtime_circuit_until.get(key,0))
            if int(cycle)<until:
                notes.append(f"{key} PAUSED {until-int(cycle)} bars")
                return {"state":"PAUSED","allow":False,"risk_mult":0.0,"reason":" · ".join(notes)}
            arr=self.runtime_health.get(key,[])
            recent=arr[-window:]
            if len(recent)<min_n: continue
            wins=[x for x in recent if x>0]; losses=[x for x in recent if x<0]
            gp=sum(wins); gl=abs(sum(losses)); pf=gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0)
            e=sum(recent)/len(recent)
            if e<=float(self.cfg.get("context_circuit_watch_expectancy_r",-0.15)) and pf<=float(self.cfg.get("context_circuit_watch_pf",0.75)):
                risk=min(risk,float(self.cfg.get("context_circuit_watch_risk_mult",0.35)))
                notes.append(f"{key} WATCH n={len(recent)} E={e:+.2f}R PF={pf:.2f}")
        return {"state":"WATCH" if risk<1.0 else "CLEAR","allow":True,"risk_mult":risk,
                "reason":" · ".join(notes) if notes else "runtime context clear"}

    def _rebuild_cache(self):
        # Fast path: the zero-lag build calls several learning views per candidate.
        # Avoid re-reading thousands of rows when no trade has closed since the
        # previous view. Older/mock DBs fall back to the original behavior.
        if hasattr(self.db,"learning_experience_count"):
            count=int(self.db.learning_experience_count())
            if count==self._cache_trade_count:
                return
        rows=list(self.db.learning_experiences())
        if len(rows)==self._cache_trade_count:
            return
        groups=defaultdict(list)
        symbol_groups=defaultdict(list)
        fast_groups=defaultdict(list)
        regime_groups=defaultdict(list)
        side_groups=defaultdict(list)
        global_regime_groups=defaultdict(list)
        global_side_groups=defaultdict(list)
        confidence_groups=defaultdict(list)
        asset_confidence_groups=defaultdict(list)
        effective_confidence_groups=defaultdict(list)
        asset_effective_confidence_groups=defaultdict(list)
        rsi_pattern_groups=defaultdict(list)
        health_exact_groups=defaultdict(list)
        health_symbol_session_groups=defaultdict(list)
        health_asset_session_regime_groups=defaultdict(list)
        health_asset_session_groups=defaultdict(list)
        health_session_groups=defaultdict(list)
        direction_asset_regime_location_groups=defaultdict(list)
        direction_side_location_groups=defaultdict(list)
        direction_asset_side_groups=defaultdict(list)
        direction_global_side_groups=defaultdict(list)
        for r in rows:
            groups[r["context_key"]].append(r)
            symbol_groups[f"{r['symbol']}|{r['side']}"].append(r)
            fast_groups[f"{r['symbol']}|{r['side']}|{r['regime']}"].append(r)
            regime_groups[f"{r['asset_class']}|{r['regime']}"].append(r)
            side_groups[f"{r['asset_class']}|{r['side']}"].append(r)
            global_regime_groups[str(r["regime"])].append(r)
            global_side_groups[str(r["side"])].append(r)
            conf=float(r["confidence"] or 0.0)
            bucket=int(clamp(conf,0.0,99.999)//5.0)*5
            confidence_groups[str(bucket)].append(r)
            asset_confidence_groups[f"{r['asset_class']}|{bucket}"].append(r)
            eff_conf=(float(r["effective_confidence"] or conf)
                      if "effective_confidence" in r.keys() else conf)
            eff_bucket=int(clamp(eff_conf,0.0,99.999)//5.0)*5
            effective_confidence_groups[str(eff_bucket)].append(r)
            asset_effective_confidence_groups[f"{r['asset_class']}|{eff_bucket}"].append(r)
            pattern_text=(r["rsi_pattern"] or "") if "rsi_pattern" in r.keys() else ""
            for tag in [x for x in pattern_text.split("|") if x]:
                rsi_pattern_groups[f"{r['asset_class']}|{r['regime']}|{r['side']}|{tag}"].append(r)
            # Hierarchical context health intentionally starts with v2.9.1+
            # telemetry. Older strategy generations did not record session/location
            # context and should not vote on the current decision architecture.
            session_profile=(str(r["session_profile"] or "") if "session_profile" in r.keys() else "")
            if session_profile:
                loc=str(r["location_state"] or "UNKNOWN") if "location_state" in r.keys() else "UNKNOWN"
                health_exact_groups[f"{r['symbol']}|{r['session']}|{r['regime']}|{r['side']}|{loc}"].append(r)
                health_symbol_session_groups[f"{r['symbol']}|{r['session']}|{r['side']}"].append(r)
                health_asset_session_regime_groups[f"{r['asset_class']}|{r['session']}|{r['regime']}|{r['side']}"].append(r)
                health_asset_session_groups[f"{r['asset_class']}|{r['session']}|{r['side']}"].append(r)
                health_session_groups[f"{r['session']}|{r['side']}"].append(r)
                direction_asset_regime_location_groups[f"{r['asset_class']}|{r['regime']}|{r['side']}|{loc}"].append(r)
                direction_side_location_groups[f"{r['side']}|{loc}"].append(r)
                direction_asset_side_groups[f"{r['asset_class']}|{r['side']}"].append(r)
                direction_global_side_groups[str(r["side"])].append(r)
        cache={}
        symbol_cache={}
        fast_context_cache={}
        regime_cache={}
        side_cache={}
        global_regime_cache={}
        global_side_cache={}
        confidence_cache={}
        asset_confidence_cache={}
        effective_confidence_cache={}
        asset_effective_confidence_cache={}
        rsi_pattern_cache={}
        health_exact_cache={}
        health_symbol_session_cache={}
        health_asset_session_regime_cache={}
        health_asset_session_cache={}
        health_session_cache={}
        direction_asset_regime_location_cache={}
        direction_side_location_cache={}
        direction_asset_side_cache={}
        direction_global_side_cache={}
        promoted=set()
        min_samples=int(self.cfg.get("learning_min_samples",24))
        promotion_samples=int(self.cfg.get("learning_promotion_samples",40))
        for key,items in groups.items():
            # DB returns newest first. Validation must respect time ordering.
            items=list(reversed(items))
            rs=[float(x["r_multiple"]) for x in items]
            wins=[r for r in rs if r>0]
            losses=[r for r in rs if r<0]
            gp=sum(wins); gl=abs(sum(losses))
            pf=gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0)
            expectancy=sum(rs)/len(rs)
            win_rate=len(wins)/len(rs)*100 if rs else 0

            # Chronological holdout / walk-forward-style sanity gate: promotion
            # requires the later sample to remain positive, not only the full sample.
            split=max(1,int(len(rs)*.65))
            validation=rs[split:] if len(rs)-split>=3 else []
            validation_expectancy=sum(validation)/len(validation) if validation else 0.0
            vw=[x for x in validation if x>0]; vl=[x for x in validation if x<0]
            vgp=sum(vw); vgl=abs(sum(vl))
            validation_pf=vgp/vgl if vgl>1e-12 else (9.99 if vgp>0 else 0.0)

            # Concept drift monitor compares a recent window with older evidence.
            drift=False; drift_delta=0.0
            drift_window=max(6,int(self.cfg.get("learning_drift_window",10)))
            if len(rs)>=drift_window*2:
                old_rs=rs[:-drift_window]; recent_rs=rs[-drift_window:]
                old_e=sum(old_rs)/len(old_rs); recent_e=sum(recent_rs)/len(recent_rs)
                drift_delta=recent_e-old_e
                drift=(drift_delta<=-abs(float(self.cfg.get("learning_drift_threshold_r",0.30)))
                       or recent_e<float(self.cfg.get("learning_drift_floor_r",-0.15)))

            # Shrink noisy small samples toward neutral.
            shrink=len(rs)/(len(rs)+min_samples)
            shrunk_expectancy=expectancy*shrink
            stability=max(0.0,1.0-min(1.0,(max(rs)-min(rs))/6.0)) if len(rs)>=2 else 0.0
            status="RESEARCH"
            if len(rs)>=min_samples:
                status="CHALLENGER"
            if drift and len(rs)>=min_samples:
                status="DRIFT"
            if (not drift and len(rs)>=promotion_samples
                    and expectancy>float(self.cfg.get("learning_min_expectancy_r",0.05))
                    and pf>=float(self.cfg.get("learning_min_profit_factor",1.10))
                    and win_rate>=float(self.cfg.get("learning_min_win_rate",40.0))
                    and validation and validation_expectancy>0
                    and validation_pf>=float(self.cfg.get("learning_validation_min_pf",1.0))):
                status="PROMOTED"
                promoted.add(key)
            cache[key]={
                "samples":len(rs),"expectancy_r":expectancy,"shrunk_expectancy_r":shrunk_expectancy,
                "pf":pf,"win_rate":win_rate,"stability":stability,"status":status,
                "validation_expectancy_r":validation_expectancy,"validation_pf":validation_pf,
                "drift":drift,"drift_delta_r":drift_delta,
            }
        # Symbol+side guard: learn persistent instrument-specific weakness
        # without allowing old execution anomalies to dominate. R is clipped for
        # robust statistics because previous simulator bugs could create huge R.
        sym_window=max(6,int(self.cfg.get("learning_symbol_recent_window",12)))
        for key,items in symbol_groups.items():
            items=list(reversed(items))
            rs=[clamp(float(x["r_multiple"]),-2.5,2.5) for x in items]
            recent=rs[-sym_window:]
            wins=[x for x in recent if x>0]
            losses=[x for x in recent if x<0]
            gp=sum(wins); gl=abs(sum(losses))
            pf=gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0)
            expectancy=sum(recent)/len(recent) if recent else 0.0
            win_rate=len(wins)/len(recent)*100 if recent else 0.0
            symbol_cache[key]={
                "samples":len(rs),
                "recent_samples":len(recent),
                "expectancy_r":expectancy,
                "pf":pf,
                "win_rate":win_rate,
            }

        fast_window=max(4,int(self.cfg.get("learning_fast_context_window",8)))
        for key,items in fast_groups.items():
            rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in list(reversed(items))][-fast_window:]
            wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
            gp=sum(wins); gl=abs(sum(losses))
            fast_context_cache[key]={
                "samples":len(rs),
                "expectancy_r":sum(rs)/len(rs) if rs else 0.0,
                "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
                "loss_rate":len(losses)/len(rs) if rs else 0.0,
            }
        regime_window=max(20,int(self.cfg.get("governor_regime_window",60)))
        for key,items in regime_groups.items():
            ordered=list(reversed(items))
            rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in ordered][-regime_window:]
            wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
            gp=sum(wins); gl=abs(sum(losses))
            recent_half=rs[-max(6,len(rs)//2):] if rs else []
            regime_cache[key]={
                "samples":len(rs),
                "expectancy_r":sum(rs)/len(rs) if rs else 0.0,
                "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
                "win_rate":len(wins)/len(rs)*100 if rs else 0.0,
                "recent_expectancy_r":sum(recent_half)/len(recent_half) if recent_half else 0.0,
            }

        side_window=max(16,int(self.cfg.get("learning_side_window",50)))
        for key,items in side_groups.items():
            rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in list(reversed(items))][-side_window:]
            wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
            gp=sum(wins); gl=abs(sum(losses))
            side_cache[key]={
                "samples":len(rs),
                "expectancy_r":sum(rs)/len(rs) if rs else 0.0,
                "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
                "win_rate":len(wins)/len(rs)*100 if rs else 0.0,
            }

        # v2.9.0: broad evidence guards. These deliberately aggregate across
        # assets so a persistent side/regime failure cannot hide inside many
        # individually small buckets. They are evidence gates only: no signal is
        # ever created from these statistics.
        global_window=max(40,int(self.cfg.get("quality_global_window",120)))
        for key,items in global_regime_groups.items():
            rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in list(reversed(items))][-global_window:]
            wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
            gp=sum(wins); gl=abs(sum(losses))
            global_regime_cache[key]={
                "samples":len(rs),"expectancy_r":sum(rs)/len(rs) if rs else 0.0,
                "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
                "win_rate":len(wins)/len(rs)*100 if rs else 0.0,
            }
        for key,items in global_side_groups.items():
            rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in list(reversed(items))][-global_window:]
            wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
            gp=sum(wins); gl=abs(sum(losses))
            global_side_cache[key]={
                "samples":len(rs),"expectancy_r":sum(rs)/len(rs) if rs else 0.0,
                "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
                "win_rate":len(wins)/len(rs)*100 if rs else 0.0,
            }

        # Empirical confidence calibration: raw AI confidence is treated as a
        # ranking score until completed PAPER trades prove what it means. Buckets
        # are shrunk toward 50% with a prior so tiny hot/cold streaks cannot
        # masquerade as calibrated probabilities.
        cal_window=max(60,int(self.cfg.get("confidence_calibration_window",240)))
        prior=max(0.0,float(self.cfg.get("confidence_calibration_prior_samples",10.0)))
        def _cal_stats(items):
            ordered=list(reversed(items))[-cal_window:]
            rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in ordered]
            wins=sum(1 for x in rs if x>0); losses=[x for x in rs if x<0]
            gp=sum(x for x in rs if x>0); gl=abs(sum(losses))
            n=len(rs)
            p=(wins+0.5*prior)/(n+prior) if n+prior>0 else 0.5
            return {
                "samples":n,"win_rate":(100*wins/n if n else 0.0),
                "calibrated_probability":p,
                "expectancy_r":sum(rs)/n if n else 0.0,
                "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
            }
        for key,items in confidence_groups.items():
            confidence_cache[key]=_cal_stats(items)
        for key,items in asset_confidence_groups.items():
            asset_confidence_cache[key]=_cal_stats(items)
        for key,items in effective_confidence_groups.items():
            effective_confidence_cache[key]=_cal_stats(items)
        for key,items in asset_effective_confidence_groups.items():
            asset_effective_confidence_cache[key]=_cal_stats(items)

        pattern_window=max(12,int(self.cfg.get("learning_rsi_pattern_window",60)))
        for key,items in rsi_pattern_groups.items():
            rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in list(reversed(items))][-pattern_window:]
            wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
            gp=sum(wins); gl=abs(sum(losses))
            rsi_pattern_cache[key]={
                "samples":len(rs),
                "expectancy_r":sum(rs)/len(rs) if rs else 0.0,
                "pf":gp/gl if gl>1e-12 else (9.99 if gp>0 else 0.0),
                "win_rate":len(wins)/len(rs)*100 if rs else 0.0,
            }

        health_window=max(20,int(self.cfg.get("context_health_window",80)))
        health_exact_cache={k:self._health_stats(v,health_window) for k,v in health_exact_groups.items()}
        health_symbol_session_cache={k:self._health_stats(v,health_window) for k,v in health_symbol_session_groups.items()}
        health_asset_session_regime_cache={k:self._health_stats(v,health_window) for k,v in health_asset_session_regime_groups.items()}
        health_asset_session_cache={k:self._health_stats(v,health_window) for k,v in health_asset_session_groups.items()}
        health_session_cache={k:self._health_stats(v,health_window) for k,v in health_session_groups.items()}
        direction_window=max(24,int(self.cfg.get("directional_health_window",80)))
        direction_asset_regime_location_cache={k:self._health_stats(v,direction_window) for k,v in direction_asset_regime_location_groups.items()}
        direction_side_location_cache={k:self._health_stats(v,direction_window) for k,v in direction_side_location_groups.items()}
        direction_asset_side_cache={k:self._health_stats(v,direction_window) for k,v in direction_asset_side_groups.items()}
        direction_global_side_cache={k:self._health_stats(v,direction_window) for k,v in direction_global_side_groups.items()}

        # Cached rolling global expectancy for sizing/governor calls.  This avoids
        # issuing a fresh SQLite scan for every market candidate.
        global_window=max(12,int(self.cfg.get("governor_global_window",40)))
        chronological=list(reversed(rows))[-global_window:]
        global_rs=[clamp(float(x["r_multiple"]),-2.0,2.0) for x in chronological]
        gw=[x for x in global_rs if x>0]; gls=[x for x in global_rs if x<0]
        ggp=sum(gw); ggl=abs(sum(gls))
        global_expectancy_cache={
            "samples":len(global_rs),
            "expectancy_r":sum(global_rs)/len(global_rs) if global_rs else 0.0,
            "pf":ggp/ggl if ggl>1e-12 else (9.99 if ggp>0 else 1.0),
        }

        self._cache=cache
        self._symbol_cache=symbol_cache
        self._fast_context_cache=fast_context_cache
        self._regime_cache=regime_cache
        self._side_cache=side_cache
        self._global_regime_cache=global_regime_cache
        self._global_side_cache=global_side_cache
        self._confidence_cache=confidence_cache
        self._asset_confidence_cache=asset_confidence_cache
        self._effective_confidence_cache=effective_confidence_cache
        self._asset_effective_confidence_cache=asset_effective_confidence_cache
        self._rsi_pattern_cache=rsi_pattern_cache
        self._health_exact_cache=health_exact_cache
        self._health_symbol_session_cache=health_symbol_session_cache
        self._health_asset_session_regime_cache=health_asset_session_regime_cache
        self._health_asset_session_cache=health_asset_session_cache
        self._health_session_cache=health_session_cache
        self._direction_asset_regime_location_cache=direction_asset_regime_location_cache
        self._direction_side_location_cache=direction_side_location_cache
        self._direction_asset_side_cache=direction_asset_side_cache
        self._direction_global_side_cache=direction_global_side_cache
        self._global_expectancy_cache=global_expectancy_cache
        self.promoted_contexts=promoted
        self._cache_trade_count=len(rows)

    def context_health(self,symbol,regime,side,session,location_state="UNKNOWN"):
        """Hierarchical empirical health for the exact trading context.

        Evidence is blended from exact -> parent buckets with sample-size
        shrinkage. Positive history never manufactures a signal or increases risk;
        negative/drifting history may reduce risk or temporarily veto execution.
        """
        self._rebuild_cache()
        asset=instrument_meta(symbol)["asset_class"]
        levels=[
            ("EXACT",self._health_exact_cache.get(f"{symbol}|{session}|{regime}|{side}|{location_state}"),1.00,8),
            ("SYMBOL_SESSION",self._health_symbol_session_cache.get(f"{symbol}|{session}|{side}"),0.85,10),
            ("ASSET_SESSION_REGIME",self._health_asset_session_regime_cache.get(f"{asset}|{session}|{regime}|{side}"),0.75,14),
            ("ASSET_SESSION",self._health_asset_session_cache.get(f"{asset}|{session}|{side}"),0.60,20),
            ("SESSION",self._health_session_cache.get(f"{session}|{side}"),0.35,30),
        ]
        parts=[]; evidence=[]
        for name,st,w,min_n in levels:
            if not st: continue
            n=int(st.get("samples",0)); evidence.append((name,st))
            if n<min_n: continue
            reliability=min(1.0,n/max(1.0,min_n*2.0))
            e=float(st.get("weighted_expectancy_r",st.get("expectancy_r",0.0)))
            pf=float(st.get("weighted_pf",st.get("pf",1.0)))
            re=float(st.get("recent_expectancy_r",e))
            raw=(clamp(e/0.30,-1,1)*0.45 + clamp((pf-1.0)/0.60,-1,1)*0.25 + clamp(re/0.30,-1,1)*0.30)
            parts.append((raw,w*reliability,name,st))
        if not parts:
            n=max([int(st.get("samples",0)) for _,st in evidence] or [0])
            return {"state":"LEARNING","allow":True,"risk_mult":1.0,"score":0.0,"samples":n,
                    "source":"HIERARCHY","reason":"collecting hierarchical context evidence"}
        score=sum(v*w for v,w,_,_ in parts)/max(sum(w for _,w,_,_ in parts),1e-9)
        strongest=max(parts,key=lambda x:x[1])
        source=strongest[2]; n=int(strongest[3].get("samples",0))
        # Hard veto needs substantial, strongly negative evidence.  Mild negatives
        # are WATCH only, preserving exploration at reduced existing governor risk.
        blocked=score<=float(self.cfg.get("context_health_block_score",-0.42)) and n>=int(self.cfg.get("context_health_block_min_samples",10))
        watch=score<=float(self.cfg.get("context_health_watch_score",-0.10))

        # v2.9.2.4: a positive blended score alone is not enough to call a
        # context HEALTHY.  The strongest evidence bucket must pass forward-like
        # validation, recent stability and drawdown checks. This directly fixes
        # the observed false-HEALTHY state in forward PAPER logs.
        strongest_st=strongest[3]
        healthy_min_n=int(self.cfg.get("context_health_healthy_min_samples",24))
        healthy=(
            score>=float(self.cfg.get("context_health_healthy_score",0.18))
            and n>=healthy_min_n
            and float(strongest_st.get("weighted_expectancy_r",strongest_st.get("expectancy_r",0.0)))>=float(self.cfg.get("context_health_healthy_min_expectancy_r",0.03))
            and float(strongest_st.get("weighted_pf",strongest_st.get("pf",1.0)))>=float(self.cfg.get("context_health_healthy_min_pf",1.05))
            and float(strongest_st.get("validation_expectancy_r",0.0))>=float(self.cfg.get("context_health_healthy_min_validation_expectancy_r",0.0))
            and float(strongest_st.get("validation_pf",1.0))>=float(self.cfg.get("context_health_healthy_min_validation_pf",1.0))
            and float(strongest_st.get("recent_expectancy_r",0.0))>=float(self.cfg.get("context_health_healthy_recent_floor_r",-0.05))
            and float(strongest_st.get("max_drawdown_r",0.0))<=float(self.cfg.get("context_health_healthy_max_drawdown_r",4.0))
        )
        # A sharp recent deterioration is WATCH even if the long-run blend still
        # looks positive.
        if (n>=healthy_min_n and float(strongest_st.get("drift_delta_r",0.0))<=-abs(float(self.cfg.get("context_health_drift_watch_r",0.30)))):
            watch=True

        if blocked:
            state="BLOCKED"; allow=False; risk=0.0
        elif watch:
            state="WATCH"; allow=True; risk=float(self.cfg.get("context_health_watch_risk_mult",0.45))
        elif healthy:
            state="HEALTHY"; allow=True; risk=1.0
        else:
            state="LEARNING"; allow=True; risk=float(self.cfg.get("context_health_learning_risk_mult",0.72))
        detail="; ".join(
            f"{name} n={st['samples']} E={st['expectancy_r']:+.2f}R PF={st['pf']:.2f} V={st.get('validation_expectancy_r',0):+.2f}R"
            for _,_,name,st in parts[:3]
        )
        return {"state":state,"allow":allow,"risk_mult":risk,"score":float(score),"samples":n,
                "source":source,"reason":detail}


    def directional_health(self,symbol,regime,side,session,location_state="UNKNOWN"):
        """Recency-weighted BUY/SELL health used only to *reduce* risk.

        The forward PAPER sample showed a large temporary BUY/SELL asymmetry.
        This layer learns that asymmetry dynamically from several hierarchical
        buckets, including price location, instead of hard-coding SELL-only or
        banning BUY. Positive evidence never boosts size by itself; weak evidence
        can only shrink risk and disable the equity sizing floor.
        """
        self._rebuild_cache()
        asset=instrument_meta(symbol)["asset_class"]
        loc=str(location_state or "UNKNOWN")
        levels=[
            ("ASSET_REGIME_LOCATION",self._direction_asset_regime_location_cache.get(f"{asset}|{regime}|{side}|{loc}"),1.00,12),
            ("SIDE_LOCATION",self._direction_side_location_cache.get(f"{side}|{loc}"),0.75,20),
            ("ASSET_SIDE",self._direction_asset_side_cache.get(f"{asset}|{side}"),0.65,24),
            ("GLOBAL_SIDE",self._direction_global_side_cache.get(str(side)),0.45,40),
        ]
        parts=[]
        for name,st,w,min_n in levels:
            if not st: continue
            n=int(st.get("samples",0))
            if n<min_n: continue
            reliability=min(1.0,n/max(float(min_n)*2.0,1.0))
            e=float(st.get("weighted_expectancy_r",st.get("expectancy_r",0.0)))
            pf=float(st.get("weighted_pf",st.get("pf",1.0)))
            re=float(st.get("recent_expectancy_r",e))
            rpf=float(st.get("recent_pf",pf))
            raw=(clamp(e/0.25,-1,1)*0.38 + clamp((pf-1.0)/0.50,-1,1)*0.22
                 + clamp(re/0.25,-1,1)*0.28 + clamp((rpf-1.0)/0.50,-1,1)*0.12)
            parts.append((raw,w*reliability,name,st))
        if not parts:
            return {"state":"LEARNING","risk_mult":1.0,"score":0.0,"samples":0,
                    "source":"HIERARCHY","reason":"collecting directional evidence"}
        score=sum(v*w for v,w,_,_ in parts)/max(sum(w for _,w,_,_ in parts),1e-9)
        strongest=max(parts,key=lambda x:x[1])
        source=strongest[2]; st=strongest[3]; n=int(st.get("samples",0))
        severe=(score<=float(self.cfg.get("directional_health_severe_score",-0.24))
                and n>=int(self.cfg.get("directional_health_severe_min_samples",20)))
        watch=score<=float(self.cfg.get("directional_health_watch_score",-0.07))
        positive=(score>=float(self.cfg.get("directional_health_positive_score",0.14))
                  and float(st.get("weighted_expectancy_r",0.0))>0
                  and float(st.get("weighted_pf",1.0))>=1.05
                  and float(st.get("recent_expectancy_r",0.0))>=0)
        if severe:
            state="SEVERE"; risk=float(self.cfg.get("directional_health_severe_risk_mult",0.30))
        elif watch:
            state="WATCH"; risk=float(self.cfg.get("directional_health_watch_risk_mult",0.55))
        elif positive:
            state="POSITIVE"; risk=1.0
        else:
            state="NEUTRAL"; risk=1.0
        detail=" · ".join(
            f"{name} n={stx['samples']} wE={stx.get('weighted_expectancy_r',0):+.2f}R PF={stx.get('weighted_pf',1):.2f}"
            for _,_,name,stx in parts[:3]
        )
        return {"state":state,"risk_mult":max(0.0,min(1.0,risk)),"score":float(score),
                "samples":n,"source":source,"reason":detail}

    def stats_for(self,symbol,regime,side,session):
        self._rebuild_cache()
        return self._cache.get(self.context_key(symbol,regime,side,session))

    def symbol_stats_for(self,symbol,side):
        self._rebuild_cache()
        return self._symbol_cache.get(f"{symbol}|{side}")

    def fast_context_stats(self,symbol,side,regime):
        self._rebuild_cache()
        return self._fast_context_cache.get(f"{symbol}|{side}|{regime}")

    def regime_stats_for(self,asset_class,regime):
        self._rebuild_cache()
        return self._regime_cache.get(f"{asset_class}|{regime}")

    def side_stats_for(self,asset_class,side):
        self._rebuild_cache()
        return self._side_cache.get(f"{asset_class}|{side}")

    def global_regime_stats(self,regime):
        self._rebuild_cache()
        return self._global_regime_cache.get(str(regime))

    def global_side_stats(self,side):
        self._rebuild_cache()
        return self._global_side_cache.get(str(side))

    def confidence_calibration(self,symbol,regime,side,raw_confidence):
        """Map raw Champion confidence to empirical PAPER reliability.

        This never turns WAIT into BUY/SELL. Asset-specific evidence is preferred
        when mature enough; otherwise the global confidence bucket is used.
        """
        self._rebuild_cache()
        conf=clamp(float(raw_confidence),0.0,99.999)
        bucket=int(conf//5.0)*5
        asset=instrument_meta(symbol)["asset_class"]
        asset_st=self._asset_confidence_cache.get(f"{asset}|{bucket}")
        global_st=self._confidence_cache.get(str(bucket))
        min_asset=int(self.cfg.get("confidence_calibration_asset_min_samples",14))
        st=asset_st if asset_st and asset_st.get("samples",0)>=min_asset else global_st
        if not st:
            return {
                "samples":0,"bucket":bucket,"source":"UNPROVEN",
                "raw_probability":conf/100.0,"calibrated_probability":0.5,
                "reliability":0.0,"expectancy_r":0.0,"pf":1.0,"overconfidence_gap":0.0,
            }
        n=int(st.get("samples",0))
        full=max(1,int(self.cfg.get("confidence_calibration_full_samples",40)))
        reliability=min(1.0,n/full)
        p=float(st.get("calibrated_probability",0.5))
        raw=conf/100.0
        return {
            **st,"bucket":bucket,
            "source":"ASSET" if st is asset_st else "GLOBAL",
            "raw_probability":raw,"reliability":reliability,
            "overconfidence_gap":max(0.0,raw-p),
        }

    def effective_confidence_calibration(self,symbol,regime,side,effective_confidence):
        """Calibration used for position sizing after context penalties.

        Raw Champion confidence remains available for diagnostics, but sizing
        should learn from the confidence that survived context/session penalties.
        """
        self._rebuild_cache()
        conf=clamp(float(effective_confidence),0.0,99.999)
        bucket=int(conf//5.0)*5
        asset=instrument_meta(symbol)["asset_class"]
        asset_st=self._asset_effective_confidence_cache.get(f"{asset}|{bucket}")
        global_st=self._effective_confidence_cache.get(str(bucket))
        min_asset=int(self.cfg.get("confidence_calibration_asset_min_samples",14))
        st=asset_st if asset_st and asset_st.get("samples",0)>=min_asset else global_st
        if not st:
            return {
                "samples":0,"bucket":bucket,"source":"UNPROVEN",
                "raw_probability":conf/100.0,"calibrated_probability":0.5,
                "reliability":0.0,"expectancy_r":0.0,"pf":1.0,"overconfidence_gap":0.0,
            }
        n=int(st.get("samples",0))
        full=max(1,int(self.cfg.get("confidence_calibration_full_samples",40)))
        reliability=min(1.0,n/full)
        p=float(st.get("calibrated_probability",0.5))
        return {
            **st,"bucket":bucket,
            "source":"ASSET" if st is asset_st else "GLOBAL",
            "raw_probability":conf/100.0,"reliability":reliability,
            "overconfidence_gap":max(0.0,conf/100.0-p),
        }

    def session_health(self,symbol,regime,side,session):
        """Dynamic session edge from recent hierarchical PAPER evidence.

        Unlike fixed London/NY priors, this view can turn positive or negative as
        the market changes.  It only scales risk; session enable/disable switches
        remain absolute and are enforced elsewhere.
        """
        self._rebuild_cache()
        asset=instrument_meta(symbol)["asset_class"]
        candidates=[
            ("ASSET_SESSION_REGIME",self._health_asset_session_regime_cache.get(f"{asset}|{session}|{regime}|{side}"),
             int(self.cfg.get("session_health_asset_regime_min_samples",14))),
            ("ASSET_SESSION",self._health_asset_session_cache.get(f"{asset}|{session}|{side}"),
             int(self.cfg.get("session_health_asset_min_samples",20))),
            ("SESSION",self._health_session_cache.get(f"{session}|{side}"),
             int(self.cfg.get("session_health_min_samples",24))),
        ]
        selected=None
        for name,st,min_n in candidates:
            if st and int(st.get("samples",0))>=min_n:
                selected=(name,st); break
        if not selected:
            n=max([int(st.get("samples",0)) for _,st,_ in candidates if st] or [0])
            return {"state":"LEARNING","risk_mult":float(self.cfg.get("session_health_learning_risk_mult",0.85)),
                    "score":0.0,"samples":n,"source":"HIERARCHY","reason":"collecting session evidence"}

        source,st=selected
        n=int(st.get("samples",0))
        e=float(st.get("weighted_expectancy_r",st.get("expectancy_r",0.0)))
        pf=float(st.get("weighted_pf",st.get("pf",1.0)))
        re=float(st.get("recent_expectancy_r",e))
        rpf=float(st.get("recent_pf",pf))
        score=(clamp(e/0.25,-1,1)*0.45 + clamp((pf-1.0)/0.50,-1,1)*0.25
               + clamp(re/0.25,-1,1)*0.30)
        negative=(e<=float(self.cfg.get("session_health_watch_expectancy_r",-0.08))
                  and pf<=float(self.cfg.get("session_health_watch_pf",0.90)))
        positive=(e>=float(self.cfg.get("session_health_positive_expectancy_r",0.05))
                  and pf>=float(self.cfg.get("session_health_positive_pf",1.08))
                  and re>=float(self.cfg.get("session_health_positive_recent_floor_r",-0.02))
                  and rpf>=float(self.cfg.get("session_health_positive_recent_pf",0.95)))
        if negative:
            state="WATCH"; risk=float(self.cfg.get("session_health_watch_risk_mult",0.45))
        elif positive:
            state="POSITIVE"; risk=1.0
        else:
            state="NEUTRAL"; risk=float(self.cfg.get("session_health_neutral_risk_mult",0.82))
        return {"state":state,"risk_mult":risk,"score":float(score),"samples":n,"source":source,
                "expectancy_r":e,"pf":pf,"recent_expectancy_r":re,"recent_pf":rpf,
                "reason":f"{source} n={n} wE={e:+.2f}R wPF={pf:.2f} recent={re:+.2f}R/{rpf:.2f}"}

    def decision_quality_assessment(self,symbol,regime,side,session,raw_confidence):
        """Evidence-first veto/risk assessment for an already valid signal.

        It combines confidence calibration, broad side/regime evidence and the
        existing context governor. The output can only preserve, reduce or veto
        execution; it cannot manufacture a trade.
        """
        cal=self.confidence_calibration(symbol,regime,side,raw_confidence)
        gs=self.global_side_stats(side)
        gr=self.global_regime_stats(regime)
        gov=self.expectancy_governor(symbol,regime,side,session)
        # Calibration is logged in SHADOW even when the new v2.9 quality gate is
        # disabled. The 161-trade development replay did not validate a hard
        # calibration veto, so it must earn execution authority on future data.
        if not self.cfg.get("decision_quality_gate_enabled",False):
            return {
                "allow":True,"risk_mult":1.0,"state":"SHADOW",
                "reason":"quality calibration shadow-only",
                "calibration":cal,"global_side":gs,"global_regime":gr,
            }
        # Context-governor risk is already applied by TradingEngine._risk_multiplier.
        # Keep this quality layer orthogonal so the same risk penalty is not
        # accidentally multiplied twice.
        allow=bool(gov.get("allow",True)); risk=1.0
        reasons=[]; state="PASS"

        cal_min=int(self.cfg.get("confidence_quality_min_samples",24))
        if cal.get("samples",0)>=cal_min:
            ce=float(cal.get("expectancy_r",0.0)); cpf=float(cal.get("pf",1.0)); cp=float(cal.get("calibrated_probability",0.5))
            reasons.append(f"cal {cal['bucket']}-{cal['bucket']+5} n={cal['samples']} p={cp:.2f} E={ce:+.2f}R PF={cpf:.2f}")
            if (ce<=float(self.cfg.get("confidence_quality_block_expectancy_r",-0.12))
                    and cpf<=float(self.cfg.get("confidence_quality_block_pf",0.82))):
                if self.cfg.get("quality_hard_veto_enabled",False):
                    allow=False; risk=0.0; state="RESEARCH-ONLY"
                else:
                    risk=min(risk,float(self.cfg.get("confidence_quality_severe_risk_mult",0.20)))
                    state="PROBATION"
            elif ce<0 or cpf<0.95:
                risk=min(risk,float(self.cfg.get("confidence_quality_negative_risk_mult",0.30)))
            # Explicitly penalize inflated raw confidence even when P/L is only
            # mildly negative. This is calibration, not a new prediction model.
            if float(cal.get("overconfidence_gap",0.0))>=float(self.cfg.get("confidence_overconfidence_gap",0.25)):
                risk=min(risk,float(self.cfg.get("confidence_overconfidence_risk_mult",0.45)))

        side_min=int(self.cfg.get("quality_global_side_min_samples",48))
        if gs and gs.get("samples",0)>=side_min:
            ge=float(gs.get("expectancy_r",0.0)); gpf=float(gs.get("pf",1.0))
            reasons.append(f"all/{side} n={gs['samples']} E={ge:+.2f}R PF={gpf:.2f}")
            if (ge<=float(self.cfg.get("quality_global_side_block_expectancy_r",-0.20))
                    and gpf<=float(self.cfg.get("quality_global_side_block_pf",0.70))):
                risk=min(risk,float(self.cfg.get("quality_global_side_severe_risk_mult",0.22)))
                state="PROBATION"
            elif ge<0 or gpf<0.95:
                risk=min(risk,float(self.cfg.get("quality_global_side_negative_risk_mult",0.40)))

        regime_min=int(self.cfg.get("quality_global_regime_min_samples",60))
        if gr and gr.get("samples",0)>=regime_min:
            ge=float(gr.get("expectancy_r",0.0)); gpf=float(gr.get("pf",1.0))
            reasons.append(f"all/{regime} n={gr['samples']} E={ge:+.2f}R PF={gpf:.2f}")
            if (ge<=float(self.cfg.get("quality_global_regime_block_expectancy_r",-0.15))
                    and gpf<=float(self.cfg.get("quality_global_regime_block_pf",0.75))):
                risk=min(risk,float(self.cfg.get("quality_global_regime_severe_risk_mult",0.22)))
                state="PROBATION"
            elif ge<0 or gpf<0.95:
                risk=min(risk,float(self.cfg.get("quality_global_regime_negative_risk_mult",0.45)))

        if not gov.get("allow",True):
            state="RESEARCH-ONLY"
            reasons.append(str(gov.get("reason","context governor veto")))
        if not reasons:
            reasons.append("collecting calibration evidence")
        return {
            "allow":allow,"risk_mult":max(0.0,min(1.0,risk)),"state":state,
            "reason":" · ".join(reasons),"calibration":cal,
            "global_side":gs,"global_regime":gr,
        }

    def opportunity_recovery_allowed(self):
        """Quality-first liveness rule: NO TRADE is valid during negative edge."""
        if not self.cfg.get("opportunity_recovery_requires_healthy_edge",True):
            return True,"quality-first recovery disabled"
        st=self.global_expectancy_stats()
        min_n=int(self.cfg.get("opportunity_recovery_quality_min_samples",20))
        if st.get("samples",0)<min_n:
            return True,"insufficient global evidence"
        e=float(st.get("expectancy_r",0.0)); pf=float(st.get("pf",1.0))
        if (e<float(self.cfg.get("opportunity_recovery_min_expectancy_r",0.0))
                or pf<float(self.cfg.get("opportunity_recovery_min_pf",0.95))):
            return False,f"quality-first NO TRADE · global E={e:+.2f}R PF={pf:.2f}"
        return True,f"global E={e:+.2f}R PF={pf:.2f}"

    def rsi_pattern_stats(self,asset_class,regime,side,tags):
        self._rebuild_cache()
        out=[]
        for tag in tags or ():
            st=self._rsi_pattern_cache.get(f"{asset_class}|{regime}|{side}|{tag}")
            if st: out.append((tag,st))
        return out

    def expectancy_governor(self,symbol,regime,side,session):
        """Return production eligibility + risk scale from observed PAPER evidence.

        The governor never creates a BUY/SELL. It can only reduce risk or move an
        already executable Champion signal to RESEARCH-ONLY.
        """
        if not self.cfg.get("expectancy_governor_enabled",True):
            return {"state":"DISABLED","risk_mult":1.0,"allow":True,"reason":"Governor disabled"}

        meta=instrument_meta(symbol)
        asset=meta["asset_class"]
        st=self.stats_for(symbol,regime,side,session)
        sym=self.symbol_stats_for(symbol,side)
        fast=self.fast_context_stats(symbol,side,regime)
        broad=self.regime_stats_for(asset,regime)
        side_stats=self.side_stats_for(asset,side)

        bootstrap=int(self.cfg.get("governor_bootstrap_samples",8))
        proof_samples=int(self.cfg.get("governor_proven_samples",16))
        min_e=float(self.cfg.get("governor_min_expectancy_r",0.03))
        min_pf=float(self.cfg.get("governor_min_pf",1.05))
        research_e=float(self.cfg.get("governor_research_only_expectancy_r",-0.12))
        research_pf=float(self.cfg.get("governor_research_only_pf",0.78))

        # Default uncertainty gets reduced capital. REVERSAL starts even more
        # conservatively because the long-run overnight sample was strongly negative.
        risk=float(self.cfg.get("governor_bootstrap_risk_mult",0.25))
        if regime=="REVERSAL":
            risk=min(risk,float(self.cfg.get("governor_reversal_bootstrap_risk_mult",0.08)))
        elif regime=="RANGE":
            risk=min(risk,float(self.cfg.get("governor_range_bootstrap_risk_mult",0.10)))
        elif regime=="TREND":
            risk=min(1.0,max(risk,float(self.cfg.get("governor_trend_bootstrap_risk_mult",0.30))))
        if asset=="FOREX":
            risk=min(risk,float(self.cfg.get("governor_forex_bootstrap_risk_mult",0.20)))

        state="BOOTSTRAP"; allow=True
        reasons=[]

        # Broad asset/regime evidence prevents thousands of trades in a regime
        # that is already losing across many symbols.
        if broad and broad["samples"]>=int(self.cfg.get("governor_regime_min_samples",30)):
            be=float(broad["expectancy_r"]); bpf=float(broad["pf"])
            reasons.append(f"{asset}/{regime} n={broad['samples']} E={be:+.2f}R PF={bpf:.2f}")
            if (be<=float(self.cfg.get("governor_regime_block_expectancy_r",-0.10))
                    and bpf<=float(self.cfg.get("governor_regime_block_pf",0.85))):
                state="RESEARCH-ONLY"; allow=False; risk=0.0

        if st:
            reasons.append(f"context n={st['samples']} E={st['expectancy_r']:+.2f}R PF={st['pf']:.2f}")
            if st["status"]=="DRIFT" and st["samples"]>=bootstrap:
                state="RESEARCH-ONLY"; allow=False; risk=0.0
            elif st["samples"]>=bootstrap and (
                    st["expectancy_r"]<=research_e or st["pf"]<=research_pf):
                state="RESEARCH-ONLY"; allow=False; risk=0.0
            elif (st["samples"]>=proof_samples
                    and st["expectancy_r"]>=min_e
                    and st["pf"]>=min_pf
                    and st.get("validation_expectancy_r",0)>0
                    and st.get("validation_pf",0)>=1.0):
                state="PROVEN"; risk=1.0
            elif st["samples"]>=bootstrap:
                state="PROBATION"
                weak_cap=float(self.cfg.get("governor_weak_regime_probation_risk_mult",0.18))
                risk=min(float(self.cfg.get("governor_probation_risk_mult",0.35)),
                         weak_cap if regime in ("REVERSAL","RANGE") else 1.0)

        # Long/short asymmetry is learned from actual PAPER outcomes instead of
        # assuming BUY and SELL have identical edge. A persistently bad side gets
        # lower risk or research-only treatment at the asset-class level.
        if side_stats and side_stats["samples"]>=int(self.cfg.get("governor_side_min_samples",20)):
            se=float(side_stats["expectancy_r"]); spf=float(side_stats["pf"])
            reasons.append(f"{asset}/{side} n={side_stats['samples']} E={se:+.2f}R PF={spf:.2f}")
            if (se<=float(self.cfg.get("governor_side_block_expectancy_r",-0.12))
                    and spf<=float(self.cfg.get("governor_side_block_pf",0.82))):
                state="RESEARCH-ONLY"; allow=False; risk=0.0
            elif se<0 or spf<.95:
                risk=min(risk,float(self.cfg.get("governor_side_negative_risk_mult",0.25)))

        # Fast and symbol evidence can veto a broad context that looks okay.
        if fast and fast["samples"]>=int(self.cfg.get("learning_fast_context_min_samples",4)):
            reasons.append(f"fast E={fast['expectancy_r']:+.2f}R PF={fast['pf']:.2f}")
            if (fast["expectancy_r"]<=float(self.cfg.get("governor_fast_veto_expectancy_r",-0.45))
                    and fast["pf"]<=float(self.cfg.get("governor_fast_veto_pf",0.60))):
                state="RESEARCH-ONLY"; allow=False; risk=0.0
        if sym and sym["recent_samples"]>=int(self.cfg.get("learning_symbol_min_samples",6)):
            reasons.append(f"symbol E={sym['expectancy_r']:+.2f}R PF={sym['pf']:.2f}")
            if (sym["expectancy_r"]<=float(self.cfg.get("governor_symbol_veto_expectancy_r",-0.25))
                    and sym["pf"]<=float(self.cfg.get("governor_symbol_veto_pf",0.70))):
                state="RESEARCH-ONLY"; allow=False; risk=0.0

        # A strong broad regime cannot override a specifically bad context, but a
        # PROVEN context can use full risk only if its broader regime is not poor.
        if state=="PROVEN" and broad and broad["samples"]>=30:
            if broad["expectancy_r"]<0 or broad["pf"]<0.95:
                state="PROBATION"; risk=min(risk,0.40)

        return {
            "state":state,"risk_mult":max(0.0,min(1.0,risk)),"allow":allow,
            "reason":" · ".join(reasons) if reasons else "collecting evidence",
        }

    def execution_edge_score(self,symbol,regime,side,session):
        """Conservative empirical edge score used only to rank/gate existing signals.

        Evidence is shrunk by sample size so a tiny hot streak cannot dominate.
        Positive values mean the observed PAPER context has been healthier than
        neutral; negative values mean it has been worse. This never creates a trade.
        """
        self._rebuild_cache()
        asset=instrument_meta(symbol)["asset_class"]
        parts=[]
        for st,weight,min_n in (
            (self.stats_for(symbol,regime,side,session),1.00,6),
            (self.fast_context_stats(symbol,side,regime),0.85,6),
            (self.symbol_stats_for(symbol,side),0.55,8),
            (self.regime_stats_for(asset,regime),0.65,20),
            (self.side_stats_for(asset,side),0.55,20),
        ):
            if not st: continue
            n=int(st.get("samples",st.get("recent_samples",0)) or 0)
            if n<min_n: continue
            e=float(st.get("expectancy_r",0.0)); pf=float(st.get("pf",1.0))
            shrink=min(1.0,n/max(float(min_n)*2.0,1.0))
            raw=max(-1.0,min(1.0,e/0.25))*0.65 + max(-1.0,min(1.0,(pf-1.0)/0.50))*0.35
            parts.append((raw*shrink,weight))
        if not parts:
            return {"score":0.0,"samples":0,"label":"UNPROVEN"}
        score=sum(v*w for v,w in parts)/max(sum(w for _,w in parts),1e-9)
        label="POSITIVE" if score>=0.18 else ("NEGATIVE" if score<=-0.18 else "MIXED")
        return {"score":max(-1.0,min(1.0,score)),"samples":len(parts),"label":label}

    def rsi_pattern_governor(self,symbol,regime,side,tags):
        """Evidence gate for RSI features observed at the current setup."""
        if not self.cfg.get("rsi_pattern_learning_enabled",True):
            return {"allow":True,"risk_mult":1.0,"reason":"RSI pattern learning disabled"}
        asset=instrument_meta(symbol)["asset_class"]
        stats=self.rsi_pattern_stats(asset,regime,side,tags)
        min_samples=int(self.cfg.get("rsi_pattern_min_samples",14))
        bad_e=float(self.cfg.get("rsi_pattern_block_expectancy_r",-0.18))
        bad_pf=float(self.cfg.get("rsi_pattern_block_pf",0.72))
        good_e=float(self.cfg.get("rsi_pattern_proven_expectancy_r",0.08))
        good_pf=float(self.cfg.get("rsi_pattern_proven_pf",1.12))
        risk=1.0; allow=True; notes=[]
        for tag,st in stats:
            if st["samples"]<min_samples:
                continue
            notes.append(f"{tag} n={st['samples']} E={st['expectancy_r']:+.2f}R PF={st['pf']:.2f}")
            if st["expectancy_r"]<=bad_e and st["pf"]<=bad_pf:
                allow=False; risk=0.0
            elif st["expectancy_r"]<0 or st["pf"]<.95:
                risk=min(risk,float(self.cfg.get("rsi_pattern_negative_risk_mult",0.30)))
            elif st["expectancy_r"]>=good_e and st["pf"]>=good_pf:
                risk=max(risk,1.0)
        return {"allow":allow,"risk_mult":risk,"reason":" · ".join(notes) if notes else "collecting RSI-pattern evidence"}

    def governor_risk_multiplier(self,symbol,regime,side,session):
        return float(self.expectancy_governor(symbol,regime,side,session)["risk_mult"])

    def global_expectancy_stats(self):
        """Cached rolling after-execution PAPER expectancy used for risk scaling."""
        self._rebuild_cache()
        return dict(self._global_expectancy_cache)

    def global_risk_multiplier(self):
        if not self.cfg.get("expectancy_governor_enabled",True):
            return 1.0
        st=self.global_expectancy_stats()
        n=st["samples"]
        if n<int(self.cfg.get("governor_global_min_samples",16)):
            return float(self.cfg.get("governor_global_bootstrap_risk_mult",0.50))
        e=float(st["expectancy_r"]); pf=float(st["pf"])
        if e<=float(self.cfg.get("governor_global_severe_expectancy_r",-0.20)) or pf<=float(self.cfg.get("governor_global_severe_pf",0.70)):
            return float(self.cfg.get("governor_global_severe_risk_mult",0.18))
        if e<0 or pf<0.95:
            return float(self.cfg.get("governor_global_negative_risk_mult",0.35))
        if e<float(self.cfg.get("governor_global_full_expectancy_r",0.05)) or pf<1.05:
            return float(self.cfg.get("governor_global_probation_risk_mult",0.60))
        return 1.0

    def adjust_decision(self,decision,snapshot,regime):
        """Apply only evidence-gated, bounded learning adjustments.

        WAIT/BLOCK is never promoted to a trade by the learner. The learner may
        downgrade a weak context, or modestly boost an already-executable signal
        after that exact context has passed the promotion gate.
        """
        if not self.cfg.get("self_learning_enabled",True):
            return decision
        if decision.action not in ("BUY","SELL"):
            return decision
        st=self.stats_for(decision.symbol,regime,decision.action,snapshot.session)
        sym=self.symbol_stats_for(decision.symbol,decision.action)
        fast=self.fast_context_stats(decision.symbol,decision.action,regime)
        if not st and not sym and not fast:
            return decision

        score=float(decision.score); conf=float(decision.confidence)
        if st:
            note=f"Learning {st['status']} n={st['samples']} E={st['expectancy_r']:+.2f}R PF={st['pf']:.2f}"
        else:
            note="Learning context collecting evidence"

        # Challenger evidence can protect the Champion from repeatedly bad contexts,
        # but positive boosts require PROMOTED evidence.
        if st and st["status"]=="DRIFT":
            penalty=clamp(3.0+abs(st.get("drift_delta_r",0))*8,3,10)
            score-=penalty
            conf-=penalty
            note+=f" · concept drift {st.get('drift_delta_r',0):+.2f}R"
        elif st and st["status"] in ("CHALLENGER","PROMOTED") and st["expectancy_r"]<0:
            penalty=clamp(abs(st["shrunk_expectancy_r"])*12,0,8)
            score-=penalty
            conf-=penalty*.8
        elif st and st["status"]=="PROMOTED" and st["expectancy_r"]>0:
            boost=clamp(st["shrunk_expectancy_r"]*8,0,5)
            score+=boost
            conf+=boost*.7

        # Instrument-specific guard. Repeated poor recent performance on the
        # same symbol+direction receives a stronger bounded penalty than the
        # broad asset/regime context. This helps the AI stop recycling weak
        # markets while still allowing future recovery after new evidence.
        symbol_block=False
        if sym and sym["recent_samples"]>=int(self.cfg.get("learning_symbol_min_samples",6)):
            sym_e=float(sym["expectancy_r"]); sym_pf=float(sym["pf"])
            if sym_e<0 or sym_pf<float(self.cfg.get("learning_symbol_penalty_pf",0.90)):
                penalty=clamp(
                    abs(min(0.0,sym_e))*float(self.cfg.get("learning_symbol_penalty_scale",12.0))
                    + max(0.0,float(self.cfg.get("learning_symbol_penalty_pf",0.90))-sym_pf)*5.0,
                    0.0,float(self.cfg.get("learning_symbol_penalty_max",12.0))
                )
                score-=penalty
                conf-=penalty*.85
                note+=f" · SymbolGuard n={sym['recent_samples']} E={sym_e:+.2f}R PF={sym_pf:.2f}"
            if (sym["recent_samples"]>=int(self.cfg.get("learning_symbol_block_samples",10))
                    and sym_e<=float(self.cfg.get("learning_symbol_block_expectancy_r",-0.30))
                    and sym_pf<=float(self.cfg.get("learning_symbol_block_pf",0.65))):
                symbol_block=True
                note+=" · symbol/direction temporarily suppressed"

        fast_block=False
        if fast and fast["samples"]>=int(self.cfg.get("learning_fast_context_min_samples",4)):
            fe=float(fast["expectancy_r"]); fpf=float(fast["pf"]); flr=float(fast["loss_rate"])
            if fe<0:
                fp=clamp(abs(fe)*float(self.cfg.get("learning_fast_context_penalty_scale",9.0)),0,9)
                score-=fp; conf-=fp*.8
                note+=f" · FastGuard {regime} n={fast['samples']} E={fe:+.2f}R PF={fpf:.2f}"
            if (fe<=float(self.cfg.get("learning_fast_context_block_expectancy_r",-0.55))
                    and fpf<=float(self.cfg.get("learning_fast_context_block_pf",0.55))
                    and flr>=float(self.cfg.get("learning_fast_context_block_loss_rate",0.70))):
                fast_block=True
                note+=" · fast context suppressed"

        gov=self.expectancy_governor(decision.symbol,regime,decision.action,snapshot.session)
        note+=f" · GOV {gov['state']} risk={gov['risk_mult']:.2f}x"
        if gov["reason"]:
            note+=f" [{gov['reason']}]"

        score=clamp(score,0,100); conf=clamp(conf,0,95)
        action=decision.action
        if not gov["allow"]:
            action="WAIT"
            note+=" · GOVERNOR RESEARCH-ONLY"
        elif symbol_block or fast_block:
            action="WAIT"
        elif score<float(self.cfg.get("min_signal_score",63)) or conf<float(self.cfg.get("min_confidence",56)):
            action="WAIT"
            note+=" · downgraded below execution threshold"

        self.last_note=note
        return replace(decision,action=action,score=round(score,1),confidence=round(conf,1),
                       reason=(decision.reason+" | "+note))

    def observe_blocked(self,decision,snapshot,regime,reason,scan_count):
        if not self.cfg.get("counterfactual_learning_enabled",True):
            return
        if decision.action not in ("BUY","SELL"):
            return
        key=f"{decision.symbol}|{decision.action}|{str(reason).split(chr(40))[0].strip()}"
        if key in self.pending_counterfactuals:
            return
        self.pending_counterfactuals[key]={
            "symbol":decision.symbol,"side":decision.action,"scan":int(scan_count),
            "entry":float(snapshot.mid),"stop_pips":float(decision.stop_pips),
            "score":float(decision.score),"confidence":float(decision.confidence),
            "regime":regime,"session":snapshot.session,"reason":str(reason),
            "last_candle_ts":str(getattr(snapshot,"timestamp","") or ""),
            "candle_steps":0,
        }

    def resolve_counterfactuals(self,snapshots,scan_count):
        horizon_bars=max(1,int(self.cfg.get("counterfactual_horizon_bars",
                                            self.cfg.get("counterfactual_horizon_scans",12))))
        horizon_scans=max(1,int(self.cfg.get("counterfactual_horizon_scans",horizon_bars)))
        done=[]
        for key,item in self.pending_counterfactuals.items():
            s=snapshots.get(item["symbol"])
            if not s:
                continue
            ts=str(getattr(s,"timestamp","") or "")
            if ts:
                if ts!=item.get("last_candle_ts",""):
                    item["last_candle_ts"]=ts
                    item["candle_steps"]=int(item.get("candle_steps",0))+1
                if int(item.get("candle_steps",0))<horizon_bars:
                    continue
                horizon=horizon_bars
            else:
                # Synthetic/legacy feeds without candle timestamps retain the
                # old scan-count behavior for backwards compatibility.
                if scan_count-item["scan"]<horizon_scans:
                    continue
                horizon=horizon_scans
            # Normalize price move by the instrument's actual stop distance.
            from .market import pip_size
            pip=pip_size(item["symbol"])
            move_pips=(s.mid-item["entry"])/pip if item["side"]=="BUY" else (item["entry"]-s.mid)/pip
            hypothetical_r=move_pips/max(item["stop_pips"],1e-9)
            self.db.counterfactual({
                "ts":datetime.now(timezone.utc).isoformat(),
                "symbol":item["symbol"],"side":item["side"],"regime":item["regime"],
                "session":item["session"],"score":item["score"],"confidence":item["confidence"],
                "block_reason":item["reason"],"horizon_scans":horizon,
                "hypothetical_r":float(hypothetical_r),
            })
            done.append(key)
        for key in done:
            self.pending_counterfactuals.pop(key,None)

    def summary(self):
        self._rebuild_cache()
        rows=list(self.db.learning_experiences())
        cf_capacity=int(self.cfg.get("counterfactual_memory_capacity",500))
        cfs=list(self.db.counterfactuals(cf_capacity))
        promoted=sum(1 for v in self._cache.values() if v["status"]=="PROMOTED")
        challengers=sum(1 for v in self._cache.values() if v["status"]=="CHALLENGER")
        drifted=sum(1 for v in self._cache.values() if v["status"]=="DRIFT")
        avg_r=sum(float(r["r_multiple"]) for r in rows)/len(rows) if rows else 0.0
        missed=sum(1 for r in cfs if float(r["hypothetical_r"])>0.5)
        global_stats=self.global_expectancy_stats()
        weak_sides=sum(
            1 for v in self._side_cache.values()
            if v["samples"]>=int(self.cfg.get("governor_side_min_samples",20))
            and v["expectancy_r"]<0
        )
        weak_symbols=sum(
            1 for v in self._symbol_cache.values()
            if v["recent_samples"]>=int(self.cfg.get("learning_symbol_min_samples",6))
            and v["expectancy_r"]<0
        )
        governor_research=sum(
            1 for v in self._cache.values()
            if v["samples"]>=int(self.cfg.get("governor_bootstrap_samples",8))
            and (v["expectancy_r"]<=float(self.cfg.get("governor_research_only_expectancy_r",-0.12))
                 or v["pf"]<=float(self.cfg.get("governor_research_only_pf",0.78)))
        )
        proven_contexts=sum(
            1 for v in self._cache.values()
            if v["samples"]>=int(self.cfg.get("governor_proven_samples",16))
            and v["expectancy_r"]>=float(self.cfg.get("governor_min_expectancy_r",0.03))
            and v["pf"]>=float(self.cfg.get("governor_min_pf",1.05))
            and v.get("validation_expectancy_r",0)>0
        )
        worst_side=min(self._global_side_cache.items(),key=lambda kv:kv[1].get("expectancy_r",0.0)) if self._global_side_cache else ("—",{})
        worst_regime=min(self._global_regime_cache.items(),key=lambda kv:kv[1].get("expectancy_r",0.0)) if self._global_regime_cache else ("—",{})
        return {
            "experiences":len(rows),"contexts":len(self._cache),"promoted":promoted,
            "challengers":challengers,"drifted":drifted,"avg_r":avg_r,"counterfactuals":len(cfs),
            "counterfactual_capacity":cf_capacity,
            "weak_symbol_directions":weak_symbols,
            "governor_research_only":governor_research,
            "governor_proven":proven_contexts,
            "global_expectancy_r":global_stats["expectancy_r"],
            "global_pf":global_stats["pf"],
            "global_governor_risk_mult":self.global_risk_multiplier(),
            "worst_global_side":worst_side[0],
            "worst_global_side_expectancy_r":float(worst_side[1].get("expectancy_r",0.0)),
            "worst_global_regime":worst_regime[0],
            "worst_global_regime_expectancy_r":float(worst_regime[1].get("expectancy_r",0.0)),
            "weak_asset_sides":weak_sides,
            "missed_positive":missed,"pending_counterfactuals":len(self.pending_counterfactuals),
            "last_note":self.last_note,
        }
