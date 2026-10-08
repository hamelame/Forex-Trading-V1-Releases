"""Adaptive RSI Shadow Lab v1 — research only, never controls execution."""
from __future__ import annotations
import json
from pathlib import Path

def _clamp(v,a,b): return max(a,min(b,v))
def _pct(values,q):
    x=sorted(float(v) for v in values)
    if not x:return 50.0
    p=(len(x)-1)*_clamp(float(q),0.0,1.0); lo=int(p); hi=min(lo+1,len(x)-1); f=p-lo
    return x[lo]*(1-f)+x[hi]*f

class AdaptiveRSIShadow:
    def __init__(self,cfg,path="data/adaptive_rsi_shadow_v1.json"):
        self.cfg=cfg; self.path=Path(path); self.groups={}; self.samples=0; self._load()
    def apply_config(self,cfg): self.cfg=cfg
    def _key(self,symbol,regime,side): return f"{symbol}|{regime}|{side}"
    def propose(self,s,d,regime,analysis):
        history=list(getattr(s,"rsi_history",()) or ())
        lookback=max(30,int(self.cfg.get("adaptive_rsi_shadow_lookback",120)))
        history=[float(v) for v in history[-lookback:]]
        if len(history)<14: history=[float(getattr(s,"rsi",50.0))]*14
        side=str(d.action); rg=str(regime)
        bands={"RANGE":((.08,.42),(.58,.92)),"REVERSAL":((.05,.38),(.62,.95)),
               "TREND":((.52,.90),(.10,.48)),"HIGH VOL":((.55,.94),(.06,.45)),
               "MIXED":((.48,.88),(.12,.52))}
        buy,sell=bands.get(rg,bands["MIXED"]); qlo,qhi=buy if side=="BUY" else sell
        lo=max(18.0,_pct(history,qlo)); hi=min(82.0,_pct(history,qhi))
        if hi-lo<6:
            mid=(lo+hi)/2; lo=max(18,mid-3); hi=min(82,mid+3)
        value=float(analysis.value); slope=float(analysis.slope)
        in_band=lo<=value<=hi
        slope_ok=(slope>=-0.20) if side=="BUY" else (slope<=0.20)
        structure=float(analysis.supports(side,rg))
        structure_ok=structure>=float(self.cfg.get("adaptive_rsi_shadow_min_structure",-12.0))
        key=self._key(s.symbol,rg,side); st=self.groups.get(key,{})
        n=int(st.get("samples",0)); expectancy=float(st.get("expectancy_r",0.0))
        learned="UNPROVEN"
        min_n=int(self.cfg.get("adaptive_rsi_shadow_min_outcomes",20))
        taken_n=int(st.get("taken_samples",0)); taken_exp=float(st.get("shadow_expectancy_r",0.0)); taken_wr=float(st.get("shadow_win_rate",0.0))
        if n>=min_n:
            # Judge the filter by trades it would actually have selected, not by every base trade.
            if taken_n>=max(8,min_n//2):
                learned="POSITIVE" if (taken_exp>0.08 and taken_wr>=52.0) else ("NEGATIVE" if (taken_exp<-0.08 or taken_wr<43.0) else "NEUTRAL")
            else: learned="UNPROVEN"
        would_take=bool(in_band and slope_ok and structure_ok and learned!="NEGATIVE")
        score=(40 if in_band else -25)+_clamp((slope if side=="BUY" else -slope)*8,-20,20)+_clamp(structure*.30,-25,25)
        if n: score+=_clamp(expectancy*20,-15,15)
        return {"mode":"SHADOW","would_take":would_take,"symbol":s.symbol,"side":side,"regime":rg,
                "rsi":value,"band_low":lo,"band_high":hi,"slope":slope,"structure":structure,
                "score":_clamp(score,-100,100),"group_samples":n,"group_expectancy_r":expectancy,
                "learned_state":learned,"taken_samples":taken_n,"taken_expectancy_r":taken_exp,"taken_win_rate":taken_wr,
                "reason":f"Adaptive RSI {value:.1f} vs {lo:.1f}-{hi:.1f} · slope {slope:+.2f} · structure {structure:+.0f} · {learned}"}
    def observe(self,prediction,r_multiple):
        if not prediction:return
        key=self._key(prediction["symbol"],prediction["regime"],prediction["side"])
        st=self.groups.setdefault(key,{"samples":0,"sum_r":0.0,"wins":0,"taken_samples":0,"taken_sum_r":0.0,"taken_wins":0})
        r=float(r_multiple); st["samples"]+=1; st["sum_r"]+=r
        if r>0:st["wins"]+=1
        if prediction.get("would_take"):
            st["taken_samples"]+=1; st["taken_sum_r"]+=r
            if r>0:st["taken_wins"]+=1
        st["expectancy_r"]=st["sum_r"]/st["samples"]; st["win_rate"]=100*st["wins"]/st["samples"]
        st["shadow_expectancy_r"]=st["taken_sum_r"]/st["taken_samples"] if st["taken_samples"] else 0.0
        st["shadow_win_rate"]=100*st["taken_wins"]/st["taken_samples"] if st["taken_samples"] else 0.0
        self.samples+=1; self._save()
    def summary(self):
        proven=sum(1 for x in self.groups.values() if int(x.get("samples",0))>=int(self.cfg.get("adaptive_rsi_shadow_min_outcomes",20)))
        return {"mode":"SHADOW","samples":self.samples,"groups":len(self.groups),"proven_groups":proven,"execution_influence":False}
    def _save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True); tmp=self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"samples":self.samples,"groups":self.groups},indent=2),encoding="utf-8"); tmp.replace(self.path)
    def _load(self):
        if not self.path.exists():return
        try:
            d=json.loads(self.path.read_text(encoding="utf-8")); self.samples=int(d.get("samples",0)); self.groups=dict(d.get("groups",{}))
        except Exception: pass
