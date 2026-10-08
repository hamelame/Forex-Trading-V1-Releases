import math
from .market import pip_size
from .instruments import instrument_meta, underlying_key, exposure_tokens, forex_pip_value_usd


class RiskEngine:
    def __init__(self,cfg):
        self.cfg=cfg
        cap=float(cfg["starting_balance"])
        self.consecutive_losses=0
        self.day_start_balance=cap
        self.week_start_balance=cap
        self.high_water_balance=cap
        self.session_start_balance=cap
        self.loss_recovery_until_scan=0
        self.recovery_active=False
        self.last_recovery_reason=""
        self.drawdown_cooldown_until_scan=0
        self.drawdown_recovery_active=False
        self.drawdown_mode="NORMAL"
        self.drawdown_trigger_high_water=0.0
        self.last_drawdown_pct=0.0
        # Adaptive day/week loss protection. These are recoverable safeguards,
        # not permanent session deadlocks.
        self.limit_mode="NORMAL"
        self.limit_cooldown_until_scan=0
        self.limit_recovery_active=False
        self.limit_trigger_reason=""
        self.last_day_loss_pct=0.0
        self.last_week_loss_pct=0.0
        self.deep_loss_trigger_balance=0.0

    def reset_session(self,capital):
        cap=float(capital)
        self.consecutive_losses=0
        self.day_start_balance=cap
        self.week_start_balance=cap
        self.high_water_balance=cap
        self.session_start_balance=cap
        self.loss_recovery_until_scan=0
        self.recovery_active=False
        self.last_recovery_reason=""
        self.drawdown_cooldown_until_scan=0
        self.drawdown_recovery_active=False
        self.drawdown_mode="NORMAL"
        self.drawdown_trigger_high_water=0.0
        self.last_drawdown_pct=0.0
        self.limit_mode="NORMAL"
        self.limit_cooldown_until_scan=0
        self.limit_recovery_active=False
        self.limit_trigger_reason=""
        self.last_day_loss_pct=0.0
        self.last_week_loss_pct=0.0
        self.deep_loss_trigger_balance=0.0

    def pip_value_per_lot(self,symbol,price):
        meta=instrument_meta(symbol)
        if meta.get("asset_class")=="FOREX":
            return max(.000001,float(forex_pip_value_usd(symbol,price)))
        return float(meta.get("point_value",10.0))

    def _profile_value(self,key,default=None):
        if self.cfg.get("trading_profile","AI TRADING")=="MANUAL":
            manual={"risk_per_trade_pct":"manual_risk_per_trade_pct","max_total_risk_pct":"manual_max_total_risk_pct","min_signal_score":"manual_min_signal_score"}.get(key)
            if manual:return self.cfg.get(manual,self.cfg.get(key,default))
        return self.cfg.get(key,default)

    def recovery_risk_multiplier(self):
        mult=1.0
        if self.recovery_active:
            mult=min(mult,float(self.cfg.get("loss_recovery_risk_multiplier",0.40)))
        if self.drawdown_recovery_active:
            mult=min(mult,float(self.cfg.get("drawdown_recovery_risk_multiplier",0.25)))
        if self.limit_recovery_active:
            if getattr(self,"limit_mode","NORMAL")=="DEEP_RECOVERY":
                mult=min(mult,float(self.cfg.get("deep_recovery_risk_multiplier",0.10)))
            else:
                mult=min(mult,float(self.cfg.get("limit_recovery_risk_multiplier",0.20)))
        if getattr(self,"drawdown_mode","NORMAL")=="DEEP_RECOVERY":
            mult=min(mult,float(self.cfg.get("deep_recovery_risk_multiplier",0.10)))
        return mult

    def size(self,balance,symbol,price,stop_pips,risk_multiplier=1.0,
             capital_basis=None,risk_floor_pct=0.0,risk_cap_pct=None,max_risk_amount=None):
        """Size from equity/capital while respecting all existing safeguards.

        v2.9.2.5 adds an evidence-gated *floor* only for healthy/neutral PAPER
        contexts so the many conservative multipliers cannot reduce a $20k paper
        account to a few dollars of risk forever. WATCH/recovery callers pass a
        zero floor.  A hard portfolio-risk remainder can also cap the result.
        """
        meta=instrument_meta(symbol); asset=meta["asset_class"]
        category_mult=float(self.cfg.get(f"category_risk_{asset.lower()}",1.0))
        recovery_mult=self.recovery_risk_multiplier()
        max_mult=float(self.cfg.get("max_risk_multiplier",1.0))
        mult=max(float(self.cfg.get("min_risk_multiplier",.05)),
                 min(max_mult,float(risk_multiplier)*recovery_mult))*category_mult
        base_risk=float(self._profile_value("risk_per_trade_pct",.4))
        basis=float(balance if capital_basis is None else capital_basis)
        basis=max(1.0,basis)
        target_risk=basis*(base_risk/100)*mult

        floor_pct=max(0.0,float(risk_floor_pct or 0.0))
        if floor_pct>0:
            # Preserve asset-class risk haircuts even when the evidence floor is active.
            target_risk=max(target_risk,basis*(floor_pct/100.0)*category_mult)
        if risk_cap_pct is not None:
            target_risk=min(target_risk,basis*(max(0.0,float(risk_cap_pct))/100.0))
        if max_risk_amount is not None:
            target_risk=min(target_risk,max(0.0,float(max_risk_amount)))
        pv=max(.000001,self.pip_value_per_lot(symbol,price))
        stop=max(float(stop_pips),.1)

        raw_lots=target_risk/(stop*pv)
        # PAPER research may use virtual fractional lots so small test balances
        # can preserve the approved percentage risk instead of deadlocking on a
        # broker-style 0.01 minimum. This never enables live execution.
        paper_fractional=(self.cfg.get("paper_only_build",True) and
                          self.cfg.get("mode")=="PAPER" and
                          bool(self.cfg.get("paper_fractional_lots_enabled",True)))
        if paper_fractional:
            min_lot=float(self.cfg.get("paper_virtual_min_lot",0.000001))
            lot_step=float(self.cfg.get("paper_virtual_lot_step",0.000001))
        else:
            min_lot=float(meta.get("min_lot",self.cfg.get("paper_min_lot",0.01)))
            lot_step=float(meta.get("lot_step",self.cfg.get("paper_lot_step",0.01)))
        max_lot=float(self.cfg.get("paper_max_lot",5.0))
        tolerance=float(self.cfg.get("min_lot_risk_tolerance",1.10))

        # Critical safety rule: never force 0.01 lots when 0.01 itself exceeds
        # the approved risk budget. The old max(.01, ...) behavior caused exotic
        # FX trades to become -10R/-30R despite a ~1R plan.
        min_lot_risk=min_lot*stop*pv
        if raw_lots < min_lot and min_lot_risk > target_risk*tolerance:
            return 0.0,target_risk

        # Floor to the broker lot step so rounding cannot increase risk.
        stepped=math.floor((raw_lots+1e-12)/lot_step)*lot_step
        lots=min(max_lot,max(min_lot,stepped))
        lots=round(lots,6)
        actual_stop_risk=lots*stop*pv
        return lots,actual_stop_risk

    def on_trade_closed(self,pnl,scan_count):
        if pnl<0:
            self.consecutive_losses+=1
            limit=int(self.cfg.get("max_consecutive_losses",3))
            if self.consecutive_losses>=limit:
                cooldown=max(1,int(self.cfg.get("loss_recovery_cooldown_scans",18)))
                self.loss_recovery_until_scan=max(self.loss_recovery_until_scan,int(scan_count)+cooldown)
                self.recovery_active=True
                self.last_recovery_reason=f"{self.consecutive_losses} consecutive losses"
        else:
            self.consecutive_losses=0
            self.loss_recovery_until_scan=0
            self.recovery_active=False
            # A profitable closed trade proves the reduced-risk recovery path is
            # functioning, so the next trade may return to normal risk.
            self.drawdown_recovery_active=False
            if getattr(self,"drawdown_mode","NORMAL")=="RECOVERY":
                self.drawdown_mode="NORMAL"
                self.drawdown_trigger_high_water=0.0
                self.drawdown_cooldown_until_scan=0
                self.high_water_balance=max(float(self.high_water_balance),float(getattr(self,"session_start_balance",0.0)))
            if getattr(self,"limit_mode","NORMAL")=="RECOVERY":
                self.limit_mode="NORMAL"
                self.limit_recovery_active=False
                self.limit_cooldown_until_scan=0
                self.limit_trigger_reason=""
                # Keep the rebased soft anchors created when cooldown completed.
                # Re-expanding them toward session start would immediately
                # retrigger the same loss limit and recreate the deadlock.
            self.last_recovery_reason=""

    def recovery_state(self,scan_count):
        dd_mode=getattr(self,"drawdown_mode","NORMAL")
        limit_mode=getattr(self,"limit_mode","NORMAL")
        if dd_mode=="EMERGENCY" or limit_mode=="EMERGENCY":
            return "EMERGENCY",0

        dd_remaining=max(0,int(self.drawdown_cooldown_until_scan)-int(scan_count))
        loss_remaining=max(0,int(self.loss_recovery_until_scan)-int(scan_count))
        limit_remaining=max(0,int(self.limit_cooldown_until_scan)-int(scan_count))
        remaining=max(dd_remaining,loss_remaining,limit_remaining)

        if dd_mode in ("COOLDOWN","DEEP_COOLDOWN") or limit_mode in ("COOLDOWN","DEEP_COOLDOWN") or remaining>0:
            return "COOLDOWN",remaining
        if (self.recovery_active or self.drawdown_recovery_active or self.limit_recovery_active
                or dd_mode in ("RECOVERY","DEEP_RECOVERY")
                or limit_mode in ("RECOVERY","DEEP_RECOVERY")):
            return "RECOVERY",0
        return "NORMAL",0

    def update_loss_limit_state(self,balance,scan_count):
        """Advance soft and deep session-loss protection once per active scan.

        Soft protection:
            NORMAL -> COOLDOWN -> RECOVERY -> NORMAL

        Deep protection:
            NORMAL/RECOVERY -> DEEP_COOLDOWN -> DEEP_RECOVERY
        Deep recovery keeps trading alive at very low risk until the session
        recovers materially. Only the catastrophic limit is a permanent stop.
        """
        balance=float(balance); scan_count=int(scan_count)
        mode=getattr(self,"limit_mode","NORMAL")

        session_loss=(self.session_start_balance-balance)/max(self.session_start_balance,1)*100
        deep_limit=float(self.cfg.get("deep_session_loss_pct",10.0))
        catastrophic=float(self.cfg.get("catastrophic_session_loss_pct",20.0))
        deep_exit=float(self.cfg.get("deep_recovery_exit_loss_pct",7.5))

        if session_loss>=catastrophic:
            self.limit_mode="EMERGENCY"
            self.limit_recovery_active=False
            self.limit_trigger_reason=f"Catastrophic session loss {session_loss:.2f}%"
            self.last_recovery_reason=self.limit_trigger_reason
            return "EMERGENCY"

        # A catastrophic stop is intentionally sticky until a new/reset session.
        if mode=="EMERGENCY":
            return "EMERGENCY"

        day_dd=(self.day_start_balance-balance)/max(self.day_start_balance,1)*100
        week_dd=(self.week_start_balance-balance)/max(self.week_start_balance,1)*100
        self.last_day_loss_pct=day_dd
        self.last_week_loss_pct=week_dd

        deep_cooldown=max(1,int(self.cfg.get("deep_recovery_cooldown_scans",180)))
        if mode=="DEEP_COOLDOWN":
            if scan_count>=int(self.limit_cooldown_until_scan):
                self.day_start_balance=balance
                self.week_start_balance=balance
                self.deep_loss_trigger_balance=balance
                self.limit_cooldown_until_scan=0
                self.limit_recovery_active=True
                self.limit_mode="DEEP_RECOVERY"
                self.last_recovery_reason="Deep session-loss cooldown completed"
                return "DEEP_RECOVERY"
            return "DEEP_COOLDOWN"

        if mode=="DEEP_RECOVERY":
            self.limit_recovery_active=True
            # Remain in low-risk recovery until the original session has healed
            # enough. Do not repeatedly re-trigger the same 10% breach.
            if session_loss<=deep_exit:
                self.limit_mode="NORMAL"
                self.limit_recovery_active=False
                self.limit_trigger_reason=""
                self.deep_loss_trigger_balance=0.0
                self.day_start_balance=balance
                self.week_start_balance=balance
                self.last_recovery_reason="Deep recovery completed"
                return "NORMAL"
            return "DEEP_RECOVERY"

        # First deep-session breach gets a long cooldown, not a permanent stop.
        if session_loss>=deep_limit:
            self.limit_mode="DEEP_COOLDOWN"
            self.limit_cooldown_until_scan=scan_count+deep_cooldown
            self.limit_recovery_active=True
            self.deep_loss_trigger_balance=balance
            self.limit_trigger_reason=f"Deep session loss {session_loss:.2f}%"
            self.last_recovery_reason=self.limit_trigger_reason
            return "DEEP_COOLDOWN"

        daily_limit=float(self.cfg.get("daily_loss_limit_pct",2.0))
        weekly_limit=float(self.cfg.get("weekly_loss_limit_pct",4.0))
        cooldown=max(1,int(self.cfg.get("limit_recovery_cooldown_scans",45)))

        if mode=="COOLDOWN":
            if scan_count>=int(self.limit_cooldown_until_scan):
                self.day_start_balance=balance
                self.week_start_balance=balance
                self.last_day_loss_pct=0.0
                self.last_week_loss_pct=0.0
                self.limit_cooldown_until_scan=0
                self.limit_recovery_active=True
                self.limit_mode="RECOVERY"
                self.last_recovery_reason="Daily/weekly cooldown completed"
                return "RECOVERY"
            return "COOLDOWN"

        hit_daily=day_dd>=daily_limit
        hit_weekly=week_dd>=weekly_limit
        if hit_daily or hit_weekly:
            reason=(
                f"Daily loss {day_dd:.2f}%"
                if hit_daily and not hit_weekly
                else f"7-day loss {week_dd:.2f}%"
                if hit_weekly and not hit_daily
                else f"Daily {day_dd:.2f}% / 7-day {week_dd:.2f}% loss"
            )
            self.limit_mode="COOLDOWN"
            self.limit_cooldown_until_scan=scan_count+cooldown
            self.limit_recovery_active=True
            self.limit_trigger_reason=reason
            self.last_recovery_reason=reason
            return "COOLDOWN"

        if mode=="RECOVERY":
            self.limit_mode="RECOVERY"
            return "RECOVERY"

        self.limit_mode="NORMAL"
        return "NORMAL"

    def loss_limit_gate(self,scan_count):
        mode=getattr(self,"limit_mode","NORMAL")
        if mode=="EMERGENCY":
            return False,self.limit_trigger_reason or "Catastrophic session-loss hard stop active."
        if mode in ("COOLDOWN","DEEP_COOLDOWN"):
            remaining=max(0,int(self.limit_cooldown_until_scan)-int(scan_count))
            if mode=="DEEP_COOLDOWN":
                return False,(
                    f"Deep session-loss recovery cooldown active · {remaining} scans remaining · "
                    f"{self.limit_trigger_reason}"
                )
            kind="Daily/weekly loss circuit breaker"
            if self.limit_trigger_reason.startswith("Daily loss"):
                kind="Daily loss circuit breaker"
            elif self.limit_trigger_reason.startswith("7-day loss"):
                kind="7-day loss circuit breaker"
            return False,(
                f"{kind} recovery cooldown active · {remaining} scans remaining · "
                f"{self.limit_trigger_reason}"
            )
        if mode=="DEEP_RECOVERY":
            return True,f"Deep recovery active · {self.recovery_risk_multiplier():.2f}x risk."
        if mode=="RECOVERY":
            return True,f"Loss-limit recovery active · {self.recovery_risk_multiplier():.2f}x risk."
        return True,""

    def sanitize_recovery_state(self,scan_count):
        """Self-heal expired/stale recoverable locks without bypassing emergencies."""
        scan_count=int(scan_count)
        repaired=[]
        if (getattr(self,"drawdown_mode","NORMAL")=="COOLDOWN"
                and scan_count>=int(self.drawdown_cooldown_until_scan)
                and self.drawdown_cooldown_until_scan>0):
            repaired.append("drawdown")
        if (getattr(self,"limit_mode","NORMAL") in ("COOLDOWN","DEEP_COOLDOWN")
                and scan_count>=int(self.limit_cooldown_until_scan)
                and self.limit_cooldown_until_scan>0):
            repaired.append("loss-limit")
        if self.recovery_active and self.loss_recovery_until_scan and scan_count>=int(self.loss_recovery_until_scan):
            # Expired loss-streak cooldown should be RECOVERY, never a stale block.
            self.loss_recovery_until_scan=0
        return repaired

    def update_drawdown_state(self,balance,scan_count):
        """Advance high-water protection once per active engine scan.

        This state machine is deliberately independent of whether a BUY/SELL
        candidate happens to reach RiskEngine.gate on that scan.  That prevents
        the old deadlock where recovery could only advance while an executable
        candidate was being gated.

        NORMAL -> COOLDOWN -> RECOVERY -> NORMAL

        A deeper emergency giveback during COOLDOWN remains a hard stop.
        Daily/weekly loss breakers remain independent hard barriers.
        """
        balance=float(balance); scan_count=int(scan_count)
        limit=float(self.cfg.get("max_drawdown_pct",6.0))
        emergency=float(self.cfg.get("catastrophic_drawdown_pct",20.0))
        cooldown=max(1,int(self.cfg.get("drawdown_recovery_cooldown_scans",60)))

        # Keep old attributes for UI/backward compatibility.
        mode=getattr(self,"drawdown_mode","NORMAL")
        trigger_hw=float(getattr(self,"drawdown_trigger_high_water",0.0) or 0.0)

        if mode=="EMERGENCY":
            self.drawdown_recovery_active=False
            return mode

        # NORMAL / RECOVERY maintain a rolling high-water mark.
        if mode in ("NORMAL","RECOVERY"):
            self.high_water_balance=max(float(self.high_water_balance),balance)

        # In recovery, a profitable closed trade may already have cleared the
        # recovery flag via on_trade_closed().
        if mode=="RECOVERY" and not self.drawdown_recovery_active:
            self.drawdown_mode="NORMAL"
            self.drawdown_trigger_high_water=0.0
            self.drawdown_cooldown_until_scan=0
            self.last_drawdown_pct=(self.high_water_balance-balance)/max(self.high_water_balance,1)*100
            return "NORMAL"

        if mode=="COOLDOWN":
            trigger_hw=trigger_hw or float(self.high_water_balance)
            dd=(trigger_hw-balance)/max(trigger_hw,1)*100
            self.last_drawdown_pct=dd

            if dd>=emergency:
                self.drawdown_mode="EMERGENCY"
                self.drawdown_recovery_active=False
                self.last_recovery_reason=f"Emergency high-water drawdown {dd:.2f}%"
                return "EMERGENCY"

            if scan_count>=int(self.drawdown_cooldown_until_scan):
                # Recovery is guaranteed to advance here even if there is no
                # executable candidate on this exact scan. Rebase only the
                # profit-giveback high-water anchor; day/week anchors remain.
                self.high_water_balance=balance
                self.drawdown_trigger_high_water=0.0
                self.drawdown_cooldown_until_scan=0
                self.drawdown_recovery_active=True
                self.drawdown_mode="RECOVERY"
                self.last_drawdown_pct=0.0
                self.last_recovery_reason="High-water cooldown completed"
                return "RECOVERY"
            return "COOLDOWN"

        # NORMAL or RECOVERY: re-arm protection if a fresh giveback reaches the
        # configured threshold from the current/rebased high-water mark.
        dd=(self.high_water_balance-balance)/max(self.high_water_balance,1)*100
        self.last_drawdown_pct=dd
        if dd>=limit:
            self.drawdown_trigger_high_water=float(self.high_water_balance)
            self.drawdown_cooldown_until_scan=scan_count+cooldown
            self.drawdown_recovery_active=True
            self.drawdown_mode="COOLDOWN"
            self.last_recovery_reason=f"High-water drawdown {dd:.2f}%"
            return "COOLDOWN"

        if mode=="RECOVERY":
            self.drawdown_mode="RECOVERY"
            return "RECOVERY"

        self.drawdown_mode="NORMAL"
        return "NORMAL"

    def _high_water_gate(self,balance,scan_count):
        """Backward-compatible wrapper used by older tests/tools.

        Production flow advances the lifecycle from TradingEngine.scan().
        """
        previous=getattr(self,"drawdown_mode","NORMAL")
        mode=self.update_drawdown_state(balance,scan_count)
        if mode=="COOLDOWN":
            remaining=max(0,int(self.drawdown_cooldown_until_scan)-int(scan_count))
            if previous!="COOLDOWN":
                cooldown=max(1,int(self.cfg.get("drawdown_recovery_cooldown_scans",60)))
                return False,(
                    f"High-water drawdown protection active "
                    f"({self.last_drawdown_pct:.2f}%) · recovery in {cooldown} scans."
                )
            return False,(
                f"High-water drawdown cooldown active "
                f"({self.last_drawdown_pct:.2f}%) · {remaining} scans remaining."
            )
        return self.drawdown_gate(balance,scan_count)

    def drawdown_gate(self,balance,scan_count):
        """Read-only execution gate for the scan-driven drawdown state."""
        mode=getattr(self,"drawdown_mode","NORMAL")
        if mode=="EMERGENCY":
            return False,(
                f"Emergency high-water drawdown hard stop active "
                f"({self.last_drawdown_pct:.2f}%)."
            )
        if mode=="COOLDOWN":
            remaining=max(0,int(self.drawdown_cooldown_until_scan)-int(scan_count))
            return False,(
                f"High-water drawdown cooldown active "
                f"({self.last_drawdown_pct:.2f}%) · {remaining} scans remaining."
            )
        if mode=="RECOVERY":
            return True,(
                f"High-water recovery active · "
                f"{self.recovery_risk_multiplier():.2f}x risk."
            )
        return True,""

    def gate(self,balance,positions,decision,snap,scan_count=0):
        if self.cfg.get("paper_only_build",True):
            if self.cfg.get("mode")!="PAPER" or str(self.cfg.get("broker","")).lower()!="synthetic":
                return False,"PAPER-ONLY build: live execution disabled."
        if decision.action not in ("BUY","SELL"):return False,"No executable signal."
        if len(positions)>=int(self.cfg.get("max_open_positions",5)):return False,"Maximum open positions reached."
        if any(p.symbol==decision.symbol for p in positions):return False,"Instrument already has an open position."

        # Strict underlying identity: XAUUSD and XAUEUR are the same Gold exposure.
        ukey=underlying_key(decision.symbol)
        if any(underlying_key(p.symbol)==ukey for p in positions):
            return False,"Underlying exposure already open via another quote/instrument."

        meta=instrument_meta(decision.symbol); asset=meta["asset_class"]
        if asset not in self.cfg.get("enabled_asset_classes",[]):return False,f"{asset} category disabled."
        max_cat=int(self.cfg.get("max_category_positions",2))
        if sum(1 for p in positions if instrument_meta(p.symbol)["asset_class"]==asset)>=max_cat:
            return False,f"{asset} concentration gate active."

        # Shared exposure gate across currencies/index regions/crypto beta.
        candidate_tokens=exposure_tokens(decision.symbol)
        token_hits={}
        for p in positions:
            for tok in candidate_tokens.intersection(exposure_tokens(p.symbol)):
                token_hits[tok]=token_hits.get(tok,0)+1
        for tok,count in token_hits.items():
            limit=int(self.cfg.get("max_shared_exposure_positions",2))
            if tok.startswith("CCY:"):
                limit=int(self.cfg.get("max_currency_exposure_positions",2))
            elif tok.startswith("INDEX_REGION:"):
                limit=int(self.cfg.get("max_index_region_positions",2))
            elif tok=="CRYPTO:BETA":
                limit=int(self.cfg.get("max_crypto_beta_positions",2))
            if count>=limit:
                return False,f"Shared exposure gate active ({tok})."

        if snap.spread_pips>float(self.cfg.get("max_spread_pips",2.5)):
            return False,"Spread exceeds safety limit."

        # Defensive fallback: production advances these once per engine scan,
        # but gate also advances them so direct/test/tool calls cannot bypass
        # or strand the recovery lifecycle.
        self.sanitize_recovery_state(scan_count)
        self.update_loss_limit_state(balance,scan_count)
        self.update_drawdown_state(balance,scan_count)

        # Daily/weekly limits are scan-driven recoverable soft barriers.
        # The deeper emergency session-loss threshold remains a hard stop.
        ok,reason=self.loss_limit_gate(scan_count)
        if not ok:
            return False,reason
        ok,reason=self.drawdown_gate(balance,scan_count)
        if not ok:
            return False,reason

        # Loss streaks and high-water givebacks are adaptive: cool down, then
        # resume at reduced risk. Hard daily/weekly breakers above remain hard.
        state,remaining=self.recovery_state(scan_count)
        if state=="COOLDOWN":
            dd_mode=getattr(self,"drawdown_mode","NORMAL")
            limit_mode=getattr(self,"limit_mode","NORMAL")
            if dd_mode=="COOLDOWN":
                return False,f"High-water recovery cooldown active · {remaining} scans remaining."
            if limit_mode=="DEEP_COOLDOWN":
                return False,f"Deep session-loss recovery cooldown active · {remaining} scans remaining."
            if limit_mode=="COOLDOWN":
                return False,f"Daily/weekly recovery cooldown active · {remaining} scans remaining."
            return False,f"Loss-streak recovery cooldown active · {remaining} scans remaining."

        open_risk=sum(p.risk_amount for p in positions)
        max_risk=balance*(float(self._profile_value("max_total_risk_pct",1.6))/100)
        base_new=balance*(float(self._profile_value("risk_per_trade_pct",.4))/100)*self.recovery_risk_multiplier()
        if open_risk+base_new>max_risk:return False,"Portfolio risk budget exhausted."
        if state=="RECOVERY":
            return True,f"Approved in RECOVERY mode · {self.recovery_risk_multiplier():.2f}x risk."
        return True,"Approved"

