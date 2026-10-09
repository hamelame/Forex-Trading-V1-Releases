"""Safe IG DEMO trading preflight: authentic broker market/account checks.

NO order submission, close, or edit code exists here. This intentionally
blocks broker execution until EPIC identity, product, size, stop rules, and
account ownership have been reviewed with real IG DEMO data.

The module only talks to https://demo-api.ig.com/gateway/deal, and never
reads or prints secrets except to send them over HTTPS to that endpoint.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from mobile.ig_demo import (
    DEMO_BASE, IGDemoError, _call as demo_call, _safe_ig_http_error,
    credentials_ready,
)

_EPIC = re.compile(r"^[A-Za-z0-9._]{6,30}$")
_MAX_BYTES = 500_000


def _finite_number(value):
    if type(value) not in (int, float):
        return None
    return round(float(value), 6) if math.isfinite(float(value)) else None


def _rule(value):
    if not isinstance(value, dict):
        return {"value": None, "unit": ""}
    return {
        "value": _finite_number(value.get("value")),
        "unit": str(value.get("unit", ""))[:20] if value.get("unit") in ("POINTS", "PERCENTAGE") else "",
    }


def _read_only_get(path, *, key, cst, xst, version=1, opener=None):
    """Strict path allowlist: never permits any HTTP method except GET."""
    if path != "/positions" and not path.startswith("/markets?searchTerm=") and not path.startswith("/markets/"):
        raise IGDemoError("IG DEMO preflight supports read-only endpoints only.")
    if path.startswith("/markets/") and not _EPIC.fullmatch(path.removeprefix("/markets/")):
        raise IGDemoError("Invalid market EPIC.")
    if path.startswith("/markets?searchTerm=") and path != "/markets?searchTerm=EURUSD":
        raise IGDemoError("IG DEMO market search term is not allowed.")
    if path == "/positions" and version != 2:
        raise IGDemoError("Invalid positions API version.")
    if path.startswith("/markets?") and version != 1:
        raise IGDemoError("Invalid market search API version.")
    if path.startswith("/markets/") and version != 3:
        raise IGDemoError("Invalid market details API version.")

    req = urllib.request.Request(
        DEMO_BASE + path, method="GET",
        headers={
            "X-IG-API-KEY": key,
            "CST": cst,
            "X-SECURITY-TOKEN": xst,
            "Accept": "application/json; charset=UTF-8",
            "Content-Type": "application/json",
            "Version": str(version),
        },
    )
    try:
        with (opener or urllib.request.urlopen)(req, timeout=10) as response:
            raw = response.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise IGDemoError("IG DEMO preflight response was too large.")
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise IGDemoError("IG DEMO preflight returned unexpected data.")
            return data
    except urllib.error.HTTPError as exc:
        raise IGDemoError(_safe_ig_http_error(exc)) from None
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
        raise IGDemoError("IG DEMO preflight could not fetch market information.") from None


def preview(environ=None, opener=None):
    """Read existing positions and discovered EURUSD CFD instrument rules.

    Do not copy internal PAPER lots to broker positions: IG contract sizes and
    stop distances are not equivalent. No order placement path is exposed.
    """
    env = os.environ if environ is None else environ
    if not credentials_ready(env):
        raise IGDemoError("IG DEMO credentials are not configured in Render.")
    key = str(env["IG_DEMO_API_KEY"]).strip()
    login, headers = demo_call(
        "/session", method="POST", key=key,
        body={
            "identifier": str(env["IG_DEMO_USERNAME"]).strip(),
            "password": str(env["IG_DEMO_PASSWORD"]).strip(),
        }, opener=opener,
    )
    cst = headers.get("CST")
    xst = headers.get("X-SECURITY-TOKEN")
    if not cst or not xst:
        raise IGDemoError("IG DEMO authentication tokens are missing.")

    # The login request is hard-pinned to demo-api.ig.com. IG's v2
    # session may return reroutingEnvironment=null (meaning no redirect),
    # even on DEMO. A missing/null redirect is therefore valid on this
    # DEMO host. NEVER follow an explicit LIVE, TEST or UAT redirect.
    # Account type, dealing privileges and current IG CFD account are
    # verified independently below. No order execution exists here.
    reroute = login.get("reroutingEnvironment")
    if reroute not in (None, "DEMO"):
        raise IGDemoError("IG requested a non-DEMO environment. Broker execution is blocked.")
    if login.get("accountType") != "CFD":
        raise IGDemoError("Current IG DEMO account is not a CFD account.")
    if login.get("dealingEnabled") is not True:
        raise IGDemoError("IG DEMO account is not enabled for dealing.")
    account_id = login.get("currentAccountId")
    if not isinstance(account_id, str) or not account_id.strip():
        raise IGDemoError("IG DEMO did not identify the active account.")

    accounts, _ = demo_call("/accounts", key=key, cst=cst, xst=xst, opener=opener)
    active = next((a for a in accounts.get("accounts", [])
                   if isinstance(a, dict) and a.get("accountId") == account_id
                   and a.get("accountType") == "CFD"), None)
    if active is None:
        raise IGDemoError("Active account could not be verified as an IG DEMO CFD account.")
    balance = (active.get("balance") or {})
    if not isinstance(balance, dict):
        raise IGDemoError("IG DEMO account balance was unavailable.")

    positions = _read_only_get("/positions", key=key, cst=cst, xst=xst, version=2, opener=opener)
    open_rows = positions.get("positions")
    if not isinstance(open_rows, list):
        raise IGDemoError("IG DEMO did not return a usable positions list.")

    candidates = _read_only_get(
        "/markets?searchTerm=EURUSD", key=key, cst=cst, xst=xst,
        version=1, opener=opener
    ).get("markets")
    if not isinstance(candidates, list):
        raise IGDemoError("IG DEMO did not return instrument search results.")

    # Only IG CFD forex EPICs are relevant; no betting products or knockouts.
    filtered = [
        row for row in candidates
        if isinstance(row, dict)
        and row.get("instrumentType") == "CURRENCIES"
        and isinstance(row.get("epic"), str)
        and _EPIC.fullmatch(row["epic"])
        and ".CFD." in row["epic"].upper()
        and "EURUSD" in row["epic"].upper()
    ]
    filtered = filtered[:3]
    details = []
    for row in filtered:
        epic = row["epic"]
        raw = _read_only_get(
            "/markets/" + epic, key=key, cst=cst, xst=xst,
            version=3, opener=opener
        )
        ins = raw.get("instrument") or {}
        snap = raw.get("snapshot") or {}
        rules = raw.get("dealingRules") or {}
        currencies = ins.get("currencies") or []
        if not isinstance(ins, dict) or not isinstance(snap, dict) or not isinstance(rules, dict):
            continue
        details.append({
            "symbol": "EURUSD",
            "epic": epic,
            "name": str(ins.get("name", row.get("instrumentName", "")))[:60],
            "type": str(ins.get("type", ""))[:30],
            "expiry": str(ins.get("expiry", ""))[:30],
            "status": str(snap.get("marketStatus", ""))[:30],
            "delay_minutes": _finite_number(snap.get("delayTime")),
            "bid": _finite_number(snap.get("bid")),
            "offer": _finite_number(snap.get("offer")),
            "contract_size": str(ins.get("contractSize", ""))[:32],
            "lot_size": _finite_number(ins.get("lotSize")),
            "value_of_one_pip": str(ins.get("valueOfOnePip", ""))[:32],
            "one_pip_means": str(ins.get("onePipMeans", ""))[:32],
            "unit": str(ins.get("unit", ""))[:25],
            "minimum_deal_size": _rule(rules.get("minDealSize")),
            "minimum_stop": _rule(rules.get("minNormalStopOrLimitDistance")),
            "stops_allowed": ins.get("stopsLimitsAllowed") is True,
            "market_order_preference": str(rules.get("marketOrderPreference", ""))[:32],
            "currencies": [
                str(c.get("code", ""))[:3] for c in currencies[:5]
                if isinstance(c, dict) and re.fullmatch(r"[A-Z]{3}", str(c.get("code", "")))
            ],
        })

    return {
        "environment": "DEMO",
        "account_type": "CFD",
        "connected": True,
        "broker_order_execution_enabled": False,
        "read_only": True,
        "paper_engine_separate": True,
        "account_currency": str(active.get("currency", ""))[:3],
        "account_balance": _finite_number(balance.get("balance")),
        "account_available": _finite_number(balance.get("available")),
        "existing_ig_positions": len(open_rows),
        "market": "EURUSD",
        "market_candidates": details,
        "blocked_reason": (
            "First review IG's minimum trade size, stop distance and CFD EPIC. "
            "Existing IG positions must be reconciled. No automatic DEMO orders "
            "will be sent until protective risk checks are verified."
        ),
    }
