"""Mobile-only IG DEMO forex catalogue.

A bounded, explicitly requested read-only discovery for every FX symbol the
desktop universe knows. This DOES NOT enable multi-FX order execution.
Only instrument details read from the logged-in IG DEMO account may be marked
as verified. The existing EURUSD Mini AUTO adapter is unchanged.
"""
from __future__ import annotations

import math
import os
import re
from forex_app.instruments import DEFAULT_UNIVERSE, FX_SYMBOLS
from mobile.ig_demo import IGDemoError, _call as demo_call, credentials_ready
from mobile.ig_demo_preflight import _read_only_get
from mobile.ig_demo_trial import _login

FOREX_SYMBOLS = tuple(symbol for symbol in DEFAULT_UNIVERSE if symbol in FX_SYMBOLS)
MAX_PAGE_SIZE = 3  # Cap IG search/detail calls and mobile request latency.
_EPIC = re.compile(r"^[A-Za-z0-9._]{6,30}$")
_ALLOWED_MARKET_ORDERS = {"AVAILABLE_DEFAULT_ON", "AVAILABLE_DEFAULT_OFF"}


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _positive(value):
    n = _number(value)
    return n if n is not None and n > 0 else None


def _pair_identity(symbol, *parts):
    """Require an actual market-pair identity, not an arbitrary substring."""
    pattern = re.compile(r"(?<![A-Z])" + re.escape(symbol) + r"(?![A-Z])")
    for value in parts:
        if not isinstance(value, str):
            continue
        name = value.upper()
        if pattern.search(name):
            return True
        # GBP/USD or GBP-USD are two exact ISO currency names.
        if re.sub(r"[^A-Z]", "", name) == symbol:
            return True
    return False


def _rule(value):
    if not isinstance(value, dict) or value.get("unit") != "POINTS":
        return None
    return _positive(value.get("value"))


def assess_market(symbol, epic, response):
    """Sanitize broker data; fail closed on all uncertain conditions."""
    if symbol not in FOREX_SYMBOLS or not _EPIC.fullmatch(epic):
        raise ValueError("Not an approved forex market")
    ins = response.get("instrument") if isinstance(response, dict) else None
    snap = response.get("snapshot") if isinstance(response, dict) else None
    rules = response.get("dealingRules") if isinstance(response, dict) else None
    ins = ins if isinstance(ins, dict) else {}
    snap = snap if isinstance(snap, dict) else {}
    rules = rules if isinstance(rules, dict) else {}
    actual = ins.get("epic", epic)
    type_ok = ins.get("type") == "CURRENCIES"
    identity_ok = actual == epic and _pair_identity(
        symbol, epic, ins.get("name"), ins.get("chartCode"),
    )
    bid, offer = _positive(snap.get("bid")), _positive(snap.get("offer"))
    price_ok = bid is not None and offer is not None and offer > bid
    size = _rule(rules.get("minDealSize"))
    stop = _rule(rules.get("minNormalStopOrLimitDistance"))
    contract = _positive(ins.get("contractSize"))
    pip_value = _positive(ins.get("valueOfOnePip"))
    # One pip's price increment can vary dramatically (e.g. JPY pairs).
    one_pip = _positive(ins.get("onePipMeans"))
    market_open = snap.get("marketStatus") == "TRADEABLE"
    verified = (
        type_ok and identity_ok and ins.get("expiry") == "-"
        and ins.get("unit") == "CONTRACTS"
        and ins.get("stopsLimitsAllowed") is True
        and rules.get("marketOrderPreference") in _ALLOWED_MARKET_ORDERS
        and snap.get("delayTime") == 0 and price_ok
        and size is not None and stop is not None and contract is not None
        and pip_value is not None and one_pip is not None
    )
    reason = (
        "Identity/type mismatch" if not (type_ok and identity_ok)
        else "IG DEMO market closed" if not market_open
        else "IG contract or protective-order rules unverified" if not verified
        else "Broker identity and rules verified; sizing/FX conversion pending"
    )
    currencies = ins.get("currencies")
    payout = []
    if isinstance(currencies, list):
        for item in currencies[:5]:
            code = item.get("code") if isinstance(item, dict) else None
            if isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code):
                payout.append(code)
    return {
        "symbol": symbol,
        "epic": epic,
        "name": str(ins.get("name", ""))[:65],
        "market_status": str(snap.get("marketStatus", "UNKNOWN"))[:24],
        "identity_verified": type_ok and identity_ok,
        "broker_rules_verified": bool(verified),
        "read_only": True,
        "auto_execution_enabled": False,
        "broker_position_allowed": False,
        "min_deal_size": size,
        "min_stop_points": stop,
        "contract_size": contract,
        "pip_value": pip_value,
        "one_pip_means": one_pip,
        "scaling_factor": _positive(snap.get("scalingFactor")),
        "settlement_currencies": payout,
        "bid": bid,
        "offer": offer,
        "status": reason,
    }


def preview_forex_page(*, offset=0, limit=2, environ=None, opener=None):
    """Read up to three forex instruments using IG DEMO only.

    Does NOT enable order execution. Each page makes <=3 searches and <=6
    market-details calls; it never loops through 70 instruments in one request.
    Never include an account ID, login secret, session token or raw broker body
    in the response. Account and instrument identities are rechecked every call.
    """
    if type(offset) is not int or not 0 <= offset < len(FOREX_SYMBOLS):
        raise ValueError("Invalid forex catalogue page offset")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("Forex catalogue page size must be 1 to 3")
    env = os.environ if environ is None else environ
    if not credentials_ready(env):
        raise IGDemoError("IG DEMO credentials missing")
    key, cst, xst, account_id = _login(env, opener=opener)
    accounts, _ = demo_call(
        "/accounts", key=key, cst=cst, xst=xst, opener=opener,
    )
    rows = accounts.get("accounts") if isinstance(accounts, dict) else None
    active = next((
        a for a in rows if isinstance(a, dict)
        and a.get("accountId") == account_id and a.get("accountType") == "CFD"
    ), None) if isinstance(rows, list) else None
    if active is None:
        raise IGDemoError("IG DEMO CFD account not verified")
    account_currency = active.get("currency")
    if not isinstance(account_currency, str) or not re.fullmatch(r"[A-Z]{3}", account_currency):
        raise IGDemoError("IG DEMO account currency unverified")

    start = offset
    batch = FOREX_SYMBOLS[start:start + limit]
    results = []
    for symbol in batch:
        search = _read_only_get(
            "/markets?searchTerm=" + symbol, key=key, cst=cst, xst=xst,
            version=1, opener=opener, allowed_fx_symbol=symbol,
        )
        found = search.get("markets") if isinstance(search, dict) else None
        if not isinstance(found, list):
            raise IGDemoError("IG DEMO forex catalogue search unavailable")
        epics = []
        for item in found[:100]:
            if not isinstance(item, dict):
                continue
            epic = item.get("epic")
            if not isinstance(epic, str) or not _EPIC.fullmatch(epic):
                continue
            if item.get("instrumentType") not in (None, "CURRENCIES"):
                continue
            if not _pair_identity(symbol, epic, item.get("instrumentName"), item.get("name")):
                continue
            if epic not in epics:
                epics.append(epic)
            if len(epics) >= 2:
                break
        candidates = []
        for epic in epics:
            raw = _read_only_get(
                "/markets/" + epic, key=key, cst=cst, xst=xst,
                version=3, opener=opener,
            )
            item = assess_market(symbol, epic, raw)
            if item["identity_verified"]:
                candidates.append(item)
        results.append({
            "symbol": symbol, "found_on_ig": bool(candidates),
            "candidates": candidates,
            "status": "FOUND" if candidates else "NOT_VERIFIED",
        })
    next_offset = offset + len(batch)
    return {
        "environment": "DEMO", "account_currency": account_currency,
        "read_only": True, "multi_forex_auto_execution_enabled": False,
        "existing_eurusd_mini_auto_unchanged": True,
        "risk_limit_pct": 0.5,
        "total_forex_symbols": len(FOREX_SYMBOLS),
        "offset": offset, "next_offset": next_offset if next_offset < len(FOREX_SYMBOLS) else None,
        "items": results,
        "note": "Research only. Each IG CFD requires a verified epic, spread, stop, lot and NOK risk conversion before automatic demo orders.",
    }
