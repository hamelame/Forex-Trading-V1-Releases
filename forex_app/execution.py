"""Execution-realistic paper fills.

PAPER ONLY. This module models execution frictions rather than routing orders.

v2.5.4 adds:
- liquidity/size-dependent nonlinear slippage
- passive / marketable-limit / market-style paper execution
- maker/taker-like commission assumptions
- partial/no-fill probability tied to liquidity and order style
- pre-trade round-trip cost estimation
- configurable long/short cost asymmetry
- bounded stop slippage and synthetic carry/funding estimates

The goal is deliberately conservative research. It must be harder for a
strategy to look profitable in paper than in a frictionless simulator.
"""
from dataclasses import dataclass
import hashlib, math
from .market import pip_size
from .instruments import instrument_meta

@dataclass
class PaperFill:
    filled: bool
    price: float
    lots: float
    requested_lots: float
    slippage_pips: float = 0.0
    latency_ms: int = 0
    entry_commission: float = 0.0
    exit_commission_estimate: float = 0.0
    partial: bool = False
    reason: str = "FILLED"
    order_style: str = "MARKETABLE_LIMIT"

@dataclass
class ExecutionEstimate:
    order_style: str
    expected_slippage_pips: float
    expected_round_trip_pips: float
    expected_commission_usd: float
    no_fill_probability: float
    size_impact: float
    liquidity_score: float

class ExecutionRealism:
    def __init__(self,cfg): self.cfg=cfg
    def apply_config(self,cfg): self.cfg=cfg
    def enabled(self): return bool(self.cfg.get("execution_realistic_paper",True))

    def _u(self,key):
        h=hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(h[:8],"big")/float(2**64-1)

    def _asset(self,symbol):
        return instrument_meta(symbol).get("asset_class","FOREX")

    def _liquidity_reference_lots(self,symbol):
        asset=self._asset(symbol)
        defaults={"FOREX":1.5,"METALS":0.55,"ENERGY":0.45,"INDICES":0.80,"CRYPTO":0.30}
        return max(.01,float(self.cfg.get(f"paper_liquidity_ref_lots_{asset.lower()}",defaults.get(asset,.5))))

    def liquidity_score(self,snap):
        max_spread=max(float(self.cfg.get("max_spread_pips",2.5)),.1)
        spread_component=max(0.0,1.0-float(snap.spread_pips)/max_spread)
        quality_component=max(0.0,min(1.0,float(snap.quality)/100.0))
        atr_penalty=min(.35,float(snap.atr_pips)/max(float(self.cfg.get("paper_high_atr_reference",30.0)),1.0)*.20)
        return max(.05,min(1.0,.52*spread_component+.48*quality_component-atr_penalty))

    def size_impact(self,symbol,lots):
        # Square-root impact is intentionally mild for normal sizes but penalizes
        # trying to scale synthetic positions far beyond their liquidity proxy.
        ref=self._liquidity_reference_lots(symbol)
        ratio=max(.01,float(lots)/ref)
        return max(.45,min(3.0,math.sqrt(ratio)))

    def direction_cost_multiplier(self,side,symbol):
        asset=self._asset(symbol)
        # Defaults are near-symmetric. The important long/short asymmetry is
        # learned separately by SelfLearningEngine; these are only friction priors.
        default_short={"FOREX":1.02,"METALS":1.03,"ENERGY":1.04,"INDICES":1.04,"CRYPTO":1.05}.get(asset,1.03)
        default_long=1.0
        key=f"paper_{side.lower()}_execution_cost_mult_{asset.lower()}"
        return max(.70,min(1.50,float(self.cfg.get(key,default_short if side=="SELL" else default_long))))

    def choose_order_style(self,side,snap,lots,urgency=.5):
        liq=self.liquidity_score(snap)
        urgency=max(0.0,min(1.0,float(urgency)))
        if urgency>=.82 and liq>=.55:
            return "MARKET"
        if liq>=.62 and urgency>=.42:
            return "MARKETABLE_LIMIT"
        return "PASSIVE_LIMIT"

    def estimate(self,side,snap,lots,scan_count=0,urgency=.5,order_style=None):
        if not self.enabled():
            return ExecutionEstimate("INSTANT",0.0,float(snap.spread_pips),0.0,0.0,1.0,1.0)

        style=order_style or self.choose_order_style(side,snap,lots,urgency)
        liq=self.liquidity_score(snap)
        impact=self.size_impact(snap.symbol,lots)
        spread_ratio=float(snap.spread_pips)/max(float(self.cfg.get("max_spread_pips",2.5)),.1)
        quality_penalty=max(0.0,(100.0-float(snap.quality))/100.0)

        base=float(self.cfg.get("paper_slippage_base_pips",0.08))
        raw=(base
             + float(snap.spread_pips)*float(self.cfg.get("paper_slippage_spread_factor",0.12))
             + float(snap.atr_pips)*float(self.cfg.get("paper_slippage_atr_factor",0.004)))
        # Non-linear size/market impact plus directional friction prior.
        raw*= (.72+.28*impact) * self.direction_cost_multiplier(side,snap.symbol)
        raw*= (1.0+(1.0-liq)*.45)

        style_slip={"PASSIVE_LIMIT":.12,"MARKETABLE_LIMIT":.58,"MARKET":1.0}.get(style,.65)
        expected_slip=max(0.0,raw*style_slip)

        base_nofill=float(self.cfg.get("paper_no_fill_base_pct",0.4))/100.0
        nofill=base_nofill+max(0,spread_ratio-.55)*.025+quality_penalty*.03+(1.0-liq)*.025
        if style=="PASSIVE_LIMIT": nofill+=float(self.cfg.get("paper_passive_limit_extra_no_fill_pct",9.0))/100.0
        elif style=="MARKETABLE_LIMIT": nofill+=float(self.cfg.get("paper_marketable_limit_extra_no_fill_pct",1.2))/100.0
        else: nofill*=.35
        nofill=max(0.0,min(.35,nofill))

        taker=float(self.cfg.get("paper_commission_per_lot_side_usd",3.50))
        maker=float(self.cfg.get("paper_maker_commission_per_lot_side_usd",1.75))
        commission_per_side=maker if style=="PASSIVE_LIMIT" else taker
        commission=2.0*float(lots)*commission_per_side

        # Spread is paid through bid/ask; include it in the pre-trade economics.
        # Exit slippage is conservatively estimated as another fraction of entry slip.
        exit_slip=expected_slip*float(self.cfg.get("paper_expected_exit_slippage_factor",.80))
        rt=float(snap.spread_pips)+expected_slip+exit_slip
        return ExecutionEstimate(style,expected_slip,rt,commission,nofill,impact,liq)

    def fill(self,side,snap,lots,scan_count,urgency=.5,order_style=None):
        base=snap.ask if side=="BUY" else snap.bid
        if not self.enabled(): return PaperFill(True,base,lots,lots,order_style="INSTANT")
        est=self.estimate(side,snap,lots,scan_count,urgency,order_style)
        style=est.order_style
        u=self._u(f"{snap.symbol}:{scan_count}:{side}:{style}:{lots:.6f}")
        latency_min=int(self.cfg.get("paper_latency_min_ms",35))
        latency_max=int(self.cfg.get("paper_latency_max_ms",180))
        style_latency={"PASSIVE_LIMIT":1.7,"MARKETABLE_LIMIT":1.0,"MARKET":.75}.get(style,1.0)
        latency=int((latency_min+(latency_max-latency_min)*u)*style_latency)

        if u < est.no_fill_probability:
            return PaperFill(False,base,0.0,lots,latency_ms=latency,
                             reason=f"NO FILL — {style.lower()} liquidity/price moved",
                             order_style=style)

        slip=est.expected_slippage_pips*(.60+u*.85)
        cap=max(float(snap.spread_pips)*float(self.cfg.get("paper_slippage_cap_spread_mult",1.4)),
                float(snap.atr_pips)*float(self.cfg.get("paper_slippage_cap_atr_mult",.08)),
                .05)
        slip=min(slip,cap)

        if style=="PASSIVE_LIMIT":
            # Filled passive limits do not cross beyond quoted bid/ask. Small
            # favorable price improvement is allowed; no fantasy mid-price fills.
            improve=min(float(snap.spread_pips)*.35,slip)
            px=base-(improve*pip_size(snap.symbol) if side=="BUY" else -improve*pip_size(snap.symbol))
            slip=-improve
        else:
            px=base+(slip*pip_size(snap.symbol) if side=="BUY" else -slip*pip_size(snap.symbol))

        partial_threshold=float(self.cfg.get("paper_partial_fill_pct",2.0))/100.0
        partial_threshold += max(0.0,1.0-est.liquidity_score)*.06
        if style=="PASSIVE_LIMIT": partial_threshold+=.05
        partial=u > (1.0-min(.30,partial_threshold))
        filled_lots=lots
        if partial:
            fraction=.45+.45*self._u(f"partial:{snap.symbol}:{scan_count}:{style}")
            filled_lots=max(.01,round(lots*fraction,2))

        taker=float(self.cfg.get("paper_commission_per_lot_side_usd",3.50))
        maker=float(self.cfg.get("paper_maker_commission_per_lot_side_usd",1.75))
        per_side=maker if style=="PASSIVE_LIMIT" else taker
        entry_comm=filled_lots*per_side
        return PaperFill(True,px,filled_lots,lots,slip,latency,entry_comm,entry_comm,
                         partial,"PARTIAL FILL" if partial else "FILLED",style)

    def stop_slippage_pips(self,position,snap):
        if not self.enabled(): return 0.0
        impact=self.size_impact(position.symbol,position.lots)
        slip=(float(self.cfg.get("paper_stop_slippage_base_pips",0.12))
              +float(snap.spread_pips)*float(self.cfg.get("paper_stop_slippage_spread_factor",0.15))
              +float(snap.atr_pips)*float(self.cfg.get("paper_stop_slippage_atr_factor",0.006)))
        slip*=.75+.25*impact
        slip*=self.direction_cost_multiplier(position.side,position.symbol)
        cap=max(float(snap.spread_pips)*1.6,float(snap.atr_pips)*.10,.08)
        return min(slip,cap)

    def swap_cost(self,position,closed_at):
        if not self.enabled(): return 0.0
        try:
            from datetime import datetime
            opened=datetime.fromisoformat(position.opened_at.replace("Z","+00:00"))
            now=datetime.fromisoformat(closed_at.replace("Z","+00:00"))
            hours=max(0.0,(now-opened).total_seconds()/3600.0)
        except Exception:
            hours=0.0
        if hours<=0:return 0.0

        meta=instrument_meta(position.symbol); asset=meta.get("asset_class","FOREX")
        if asset=="CRYPTO":
            # Synthetic perpetual carry reserve. We do not pretend to know live
            # funding; use configurable absolute cost per 8h interval.
            intervals=hours/float(self.cfg.get("paper_crypto_funding_interval_hours",8.0))
            rate=abs(float(self.cfg.get("paper_crypto_funding_abs_rate_per_interval",0.00010)))
            notional=abs(float(position.entry)*float(position.lots)*float(meta.get("point_value",5.0)))
            return notional*rate*intervals

        # Traditional swap/financing is accrued fractionally so overnight holds
        # are not free merely because they lasted < 24 whole hours.
        days=hours/24.0
        rate=float(self.cfg.get(f"paper_swap_per_lot_day_{asset.lower()}",
                                self.cfg.get("paper_swap_per_lot_day_usd",2.5)))
        side_mult=float(self.cfg.get(f"paper_{position.side.lower()}_carry_mult_{asset.lower()}",1.0))
        return position.lots*rate*days*side_mult
