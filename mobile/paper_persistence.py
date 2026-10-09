"""Mobile-only durable PAPER checkpoints. No broker execution or PC changes.

The SQLite database and checkpoints MUST live on an attached Render disk.
A checkpoint holds risk/position/account state while ordinary trade history
continues to be stored in the existing SQLite tables.
"""
from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime, timezone

from forex_app.database import Database
from forex_app.models import Position


class PreservingMobileDatabase(Database):
    """Skip the desktop engine's unconditional position purge on initialization."""

    def clear_open_positions(self):
        if getattr(self, "_preserve_positions_during_boot", False):
            self._preserve_positions_during_boot = False
            return
        return super().clear_open_positions()


RISK_FIELDS = (
    "consecutive_losses", "day_start_balance", "week_start_balance",
    "high_water_balance", "session_start_balance", "loss_recovery_until_scan",
    "recovery_active", "last_recovery_reason", "drawdown_cooldown_until_scan",
    "drawdown_recovery_active", "drawdown_mode", "drawdown_trigger_high_water",
    "last_drawdown_pct", "limit_mode", "limit_cooldown_until_scan",
    "limit_recovery_active", "limit_trigger_reason", "last_day_loss_pct",
    "last_week_loss_pct", "deep_loss_trigger_balance",
)
COUNTER_FIELDS = (
    "scan_count", "decision_cycle_count", "last_open_scan", "last_any_entry_scan",
)
MAP_FIELDS = (
    "symbol_loss_streaks", "symbol_loss_cooldown_until", "last_exit_scan",
    "last_candle_ts", "symbol_candle_seq", "last_exit_candle_seq",
    "entry_context", "position_excursions", "reversal_votes",
)


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite PAPER checkpoint number")
    if isinstance(value, dict):
        return {str(k): _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    if value is None or type(value) in (bool, str, int, float):
        return value
    raise ValueError("unsupported PAPER checkpoint value")


class PaperCheckpoint:
    def __init__(self, db):
        self.db = db
        self.db.conn.execute(
            "CREATE TABLE IF NOT EXISTS mobile_paper_checkpoint "
            "(id INTEGER PRIMARY KEY CHECK(id=1), state_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self.db.conn.commit()

    def load(self):
        row = self.db.conn.execute(
            "SELECT state_json FROM mobile_paper_checkpoint WHERE id=1"
        ).fetchone()
        if not row:
            return None
        state = json.loads(row["state_json"])
        if not isinstance(state, dict) or state.get("schema") != 1:
            raise ValueError("Unsupported PAPER checkpoint")
        return state

    def save(self, engine, auto_resume=False):
        # Persist exact positions and risk/strategy timebase while the server
        # holds the engine lock. Do not log keys, tokens or credentials.
        state = {
            "schema": 1,
            "session_started_at": engine.session_started_at,
            "balance": float(engine.balance),
            "equity": float(engine.equity),
            "enabled": bool(engine.enabled or auto_resume),
            "positions": [dataclasses.asdict(p) for p in engine.positions],
            "risk": {key: getattr(engine.risk, key) for key in RISK_FIELDS},
            "counters": {key: getattr(engine, key) for key in COUNTER_FIELDS},
            "maps": {key: getattr(engine, key) for key in MAP_FIELDS},
        }
        payload = json.dumps(_finite(state), separators=(",", ":"), allow_nan=False)
        self.db.conn.execute(
            "INSERT INTO mobile_paper_checkpoint(id,state_json,updated_at) "
            "VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET "
            "state_json=excluded.state_json,updated_at=excluded.updated_at",
            (payload, datetime.now(timezone.utc).isoformat()),
        )
        self.db.conn.commit()

    def restore(self, engine, symbols):
        state = self.load()
        if state is None:
            rows = self.db.load_positions()
            if rows:
                raise ValueError("Open positions exist without account checkpoint; manual review required")
            return False

        # Validate before mutating the engine. The current database is the
        # authority for closed trades, so never resurrect their position IDs.
        balance = float(state["balance"])
        equity = float(state["equity"])
        if not all(math.isfinite(x) for x in (balance, equity)) or balance <= 0:
            raise ValueError("Invalid PAPER account balance")
        ts = state["session_started_at"]
        datetime.fromisoformat(ts)
        items = state["positions"]
        if not isinstance(items, list) or len(items) > 10:
            raise ValueError("Invalid persisted PAPER position count")
        positions = [Position(**item) for item in items]
        ids = [p.id for p in positions]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate PAPER position IDs")
        db_ids = {row["id"] for row in self.db.load_positions()}
        if set(ids) != db_ids:
            # A crash can occur after a trade was committed but before its
            # checkpoint. Never erase such unmatched ledger rows automatically.
            raise ValueError("PAPER positions disagree with SQLite ledger; manual review required")
        if any(p.symbol not in symbols or p.side not in ("BUY", "SELL")
               or p.lots <= 0 or p.risk_amount <= 0
               or not all(math.isfinite(float(n)) for n in
                          (p.entry, p.stop, p.target, p.lots, p.unrealized))
               for p in positions):
            raise ValueError("Invalid PAPER position details")
        if ids:
            placeholders = ",".join("?" for _ in ids)
            row = self.db.conn.execute(
                f"SELECT id FROM trades WHERE id IN ({placeholders}) LIMIT 1", ids
            ).fetchone()
            if row:
                raise ValueError("Persisted PAPER position is already closed; manual review required")
        stored_risk = state["risk"]
        current_risk = {key: getattr(engine.risk, key) for key in RISK_FIELDS}
        if not isinstance(stored_risk, dict) or set(stored_risk) != set(current_risk):
            raise ValueError("Risk state is incomplete")
        for key, old in current_risk.items():
            new = stored_risk[key]
            if type(new) is not type(old) or (type(new) is float and not math.isfinite(new)):
                raise ValueError(f"Invalid PAPER risk field {key}")
        counters, maps = state["counters"], state["maps"]
        if not isinstance(counters, dict) or not isinstance(maps, dict):
            raise ValueError("Invalid PAPER strategy timebase")
        for key in COUNTER_FIELDS:
            if type(counters.get(key)) is not int:
                raise ValueError(f"Invalid PAPER counter {key}")
        for key in MAP_FIELDS:
            if not isinstance(maps.get(key), dict):
                raise ValueError(f"Invalid PAPER timebase {key}")

        engine.balance, engine.equity = balance, equity
        engine.session_started_at = ts
        engine.positions = positions
        for key, value in stored_risk.items():
            setattr(engine.risk, key, value)
        for key, value in counters.items():
            if key in COUNTER_FIELDS:
                setattr(engine, key, value)
        for key, value in maps.items():
            if key in MAP_FIELDS:
                setattr(engine, key, value)
        # Keep execution paused until a complete, fresh real-price scan.
        engine.enabled = False
        engine.last_status = "PAPER account restored from persistent disk; checking LIVE feed"
        self.db.save_positions(positions)
        return bool(state["enabled"])
