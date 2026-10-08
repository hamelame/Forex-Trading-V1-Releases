"""v2.9.2 session/location/context-health support layer.

This module is deliberately deterministic. It does not create a BUY/SELL signal;
it only describes whether an already-proposed signal is being attempted in a
sensible market/session/location context.
"""
from __future__ import annotations

import math
from .instruments import instrument_meta, instrument_spec


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def synthetic_market_mode(cfg):
    """True only when the *market data* is synthetic.

    Older code treated broker='synthetic' as synthetic market data. In PAPER mode
    that accidentally bypassed session controls even while LIVE public candles
    were in use. The source of truth is market_data_mode.
    """
    return str((cfg or {}).get("market_data_mode", "LIVE")).upper() == "SYNTHETIC"


def _global_session_switch(session, cfg):
    mapping = {
        "Asia": "allow_asia_session",
        "London": "allow_london_session",
        "New York": "allow_new_york_session",
        "London + New York": "allow_overlap_session",
        "Rollover / Thin": "allow_rollover_session",
    }
    key = mapping.get(session)
    return True if key is None else bool((cfg or {}).get(key, True))


def session_policy(symbol, session, cfg):
    """Return an instrument-aware session policy plus a conservative risk prior.

    v2.9.2 keeps the strong v2.9.1 hard gates, but treats the London/New-York
    overlap as its own execution environment.  Standalone London/New York stay
    available where appropriate, at reduced prior risk until empirical context
    health earns more confidence.  This is a prior, not a claim of profitability.
    """
    cfg = cfg or {}
    meta = instrument_meta(symbol)
    asset = meta["asset_class"]
    category = meta.get("category", "")

    if not cfg.get("context_session_gate_enabled", True):
        return {"allowed": True, "code": "SESSION_GATE_OFF", "profile": "DISABLED",
                "quality": 65.0, "risk_mult": 1.0}

    # Global session switches are absolute.  Crypto may be eligible 24/7, but it
    # must never bypass an explicit user/session test switch (for example an
    # OVERLAP-ONLY A/B test).
    if not _global_session_switch(session, cfg):
        return {"allowed": False, "code": "SESSION_DISABLED", "profile": session,
                "quality": 0.0, "risk_mult": 0.0}

    if asset == "CRYPTO" and cfg.get("context_crypto_24_7", True):
        # 721 forward PAPER trades showed that fixed session favoritism was not
        # stable. Keep a conservative, mostly neutral prior and let Context
        # Health / runtime evidence decide which session deserves risk.
        q = 78.0 if session in ("Asia", "Rollover / Thin") else 82.0
        rm = 0.68 if session == "Rollover / Thin" else (0.74 if session == "Asia" else 0.78)
        return {"allowed": True, "code": "CRYPTO_24_7", "profile": f"CRYPTO_{session.upper().replace(' ','_')}",
                "quality": q, "risk_mult": rm}

    if session == "Rollover / Thin":
        return {"allowed": False, "code": "ROLLOVER_THIN", "profile": "NO_TRADE",
                "quality": 0.0, "risk_mult": 0.0}

    allowed = False
    profile = "UNVALIDATED"
    if asset == "FOREX":
        allowed = session in ("London", "London + New York")
        if session == "New York" and cfg.get("context_forex_new_york_enabled", False): allowed = True
        if session == "Asia" and cfg.get("context_unvalidated_asia_enabled", False): allowed = True
        profile = {"London":"FX_LONDON","London + New York":"FX_OVERLAP",
                   "New York":"FX_NY_PROBATION","Asia":"FX_ASIA_RESEARCH"}.get(session,"FX_OFF_HOURS")
    elif asset == "METALS":
        allowed = session in ("London + New York", "New York")
        if session == "London" and cfg.get("context_metals_london_enabled", False): allowed = True
        profile = {"London":"METALS_LONDON_RESEARCH","London + New York":"METALS_OVERLAP",
                   "New York":"METALS_NY"}.get(session,"METALS_OFF_HOURS")
    elif asset == "ENERGY":
        allowed = session in ("London + New York", "New York")
        profile = {"London + New York":"ENERGY_OVERLAP","New York":"ENERGY_NY"}.get(session,"ENERGY_OFF_HOURS")
    elif asset == "INDICES":
        if category == "US Index":
            allowed = session in ("London + New York", "New York")
            profile = {"London + New York":"US_INDEX_OVERLAP","New York":"US_INDEX_NY"}.get(session,"US_INDEX_OFF_HOURS")
        elif "European" in category:
            allowed = session in ("London", "London + New York")
            profile = {"London":"EU_INDEX_LONDON","London + New York":"EU_INDEX_OVERLAP"}.get(session,"EU_INDEX_OFF_HOURS")
        elif "Asia-Pacific" in category:
            allowed = session == "Asia" and cfg.get("context_unvalidated_asia_enabled", False)
            profile = "APAC_INDEX_ASIA_RESEARCH"
        else:
            allowed = session in ("London", "London + New York", "New York")
            profile = f"INDEX_{session.upper().replace(' ','_')}"
    else:
        allowed = session in ("London", "London + New York", "New York")
        profile = f"ACTIVE_{session.upper().replace(' ','_')}"

    # Do not hard-code a belief that the London/NY overlap is inherently best.
    # Across two independent forward batches the overlap was negative while NY
    # was positive, despite an earlier green overlap day.  Use a neutral active-
    # session prior; empirical Context Health is responsible for adapting.
    quality = {"London + New York":84.0,"London":82.0,"New York":84.0,"Asia":68.0}.get(session,45.0)
    risk_mult = {"London + New York":0.78,"London":0.74,"New York":0.78,"Asia":0.58}.get(session,0.40)
    return {
        "allowed": bool(allowed),
        "code": "SESSION_OK" if allowed else "WRONG_SESSION",
        "profile": profile,
        "quality": quality if allowed else min(quality, 35.0),
        "risk_mult": risk_mult if allowed else 0.0,
    }


def entry_location_context(snapshot, side, regime_name, cfg):
    """Measure entry location using only information available at decision time.

    The core late-entry detector stays close-based for robustness across providers,
    while v2.9.2 additionally consumes point-in-time OHLC session/prior-day levels
    when the live feed supplies them. No future candles are consulted.
    """
    cfg = cfg or {}
    hist = [float(x) for x in (getattr(snapshot, "price_history", ()) or ()) if math.isfinite(float(x))]
    side = str(side).upper()
    step = max(float(instrument_spec(snapshot.symbol).get("tick_size", 0.0001)), 1e-12)
    atr_price = max(float(getattr(snapshot, "atr_pips", 0.0)) * step, step * 2.0)
    price = float(getattr(snapshot, "mid", hist[-1] if hist else 0.0))
    ema = float(getattr(snapshot, "ema_fast", price))

    lookback = max(12, int(cfg.get("context_location_lookback_bars", 40)))
    if len(hist) >= 3:
        # Exclude the latest closed signal candle from the reference range so a
        # fresh break can be distinguished from an already-established extreme.
        prior = hist[-min(lookback + 1, len(hist)):-1]
        if len(prior) < 2:
            prior = hist[:-1]
    else:
        prior = []

    if prior:
        hi = max(prior); lo = min(prior)
        width = max(hi - lo, atr_price * 0.25)
        range_pos = clamp((price - lo) / width, 0.0, 1.0)
        dist_hi_atr = abs(hi - price) / atr_price
        dist_lo_atr = abs(price - lo) / atr_price
        breakout_up_atr = max(0.0, (price - hi) / atr_price)
        breakout_down_atr = max(0.0, (lo - price) / atr_price)
    else:
        hi = lo = price
        range_pos = 0.5
        dist_hi_atr = dist_lo_atr = 99.0
        breakout_up_atr = breakout_down_atr = 0.0

    signed_ema_atr = (price - ema) / atr_price
    side_extension_atr = signed_ema_atr if side == "BUY" else -signed_ema_atr
    absolute_extension_atr = abs(signed_ema_atr)

    if breakout_up_atr > 0:
        location = "BREAKOUT_UP"
    elif breakout_down_atr > 0:
        location = "BREAKOUT_DOWN"
    elif range_pos >= 0.82:
        location = "NEAR_HIGH"
    elif range_pos <= 0.18:
        location = "NEAR_LOW"
    else:
        location = "MID_RANGE"

    side_breakout_atr = breakout_up_atr if side == "BUY" else breakout_down_atr
    opposite_breakout_atr = breakout_down_atr if side == "BUY" else breakout_up_atr
    at_chase_edge = range_pos >= 0.84 if side == "BUY" else range_pos <= 0.16

    base_late = float(cfg.get("context_late_entry_extension_atr", 0.95))
    if regime_name == "HIGH VOL":
        late_threshold = min(base_late, float(cfg.get("context_high_vol_late_entry_extension_atr", 0.75)))
    elif regime_name == "TREND":
        late_threshold = min(base_late, float(cfg.get("context_trend_late_entry_extension_atr", 0.90)))
    else:
        late_threshold = base_late

    max_breakout_extension = float(cfg.get("context_max_fresh_breakout_extension_atr", 0.35))
    extended_breakout = side_breakout_atr > max_breakout_extension
    late_entry = bool(
        (side_extension_atr >= late_threshold and at_chase_edge)
        or (side_extension_atr >= late_threshold * 0.75 and extended_breakout)
    )

    poor_location = False
    if regime_name in ("RANGE", "REVERSAL"):
        # Mean-reversion/reversal entries should originate near the opposite edge,
        # not chase through the far side of the recent range.
        poor_location = (side == "BUY" and range_pos >= 0.68) or (side == "SELL" and range_pos <= 0.32)
    elif opposite_breakout_atr > 0.10:
        poor_location = True

    fresh_breakout = bool(side_breakout_atr > 0 and side_breakout_atr <= max_breakout_extension and not late_entry)

    # True session / prior-day structure comes from OHLC candles available at
    # decision time.  These fields are telemetry-first: they add a bounded
    # location penalty but never create a trade on their own.
    session_hi=getattr(snapshot,"session_high",None); session_lo=getattr(snapshot,"session_low",None)
    session_bars=int(getattr(snapshot,"session_bars",0) or 0)
    session_range_atr=getattr(snapshot,"session_range_atr",None)
    if session_hi is not None and session_lo is not None and float(session_hi)>float(session_lo):
        sw=max(float(session_hi)-float(session_lo),atr_price*0.25)
        session_range_position=clamp((price-float(session_lo))/sw,0.0,1.0)
        distance_session_high_atr=abs(float(session_hi)-price)/atr_price
        distance_session_low_atr=abs(price-float(session_lo))/atr_price
    else:
        session_range_position=0.5; distance_session_high_atr=distance_session_low_atr=99.0

    pdh=getattr(snapshot,"previous_day_high",None); pdl=getattr(snapshot,"previous_day_low",None)
    pd_complete=bool(getattr(snapshot,"previous_day_complete",False))
    distance_pdh_atr=(abs(float(pdh)-price)/atr_price if pdh is not None else 99.0)
    distance_pdl_atr=(abs(price-float(pdl))/atr_price if pdl is not None else 99.0)
    barrier_atr=float(cfg.get("context_structure_barrier_atr",0.18))
    near_session_barrier=(distance_session_high_atr<=barrier_atr if side=="BUY" else distance_session_low_atr<=barrier_atr)
    near_prior_barrier=(pd_complete and (distance_pdh_atr<=barrier_atr if side=="BUY" else distance_pdl_atr<=barrier_atr))
    structure_barrier=bool((near_session_barrier or near_prior_barrier) and not fresh_breakout and side_extension_atr>0.35)

    # v2.9.2.4 Trend Entry Repair.  A strong trend is context, not permission
    # to chase.  Detect a simple point-in-time pullback/retest using only closed
    # prices already available to the snapshot.  This intentionally does not
    # create a trade; it only distinguishes "continuation after retest" from
    # "late momentum chase".
    recent_prior = hist[-7:-1] if len(hist) >= 7 else hist[:-1]
    pullback_band_atr = float(cfg.get("trend_pullback_touch_band_atr", 0.30))
    recovery_atr = float(cfg.get("trend_pullback_recovery_atr", 0.04))
    momentum = float(getattr(snapshot, "momentum", 0.0) or 0.0)
    ema_slow = float(getattr(snapshot, "ema_slow", ema))
    trend_aligned = (ema > ema_slow) if side == "BUY" else (ema < ema_slow)
    trend_pullback_retest = False
    if recent_prior and trend_aligned:
        if side == "BUY":
            touched = min(recent_prior) <= ema + atr_price * pullback_band_atr
            recovered = price >= ema + atr_price * recovery_atr and momentum > 0
        else:
            touched = max(recent_prior) >= ema - atr_price * pullback_band_atr
            recovered = price <= ema - atr_price * recovery_atr and momentum < 0
        trend_pullback_retest = bool(touched and recovered)

    chase_soft_atr = float(cfg.get("trend_chase_soft_extension_atr", 0.35))
    trend_chase = bool(
        regime_name == "TREND"
        and trend_aligned
        and not fresh_breakout
        and not trend_pullback_retest
        and side_extension_atr >= chase_soft_atr
    )

    return {
        "location": location,
        "range_position": float(range_pos),
        "extension_atr": float(absolute_extension_atr),
        "side_extension_atr": float(side_extension_atr),
        "distance_high_atr": float(dist_hi_atr),
        "distance_low_atr": float(dist_lo_atr),
        "breakout_extension_atr": float(side_breakout_atr),
        "late_entry": late_entry,
        "poor_location": bool(poor_location),
        "fresh_breakout": fresh_breakout,
        "session_range_position": float(session_range_position),
        "session_range_atr": None if session_range_atr is None else float(session_range_atr),
        "session_bars": session_bars,
        "distance_session_high_atr": float(distance_session_high_atr),
        "distance_session_low_atr": float(distance_session_low_atr),
        "distance_pdh_atr": float(distance_pdh_atr),
        "distance_pdl_atr": float(distance_pdl_atr),
        "previous_day_complete": 1 if pd_complete else 0,
        "structure_barrier": structure_barrier,
        "trend_pullback_retest": bool(trend_pullback_retest),
        "trend_chase": bool(trend_chase),
        "reference_high": float(hi),
        "reference_low": float(lo),
    }


def context_penalties(context, cfg):
    """Return bounded score/confidence penalties for an already-proposed side."""
    cfg = cfg or {}
    score_penalty = 0.0
    confidence_penalty = 0.0
    codes = []

    if context.get("late_entry"):
        score_penalty += float(cfg.get("context_late_entry_score_penalty", 14.0))
        confidence_penalty += float(cfg.get("context_late_entry_confidence_penalty", 20.0))
        codes.append("LATE_ENTRY")
    elif float(context.get("side_extension_atr", 0.0)) > float(cfg.get("context_extension_soft_start_atr", 0.60)):
        extra = float(context.get("side_extension_atr", 0.0)) - float(cfg.get("context_extension_soft_start_atr", 0.60))
        score_penalty += min(6.0, max(0.0, extra) * 6.0)
        confidence_penalty += min(9.0, max(0.0, extra) * 9.0)
        codes.append("EXTENDED")

    if context.get("poor_location"):
        score_penalty += float(cfg.get("context_poor_location_score_penalty", 8.0))
        confidence_penalty += float(cfg.get("context_poor_location_confidence_penalty", 12.0))
        codes.append("POOR_LOCATION")

    if context.get("structure_barrier"):
        score_penalty += float(cfg.get("context_structure_barrier_score_penalty", 4.0))
        confidence_penalty += float(cfg.get("context_structure_barrier_confidence_penalty", 6.0))
        codes.append("STRUCTURE_BARRIER")

    if context.get("trend_chase"):
        score_penalty += float(cfg.get("trend_chase_score_penalty", 7.0))
        confidence_penalty += float(cfg.get("trend_chase_confidence_penalty", 12.0))
        codes.append("TREND_CHASE")

    return score_penalty, confidence_penalty, codes
