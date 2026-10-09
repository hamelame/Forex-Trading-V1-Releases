"""IG DEMO risk policy v1 — NON-TRADING.

The user selected 0.5% as the maximum estimated loss at the initial,
broker-side stop, expressed in the DEMO account's currency. This is a
risk budget, NOT permission to place orders or a guarantee: commissions,
spreads, non-guaranteed-stop slippage and overnight costs can add loss.

The future execution engine MUST:
 * read current broker equity and account currency on every proposed entry;
 * reconcile pending/open positions before approving any entry;
 * map the broker's point/pip and contract values to the account currency;
 * reject unknown FX rates, precision, fees, and contract increments;
 * calculate projected stop loss plus conservative costs and slippage;
 * round DOWN to the broker's verified size increment, never round up;
 * block if minimum size exceeds budget or risk inputs are stale.
No broker order size or order is produced by this module yet.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN

MAX_RISK_FRACTION = Decimal("0.005")
MAX_RISK_PERCENT = 0.5
MAX_IG_OPEN_POSITIONS = 1
_PERCENT_BASE = Decimal("100")
_CENT = Decimal("0.01")


def _positive_amount(value):
    """Safely coerce broker-provided amounts without rounding up."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number


def policy(account_balance=None, account_available=None, account_currency=None):
    """Read-only planned risk limit using the lower usable broker funds value.

    This is a display/budget preview. Never use balance or available alone
    as the authoritative live equity, never claim an executable order is safe
    based solely on this calculation.
    """
    currency = account_currency if isinstance(account_currency, str) and len(account_currency) == 3 and account_currency.isalpha() and account_currency.isupper() else None
    balance = _positive_amount(account_balance)
    available = _positive_amount(account_available)
    inputs = [number for number in (balance, available) if number is not None]
    amount = (min(inputs) * MAX_RISK_FRACTION).quantize(_CENT, rounding=ROUND_DOWN) if len(inputs) == 2 and currency else None
    return {
        "broker": "IG",
        "environment": "DEMO",
        "account_currency": currency,
        "max_planned_risk_pct": MAX_RISK_PERCENT,
        "max_planned_risk_fraction": float(MAX_RISK_FRACTION),
        "indicative_risk_budget": float(amount) if amount is not None else None,
        "budget_unit": currency,
        "limit_type": "estimated loss at initial broker stop",
        "risk_budget_is_loss_guarantee": False,
        "max_open_ig_positions": MAX_IG_OPEN_POSITIONS,
        "risk_sizing_verified": False,
        "automatic_orders_enabled": False,
        "paper_trading_unchanged": True,
        "broker_live_trading_enabled": False,
        "blockers": [
            "Automated IG DEMO entry/exit has not been live-broker tested",
            "Broker point/pip/contract exposure and currency conversion to account currency are not yet validated",
            "Stop slippage, spread, costs, minimum size, and portfolio risk must be included before entry",
            "A durable broker order-state reconciler and kill-switch must be verified before automation",
        ],
    }
