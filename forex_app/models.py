from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Tuple

@dataclass
class MarketSnapshot:
    symbol: str
    bid: float
    ask: float
    spread_pips: float
    rsi: float
    ema_fast: float
    ema_slow: float
    atr_pips: float
    momentum: float
    session: str
    timestamp: str
    quality: float = 100.0
    rsi_history: Tuple[float,...] = ()
    price_history: Tuple[float,...] = ()
    feed_status: str = "UNKNOWN"
    feed_provider: str = ""
    data_age_seconds: float = 0.0
    # v2.9.2 point-in-time structure telemetry derived only from candles that
    # already existed at the decision timestamp.  Defaults keep older tests and
    # synthetic feeds backward-compatible.
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    session_bars: int = 0
    session_range_atr: Optional[float] = None
    previous_day_high: Optional[float] = None
    previous_day_low: Optional[float] = None
    previous_day_complete: bool = False

    @property
    def mid(self):
        return (self.bid + self.ask) / 2.0

@dataclass
class Decision:
    symbol: str
    action: str
    score: float
    confidence: float
    reason: str
    stop_pips: float
    target_pips: float
    timestamp: str
    brain: str = "Champion"

@dataclass
class Position:
    id: str
    symbol: str
    side: str
    lots: float
    entry: float
    stop: float
    target: float
    risk_amount: float
    opened_at: str
    brain: str = "Champion"
    unrealized: float = 0.0
    bars_open: int = 0
    entry_score: float = 0.0

@dataclass
class ClosedTrade:
    id: str
    symbol: str
    side: str
    lots: float
    entry: float
    exit: float
    pnl: float
    r_multiple: float
    opened_at: str
    closed_at: str
    reason: str
    brain: str = "Champion"
