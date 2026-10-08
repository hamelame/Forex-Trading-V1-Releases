import uuid, random
from datetime import datetime, timezone, timedelta
from .models import Position, ClosedTrade
from .market import pip_size
from .multi_market_brain import MultiMarketResearchBrain, build_currency_strength
from .risk import RiskEngine
from .execution import ExecutionRealism
from .opportunity_selector import OpportunitySelector
from .intelligence import regime, market_rank, exposure_summary
from .self_learning import SelfLearningEngine
from .rsi_intelligence import analyze_snapshot
from .neural_edge import NeuralEdgeBrain
from .adaptive_rsi_shadow import AdaptiveRSIShadow
from .context_edge import session_policy, entry_location_context, synthetic_market_mode

class TradingEngine:
    def __init__(self,cfg,feed,db):
        self.cfg=cfg; self.feed=feed; self.db=db
        # Every application launch begins a fresh visible PAPER session.
        # Historical closed trades stay in the database for future AI research.
        self.session_started_at=datetime.now(timezone.utc).isoformat()
        self.balance=float(cfg.get("paper_trading_capital",cfg.get("starting_balance",20000.0)))
        self.equity=self.balance
        self.positions=[]
        self.db.clear_open_positions()
        self.snapshots={}; self.decisions={}
        self.risk=RiskEngine(cfg)
        self.selector=OpportunitySelector(cfg)
        self.learning=SelfLearningEngine(cfg,db)
        self.execution=ExecutionRealism(cfg)
        self.neural=NeuralEdgeBrain(cfg)
        self.adaptive_rsi=AdaptiveRSIShadow(cfg)
        self.neural_predictions={}
        self.entry_context={}
        self.position_excursions={}
        self.reversal_votes={}
        # v2.9.2 exit-policy challengers run in SHADOW only. They never alter
        # the production stop/target until forward evidence proves an improvement.
        self.exit_shadows={}
        self.execution_starvation_scans=0
        self.decision_starvation_scans=0
        self.opportunity_recovery_active=False
        self.last_open_scan=0
        self.last_gate_reasons={}
        self.symbol_loss_streaks={}
        self.symbol_loss_cooldown_until={}
        self.last_any_entry_scan=-10**9
        # v2.9.0 candle-aware timebase. Engine scans may run every few seconds,
        # but the live strategy consumes 1-minute candles. Decisions, hold-time
        # bars and reversal confirmations must therefore advance on new CLOSED
        # signal candles, not on UI/HTTP refresh loops.
        self.last_candle_ts={}
        self.symbol_candle_seq={}
        self.last_exit_candle_seq={}
        self.decision_cycle_count=0
        # Fresh paper session gets fresh circuit-breaker baselines.
        self.risk.reset_session(self.balance)
        self.champion=MultiMarketResearchBrain("Multi-Market Research Brain v2",cfg["min_signal_score"],cfg)
        # V2.1 intentionally runs one Champion AI module only.
        self.challengers=[]
        self.shadow_stats={}
        self.last_status="AI paused · press START AI"
        self.enabled=False; self.rankings={}; self.regimes={}; self.scan_count=0
        self.last_exit_scan={}
        self.db.equity(self.session_started_at,self.balance,self.equity)

    def reset_paper_session(self, capital=None):
        cap=float(capital if capital is not None else self.cfg.get("paper_trading_capital",self.cfg.get("starting_balance",20000.0)))
        cap=max(1000.0,min(100000.0,cap))
        self.cfg["paper_trading_capital"]=cap
        self.cfg["starting_balance"]=cap
        self.session_started_at=datetime.now(timezone.utc).isoformat()
        self.balance=cap
        self.equity=cap
        self.positions=[]
        self.db.clear_open_positions()
        self.risk.reset_session(cap)
        self.entry_context={}
        self.position_excursions={}
        self.reversal_votes={}
        self.exit_shadows={}
        self.learning.reset_runtime_state()
        self.execution_starvation_scans=0
        self.decision_starvation_scans=0
        self.opportunity_recovery_active=False
        self.last_open_scan=0
        self.last_gate_reasons={}
        self.learning.pending_counterfactuals={}
        self.last_exit_scan={}
        self.last_candle_ts={}
        self.symbol_candle_seq={}
        self.last_exit_candle_seq={}
        self.decision_cycle_count=0
        self.scan_count=0
        self.selector.force_reselect()
        self.db.equity(self.session_started_at,self.balance,self.equity)
        self.last_status=f"New paper session · ${cap:,.0f} · balance/P&L/risk state reset"

    def apply_paper_capital(self, capital, start_new_session=False):
        cap=max(1000.0,min(100000.0,float(capital)))
        self.cfg["paper_trading_capital"]=cap
        self.cfg["starting_balance"]=cap
        self.risk.cfg=self.cfg
        if not start_new_session:
            self.last_status=f"Paper capital ${cap:,.0f} saved for next session"
            return False

        was_enabled=self.enabled
        self.reset_paper_session(cap)
        self.enabled=was_enabled
        # Rebuild market/selector state immediately. This never opens entries;
        # an active AI will execute normally on the next scan.
        self.refresh_market_state(force_reselect=True)
        self.enabled=was_enabled
        self.last_status=(
            f"New paper session ready · ${cap:,.0f} · "
            f"{'AI active' if was_enabled else 'AI paused'}"
        )
        return True

    def set_enabled(self, value, reset_on_start=True):
        value=bool(value)
        if value and not self.enabled and reset_on_start:
            self.reset_paper_session()
        self.enabled=value
        self.last_status="AI started · new paper session" if self.enabled else "AI paused"

    def apply_config(self, cfg, force_reselect=False):
        self.cfg=cfg
        self.risk.cfg=cfg
        self.champion.apply_config(cfg)
        self.selector.apply_config(cfg)
        self.learning.apply_config(cfg)
        self.execution.apply_config(cfg)
        self.neural.apply_config(cfg)
        self.adaptive_rsi.apply_config(cfg)
        if force_reselect:
            self.selector.force_reselect()
        self.last_status="Settings applied"

    def _register_candle_updates(self,fresh):
        """Return symbols whose CLOSED signal candle changed this scan.

        LiveMarketFeed timestamps snapshots with the most recent completed 1m
        candle. Repeated 2-second scans of the same candle update quotes/equity,
        but must not be counted as new evidence or new holding bars.
        """
        new=set()
        for symbol,s in fresh.items():
            ts=str(getattr(s,"timestamp","") or "")
            if not ts:
                continue
            if self.last_candle_ts.get(symbol)!=ts:
                self.last_candle_ts[symbol]=ts
                self.symbol_candle_seq[symbol]=int(self.symbol_candle_seq.get(symbol,0))+1
                new.add(symbol)
        if new:
            self.decision_cycle_count+=1
        return new

    def top_markets(self, n=None):
        n=n or self.cfg.get("market_scan_top_n",12)
        selected=list(getattr(getattr(self,"selector",None),"shortlist",[]) or [])
        if selected:
            return selected[:n]
        return sorted(self.rankings,key=self.rankings.get,reverse=True)[:n]

    def exposure_summary(self): return exposure_summary(self.positions)

    def unrealized_pnl(self):
        return sum(float(p.unrealized) for p in self.positions)


    def selection_summary(self):
        st=getattr(self.selector,"stats",{})
        return {
            "mode":self.cfg.get("selection_mode","AUTO TOP 10"),
            "shortlist":list(getattr(self.selector,"shortlist",[])),
            "eligible":list(getattr(self.selector,"eligible",[])),
            "scanned":int(st.get("scanned",0)),
            "eligible_count":int(st.get("eligible",0)),
            "selected_count":int(st.get("selected",0)),
            "trade_ready":int(st.get("trade_ready",0)),
        }


    def strength_summary(self):
        s=getattr(self,"currency_strength",{}) or {}
        if not s:return "Collecting cross-pair data"
        ordered=sorted(s.items(),key=lambda x:x[1],reverse=True)
        strong=" · ".join(f"{c} {v:+.0f}" for c,v in ordered[:2])
        weak=" · ".join(f"{c} {v:+.0f}" for c,v in ordered[-2:])
        return f"Strong: {strong}  |  Weak: {weak}"

    def _risk_multiplier(self,d,s,effective_confidence=None,directional_health=None):
        if self.cfg.get("trading_profile","AI TRADING")=="MANUAL":
            return 1.0
        r=self.regimes.get(s.symbol,regime(s))
        mult=1.0
        # v2.9.2.4 forward calibration repair.  Across 721 PAPER trades the
        # previous monotonic sizing rule did the opposite of what we intended:
        # >=70 effective-confidence trades were persistently weaker while the
        # sub-70 group was materially better.  Confidence is therefore a ranking
        # score, not a license to size up, until its own bucket proves positive.
        sizing_conf=float(d.confidence if effective_confidence is None else effective_confidence)
        if sizing_conf < 60:
            mult*=float(self.cfg.get("confidence_low_risk_mult",0.65))
        elif sizing_conf < float(self.cfg.get("unproven_high_confidence_threshold",70.0)):
            mult*=float(self.cfg.get("confidence_mid_risk_mult",0.82))
        else:
            cal=self.learning.effective_confidence_calibration(s.symbol,r,d.action,sizing_conf)
            cal_min=int(self.cfg.get("confidence_quality_min_samples",24))
            if cal.get("samples",0) >= cal_min and float(cal.get("expectancy_r",0.0))>0 and float(cal.get("pf",1.0))>=1.05:
                mult*=float(self.cfg.get("proven_high_confidence_risk_mult",0.88))
            elif cal.get("samples",0) >= cal_min:
                mult*=float(self.cfg.get("negative_high_confidence_risk_mult",0.28))
            else:
                mult*=float(self.cfg.get("unproven_high_confidence_risk_mult",0.48))
        if r=="HIGH VOL": mult*=float(self.cfg.get("high_vol_risk_multiplier",0.40))
        elif r=="MIXED": mult*=0.80
        session_ctx=session_policy(s.symbol,s.session,self.cfg)
        if not synthetic_market_mode(self.cfg):
            mult*=float(session_ctx.get("risk_mult",1.0))
        # v2.9.2.5 dynamic session health. Fixed clock-time priors are no longer
        # trusted to remain good forever; recent PAPER evidence may shrink risk
        # when this asset/session/regime has deteriorated. Positive evidence does
        # not create a signal and does not by itself lever the trade up.
        session_health=self.learning.session_health(s.symbol,r,d.action,s.session)
        mult*=min(1.0,float(session_health.get("risk_mult",1.0)))
        if s.spread_pips>1.4: mult*=0.75
        from .instruments import instrument_meta
        asset=instrument_meta(s.symbol)["asset_class"]
        if asset=="CRYPTO": mult*=0.72
        elif asset=="ENERGY": mult*=0.82
        elif asset=="METALS": mult*=0.90

        # v2.9.2: context health is hierarchical and conservative. Positive
        # history does not boost size; negative/drifting history can only shrink it.
        location_ctx=entry_location_context(s,d.action,r,self.cfg)
        health=self.learning.context_health(
            s.symbol,r,d.action,s.session,location_ctx.get("location","UNKNOWN")
        )
        mult*=min(1.0,float(health.get("risk_mult",1.0)))
        # v2.9.2.6: BUY/SELL asymmetry is learned dynamically.  This layer can
        # only reduce risk; a strong SELL run never forces the engine to SELL and
        # a weak BUY run is allowed to recover as fresh evidence changes.
        if directional_health is None:
            directional_health=self.learning.directional_health(
                s.symbol,r,d.action,s.session,location_ctx.get("location","UNKNOWN")
            )
        mult*=min(1.0,float(directional_health.get("risk_mult",1.0)))
        runtime=self.learning.runtime_context_guard(
            s.symbol,r,d.action,s.session,self.decision_cycle_count
        )
        mult*=min(1.0,float(runtime.get("risk_mult",1.0)))
        # CLEAR means "no short-term alarm", not "proven edge".  Forward logs
        # showed CLEAR losing while WATCH (already risk-reduced) held up better.
        # Keep unproven CLEAR contexts conservative until Context Health is truly
        # HEALTHY under forward-validation criteria.
        if runtime.get("state")=="CLEAR" and health.get("state")!="HEALTHY":
            mult*=float(self.cfg.get("runtime_clear_unproven_risk_mult",0.72))

        if "OPPORTUNITY RECOVERY" in str(getattr(d,"reason","")):
            mult*=float(self.cfg.get("opportunity_recovery_risk_multiplier",0.35))

        # Expectancy Governor controls how much production risk this exact
        # context has earned. Unknown contexts bootstrap small; proven contexts
        # can earn full risk; negative contexts never reach _open().
        gov_mult=self.learning.governor_risk_multiplier(
            s.symbol,r,d.action,s.session
        )
        mult*=gov_mult

        # RSI-pattern evidence is learned separately. A pattern can earn reduced
        # risk or be quarantined without hard-coding that it always works.
        rsi_a=analyze_snapshot(s)
        rsi_gov=self.learning.rsi_pattern_governor(
            s.symbol,r,d.action,rsi_a.tags
        )
        mult*=float(rsi_gov.get("risk_mult",1.0))

        # Rolling global expectancy is a second capital-preservation layer. It
        # reduces size during a broadly losing run without stopping research.
        global_mult=float(self.learning.global_risk_multiplier())
        mult*=global_mult

        # v2.9.0 Evidence-First quality layer. Raw confidence is not treated as
        # probability until PAPER outcomes calibrate it. Broad side/regime
        # failures may only reduce risk here; hard vetoes are enforced in _open().
        quality=self.learning.decision_quality_assessment(
            s.symbol,r,d.action,s.session,d.confidence
        )
        mult*=float(quality.get("risk_mult",1.0))

        # v2.8.7: allocate more PAPER risk only to contexts that have earned it.
        # Unknown/negative contexts never get a size boost; positive empirical
        # edge can earn a modest increase while portfolio risk caps remain hard.
        edge=self.learning.execution_edge_score(s.symbol,r,d.action,s.session)
        if (edge.get("label")=="POSITIVE"
                and health.get("state")=="HEALTHY"
                and int(health.get("samples",0))>=int(self.cfg.get("context_health_size_boost_min_samples",16))):
            mult*=1.0+min(float(self.cfg.get("max_empirical_edge_size_boost",0.35)),
                          max(0.0,float(edge.get("score",0.0)))*float(self.cfg.get("empirical_edge_size_boost",0.35)))
        elif edge.get("label")=="NEGATIVE":
            mult*=float(self.cfg.get("negative_edge_risk_multiplier",0.35))
        max_mult=float(self.cfg.get("max_risk_multiplier",1.35))
        return max(float(self.cfg.get("min_risk_multiplier",0.05)),min(max_mult,mult))

    def _equity_sizing_plan(self,d,s,rg,effective_confidence,context_health,runtime_guard,directional_health=None):
        """Evidence-aware capital sizing without relaxing safety gates.

        The old engine already sized as a percentage of balance, but multiple
        conservative research multipliers could compound into only a few dollars
        of risk on a $20k account.  v2.9.2.5 keeps those protections and adds a
        small *effective risk floor* only while no negative/protective state is
        active.  Better evidence can earn a modestly higher floor; drawdown,
        WATCH/circuit/recovery or weak session evidence immediately remove it.
        """
        basis=min(float(self.balance),float(self.equity)) if self.cfg.get("equity_sizing_conservative_basis",True) else float(self.equity)
        basis=max(1.0,basis)
        base={
            "state":"LEGACY","capital_basis":basis,"floor_pct":0.0,
            "cap_pct":float(self.cfg.get("equity_sizing_max_trade_risk_pct",0.18)),
            "session_health":{"state":"DISABLED","risk_mult":1.0,"samples":0,"score":0.0},
            "directional_health":{"state":"LEARNING","risk_mult":1.0,"samples":0,"score":0.0,"source":"HIERARCHY"},
            "confidence_edge":{"samples":0,"expectancy_r":0.0,"pf":1.0},
        }
        if not self.cfg.get("equity_scaled_sizing_enabled",True):
            return base

        sh=self.learning.session_health(s.symbol,rg,d.action,s.session)
        if directional_health is None:
            loc=entry_location_context(s,d.action,rg,self.cfg)
            directional_health=self.learning.directional_health(
                s.symbol,rg,d.action,s.session,loc.get("location","UNKNOWN")
            )
        cal=self.learning.effective_confidence_calibration(
            s.symbol,rg,d.action,float(effective_confidence)
        )
        global_st=self.learning.global_expectancy_stats()
        base["session_health"]=sh; base["directional_health"]=directional_health; base["confidence_edge"]=cal

        recovery_state,_=self.risk.recovery_state(self.decision_cycle_count)
        protective=(
            recovery_state!="NORMAL"
            or context_health.get("state") in ("WATCH","BLOCKED")
            or runtime_guard.get("state") in ("WATCH","PAUSED")
            or sh.get("state")=="WATCH"
            or directional_health.get("state") in ("WATCH","SEVERE")
        )
        if protective:
            base["state"]="PROTECTIVE"
            return base

        # New/neutral contexts still receive a small account-equity floor. At
        # 0.06% this is $12 on a $20k account: materially larger than the old
        # few-dollar research sizes but still far below the 0.4% base risk cap.
        floor=float(self.cfg.get("equity_sizing_learning_floor_pct",0.06))
        state="EQUITY_BASE"

        cal_n=int(cal.get("samples",0))
        cal_good=(cal_n>=int(self.cfg.get("equity_sizing_confidence_min_samples",24))
                  and float(cal.get("expectancy_r",0.0))>=float(self.cfg.get("equity_sizing_confidence_min_expectancy_r",0.04))
                  and float(cal.get("pf",1.0))>=float(self.cfg.get("equity_sizing_confidence_min_pf",1.08)))
        global_good=(int(global_st.get("samples",0))>=int(self.cfg.get("equity_sizing_global_min_samples",24))
                     and float(global_st.get("expectancy_r",0.0))>=float(self.cfg.get("equity_sizing_global_min_expectancy_r",0.02))
                     and float(global_st.get("pf",1.0))>=float(self.cfg.get("equity_sizing_global_min_pf",1.03)))
        session_good=(sh.get("state")=="POSITIVE")

        if cal_good and global_good and session_good:
            floor=max(floor,float(self.cfg.get("equity_sizing_proven_floor_pct",0.10)))
            state="PROVEN_EDGE"
        if (state=="PROVEN_EDGE" and context_health.get("state")=="HEALTHY"
                and int(context_health.get("samples",0))>=int(self.cfg.get("equity_sizing_context_min_samples",24))):
            floor=max(floor,float(self.cfg.get("equity_sizing_healthy_floor_pct",0.12)))
            state="HEALTHY_EDGE"

        # Never let the evidence floor exceed the explicit single-trade cap.
        cap=max(0.01,float(base["cap_pct"]))
        base.update({"state":state,"floor_pct":min(floor,cap),"cap_pct":cap})
        return base

    def _research_decision(self,s,liveness_relax=0.0):
        d=self.champion.decide(s,self.currency_strength,liveness_relax=liveness_relax)
        rg=regime(s)
        d=self.learning.adjust_decision(d,s,rg)
        return d,rg

    def learning_summary(self):
        out=self.learning.summary()
        state,remaining=self.risk.recovery_state(self.decision_cycle_count)
        out["risk_state"]=state
        out["recovery_scans_remaining"]=remaining  # backward-compatible UI key
        out["recovery_candle_cycles_remaining"]=remaining
        out["recovery_risk_multiplier"]=self.risk.recovery_risk_multiplier()
        out["drawdown_mode"]=getattr(self.risk,"drawdown_mode","NORMAL")
        out["drawdown_pct"]=float(getattr(self.risk,"last_drawdown_pct",0.0))
        out["loss_limit_mode"]=getattr(self.risk,"limit_mode","NORMAL")
        out["day_loss_pct"]=float(getattr(self.risk,"last_day_loss_pct",0.0))
        out["week_loss_pct"]=float(getattr(self.risk,"last_week_loss_pct",0.0))
        out["execution_starvation_scans"]=int(getattr(self,"execution_starvation_scans",0))
        out["decision_starvation_scans"]=int(getattr(self,"decision_starvation_scans",0))
        out["opportunity_recovery_active"]=bool(getattr(self,"opportunity_recovery_active",False))
        out["decision_cycle_count"]=int(getattr(self,"decision_cycle_count",0))
        out["candle_aware_execution"]=True
        out["neural_edge"]=self.neural.summary()
        out["adaptive_rsi_shadow"]=self.adaptive_rsi.summary()
        return out

    def refresh_market_state(self, force_reselect=False):
        """Refresh quotes, AI decisions, rankings and selector without opening trades."""
        self.scan_count+=1
        advance=getattr(self.feed,"advance",None)
        if callable(advance):
            advance()

        fresh={}
        for symbol in self.cfg["symbols"]:
            try:
                fresh[symbol]=self.feed.snapshot(symbol)
            except Exception as exc:
                self.last_status=f"{symbol}: data refresh error: {exc}"
        if not fresh:
            return

        self.snapshots.update(fresh)
        new_candles=self._register_candle_updates(fresh)
        self.currency_strength=build_currency_strength(fresh)
        batch=[]
        for symbol in new_candles:
            s=fresh[symbol]
            try:
                d,rg=self._research_decision(s)
                if d.action in ("BUY","SELL") and self.cfg.get("neural_edge_enabled",True):
                    self.neural_predictions[symbol]=self.neural.predict(s,d,rg)
                self.decisions[symbol]=d
                self.regimes[symbol]=rg
                self.rankings[symbol]=market_rank(s,d)
                batch.append(d)
            except Exception as exc:
                self.last_status=f"{symbol}: decision refresh error: {exc}"

        self.db.decisions_batch(batch)
        if force_reselect:
            self.selector.force_reselect()
        self.selector.select(self.decision_cycle_count,fresh,self.decisions,self.rankings,self.regimes)
        self._mark_and_close(new_candles)
        if new_candles:
            self.last_status=(
                f"Market refreshed · {len(new_candles)} new closed candle(s) · "
                f"{self.selector.stats['eligible']} eligible · {self.selector.stats['selected']} selected"
            )

    def scan(self):
        if not self.enabled:
            # PAUSED means no new entries, but quotes/positions still update.
            self.refresh_market_state(force_reselect=False)
            return
        self.scan_count+=1
        # Advance all recoverable risk lifecycles once per active engine scan,
        # independent of whether this scan produces an executable BUY/SELL candidate.
        self.risk.sanitize_recovery_state(self.decision_cycle_count)
        limit_mode=self.risk.update_loss_limit_state(self.balance,self.decision_cycle_count)
        dd_mode=self.risk.update_drawdown_state(self.balance,self.decision_cycle_count)
        if dd_mode=="COOLDOWN":
            _,remaining=self.risk.recovery_state(self.decision_cycle_count)
            self.last_status=(
                f"Drawdown protection cooldown · {self.risk.last_drawdown_pct:.2f}% · "
                f"{remaining} closed-candle cycles remaining"
            )
        elif dd_mode=="RECOVERY":
            self.last_status=(
                f"Drawdown recovery active · "
                f"{self.risk.recovery_risk_multiplier():.2f}x entry risk"
            )
        elif dd_mode=="EMERGENCY":
            self.last_status=(
                f"Emergency drawdown hard stop · {self.risk.last_drawdown_pct:.2f}%"
            )
        elif limit_mode=="DEEP_COOLDOWN":
            _,remaining=self.risk.recovery_state(self.decision_cycle_count)
            self.last_status=(
                f"DEEP RECOVERY COOLDOWN · {remaining} closed-candle cycles remaining · "
                f"{self.risk.limit_trigger_reason}"
            )
        elif limit_mode=="DEEP_RECOVERY":
            self.last_status=(
                f"DEEP RECOVERY ACTIVE · "
                f"{self.risk.recovery_risk_multiplier():.2f}x entry risk"
            )
        elif limit_mode=="COOLDOWN":
            _,remaining=self.risk.recovery_state(self.decision_cycle_count)
            self.last_status=(
                f"Daily/weekly protection cooldown · {remaining} closed-candle cycles remaining · "
                f"{self.risk.limit_trigger_reason}"
            )
        elif limit_mode=="RECOVERY":
            self.last_status=(
                f"Daily/weekly recovery active · "
                f"{self.risk.recovery_risk_multiplier():.2f}x entry risk"
            )
        elif limit_mode=="EMERGENCY":
            self.last_status=(
                f"Emergency session-loss hard stop · {self.risk.limit_trigger_reason}"
            )

        advance=getattr(self.feed,"advance",None)
        if callable(advance):
            advance()

        # Phase 1: snapshot the whole enabled multi-market universe first.
        fresh={}
        for symbol in self.cfg["symbols"]:
            try:
                fresh[symbol]=self.feed.snapshot(symbol)
            except Exception as e:
                self.last_status=f"{symbol}: data error: {e}"
        if not fresh:
            return
        self.snapshots.update(fresh)
        new_candles=self._register_candle_updates(fresh)
        self.learning.resolve_counterfactuals(self.snapshots,self.scan_count)

        # Repeated refresh of the same closed 1m candle is quote maintenance, not
        # new predictive evidence. Mark P/L, but do not re-decide/re-enter or age
        # positions by another fake "bar".
        if not new_candles:
            self._mark_and_close(set())
            self.db.equity(datetime.now(timezone.utc).isoformat(),self.balance,self.equity)
            return

        # Phase 2: calculate cross-sectional currency strength before decisions.
        self.currency_strength=build_currency_strength(fresh)

        # Phase 3: one Multi-Market Champion AI + independent shadow research specialists.
        batch=[]
        pending_entries=[]
        for symbol in new_candles:
            s=fresh[symbol]
            try:
                d,rg=self._research_decision(s)
                if d.action in ("BUY","SELL") and self.cfg.get("neural_edge_enabled",True):
                    self.neural_predictions[symbol]=self.neural.predict(s,d,rg)
                self.decisions[symbol]=d
                self.regimes[symbol]=rg
                self.rankings[symbol]=market_rank(s,d)
                batch.append(d)

                if d.action in ("BUY","SELL"):
                    pending_entries.append((self.rankings[symbol],d,s))
            except Exception as e:
                self.last_status=f"{symbol}: decision error: {e}"

        governor_waits=sum(
            1 for d in self.decisions.values()
            if d.action=="WAIT" and "GOVERNOR RESEARCH-ONLY" in str(d.reason)
        )
        if not pending_entries and governor_waits:
            self.last_status=(
                f"EXPECTANCY GOVERNOR · {governor_waits} research-only context(s) · "
                f"scanning for proven/probation edge"
            )

        # Decision-layer liveness guard.
        # If the portfolio has capacity but the Champion has produced no executable
        # BUY/SELL for a sustained period, run one bounded PAPER-only re-evaluation.
        # Hard BLOCK conditions and RiskEngine protections are never relaxed.
        capacity=len(self.positions)<int(self.cfg.get("max_open_positions",5))
        rstate,_=self.risk.recovery_state(self.decision_cycle_count)
        if capacity and not pending_entries and rstate not in ("COOLDOWN","EMERGENCY"):
            self.decision_starvation_scans+=1
        else:
            self.decision_starvation_scans=0
            self.opportunity_recovery_active=False

        recovery_after=max(3,int(self.cfg.get("opportunity_recovery_after_candles", self.cfg.get("opportunity_recovery_after_scans",45))))
        recovery_quality_ok,recovery_quality_reason=self.learning.opportunity_recovery_allowed()
        if (capacity and not pending_entries
                and self.decision_starvation_scans>=recovery_after
                and rstate not in ("COOLDOWN","EMERGENCY")
                and self.cfg.get("opportunity_recovery_enabled",True)
                and recovery_quality_ok):
            recovery_batch=[]
            recovery_entries=[]
            for symbol in new_candles:
                s=fresh[symbol]
                try:
                    d,rg=self._research_decision(s,liveness_relax=1.0)
                    self.decisions[symbol]=d
                    self.regimes[symbol]=rg
                    self.rankings[symbol]=market_rank(s,d)
                    recovery_batch.append(d)
                    if d.action in ("BUY","SELL"):
                        recovery_entries.append((self.rankings[symbol],d,s))
                except Exception as exc:
                    self.last_status=f"{symbol}: opportunity recovery error: {exc}"
            if recovery_batch:
                batch=recovery_batch
            if recovery_entries:
                pending_entries=recovery_entries
                self.opportunity_recovery_active=True
                self.selector.force_reselect()
                self.last_status=(
                    f"OPPORTUNITY RECOVERY · {self.decision_starvation_scans} closed-candle cycles without entry · "
                    f"{len(recovery_entries)} bounded recovery candidate(s)"
                )
        elif (capacity and not pending_entries
                and self.decision_starvation_scans>=recovery_after
                and self.cfg.get("opportunity_recovery_enabled",True)
                and not recovery_quality_ok):
            # Research finding: trade starvation is not itself a failure. If
            # rolling PAPER expectancy is unhealthy, NO TRADE is safer than
            # relaxing the decision thresholds just to keep the engine busy.
            self.opportunity_recovery_active=False
            self.last_status=f"NO TRADE · {recovery_quality_reason}"

        # One DB transaction per scan instead of one commit per instrument.
        self.db.decisions_batch(batch)

        # Selection Engine: all markets are analysed, but only Top 5 / Top 10
        # (or the user's MANUAL shortlist) may reach execution.
        shortlist=self.selector.select(self.decision_cycle_count,fresh,self.decisions,self.rankings,self.regimes)
        allowed=set(shortlist)
        pending_entries=[x for x in pending_entries if x[1].symbol in allowed]

        if not pending_entries:
            st=self.selector.stats
            self.last_status=(
                f"NO TRADE · {st['scanned']} scanned · {st['eligible']} eligible · "
                f"{st['selected']} selected · {st['trade_ready']} trade-ready"
            )

        # Selected opportunities get risk-gated first.
        opened_before=len(self.positions)
        executable_candidates=len(pending_entries)
        gate_reasons={}

        def execution_priority(item):
            raw_rank,d,s=item
            regime=self.regimes.get(s.symbol,"MIXED")
            gov=self.learning.expectancy_governor(s.symbol,regime,d.action,s.session)
            st=self.learning.stats_for(s.symbol,regime,d.action,s.session)
            quality=float(raw_rank)
            quality += {"PROVEN":18.0,"PROBATION":5.0,"BOOTSTRAP":0.0}.get(gov.get("state","BOOTSTRAP"),0.0)
            # v2.8.5: rank by observed PAPER edge instead of assuming TREND is
            # inherently superior. The 580-trade review showed that assumption
            # can be wrong for a particular run/asset/side.
            edge=self.learning.execution_edge_score(s.symbol,regime,d.action,s.session)
            quality += float(edge.get("score",0.0))*float(self.cfg.get("execution_empirical_edge_weight",18.0))
            # v2.9 records confidence calibration in SHADOW first. The initial
            # development replay did not justify letting it reorder execution, so
            # promotion is an explicit future switch after forward validation.
            if self.cfg.get("calibrated_confidence_ranking_enabled",False):
                cal=self.learning.confidence_calibration(s.symbol,regime,d.action,d.confidence)
                if cal.get("samples",0)>=int(self.cfg.get("confidence_quality_min_samples",24)):
                    quality += (float(cal.get("calibrated_probability",0.5))-.5)*float(self.cfg.get("calibrated_probability_rank_weight",28.0))
                    quality -= float(cal.get("overconfidence_gap",0.0))*float(self.cfg.get("overconfidence_rank_penalty",22.0))
            if regime in ("REVERSAL","RANGE") and edge.get("label")!="POSITIVE":
                quality -= float(self.cfg.get("execution_weak_regime_penalty",8.0))
            if st and st.get("samples",0)>=8:
                quality += max(-10.0,min(10.0,float(st.get("expectancy_r",0))*20.0))
                quality += max(-6.0,min(6.0,(float(st.get("pf",1.0))-1.0)*8.0))
            loc=entry_location_context(s,d.action,regime,self.cfg)
            health=self.learning.context_health(s.symbol,regime,d.action,s.session,loc.get("location","UNKNOWN"))
            quality += float(health.get("score",0.0))*float(self.cfg.get("context_health_rank_weight",12.0))
            runtime=self.learning.runtime_context_guard(s.symbol,regime,d.action,s.session,self.decision_cycle_count)
            if runtime.get("state")=="WATCH":
                quality -= float(self.cfg.get("context_runtime_watch_rank_penalty",8.0))
            elif runtime.get("state")=="PAUSED":
                quality -= 1000.0
            return quality

        pending_entries=sorted(pending_entries,key=execution_priority,reverse=True)
        # UI may request STOP while a background scan is already analysing the
        # market. Honor that request before any new PAPER entry is opened.
        if not self.enabled:
            pending_entries=[]
            self.last_status="AI paused · scan completed without new entries"
        for _,d,s in pending_entries:
            if not self.enabled:
                break
            loss_until=int(self.symbol_loss_cooldown_until.get(s.symbol,0))
            if self.decision_cycle_count<loss_until:
                why=f"Adaptive loss cooldown · {loss_until-self.decision_cycle_count} closed-candle cycle(s) remaining."
                gate_reasons[why]=gate_reasons.get(why,0)+1
                self.last_status=f"{s.symbol}: {why}"
                continue
            min_gap=max(0,int(self.cfg.get("global_entry_pacing_scans",2)))
            if self.decision_cycle_count-int(getattr(self,"last_any_entry_scan",-10**9))<min_gap:
                why="Global entry pacing active."
                gate_reasons[why]=gate_reasons.get(why,0)+1
                continue
            last_exit_bar=self.last_exit_candle_seq.get(s.symbol,-10**9)
            current_bar=int(self.symbol_candle_seq.get(s.symbol,0))
            cooldown_bars=max(0,int(self.cfg.get("entry_cooldown_bars",3)))
            if current_bar-last_exit_bar<cooldown_bars:
                why=f"Re-entry cooldown active · {cooldown_bars-(current_bar-last_exit_bar)} candle(s) remaining."
                gate_reasons[why]=gate_reasons.get(why,0)+1
                self.last_status=f"{s.symbol}: {why}"
                continue
            regime=self.regimes.get(s.symbol,"MIXED")
            gov=self.learning.expectancy_governor(s.symbol,regime,d.action,s.session)
            weak_open=sum(1 for p in self.positions if self.regimes.get(p.symbol,"MIXED") in ("REVERSAL","RANGE"))
            if regime in ("REVERSAL","RANGE") and gov.get("state")!="PROVEN":
                weak_cap=int(self.cfg.get("max_unproven_weak_regime_positions",1))
                if weak_open>=weak_cap:
                    why="Edge allocation gate · weak unproven regime capacity full."
                    gate_reasons[why]=gate_reasons.get(why,0)+1
                    self.learning.observe_blocked(d,s,regime,why,self.scan_count)
                    continue

            ok,why=self.risk.gate(self.balance,self.positions,d,s,self.decision_cycle_count)
            if ok:
                before=len(self.positions)
                self.last_open_block_reason=""
                self._open(d,s)
                if len(self.positions)>before:
                    self.last_open_scan=self.decision_cycle_count
                    self.last_any_entry_scan=self.decision_cycle_count
                    self.execution_starvation_scans=0
                    self.decision_starvation_scans=0
                    self.opportunity_recovery_active=False
                else:
                    # _open has additional execution gates (freshness, RSI, sizing,
                    # cost/fill). Surface them to the watchdog instead of reporting
                    # the misleading "no gate reason".
                    open_why=str(getattr(self,"last_open_block_reason","") or self.last_status or "Execution gate blocked entry")
                    gate_reasons[open_why]=gate_reasons.get(open_why,0)+1
            else:
                gate_reasons[why]=gate_reasons.get(why,0)+1
                self.learning.observe_blocked(d,s,self.regimes.get(s.symbol,"MIXED"),why,self.scan_count)
                self.last_status=f"{s.symbol}: {why}"

        opened_now=len(self.positions)>opened_before
        if executable_candidates>0 and not opened_now and len(self.positions)<int(self.cfg.get("max_open_positions",5)):
            self.execution_starvation_scans+=1
        else:
            self.execution_starvation_scans=0
        self.last_gate_reasons=gate_reasons

        # Liveness watchdog: recoverable states may never silently deadlock.
        # It never bypasses EMERGENCY or explicit user-disabled trading.
        watchdog=int(self.cfg.get("execution_starvation_watchdog_scans",90))
        if self.execution_starvation_scans>=watchdog:
            rstate,_=self.risk.recovery_state(self.decision_cycle_count)
            if rstate!="EMERGENCY":
                self.risk.sanitize_recovery_state(self.decision_cycle_count)
                # Force selector turnover as well, in case a stale shortlist and
                # a stale recovery state happen at the same time.
                self.selector.force_reselect()
                top_reason=max(gate_reasons,key=gate_reasons.get) if gate_reasons else "no gate reason"
                self.last_status=(
                    f"EXECUTION WATCHDOG · {self.execution_starvation_scans} closed-candle cycles without a new position · "
                    f"rechecking recoverable gates · top reason: {top_reason}"
                )
                # Do not reset the counter to zero; keep visibility until an
                # actual position is opened or no executable candidate exists.

        self._mark_and_close(new_candles)
        self.db.equity(datetime.now(timezone.utc).isoformat(),self.balance,self.equity)

    def _directional_setup_gate(self,d,s):
        """Production validator using RSI structure + price structure.

        RSI extremes alone are never enough. TREND uses RSI as continuation/context;
        REVERSAL requires structural confirmation; RANGE requires a turn from an
        extreme rather than merely being at an extreme.
        """
        if not self.cfg.get("directional_setup_gate_enabled",True):
            return True,"Directional setup gate disabled."

        rg=self.regimes.get(s.symbol,regime(s))
        side=d.action
        ema_up=s.ema_fast>s.ema_slow
        mom=float(s.momentum)
        a=analyze_snapshot(s)
        support=a.supports(side,rg)

        if rg=="TREND":
            min_mom=float(self.cfg.get("trend_direction_min_momentum",0.10))
            price_aligned=(side=="BUY" and ema_up and mom>=min_mom) or (
                side=="SELL" and (not ema_up) and mom<=-min_mom
            )
            if not price_aligned:
                return False,"RSI/price gate · TREND signal not aligned with EMA + momentum."
            # RSI need not be oversold/overbought; it must simply not strongly oppose.
            if support<float(self.cfg.get("trend_min_rsi_structure_support",-15.0)):
                return False,f"RSI/price gate · TREND has opposing RSI structure ({support:+.0f})."

        elif rg=="REVERSAL":
            structural=(
                (side=="BUY" and (a.failure_swing=="BULLISH" or a.regular_divergence=="BULLISH"))
                or
                (side=="SELL" and (a.failure_swing=="BEARISH" or a.regular_divergence=="BEARISH"))
            )
            if not structural:
                return False,"RSI/price gate · REVERSAL needs divergence or RSI failure-swing confirmation."
            # Avoid reversing directly into strong still-expanding momentum.
            if side=="BUY" and mom<-float(self.cfg.get("reversal_max_opposing_momentum",0.32)):
                return False,"RSI/price gate · bullish reversal still has excessive bearish momentum."
            if side=="SELL" and mom>float(self.cfg.get("reversal_max_opposing_momentum",0.32)):
                return False,"RSI/price gate · bearish reversal still has excessive bullish momentum."

        elif rg=="RANGE":
            if side=="BUY":
                ok=(a.value<=float(self.cfg.get("range_rsi_buy_zone",42.0)) and a.slope>0)
            else:
                ok=(a.value>=float(self.cfg.get("range_rsi_sell_zone",58.0)) and a.slope<0)
            if not ok:
                return False,"RSI/price gate · RANGE requires RSI turn away from an edge, not a raw extreme."

        elif rg=="HIGH VOL":
            min_mom=float(self.cfg.get("high_vol_direction_min_momentum",0.22))
            if side=="BUY" and mom<min_mom:
                return False,"RSI/price gate · HIGH VOL long lacks positive impulse."
            if side=="SELL" and mom>-min_mom:
                return False,"RSI/price gate · HIGH VOL short lacks negative impulse."
            if support<float(self.cfg.get("high_vol_min_rsi_structure_support",-8.0)):
                return False,"RSI/price gate · HIGH VOL RSI structure opposes impulse."

        # A fresh failure swing directly against the proposed side is a universal veto.
        if side=="BUY" and a.failure_swing=="BEARISH":
            return False,"RSI/price gate · bearish RSI failure swing vetoes long."
        if side=="SELL" and a.failure_swing=="BULLISH":
            return False,"RSI/price gate · bullish RSI failure swing vetoes short."

        return True,f"RSI/price structure confirmed · {a.summary()}"

    def _open(self,d,s):
        if getattr(self.feed,"requires_fresh_live_data",False) and str(getattr(s,"feed_status","UNKNOWN")).upper()!="LIVE":
            self.last_status=f"{s.symbol}: blocked — live feed not fresh ({getattr(s,'feed_status','NO DATA')})"; self.last_open_block_reason=self.last_status
            return
        rg=self.regimes.get(s.symbol,regime(s))

        # v2.9.1 Context Edge: enforce the same instrument/session/location policy
        # at execution even if a caller bypasses the normal selector. This gate only
        # removes trades; it never manufactures a BUY/SELL signal.
        session_ctx=session_policy(s.symbol,s.session,self.cfg)
        location_ctx=entry_location_context(s,d.action,rg,self.cfg)
        if not synthetic_market_mode(self.cfg) and not session_ctx.get("allowed",True):
            reason=f"{session_ctx.get('code','WRONG_SESSION')} · {session_ctx.get('profile','context policy')}"
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return
        if self.cfg.get("context_late_entry_gate_enabled",True) and location_ctx.get("late_entry",False):
            reason=(f"LATE_ENTRY · {location_ctx.get('location','?')} · "
                    f"extension {location_ctx.get('side_extension_atr',0.0):+.2f}ATR · "
                    f"breakout {location_ctx.get('breakout_extension_atr',0.0):.2f}ATR")
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return
        if (self.cfg.get("context_poor_location_gate_enabled",True)
                and rg in ("RANGE","REVERSAL") and location_ctx.get("poor_location",False)):
            reason=(f"POOR_LOCATION · {rg} {d.action} at range position "
                    f"{location_ctx.get('range_position',0.5):.2f}")
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return

        # v2.9.2 Context Health: distinguish market regime from strategy-health
        # regime. The same setup may be healthy one day and weak the next.
        context_health=self.learning.context_health(
            s.symbol,rg,d.action,s.session,location_ctx.get("location","UNKNOWN")
        )
        if not context_health.get("allow",True):
            reason=(f"CONTEXT_HEALTH_BLOCKED · {context_health.get('source','HIERARCHY')} · "
                    f"score {float(context_health.get('score',0.0)):+.2f} · "
                    f"n={int(context_health.get('samples',0))}")
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return
        runtime_guard=self.learning.runtime_context_guard(
            s.symbol,rg,d.action,s.session,self.decision_cycle_count
        )
        if not runtime_guard.get("allow",True):
            reason=f"CONTEXT_CIRCUIT_PAUSED · {runtime_guard.get('reason','runtime drift')}"
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return
        directional_health=self.learning.directional_health(
            s.symbol,rg,d.action,s.session,location_ctx.get("location","UNKNOWN")
        )
        health_penalty=max(0.0,-float(context_health.get("score",0.0)))*float(
            self.cfg.get("context_health_confidence_penalty_points",25.0)
        )
        runtime_penalty=(float(self.cfg.get("context_runtime_watch_confidence_penalty",8.0))
                         if runtime_guard.get("state")=="WATCH" else 0.0)
        directional_penalty=max(0.0,-float(directional_health.get("score",0.0)))*float(
            self.cfg.get("directional_health_confidence_penalty_points",16.0)
        )
        effective_confidence=max(0.0,min(float(d.confidence),
            float(d.confidence)-health_penalty-runtime_penalty-directional_penalty))

        # v2.9.2.4 Trend Entry Repair.  The strongest repeated failure across
        # development + forward batches was high-confidence TREND BUY chasing.
        # Do not ban trends: require either a point-in-time pullback/retest or a
        # genuinely fresh breakout before a high-confidence long can execute.
        # Blocked candidates are still recorded counterfactually for revalidation.
        if (self.cfg.get("trend_high_confidence_chase_gate_enabled",True)
                and rg=="TREND" and d.action=="BUY"
                and float(effective_confidence)>=float(self.cfg.get("trend_high_confidence_chase_threshold",70.0))
                and location_ctx.get("trend_chase",False)
                and not location_ctx.get("trend_pullback_retest",False)
                and not location_ctx.get("fresh_breakout",False)):
            reason=(f"TREND_PULLBACK_WAIT · high-confidence chase {effective_confidence:.0f}% · "
                    f"extension {location_ctx.get('side_extension_atr',0.0):+.2f}ATR · "
                    f"waiting for retest/fresh breakout")
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return

        # v2.8.0: the latest 99-trade PAPER review showed HIGH VOL materially
        # weaker than TREND. Use a conservative regime-specific quality gate
        # instead of overfitting individual symbols from a small sample.
        if rg=="HIGH VOL":
            hv_edge=self.learning.execution_edge_score(s.symbol,rg,d.action,s.session)
            if self.cfg.get("high_vol_require_positive_edge",True) and hv_edge.get("label")!="POSITIVE":
                reason=(f"HIGH_VOL_RESEARCH_ONLY · empirical edge {hv_edge.get('label','UNPROVEN')} "
                        f"({float(hv_edge.get('score',0.0)):+.2f})")
                self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
                self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
                return
            min_conf=float(self.cfg.get("high_vol_min_confidence",76.0))
            min_score=float(self.cfg.get("high_vol_min_signal_score",78.0))
            if float(effective_confidence)<min_conf or float(d.score)<min_score:
                reason=(f"HIGH VOL quality gate · score {float(d.score):.0f}/{min_score:.0f} · "
                        f"effective confidence {float(effective_confidence):.0f}/{min_conf:.0f}")
                self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
                self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
                return

        direction_ok,direction_reason=self._directional_setup_gate(d,s)
        if not direction_ok:
            self.learning.observe_blocked(d,s,self.regimes.get(s.symbol,"MIXED"),direction_reason,self.scan_count)
            self.last_status=f"{s.symbol}: {direction_reason}"; self.last_open_block_reason=direction_reason
            return

        # v2.8.7: adaptive RSI follows each market/regime instead of fixed 70/30.
        # The dynamic band comes from the instrument's own recent RSI distribution.
        # Neural Edge remains SHADOW-only; this is deterministic RSI/price validation.
        if self.cfg.get("adaptive_rsi_execution_gate_enabled",False):
            arsi=self.adaptive_rsi.propose(s,d,rg,analyze_snapshot(s))
            if not (arsi.get("band_low",0)<=arsi.get("rsi",50)<=arsi.get("band_high",100)):
                reason=(f"Adaptive RSI gate · RSI {arsi.get('rsi',50):.1f} outside "
                        f"{arsi.get('band_low',0):.1f}-{arsi.get('band_high',100):.1f} for {rg} {d.action}.")
                self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
                self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
                return

        # v2.8.7: FOREX probation after the clean v2.8.6 batch showed the weakest
        # hit-rate in FX, especially BUY/MIXED. Require stronger confirmation,
        # but let empirically POSITIVE contexts earn their way out of probation.
        from .instruments import instrument_meta as _instrument_meta
        asset=_instrument_meta(s.symbol)["asset_class"]
        edge_now=self.learning.execution_edge_score(s.symbol,rg,d.action,s.session)
        if self.cfg.get("forex_probation_enabled",False) and asset=="FOREX" and edge_now.get("label")!="POSITIVE":
            req_score=float(self.cfg.get("forex_probation_min_score",82.0))
            req_conf=float(self.cfg.get("forex_probation_min_confidence",80.0))
            if rg=="MIXED" or d.action=="BUY":
                req_score+=float(self.cfg.get("forex_weak_context_extra_score",4.0))
                req_conf+=float(self.cfg.get("forex_weak_context_extra_confidence",4.0))
            if float(d.score)<req_score or float(effective_confidence)<req_conf:
                reason=f"FOREX probation · score {d.score:.0f}/{req_score:.0f} · effective confidence {effective_confidence:.0f}/{req_conf:.0f}"
                self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
                self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
                return

        rsi_a=analyze_snapshot(s)
        rsi_gov=self.learning.rsi_pattern_governor(
            s.symbol,self.regimes.get(s.symbol,"MIXED"),d.action,rsi_a.tags
        )
        if not rsi_gov.get("allow",True):
            reason=f"RSI pattern governor · RESEARCH-ONLY · {rsi_gov.get('reason','negative RSI-pattern expectancy')}"
            self.learning.observe_blocked(d,s,self.regimes.get(s.symbol,"MIXED"),reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return

        # v2.9.0 Evidence-First gate. This is deliberately downstream of the
        # technical setup gate: it never invents a BUY/SELL, it only asks whether
        # this type of confidence/side/regime has earned the right to execute.
        quality_gate=self.learning.decision_quality_assessment(
            s.symbol,rg,d.action,s.session,d.confidence
        )
        if not quality_gate.get("allow",True):
            reason=f"Evidence quality gate · RESEARCH-ONLY · {quality_gate.get('reason','negative empirical edge')}"
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"; self.last_open_block_reason=reason
            return

        risk_mult=self._risk_multiplier(d,s,effective_confidence,directional_health)
        sizing_plan=self._equity_sizing_plan(
            d,s,rg,effective_confidence,context_health,runtime_guard,directional_health
        )
        capital_basis=float(sizing_plan.get("capital_basis",min(self.balance,self.equity)))
        max_total=capital_basis*(float(self.risk._profile_value("max_total_risk_pct",1.6))/100.0)
        open_risk=sum(float(p.risk_amount) for p in self.positions)
        remaining_risk=max(0.0,max_total-open_risk)
        lots,risk_amt=self.risk.size(
            self.balance,s.symbol,s.mid,d.stop_pips,risk_mult,
            capital_basis=capital_basis,
            risk_floor_pct=float(sizing_plan.get("floor_pct",0.0)),
            risk_cap_pct=float(sizing_plan.get("cap_pct",self.cfg.get("equity_sizing_max_trade_risk_pct",0.18))),
            max_risk_amount=remaining_risk,
        )
        pip=pip_size(s.symbol)
        if lots<=0:
            reason=(
                f"MIN LOT TOO RISKY — {s.symbol} minimum tradable size would exceed "
                f"approved risk ${risk_amt:.2f}"
            )
            self.learning.observe_blocked(d,s,self.regimes.get(s.symbol,"MIXED"),reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"
            return
        rg=self.regimes.get(s.symbol,"MIXED")
        gov=self.learning.expectancy_governor(s.symbol,rg,d.action,s.session)
        urgency=max(0.0,min(1.0,(float(d.score)+float(effective_confidence)-120.0)/70.0))
        if gov.get("state")=="PROVEN":
            urgency=min(1.0,urgency+.12)
        elif gov.get("state")=="BOOTSTRAP":
            urgency=max(0.0,urgency-.12)

        # Neural Edge remains SHADOW-only in v2.9.0. We still capture its
        # pre-trade opinion here so Champion/Challenger disagreement is measured
        # at the exact moment of execution rather than reconstructed later.
        npred=self.neural.predict(s,d,rg) if self.cfg.get("neural_edge_enabled",True) else None
        if npred:
            self.neural_predictions[s.symbol]=npred

        est=self.execution.estimate(d.action,s,lots,self.scan_count,urgency)
        pv=self.risk.pip_value_per_lot(s.symbol,s.mid)
        expected_cost_usd=est.expected_round_trip_pips*pv*lots+est.expected_commission_usd
        expected_cost_r=expected_cost_usd/max(risk_amt,1e-9)

        max_cost_r=float(self.cfg.get("max_execution_cost_r",0.20))
        if rg in ("REVERSAL","RANGE"):
            max_cost_r=min(max_cost_r,float(self.cfg.get("weak_regime_max_execution_cost_r",0.14)))
        if gov.get("state")=="PROVEN":
            max_cost_r=max(max_cost_r,float(self.cfg.get("proven_max_execution_cost_r",0.24)))

        # Cost must also be small relative to the planned reward. This prevents
        # high-turnover "edge" that is mostly consumed by spread/slippage/fees.
        planned_reward_r=max(float(d.target_pips)/max(float(d.stop_pips),1e-9),1.0)
        max_cost_share=float(self.cfg.get("max_execution_cost_share_of_reward",0.12))
        if self.cfg.get("execution_economics_gate_enabled",True) and (
                expected_cost_r>max_cost_r or expected_cost_r>planned_reward_r*max_cost_share):
            reason=(
                f"Execution economics gate · est cost {expected_cost_r:.2f}R · "
                f"{est.order_style} · liquidity {est.liquidity_score:.2f}"
            )
            self.learning.observe_blocked(d,s,rg,reason,self.scan_count)
            self.last_status=f"{s.symbol}: {reason}"
            return

        fill=self.execution.fill(d.action,s,lots,self.scan_count,urgency,est.order_style)
        if not fill.filled:
            self.learning.observe_blocked(d,s,self.regimes.get(s.symbol,"MIXED"),fill.reason,self.scan_count)
            self.last_status=f"{s.symbol}: {fill.reason}"
            return
        if fill.requested_lots>0 and fill.lots<fill.requested_lots:
            risk_amt*=fill.lots/fill.requested_lots
        lots=fill.lots
        entry=fill.price

        # Payoff-quality gate: the previous long run still had too many small
        # winners versus full stop-outs. Preserve the signal's stop, but require
        # a minimum planned reward/risk before committing production capital.
        # Keep one regime variable throughout _open.  In v2.8.0 a later local
        # variable named `regime` shadowed the imported regime() function, which
        # caused UnboundLocalError at the first fallback lookup.
        rg=self.regimes.get(s.symbol,"MIXED")
        gov=self.learning.expectancy_governor(s.symbol,rg,d.action,s.session)
        min_rr=float(self.cfg.get("minimum_planned_rr",1.65))
        if rg=="TREND":
            min_rr=max(min_rr,float(self.cfg.get("trend_minimum_planned_rr",1.80)))
        elif rg=="HIGH VOL":
            min_rr=max(min_rr,float(self.cfg.get("high_vol_min_planned_rr",2.00)))
        elif rg in ("REVERSAL","RANGE"):
            min_rr=max(min_rr,float(self.cfg.get("weak_regime_minimum_planned_rr",1.90)))
        if gov.get("state")=="PROVEN":
            min_rr=float(self.cfg.get("proven_minimum_planned_rr",1.55))
        target_pips=max(float(d.target_pips),float(d.stop_pips)*min_rr)

        # v2.9.0 economic-edge telemetry is SHADOW-only until forward validation.
        # Unlike the legacy score called "after-cost edge", this is expressed in R:
        #   p(win)*planned_RR - p(loss)*1R - estimated execution cost.
        # It is intentionally left UNKNOWN until the confidence bucket has enough
        # prior PAPER outcomes; no raw 80/90% UI confidence is treated as probability.
        cal=quality_gate.get("calibration",{}) or {}
        cal_n=int(cal.get("samples",0) or 0)
        cal_min=int(self.cfg.get("confidence_quality_min_samples",24))
        cal_p=float(cal.get("calibrated_probability",0.5))
        final_planned_rr=target_pips/max(float(d.stop_pips),1e-9)
        economic_edge_shadow_r=(
            cal_p*final_planned_rr-(1.0-cal_p)-expected_cost_r
            if cal_n>=cal_min else None
        )
        neural_p=float(npred.get("win_probability",0.5)) if npred else None
        probability_disagreement=(abs(cal_p-neural_p) if neural_p is not None and cal_n>=cal_min else None)

        if d.action=="BUY":
            stop=entry-d.stop_pips*pip; target=entry+target_pips*pip
        else:
            stop=entry+d.stop_pips*pip; target=entry-target_pips*pip
        p=Position(str(uuid.uuid4())[:8],s.symbol,d.action,lots,entry,stop,target,risk_amt,d.timestamp,d.brain,0.0,0,d.score)
        self.positions.append(p)
        # Neural Edge v1 is shadow-only: record its exact pre-trade opinion but never alter execution.
        rsi_shadow=self.adaptive_rsi.propose(s,d,rg,analyze_snapshot(s)) if self.cfg.get("adaptive_rsi_shadow_enabled",True) else None
        if npred and self.cfg.get("neural_edge_enabled",True):
            import json as _json
            self.db.neural_shadow_prediction({
                "trade_id":p.id,"ts":datetime.now(timezone.utc).isoformat(),"symbol":s.symbol,"side":d.action,
                "regime":self.regimes.get(s.symbol,"MIXED"),"provider":getattr(s,"feed_provider",""),
                "feed_status":getattr(s,"feed_status","UNKNOWN"),"data_age_seconds":getattr(s,"data_age_seconds",0.0),
                "win_probability":npred.get("win_probability"),"expected_r":npred.get("expected_r"),
                "model_confidence":npred.get("confidence"),"model_samples":npred.get("samples"),
                "features_json":_json.dumps(npred.get("features",[]))})
        self.entry_context[p.id]={
            "regime":self.regimes.get(s.symbol,"MIXED"),"session":s.session,
            "score":d.score,"confidence":d.confidence,"spread":s.spread_pips,
            "entry_reason":str(d.reason),"entry_action":str(d.action),
            "initial_stop":float(stop),"initial_target":float(target),
            "rsi":s.rsi,"atr":s.atr_pips,
            "session_profile":session_ctx.get("profile","UNKNOWN"),
            "location_state":location_ctx.get("location","UNKNOWN"),
            "range_position":location_ctx.get("range_position",0.5),
            "extension_atr":location_ctx.get("extension_atr",0.0),
            "side_extension_atr":location_ctx.get("side_extension_atr",0.0),
            "breakout_extension_atr":location_ctx.get("breakout_extension_atr",0.0),
            "late_entry":1 if location_ctx.get("late_entry",False) else 0,
            "poor_location":1 if location_ctx.get("poor_location",False) else 0,
            "fresh_breakout":1 if location_ctx.get("fresh_breakout",False) else 0,
            "session_range_position":location_ctx.get("session_range_position",0.5),
            "session_range_atr":location_ctx.get("session_range_atr"),
            "session_bars":location_ctx.get("session_bars",0),
            "distance_session_high_atr":location_ctx.get("distance_session_high_atr",99.0),
            "distance_session_low_atr":location_ctx.get("distance_session_low_atr",99.0),
            "distance_pdh_atr":location_ctx.get("distance_pdh_atr",99.0),
            "distance_pdl_atr":location_ctx.get("distance_pdl_atr",99.0),
            "previous_day_complete":location_ctx.get("previous_day_complete",0),
            "structure_barrier":1 if location_ctx.get("structure_barrier",False) else 0,
            "trend_pullback_retest":1 if location_ctx.get("trend_pullback_retest",False) else 0,
            "trend_chase":1 if location_ctx.get("trend_chase",False) else 0,
            "context_health_state":context_health.get("state","LEARNING"),
            "context_health_score":float(context_health.get("score",0.0)),
            "context_health_samples":int(context_health.get("samples",0)),
            "context_health_source":context_health.get("source","HIERARCHY"),
            "runtime_context_state":runtime_guard.get("state","CLEAR"),
            "effective_confidence":float(effective_confidence),
            "session_health_state":sizing_plan.get("session_health",{}).get("state","LEARNING"),
            "session_health_score":float(sizing_plan.get("session_health",{}).get("score",0.0)),
            "session_health_samples":int(sizing_plan.get("session_health",{}).get("samples",0)),
            "session_health_source":sizing_plan.get("session_health",{}).get("source","HIERARCHY"),
            "directional_health_state":directional_health.get("state","LEARNING"),
            "directional_health_score":float(directional_health.get("score",0.0)),
            "directional_health_samples":int(directional_health.get("samples",0)),
            "directional_health_source":directional_health.get("source","HIERARCHY"),
            "sizing_state":sizing_plan.get("state","LEGACY"),
            "sizing_floor_pct":float(sizing_plan.get("floor_pct",0.0)),
            "sizing_cap_pct":float(sizing_plan.get("cap_pct",0.0)),
            "sizing_capital_basis":float(sizing_plan.get("capital_basis",self.balance)),
            "rsi_state":analyze_snapshot(s).operating_range,
            "rsi_pattern":"|".join(analyze_snapshot(s).tags),
            "rsi_bias":analyze_snapshot(s).bias,
            "rsi_slope":analyze_snapshot(s).slope,
            "rsi_support":analyze_snapshot(s).supports(d.action,rg),
            "trend":self.learning._parse_factor(d.reason,"trend"),
            "meanrev":self.learning._parse_factor(d.reason,"meanrev"),
            "breakout":self.learning._parse_factor(d.reason,"breakout"),
            "consensus":self.learning._parse_factor(d.reason,"consensus"),
            "execution_entry_commission":fill.entry_commission,
            "execution_exit_commission":fill.exit_commission_estimate,
            "execution_slippage_pips":fill.slippage_pips,
            "execution_latency_ms":fill.latency_ms,
            "execution_partial":fill.partial,
            "execution_order_style":fill.order_style,
            "execution_estimated_cost_r":expected_cost_r,
            "execution_liquidity_score":est.liquidity_score,
            "execution_size_impact":est.size_impact,
            "planned_rr":final_planned_rr,
            "economic_edge_shadow_r":economic_edge_shadow_r,
            "model_probability_disagreement":probability_disagreement,
            "governor_state":gov.get("state","UNKNOWN"),
            "quality_gate_state":quality_gate.get("state","UNKNOWN"),
            "entry_candle_ts":str(getattr(s,"timestamp","")),
            "entry_candle_seq":int(self.symbol_candle_seq.get(s.symbol,0)),
            "calibrated_win_probability":float(quality_gate.get("calibration",{}).get("calibrated_probability",0.5)),
            "confidence_calibration_samples":int(quality_gate.get("calibration",{}).get("samples",0)),
            "confidence_overconfidence_gap":float(quality_gate.get("calibration",{}).get("overconfidence_gap",0.0)),
            "neural_features":list(npred.get("features",[])) if npred else [],
            "neural_win_probability":float(npred.get("win_probability",0.5)) if npred else None,
            "neural_expected_r":float(npred.get("expected_r",0.0)) if npred else None,
            "adaptive_rsi_shadow":dict(rsi_shadow) if rsi_shadow else None,
        }
        self.position_excursions[p.id]={"mfe_r":0.0,"mae_r":0.0}
        self._init_profit_protection_shadow(p)
        self.reversal_votes[p.id]=0
        self.db.save_positions(self.positions)
        recovery_state,_=self.risk.recovery_state(self.decision_cycle_count)
        self.last_status=(
            f"Paper {d.action} opened: {s.symbol} {lots:.4f} lots · ${risk_amt:.2f} risk · {sizing_plan.get('state','LEGACY')}"
            + (f" · {fill.order_style} · cost~{expected_cost_r:.2f}R · fill {fill.slippage_pips:.2f}p/{fill.latency_ms}ms" if self.execution.enabled() else "")
            + (f" · {recovery_state}" if recovery_state!="NORMAL" else "")
        )

    def _init_profit_protection_shadow(self,p):
        if not self.cfg.get("profit_protection_shadow_enabled",True):
            return
        self.exit_shadows[p.id]={
            "BE_050":{"trigger":0.50,"kind":"fixed","lock":0.02,"armed":False,"exited":False,"exit_r":None},
            "BE_075":{"trigger":0.75,"kind":"fixed","lock":0.05,"armed":False,"exited":False,"exit_r":None},
            "LOCK_075_25":{"trigger":0.75,"kind":"fraction","fraction":0.25,"min_lock":0.10,"armed":False,"exited":False,"exit_r":None},
            "LOCK_100_40":{"trigger":1.00,"kind":"fraction","fraction":0.40,"min_lock":0.20,"armed":False,"exited":False,"exit_r":None},
        }

    def _update_profit_protection_shadow(self,p,r_open,mfe_r):
        variants=self.exit_shadows.get(p.id)
        if not variants:
            return
        buffer_r=max(0.0,float(self.cfg.get("profit_protection_shadow_execution_buffer_r",0.02)))
        for state in variants.values():
            if state.get("exited"):
                continue
            if float(mfe_r)>=float(state.get("trigger",999.0)):
                state["armed"]=True
            if not state.get("armed"):
                continue
            if state.get("kind")=="fraction":
                lock=max(float(state.get("min_lock",0.0)),float(mfe_r)*float(state.get("fraction",0.0)))
            else:
                lock=float(state.get("lock",0.0))
            state["lock_r"]=lock
            if float(r_open)<=lock:
                # Shadow exits use the observed mark minus a small execution
                # allowance. They never overwrite the production position.
                state["exited"]=True
                state["exit_r"]=float(r_open)-buffer_r

    def _persist_profit_protection_shadow(self,p,actual_r,exc,ctx):
        variants=self.exit_shadows.pop(p.id,{})
        if not variants:
            return
        ts=datetime.now(timezone.utc).isoformat()
        for name,state in variants.items():
            shadow_r=float(state.get("exit_r")) if state.get("exited") and state.get("exit_r") is not None else float(actual_r)
            self.db.exit_shadow({
                "trade_id":p.id,"ts":ts,"symbol":p.symbol,"side":p.side,
                "regime":ctx.get("regime","MIXED"),"session":ctx.get("session","Unknown"),
                "variant":name,"triggered":1 if state.get("armed") else 0,
                "shadow_exit_r":shadow_r,"actual_r":float(actual_r),
                "improvement_r":shadow_r-float(actual_r),
                "mfe_r":float(exc.get("mfe_r",0.0)),"bars_open":int(p.bars_open),
            })

    def _mark_and_close(self,new_candle_symbols=None):
        # None preserves legacy/test behavior (treat every symbol as advanced).
        # Runtime callers pass the exact set of symbols with a new CLOSED candle.
        new_candle_symbols=set(self.snapshots) if new_candle_symbols is None else set(new_candle_symbols)
        unreal=0.0
        max_hold=int(self.cfg.get("max_hold_bars",180))
        opposite_score=float(self.cfg.get("opposite_exit_score",72))

        for p in list(self.positions):
            s=self.snapshots.get(p.symbol)
            if not s: continue
            is_new_candle=p.symbol in new_candle_symbols
            if is_new_candle:
                p.bars_open+=1

            px=s.bid if p.side=="BUY" else s.ask
            pip=pip_size(p.symbol)
            move=(px-p.entry)/pip if p.side=="BUY" else (p.entry-px)/pip
            pv=self.risk.pip_value_per_lot(p.symbol,px)
            gross_unrealized=move*pv*p.lots
            ctx=self.entry_context.get(p.id,{})
            estimated_cost=float(ctx.get("execution_entry_commission",0.0))+float(ctx.get("execution_exit_commission",0.0))
            p.unrealized=gross_unrealized-estimated_cost
            unreal+=p.unrealized

            # PAPER research protection: move to break-even and trail only in profit.
            r_open=p.unrealized/max(p.risk_amount,1e-9)
            exc=self.position_excursions.setdefault(p.id,{"mfe_r":0.0,"mae_r":0.0})
            exc["mfe_r"]=max(float(exc.get("mfe_r",0.0)),r_open)
            exc["mae_r"]=min(float(exc.get("mae_r",0.0)),r_open)
            self._update_profit_protection_shadow(p,r_open,float(exc.get("mfe_r",0.0)))
            be_trigger=float(self.cfg.get("break_even_at_r",0.60))
            if self.regimes.get(p.symbol)=="TREND":
                be_trigger=float(self.cfg.get("trend_break_even_at_r",0.75))
            if r_open>=be_trigger:
                pad=pip*0.10
                if p.side=="BUY" and p.stop<p.entry:
                    p.stop=p.entry+pad
                elif p.side=="SELL" and p.stop>p.entry:
                    p.stop=p.entry-pad

            trail_trigger=float(self.cfg.get("trail_start_r",1.15))
            if self.regimes.get(p.symbol)=="TREND":
                trail_trigger=float(self.cfg.get("trend_trail_start_r",1.35))
            if r_open>=trail_trigger:
                trail=max(4.0,s.atr_pips*float(self.cfg.get("trail_atr_mult",1.0)))*pip
                if p.side=="BUY":
                    p.stop=max(p.stop,px-trail)
                else:
                    p.stop=min(p.stop,px+trail)

            # v2.8.5 Early Failure Detector. Losing PAPER trades in the 580-trade
            # review typically showed little/no favorable excursion before moving
            # deeply adverse. Exit only after a minimum observation window and
            # only when the trade has failed to demonstrate expected follow-through.
            early_failure=False
            if self.cfg.get("early_failure_detector_enabled",True):
                ef_min_bars=int(self.cfg.get("early_failure_min_bars",24))
                ef_max_r=float(self.cfg.get("early_failure_max_r",-0.55))
                ef_max_mfe=float(self.cfg.get("early_failure_max_mfe_r",0.08))
                ef_regimes=set(self.cfg.get("early_failure_regimes",["TREND","MIXED","HIGH VOL"]))
                # Require both adverse P/L and price momentum against the thesis.
                # This prevents normal early noise/retracement from killing a future winner.
                mom=float(getattr(s,"momentum",0.0))
                adverse_momentum=(p.side=="BUY" and mom<=-float(self.cfg.get("early_failure_min_adverse_momentum",0.12))) or \
                                 (p.side=="SELL" and mom>= float(self.cfg.get("early_failure_min_adverse_momentum",0.12)))
                if (p.bars_open>=ef_min_bars and r_open<=ef_max_r
                        and float(exc.get("mfe_r",0.0))<ef_max_mfe and adverse_momentum
                        and self.regimes.get(p.symbol,"MIXED") in ef_regimes):
                    early_failure=True

            # v2.9.0 MFE giveback guard. A trade that has already demonstrated
            # meaningful favorable excursion should not be allowed to surrender
            # almost all of it while momentum turns against the position. This is
            # deliberately inactive on small/noisy MFE and never fires at a loss.
            profit_giveback=False
            if self.cfg.get("profit_giveback_guard_enabled",False):
                mfe=float(exc.get("mfe_r",0.0))
                start=float(self.cfg.get("profit_giveback_start_mfe_r",0.80))
                max_fraction=float(self.cfg.get("profit_giveback_max_fraction",0.70))
                min_lock=float(self.cfg.get("profit_giveback_min_lock_r",0.12))
                min_bars_giveback=int(self.cfg.get("profit_giveback_min_bars",8))
                mom=float(getattr(s,"momentum",0.0))
                momentum_turned=(p.side=="BUY" and mom<=0.0) or (p.side=="SELL" and mom>=0.0)
                retain=max(min_lock,mfe*(1.0-max_fraction))
                if (p.bars_open>=min_bars_giveback and mfe>=start
                        and r_open>0 and r_open<=retain and momentum_turned):
                    profit_giveback=True

            hit_stop=(p.side=="BUY" and px<=p.stop) or (p.side=="SELL" and px>=p.stop)
            hit_target=(p.side=="BUY" and px>=p.target) or (p.side=="SELL" and px<=p.target)
            timed=p.bars_open>=max_hold

            opposite=False
            d=self.decisions.get(p.symbol)
            is_opp=bool(
                d and d.score>=opposite_score
                and d.confidence>=float(self.cfg.get("opposite_exit_confidence",68))
                and ((p.side=="BUY" and d.action=="SELL") or (p.side=="SELL" and d.action=="BUY"))
            )
            if is_new_candle:
                if is_opp:
                    self.reversal_votes[p.id]=self.reversal_votes.get(p.id,0)+1
                else:
                    self.reversal_votes[p.id]=0

            votes_needed=int(self.cfg.get("opposite_exit_confirmations",3))
            min_bars=int(self.cfg.get("opposite_exit_min_bars",8))
            # Strong reversals cut a losing trade after confirmation, but do not
            # repeatedly scalp tiny winners that should be allowed to reach TP/trail.
            opposite=(
                self.reversal_votes.get(p.id,0)>=votes_needed
                and p.bars_open>=min_bars
                and (r_open<=float(self.cfg.get("opposite_exit_max_profit_r",0.35)))
            )

            if hit_stop or hit_target or early_failure or profit_giveback or opposite or timed:
                close_px=px
                if hit_stop:
                    reason="Stop Loss"
                    # A stop may slip adversely, but a synthetic scan gap must not
                    # turn a planned ~1R stop into tens of R. Model bounded stop
                    # slippage from spread/ATR instead of closing at an arbitrary
                    # far-away scan price.
                    slip_pips=self.execution.stop_slippage_pips(p,s)
                    close_px=p.stop-slip_pips*pip if p.side=="BUY" else p.stop+slip_pips*pip
                elif hit_target:
                    reason="Take Profit"
                elif early_failure:
                    reason="Early Failure Detector"
                    # Early Failure is an adverse market exit. Close at the
                    # current executable bid/ask. Never substitute the profit
                    # target: doing so converts a detected loser into a fake win.
                    close_px=px
                elif profit_giveback:
                    reason="Profit Giveback Guard"
                    close_px=px
                elif opposite:
                    reason="AI Brain Reversal"
                else:
                    reason="Max Hold Exit"
                self._close(p,close_px,reason)

        self.equity=self.balance+sum(p.unrealized for p in self.positions)
        if self.positions:
            self.db.save_positions(self.positions)

    def _persist_trade_replay(self,p,trade,ctx,exc,reason):
        """Persist a compact OHLC replay for the closed PAPER trade.

        The feed buffers are already resident in memory. No HTTP/network request
        is ever made here, preserving the v2.9.2.2 responsiveness architecture.
        """
        try:
            pre=int(self.cfg.get("trade_replay_pre_bars",30))
            max_bars=int(self.cfg.get("trade_replay_max_bars",300))
            feed=getattr(self,"feed",None)
            candles=[]
            if feed is not None and hasattr(feed,"candle_window"):
                candles=feed.candle_window(p.symbol,p.opened_at,trade.closed_at,pre_bars=pre,max_bars=max_bars)
            # Synthetic/backward-compatible fallback: close-only history rendered
            # as flat-body candles. This path also stays local/in-memory.
            if not candles and feed is not None:
                hist=list(getattr(feed,"history",{}).get(p.symbol,[]) or [])[-max_bars:]
                times=list(getattr(feed,"candle_times",{}).get(p.symbol,[]) or [])[-len(hist):]
                for i,c in enumerate(hist):
                    op=hist[i-1] if i else c
                    candles.append({"ts":float(times[i]) if i<len(times) else float(i),
                                    "open":float(op),"high":float(max(op,c)),
                                    "low":float(min(op,c)),"close":float(c)})
            meta={
                "entry":float(p.entry),"exit":float(trade.exit),
                "initial_stop":float(ctx.get("initial_stop",p.stop)),
                "initial_target":float(ctx.get("initial_target",p.target)),
                "final_stop":float(p.stop),
                "opened_at":str(p.opened_at),"closed_at":str(trade.closed_at),
                "entry_reason":str(ctx.get("entry_reason","Decision reason unavailable")),
                "exit_reason":str(reason),"brain":str(p.brain),
                "regime":str(ctx.get("regime","MIXED")),"session":str(ctx.get("session","Unknown")),
                "session_profile":str(ctx.get("session_profile","UNKNOWN")),
                "location_state":str(ctx.get("location_state","UNKNOWN")),
                "score":float(ctx.get("score",0.0) or 0.0),
                "raw_confidence":float(ctx.get("confidence",0.0) or 0.0),
                "effective_confidence":float(ctx.get("effective_confidence",ctx.get("confidence",0.0)) or 0.0),
                "context_health_state":str(ctx.get("context_health_state","LEARNING")),
                "context_health_score":float(ctx.get("context_health_score",0.0) or 0.0),
                "runtime_context_state":str(ctx.get("runtime_context_state","CLEAR")),
                "session_health_state":str(ctx.get("session_health_state","LEARNING")),
                "session_health_score":float(ctx.get("session_health_score",0.0) or 0.0),
                "directional_health_state":str(ctx.get("directional_health_state","LEARNING")),
                "directional_health_score":float(ctx.get("directional_health_score",0.0) or 0.0),
                "directional_health_samples":int(ctx.get("directional_health_samples",0) or 0),
                "directional_health_source":str(ctx.get("directional_health_source","HIERARCHY")),
                "sizing_state":str(ctx.get("sizing_state","LEGACY")),
                "sizing_floor_pct":float(ctx.get("sizing_floor_pct",0.0) or 0.0),
                "sizing_cap_pct":float(ctx.get("sizing_cap_pct",0.0) or 0.0),
                "sizing_capital_basis":float(ctx.get("sizing_capital_basis",0.0) or 0.0),
                "range_position":float(ctx.get("range_position",0.5) or 0.5),
                "extension_atr":float(ctx.get("extension_atr",0.0) or 0.0),
                "side_extension_atr":float(ctx.get("side_extension_atr",0.0) or 0.0),
                "breakout_extension_atr":float(ctx.get("breakout_extension_atr",0.0) or 0.0),
                "late_entry":bool(ctx.get("late_entry",0)),"poor_location":bool(ctx.get("poor_location",0)),
                "fresh_breakout":bool(ctx.get("fresh_breakout",0)),
                "structure_barrier":bool(ctx.get("structure_barrier",0)),
                "trend_pullback_retest":bool(ctx.get("trend_pullback_retest",0)),
                "trend_chase":bool(ctx.get("trend_chase",0)),
                "mfe_r":float(exc.get("mfe_r",0.0) or 0.0),
                "mae_r":float(exc.get("mae_r",0.0) or 0.0),
                "bars_open":int(p.bars_open),"pnl":float(trade.pnl),"r_multiple":float(trade.r_multiple),
                "lots":float(p.lots),"risk_amount":float(p.risk_amount),
            }
            self.db.save_trade_replay(
                trade.id,trade.closed_at,trade.symbol,trade.side,candles,meta
            )
        except Exception as exc:
            # Replay is observability only. It must never interfere with execution.
            self.last_status=f"{self.last_status} · replay capture skipped: {exc}" if self.last_status else f"Replay capture skipped: {exc}"

    def _close(self,p,px,reason):
        closed_at=datetime.now(timezone.utc).isoformat()
        pip=pip_size(p.symbol)
        move=(px-p.entry)/pip if p.side=="BUY" else (p.entry-px)/pip
        pv=self.risk.pip_value_per_lot(p.symbol,px)
        gross_pnl=move*pv*p.lots
        ctx=self.entry_context.get(p.id,{})
        execution_cost=(
            float(ctx.get("execution_entry_commission",0.0))
            + float(ctx.get("execution_exit_commission",0.0))
        )
        swap_cost=self.execution.swap_cost(p,closed_at)
        pnl=gross_pnl-execution_cost-swap_cost
        p.unrealized=pnl
        self.balance+=pnl
        r=pnl/max(p.risk_amount,1e-9)

        if pnl<0 and reason=="Stop Loss":
            streak=int(self.symbol_loss_streaks.get(p.symbol,0))+1
            self.symbol_loss_streaks[p.symbol]=streak
            if streak>=int(self.cfg.get("symbol_loss_streak_trigger",2)):
                base=int(self.cfg.get("symbol_loss_cooldown_scans",36))
                extra=int(self.cfg.get("symbol_loss_cooldown_extra_scans",18))*max(0,streak-2)
                self.symbol_loss_cooldown_until[p.symbol]=self.decision_cycle_count+base+extra
        elif pnl>0:
            self.symbol_loss_streaks[p.symbol]=0
            self.symbol_loss_cooldown_until.pop(p.symbol,None)

        t=ClosedTrade(p.id,p.symbol,p.side,p.lots,p.entry,px,pnl,r,p.opened_at,
                      closed_at,reason,p.brain)
        self.db.trade(t)
        self.positions.remove(p)
        self.last_exit_scan[p.symbol]=self.scan_count
        self.last_exit_candle_seq[p.symbol]=int(self.symbol_candle_seq.get(p.symbol,0))
        self.db.save_positions(self.positions)
        exc=self.position_excursions.pop(p.id,{"mfe_r":0.0,"mae_r":0.0})
        ctx=self.entry_context.pop(p.id,{})
        self._persist_trade_replay(p,t,ctx,exc,reason)
        self._persist_profit_protection_shadow(p,r,exc,ctx)
        self.reversal_votes.pop(p.id,None)
        self.learning.record_trade(
            p,ctx,pnl,r,reason,p.bars_open,
            exc.get("mfe_r",0.0),exc.get("mae_r",0.0),
            cycle=self.decision_cycle_count
        )
        neural_features=ctx.get("neural_features")
        if neural_features and self.cfg.get("neural_edge_enabled",True):
            result=self.neural.observe(neural_features,r,p.id)
            self.db.neural_shadow_outcome(p.id,r,pnl,result.get("holdout",False))
        if self.cfg.get("adaptive_rsi_shadow_enabled",True):
            self.adaptive_rsi.observe(ctx.get("adaptive_rsi_shadow"),r)
        self.risk.on_trade_closed(pnl,self.decision_cycle_count)
        risk_state,remaining=self.risk.recovery_state(self.decision_cycle_count)
        suffix=""
        if risk_state=="COOLDOWN":
            suffix=f" · recovery cooldown {remaining} closed-candle cycles"
        elif risk_state=="RECOVERY":
            suffix=f" · recovery mode {self.risk.recovery_risk_multiplier():.2f}x risk"
        self.last_status=f"Closed {p.symbol}: {pnl:+.2f} USD ({r:+.2f}R){suffix}"

    def close_all_positions(self, reason="Manual Close All"):
        """Close every open PAPER position at the latest executable quote."""
        if not self.positions:
            self.last_status="No open positions to close."
            return 0
        # Get a fresh market snapshot without allowing new entries.
        self.refresh_market_state(force_reselect=False)
        closed=0
        for p in list(self.positions):
            s=self.snapshots.get(p.symbol)
            if not s:
                continue
            px=s.bid if p.side=="BUY" else s.ask
            pip=pip_size(p.symbol)
            move=(px-p.entry)/pip if p.side=="BUY" else (p.entry-px)/pip
            pv=self.risk.pip_value_per_lot(p.symbol,px)
            ctx=self.entry_context.get(p.id,{})
            estimated_cost=float(ctx.get("execution_entry_commission",0.0))+float(ctx.get("execution_exit_commission",0.0))
            p.unrealized=move*pv*p.lots-estimated_cost
            self._close(p,px,reason)
            closed+=1
        self.equity=self.balance+sum(p.unrealized for p in self.positions)
        self.db.equity(datetime.now(timezone.utc).isoformat(),self.balance,self.equity)
        self.last_status=f"Closed all positions · {closed} paper trades"
        return closed

    def performance(self, all_history=False):
        rows=self.db.trades() if all_history else self.db.trades_since(self.session_started_at)
        if not rows: return {"trades":0,"pnl":0,"win_rate":0,"pf":0,"expectancy":0}
        pnls=[r["pnl"] for r in rows]
        wins=[x for x in pnls if x>0]; losses=[x for x in pnls if x<0]
        gp=sum(wins); gl=abs(sum(losses))
        return {"trades":len(rows),"pnl":sum(pnls),"win_rate":len(wins)/len(rows)*100,
                "pf":gp/gl if gl else (999 if gp else 0),"expectancy":sum(pnls)/len(pnls)}
