"""Neural Edge Brain v1 -- PAPER shadow-only online learner.
Never authorizes, blocks, sizes, opens or closes a trade in v2.7.0.
"""
from __future__ import annotations
import json, math, random
from pathlib import Path

class NeuralEdgeBrain:
    FEATURES=12; HIDDEN=8
    def __init__(self,cfg,path="data/neural_edge_v1.json"):
        self.cfg=cfg; self.path=Path(path); self.lr=float(cfg.get("neural_edge_learning_rate",0.025))
        rnd=random.Random(27001)
        self.w1=[[rnd.uniform(-.12,.12) for _ in range(self.FEATURES)] for _ in range(self.HIDDEN)]
        self.b1=[0.0]*self.HIDDEN; self.w2=[rnd.uniform(-.12,.12) for _ in range(self.HIDDEN)]; self.b2=0.0
        self.samples=0; self.train_samples=0; self.validation_samples=0; self.validation_brier=0.0; self.expected_r_ema=0.0
        self.validation_wins=0; self.validation_logloss=0.0; self.calibration_ema=0.0
        self._load()

    def apply_config(self,cfg): self.cfg=cfg; self.lr=float(cfg.get("neural_edge_learning_rate",self.lr))
    @staticmethod
    def _sigmoid(x):
        x=max(-30,min(30,x)); return 1/(1+math.exp(-x))
    def features(self,s,d,regime):
        side=1.0 if d.action=="BUY" else -1.0
        ema_gap=(s.ema_fast-s.ema_slow)/max(abs(s.mid),1e-9)*1000.0
        rg={"TREND":1.0,"HIGH VOL":.6,"MIXED":0.0,"RANGE":-.5,"REVERSAL":-.8}.get(regime,0.0)
        sess={"London":.7,"London + New York":1.0,"New York":.6,"Asia":.2,"Rollover / Thin":-.8}.get(s.session,0.0)
        return [
            side,(float(d.score)-65)/25,(float(d.confidence)-65)/25,(float(s.rsi)-50)/25,
            max(-2,min(2,float(s.momentum)/2)),max(-2,min(2,ema_gap)),
            max(-2,min(2,(float(s.atr_pips)-15)/25)),max(-2,min(2,(float(s.spread_pips)-1)/2)),
            rg,sess,max(-1,min(1,(float(s.quality)-85)/15)),
            max(-1,min(1,float(d.target_pips)/max(float(d.stop_pips),1e-9)/2-0.75)),
        ]
    def _forward(self,x):
        h=[math.tanh(sum(w*v for w,v in zip(row,x))+b) for row,b in zip(self.w1,self.b1)]
        p=self._sigmoid(sum(w*v for w,v in zip(self.w2,h))+self.b2)
        return h,p
    def predict(self,s,d,regime):
        x=self.features(s,d,regime); _,p=self._forward(x)
        raw_p=p
        # Keep an online reliability shrinkage: immature or poorly calibrated models
        # are pulled toward 50% instead of presenting noisy confidence as edge.
        maturity=min(1.0,self.validation_samples/max(40.0,float(self.cfg.get("neural_edge_full_confidence_samples",120))))
        quality=max(0.0,1.0-min(1.0,self.validation_brier/0.25)) if self.validation_samples else 0.0
        reliability=maturity*(0.35+0.65*quality)
        p=.5+(p-.5)*reliability
        confidence=abs(p-.5)*200
        expected_r=(p-.5)*2*max(.25,abs(self.expected_r_ema) if self.samples>=10 else .55)
        return {"win_probability":p,"confidence":confidence,"expected_r":expected_r,"features":x,
                "samples":self.samples,"raw_win_probability":raw_p,"reliability":reliability,"mode":"SHADOW"}
    def observe(self,x,r_multiple,trade_id=""):
        y=1.0 if float(r_multiple)>0 else 0.0; h,p=self._forward(x); self.samples+=1
        # Deterministic 20% validation holdout: never train on these examples.
        holdout=(sum(ord(c) for c in str(trade_id)) % 5)==0
        if holdout:
            self.validation_samples+=1; err=(p-y)**2
            self.validation_brier += (err-self.validation_brier)/self.validation_samples
            self.validation_wins += int(y>0.5)
            eps=1e-9; ll=-(y*math.log(max(eps,p))+(1-y)*math.log(max(eps,1-p)))
            self.validation_logloss += (ll-self.validation_logloss)/self.validation_samples
            self.calibration_ema += ((y-p)-self.calibration_ema)/self.validation_samples
        else:
            self.train_samples+=1; dz=p-y
            old_w2=self.w2[:]
            for j in range(self.HIDDEN): self.w2[j]-=self.lr*dz*h[j]
            self.b2-=self.lr*dz
            for j in range(self.HIDDEN):
                dh=dz*old_w2[j]*(1-h[j]*h[j])
                for i in range(self.FEATURES): self.w1[j][i]-=self.lr*dh*x[i]
                self.b1[j]-=self.lr*dh
        alpha=.08; self.expected_r_ema=(1-alpha)*self.expected_r_ema+alpha*float(r_multiple)
        self._save()
        return {"holdout":holdout,"predicted":p,"actual":y}
    def summary(self):
        return {"mode":"SHADOW","samples":self.samples,"train_samples":self.train_samples,
                "validation_samples":self.validation_samples,"validation_brier":self.validation_brier,
                "expected_r_ema":self.expected_r_ema,"validation_win_rate":(100*self.validation_wins/self.validation_samples if self.validation_samples else 0.0),"validation_logloss":self.validation_logloss,"calibration_error":self.calibration_ema}
    def _save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        data={k:getattr(self,k) for k in ("w1","b1","w2","b2","samples","train_samples","validation_samples","validation_brier","expected_r_ema","validation_wins","validation_logloss","calibration_ema")}
        tmp=self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(data),encoding='utf-8'); tmp.replace(self.path)
    def _load(self):
        if not self.path.exists(): return
        try:
            d=json.loads(self.path.read_text(encoding='utf-8'))
            for k in ("w1","b1","w2","b2","samples","train_samples","validation_samples","validation_brier","expected_r_ema","validation_wins","validation_logloss","calibration_ema"):
                if k in d:setattr(self,k,d[k])
        except Exception: pass
