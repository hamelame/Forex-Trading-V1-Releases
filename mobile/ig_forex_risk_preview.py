"""Conservative, no-order IG DEMO forex sizing research.

NEVER places broker orders. Every trading pair must first pass actual
IG DEMO market identity checks, account-currency settlement verification,
fresh price comparison and conservative <= 0.5% planned loss at stop.
The old EUR/USD Mini AUTO order adapter is deliberately untouched.
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from forex_app.instruments import FX_SYMBOLS


class IGForexPlanError(ValueError):
    pass


def _dec(value, label):
    if isinstance(value, bool) or value is None:
        raise IGForexPlanError(label + " missing")
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise IGForexPlanError(label + " invalid") from None
    if not v.is_finite() or v <= 0:
        raise IGForexPlanError(label + " must be positive")
    return v


def dry_run_order_plan(*, account_currency, balance, available,
                       market, paper_symbol, paper_entry, paper_stop,
                       paper_target, paper_side):
    """Make an *indicative* broker plan without authorizing any order.

    Initial conservative tier: IG DEMO CFD instruments which can actually
    settle into the account's NOK currency. For cross-currency payout,
    reject until a separate broker-verified NOK FX conversion exists.
    A successful dry run is NOT permission to trade: broker margin, identity,
    stale-data, confirmations and no-duplicate order journal are separate
    hard gates required for executable multi-FX.
    """
    if not isinstance(market, dict):
        raise IGForexPlanError("Broker instrument missing")
    if paper_symbol not in FX_SYMBOLS or market.get("symbol") != paper_symbol:
        raise IGForexPlanError("Forex symbol/IG market mismatch")
    if account_currency != "NOK":
        raise IGForexPlanError("Only the known NOK demo account is eligible")
    if market.get("broker_rules_verified") is not True:
        raise IGForexPlanError("IG forex broker contract/rules unverified")
    if market.get("market_status") != "TRADEABLE":
        raise IGForexPlanError("IG DEMO forex market currently closed")
    if "NOK" not in market.get("settlement_currencies", []):
        raise IGForexPlanError("NOK settlement unavailable; FX conversion unverified")
    if paper_side not in ("BUY", "SELL"):
        raise IGForexPlanError("Invalid PAPER direction")

    entry = _dec(paper_entry, "PAPER entry")
    stop = _dec(paper_stop, "PAPER stop")
    target = _dec(paper_target, "PAPER target")
    if paper_side == "BUY" and not (stop < entry < target):
        raise IGForexPlanError("Invalid PAPER BUY stop or target")
    if paper_side == "SELL" and not (target < entry < stop):
        raise IGForexPlanError("Invalid PAPER SELL stop or target")

    bid = _dec(market.get("bid"), "IG bid")
    offer = _dec(market.get("offer"), "IG ask")
    if offer <= bid:
        raise IGForexPlanError("IG broker spread unverified")
    scaling = _dec(market.get("scaling_factor"), "IG quote scaling")
    pip = _dec(market.get("one_pip_means"), "IG price-per-pip")
    pip_value = _dec(market.get("pip_value"), "IG NOK-per-pip")
    size = _dec(market.get("min_deal_size"), "IG minimum size")
    min_stop = _dec(market.get("min_stop_points"), "IG minimum stop")
    broker_mid = (bid + offer) / Decimal(2) / scaling
    # A two percent maximum mismatch is a final hard block threshold, not
    # a target. Broker-side quotes are additionally checked on submit later.
    mismatch = abs(broker_mid - entry) / entry
    if mismatch > Decimal("0.02"):
        raise IGForexPlanError("PAPER and broker forex quote scales diverge")

    stop_price_distance = abs(entry - stop)
    target_price_distance = abs(entry - target)
    stop_display_points = stop_price_distance * scaling
    target_display_points = target_price_distance * scaling
    if stop_display_points < min_stop or target_display_points < min_stop:
        raise IGForexPlanError("IG minimum stop/limit distance not met")

    broker_spread_price = (offer - bid) / scaling
    if broker_spread_price > stop_price_distance / Decimal(4):
        raise IGForexPlanError("Spread too wide relative to stop distance")

    low = min(_dec(balance, "IG balance"), _dec(available, "IG available funds"))
    budget = low * Decimal("0.005")
    # Contract pip value is stated in account NOK for a verified NOK
    # settlement contract. Do NOT use this estimate for other currencies.
    stop_pips = stop_price_distance / pip
    spread_pips = broker_spread_price / pip
    planned_nok = (stop_pips + spread_pips) * pip_value * size
    padded_nok = planned_nok * Decimal("1.25") + Decimal("25")
    if padded_nok > budget:
        raise IGForexPlanError("IG minimum size exceeds 0.5% NOK risk budget")
    return {
        "symbol": paper_symbol,
        "epic": market["epic"],
        "direction": paper_side,
        "size": float(size),
        "stop_distance_broker_points": float(stop_display_points),
        "target_distance_broker_points": float(target_display_points),
        "estimated_planned_loss_nok": float(planned_nok.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
        "risk_with_buffer_nok": float(padded_nok.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
        "max_risk_nok": float(budget.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
        "price_mismatch_pct": float((mismatch * 100).quantize(Decimal("0.001"))),
        "account": "IG DEMO",
        "send_order_enabled": False,
        "requires_forward_broker_validation": True,
        "note": "Dry run only. Never send an order from this preview.",
    }
