"""Read-only IG DEMO USD/NOK conversion check for a NOK-account EUR/USD Mini.

All GET requests are authenticated against IG DEMO by the caller and confined
to the preflight allowlist. A missing/stale/ambiguous broker quote blocks AUTO.
No third-party feed, speculative rate or account credential is ever returned.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
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
            # Support live quotes immediately around 00:00 without confusing
            # the previous day's 23:59 update for a whole-day-old quote.
            for day_offset in (-1, 0, 1):
                age = abs((local - (candidate + timedelta(days=day_offset))).total_seconds())
                if age <= 180:
                    return True
    except (ValueError, TypeError, OverflowError):
        return False
    return False


def _quote_nok_per_usd(instrument, snapshot):
    """Decode IG price units without assuming onePipMeans is the level scale.

    The v3 REST market snapshot exposes bid/offer levels plus the instrument's
    onePipMeans. Some IG CFDs express rates directly (10.25 NOK/USD), others
    in display points (102500). Only one interpretation can pass.
    Ambiguous or unsupported scaling always blocks trading.
    """
    pip = _positive(instrument.get("onePipMeans"))
    bid = _positive(snapshot.get("bid"))
    ask = _positive(snapshot.get("offer"))
    if pip is None:
        raise IGTrialError("IG USD/NOK pip definition is missing or invalid; AUTO remains blocked.")
    if bid is None or ask is None or ask <= bid:
        raise IGTrialError("IG USD/NOK bid/offer quote is missing or inverted; AUTO remains blocked.")
    candidates = []
    for name, multiplier in (("DIRECT", Decimal("1")), ("PIP_LEVEL", pip)):
        b, a = bid * multiplier, ask * multiplier
        if Decimal("5") <= b < a <= Decimal("20") and a - b <= Decimal("0.25"):
            if not any(prev_b == b and prev_a == a for _, prev_b, prev_a in candidates):
                candidates.append((name, b, a))
    if not candidates:
        raise IGTrialError(
            "IG USD/NOK bid/offer scaling or spread is outside verified "
            "NOK-per-USD bounds; no IG AUTO orders allowed."
        )
    if len(candidates) != 1:
        raise IGTrialError(
            "IG USD/NOK quote scale is ambiguous (direct versus pip levels); "
            "no IG AUTO orders allowed."
        )
    return candidates[0][2]


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
    reasons = []
    for epic in epics:
        raw = _read_only_get("/markets/" + epic, key=key, cst=cst, xst=xst,
                             version=3, opener=opener)
        ins = raw.get("instrument") if isinstance(raw, dict) else None
        snap = raw.get("snapshot") if isinstance(raw, dict) else None
        if not isinstance(ins, dict) or not isinstance(snap, dict):
            continue
        if ins.get("epic") != epic or ins.get("type") != "CURRENCIES":
            reasons.append("IG USD/NOK broker market identity/type is not verified")
            continue
        if snap.get("marketStatus") != "TRADEABLE":
            reasons.append("IG USD/NOK market is not TRADEABLE")
            continue
        if snap.get("delayTime") != 0:
            reasons.append("IG USD/NOK broker quote is delayed or delay is unknown")
            continue
        if not _recent_ig_update(snap, now=now):
            reasons.append(
                "IG USD/NOK quote updateTime is missing or older than 180 seconds"
            )
            continue
        try:
            rate = _quote_nok_per_usd(ins, snap)
        except IGTrialError as exc:
            # Only messages authored above; never return raw IG response data
            # or authentication/session identifiers.
            reasons.append(str(exc))
            continue
        return rate
    if reasons:
        raise IGTrialError(reasons[0] + "; broker orders remain OFF.")
    raise IGTrialError(
        "IG DEMO USD/NOK exchange rate or quote freshness not verified; "
        "no automatic orders allowed."
    )
