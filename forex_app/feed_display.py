"""Display-only market-data status labels for PC and mobile.

Never use these labels for trading permission: the engine continues to
require fresh MarketSnapshot/closed candles regardless of trading session.
No network calls; UTC-aware clock handles US DST at the weekend boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from forex_app.instruments import instrument_meta

NEW_YORK = ZoneInfo("America/New_York")


def weekend_closed(symbol, when=None):
    """Safely identify the shared non-crypto market weekend shutdown.

    Forex normally stops Friday 17:00 ET and restarts Sunday 17:00 ET.
    Non-forex instruments have differing exchange hours, so this function
    only identifies the common weekend closure, NOT market-open eligibility.
    """
    if instrument_meta(symbol).get("asset_class") == "CRYPTO":
        return False
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Weekend label requires timezone-aware clock")
    ny = moment.astimezone(NEW_YORK)
    return (
        ny.weekday() == 5
        or (ny.weekday() == 4 and ny.hour >= 17)
        or (ny.weekday() == 6 and ny.hour < 17)
    )


def display_feed_status(symbol, raw_status, age_seconds=None, error=None, when=None):
    """Separate provider health from the known weekend market closure.

    Input status originates in LiveMarketFeed.feed_status(), not cached
    engine snapshots. The returned labels must NEVER authorize execution.
    """
    raw = str(raw_status or "").upper()
    if raw == "LIVE":
        return {"status": "LIVE", "note": "Fresh price candles"}
    if weekend_closed(symbol, when):
        return {"status": "MARKET CLOSED", "note": "Weekend market closure; new entries blocked"}
    if error:
        return {"status": "FEED ERROR", "note": "Price provider unavailable or invalid"}
    if raw in ("STALE", "STALE DATA"):
        return {"status": "STALE DATA", "note": "No fresh price candles"}
    return {"status": "STALE DATA", "note": "Waiting for live price candles"}
