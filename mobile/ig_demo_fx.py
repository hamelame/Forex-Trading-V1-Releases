"""Read-only IG DEMO USD/NOK conversion check for a NOK-account EUR/USD Mini.

All GET requests are authenticated against IG DEMO by the caller and confined
to the preflight allowlist. A missing/stale/ambiguous broker quote blocks AUTO.
No third-party feed, speculative rate or account credential is ever returned.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from mobile.ig_demo_trial import IGTrialError
from mobile.ig_demo_preflight import _read_only_get


_EPIC = re.compile(r"^[A-Za-z0-9._]{6,30}$")
_PAIR = re.compile(r"(?:^|[.])USDNOK(?:[.]|$)", re.I)
_LONDON = ZoneInfo("Europe/London")


def _positive(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = Decimal(str(value))
        if n.is_finite() and n > 0:
            return n
    except (InvalidOperation, TypeError, ValueError, ArithmeticError):
        pass
    return None


def _recent_ig_update(snapshot, now=None):
    """Honor IG quote update time when present. UTC or London time allowed."""
    stamp = snapshot.get("updateTime")
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{2}:\d{2}:\d{2}", stamp):
        return False
    try:
        h, m, s = [int(part) for part in stamp.split(":")]
        if h > 23 or m > 59 or s > 59:
            return False
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            return False
        # IG's older API may label time in UTC or London exchange time.
        for tz in (timezone.utc, _LONDON):
            local = now.astimezone(tz)
            candidate = local.replace(hour=h, minute=m, second=s, microsecond=0)
            age = abs((local - candidate).total_seconds())
            if age <= 180:
                return True
    except (ValueError, TypeError, OverflowError):
        return False
    return False


def validated_usdnok_ask(key, cst, xst, *, opener=None, now=None):
    """Return NOK per USD, or block. NEVER sends a dealing request.

    Use IG's actual onePipMeans multiplier for quote-level normalization.
    No guesses about symbol-specific levels or FX conversion direction.
    """
    search = _read_only_get(
        "/markets?searchTerm=USDNOK", key=key, cst=cst, xst=xst,
        version=1, opener=opener,
        allowed_fx_symbol="USDNOK",
    )
    rows = search.get("markets") if isinstance(search, dict) else None
    if not isinstance(rows, list):
        raise IGTrialError("IG DEMO USD/NOK conversion market search unavailable.")
    epics = []
    for row in rows[:40]:
        epic = row.get("epic") if isinstance(row, dict) else None
        if (isinstance(epic, str) and _EPIC.fullmatch(epic)
                and _PAIR.search(epic)
                and row.get("instrumentType") in (None, "CURRENCIES")):
            if epic not in epics:
                epics.append(epic)
            if len(epics) >= 2:
                break
    if not epics:
        raise IGTrialError("IG DEMO USD/NOK forex market unavailable; conversion unverified.")
    # Only the first two verified market candidates; bound IG rate-limit usage.
    for epic in epics:
        raw = _read_only_get("/markets/" + epic, key=key, cst=cst, xst=xst,
                             version=3, opener=opener)
        ins = raw.get("instrument") if isinstance(raw, dict) else None
        snap = raw.get("snapshot") if isinstance(raw, dict) else None
        if not isinstance(ins, dict) or not isinstance(snap, dict):
            continue
        if (ins.get("epic") != epic or ins.get("type") != "CURRENCIES"
                or snap.get("marketStatus") != "TRADEABLE"
                or snap.get("delayTime") != 0):
            continue
        pip_increment = _positive(ins.get("onePipMeans"))
        bid = _positive(snap.get("bid"))
        offer = _positive(snap.get("offer"))
        if pip_increment is None or bid is None or offer is None or offer <= bid:
            continue
        # IG market levels are expressed in pip units: e.g. 105000 *
        # onePipMeans(0.0001) = 10.5 NOK per 1 USD.
        bid_nok = bid * pip_increment
        ask_nok = offer * pip_increment
        if not (Decimal("5") <= bid_nok < ask_nok <= Decimal("20")):
            continue
        if ask_nok - bid_nok > Decimal("0.25"):
            continue
        if not _recent_ig_update(snap, now=now):
            continue
        return ask_nok
    raise IGTrialError(
        "IG DEMO USD/NOK exchange rate or quote freshness not verified; "
        "no automatic orders allowed."
    )
