"""FX // AI mobile bridge. Stdlib-only; one process owns one PAPER engine.

Run: python -m mobile.server.  It serves a responsive client and JSON API.
Production requires FX_MOBILE_TOKEN (>=16 chars), HTTPS and a single worker.
"""
from __future__ import annotations

import dataclasses
from collections import Counter
import hmac
import json
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from forex_app import __version__
from forex_app.database import Database
from forex_app.engine import TradingEngine
from forex_app.instruments import instrument_meta
from forex_app.market import SyntheticFeed
from mobile.crypto_feed import MobileLiveMarketFeed
from mobile.paper_persistence import PaperCheckpoint, PreservingMobileDatabase

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 32_768


def _non_crypto_session_open(now=None):
    """Conservative mobile-only weekend gate; NY close/open follows US DST.

    Public quote services may keep reporting Friday's last valid candle. Never
    let an unrelated live crypto candle re-enable non-crypto PAPER entries.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    weekday = moment.weekday()  # Monday=0; Saturday=5, Sunday=6
    return not (weekday == 5 or (weekday == 4 and moment.hour >= 17)
                or (weekday == 6 and moment.hour < 17))

# Deliberately exclude strategy/equity/AI promotion internals. No arbitrary config patching.
EDITABLE = {
    "paper_trading_capital": (float, 1000.0, 100000.0),
    "selection_mode": (str, {"AUTO TOP 5", "AUTO TOP 10", "MANUAL"}),
    "market_scan_top_n": (int, 5, 20),
    "risk_per_trade_pct": (float, 0.05, 0.4),
    "max_total_risk_pct": (float, 0.1, 1.6),
    "max_open_positions": (int, 1, 10),
    "min_signal_score": (int, 50, 95),
    "trading_profile": (str, {"AI TRADING", "MANUAL"}),
    "allow_asia_session": (bool,),
    "allow_london_session": (bool,),
    "allow_new_york_session": (bool,),
    "allow_overlap_session": (bool,),
    "allow_rollover_session": (bool,),
    "self_learning_enabled": (bool,),
    "counterfactual_learning_enabled": (bool,),
}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def clean_value(value):
    """Convert sqlite rows, dataclasses and nested stats into bounded JSON values."""
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): clean_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return clean_value(dict(value))
    except Exception:
        return str(value)


def valid_setting(key, value):
    if key not in EDITABLE:
        return False
    spec = EDITABLE[key]
    typ = spec[0]
    if typ is bool:
        return type(value) is bool
    if typ is str:
        return isinstance(value, str) and value in spec[1]
    return type(value) in (int, float) and math.isfinite(value) and spec[1] <= value <= spec[2]


class MobileTradingEngine(TradingEngine):
    """Fresh multi-asset PAPER; live-priced crypto 24/7, non-crypto in session."""
    entry_asset_scope = "MULTI_MARKET"

    def scan(self):
        """Observe real new-candle admission after the UNMODIFIED parent scan.

        A 'trade-ready' selector row alone is not an order attempt: the
        Champion may have given that signal on a previous closed candle.
        Distinguish new selected BUY/SELL, risk veto, and a successful entry.
        """
        before_cycle = self.decision_cycle_count
        before_candles = dict(self.last_candle_ts)
        before_positions = {p.id for p in self.positions}
        super().scan()
        if not self.enabled or self.decision_cycle_count == before_cycle:
            return
        fresh_symbols = {
            symbol for symbol, ts in self.last_candle_ts.items()
            if before_candles.get(symbol) != ts
        }
        selected = set(self.selector.shortlist)
        ready = [sym for sym in selected if self.decisions.get(sym)
                 and self.decisions[sym].action in ("BUY", "SELL")]
        fresh_ready = [sym for sym in ready if sym in fresh_symbols]
        opened = [p.symbol for p in self.positions if p.id not in before_positions]
        rejects = {}
        for reason, count in self.last_gate_reasons.items():
            root = str(reason).split(" · ", 1)[0][:100]
            rejects[root] = rejects.get(root, 0) + int(count)
        # Never assert that 'SESSION' is a gate merely because a last_status
        # message includes "paper session".
        if opened:
            outcome = "POSITION_OPENED"
        elif rejects:
            outcome = "EXECUTION_REJECTED"
        elif fresh_ready and not self.enabled:
            outcome = "AI_PAUSED"
        elif fresh_ready:
            outcome = "FRESH_SIGNAL_WITHOUT_RECORDED_GATE"
        elif ready:
            outcome = "SELECTED_SIGNAL_FROM_PRIOR_CANDLE"
        else:
            outcome = "NO_SELECTED_BUY_SELL"
        self.mobile_order_audit = {
            "cycle": self.decision_cycle_count,
            "fresh_candle_symbols": sorted(fresh_symbols),
            "selected_buy_sell": [
                {"symbol": sym,
                 "side": self.decisions[sym].action,
                 "regime": str(self.regimes.get(sym, "UNKNOWN")),
                 "score": float(self.decisions[sym].score),
                 "confidence": float(self.decisions[sym].confidence),
                 "candle_timestamp": str(self.decisions[sym].timestamp)}
                for sym in sorted(ready)
            ],
            "fresh_selected_buy_sell": sorted(fresh_ready),
            "recent_opened_symbols": opened,
            "real_execution_rejections": rejects,
            "outcome": outcome,
            "engine_status": str(self.last_status)[:220],
        }

    def _open(self, d, s):
        # This mobile installation remains PAPER ONLY. Never allow synthetic
        # prices, unverified futures delay, a closed weekend or an unrelated
        # fresh crypto candle to authorize an FX/metal/energy position.
        asset = instrument_meta(s.symbol).get("asset_class")
        if self.entry_asset_scope not in ("CRYPTO_ONLY", "MULTI_MARKET"):
            self.last_open_block_reason = "PAPER feed not ready: no verified LIVE market."
            return
        if asset not in ("CRYPTO", "FOREX", "METALS", "ENERGY"):
            self.last_open_block_reason = "Unsupported mobile PAPER asset class."
            return
        if asset != "CRYPTO":
            if self.entry_asset_scope != "MULTI_MARKET" or not _non_crypto_session_open():
                self.last_open_block_reason = "NONCRYPTO_SESSION_OR_FEED_BLOCKED"
                return
            age = float(getattr(s, "data_age_seconds", float("inf")))
            stamp = getattr(s, "timestamp", None)
            try:
                signal_time = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                live_age = (datetime.now(timezone.utc) - signal_time).total_seconds()
            except (ValueError, TypeError, OverflowError):
                live_age = float("inf")
            max_age = float(self.cfg.get("mobile_noncrypto_max_entry_age_seconds", 180))
            if (getattr(s, "feed_status", "") != "LIVE"
                    or not math.isfinite(age) or age < 0 or age > max_age
                    or not math.isfinite(live_age) or live_age < -90
                    or live_age > max_age):
                self.last_open_block_reason = "NONCRYPTO_STALE_OR_DELAYED_PUBLIC_FEED"
                return
        return super()._open(d, s)


class MobileRuntime:
    """Serialize all engine and SQLite access in one lock; scan outside HTTP handlers."""

    def __init__(self, config_path=None, db_path=None, feed=None, start_worker=True):
        self.lock = threading.RLock()
        self.shutdown = threading.Event()
        self.config_path = Path(config_path or os.getenv("FX_MOBILE_CONFIG", str(ROOT / "config.json")))
        self.cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        # Fresh PAPER learning starts on a SEPARATE persistent SQLite ledger,
        # configured by FX_MOBILE_DB on Render. The old 126-trade database remains
        # untouched for reference and cannot poison the fresh strategy.
        # Keep one bounded universe: 8 liquid cryptos, 5 FX majors and 4
        # metals/energy symbols. All non-crypto quotes require LIVE freshness.
        self.full_symbol_count = len(self.cfg["symbols"])
        test_symbols = (
            "BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD",
            "LINKUSD", "ADAUSD", "LTCUSD", "AVAXUSD",
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
            "XAUUSD", "XAGUSD", "WTIUSD", "BRENTUSD",
        )
        self.staged_paper_test = True
        self.cfg["symbols"] = [symbol for symbol in test_symbols if symbol in self.cfg["symbols"]]
        required = {"BTCUSD", "ETHUSD"}
        if not required.issubset(self.cfg["symbols"]):
            raise RuntimeError("Fresh mobile PAPER baseline requires BTCUSD and ETHUSD")
        # Restore only previously validated mutable user controls, not strategy internals.
        self.settings_path = Path(os.getenv("FX_MOBILE_SETTINGS", str(ROOT / "data" / "mobile_settings.json")))
        if self.settings_path.is_file():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                for key, value in saved.items():
                    if key in EDITABLE and valid_setting(key, value):
                        self.cfg[key] = value
                    elif key == "selected_chart_symbol" and value in self.cfg.get("symbols", []):
                        self.cfg[key] = value
                    elif key == "manual_selected_symbols" and isinstance(value, list) and all(
                            isinstance(v, str) and v in self.cfg.get("symbols", []) for v in value):
                        self.cfg[key] = list(dict.fromkeys(value))[:25]
            except (ValueError, OSError, TypeError) as exc:
                logging.warning("Ignoring unreadable mobile settings: %s", exc)
        # The new PAPER database has no historical positive edge. Requiring
        # POSITIVE from trade zero permanently prohibits HIGH VOL learning.
        # Permit strong HIGH VOL setups with tighter sizing; the other RSI,
        # late-entry, cost, context, loss and equity gates remain active.
        self.cfg.update(
            mode="PAPER", paper_only_build=True, broker="synthetic",
            neural_edge_shadow_only=True, selection_mode="AUTO TOP 10",
            market_scan_top_n=10,
            enabled_asset_classes=["CRYPTO", "FOREX", "METALS", "ENERGY"],
            context_crypto_24_7=True,
            context_forex_new_york_enabled=True,
            high_vol_require_positive_edge=False,
            high_vol_min_signal_score=74,
            high_vol_min_confidence=68,
            forex_probation_enabled=True,
            forex_probation_min_score=73,
            forex_probation_min_confidence=66,
            # Futures quotes from Yahoo can be 10-15 minutes delayed. Such
            # quotes must NEVER be executable in a minute-candle strategy.
            mobile_noncrypto_max_entry_age_seconds=180,
            allow_asia_session=True, allow_london_session=True,
            allow_new_york_session=True, allow_overlap_session=True,
            allow_rollover_session=True,
        )
        self.cfg["risk_per_trade_pct"] = min(
            float(self.cfg.get("risk_per_trade_pct", 0.4)), 0.10)
        self.cfg["equity_sizing_max_trade_risk_pct"] = min(
            float(self.cfg.get("equity_sizing_max_trade_risk_pct", 0.18)), 0.10)
        self.cfg["max_total_risk_pct"] = min(
            float(self.cfg.get("max_total_risk_pct", 1.6)), 0.50)
        self.cfg["max_open_positions"] = min(
            int(self.cfg.get("max_open_positions", 5)), 3)
        if self.cfg.get("adaptive_rsi_shadow_only", False):
            # The original config simultaneously set Shadow-only=True AND
            # adaptive_rsi_execution_gate_enabled=True. A research-only RSI
            # should not be permitted to veto a Champion PAPER order.
            # Do not modify common PC config or any other trading gate.
            self.cfg["adaptive_rsi_execution_gate_enabled"] = False
        # A saved old manual list cannot narrow the new clean strategy to
        # historical forex choices. Other user controls remain in settings.
        self.cfg["manual_selected_symbols"] = []
        if self.cfg.get("selected_chart_symbol") not in self.cfg["symbols"]:
            self.cfg["selected_chart_symbol"] = "BTCUSD"
        logging.warning(
            "FRESH_MULTIMARKET_PAPER no IG, symbols=%s, "
            "selection=AUTO TOP 10, risk_per_trade_pct=%.2f, broker=synthetic",
            ",".join(self.cfg["symbols"]), self.cfg["risk_per_trade_pct"],
        )
        # The mobile bridge does not modify the trading strategy defaults.
        db_file = Path(db_path or os.getenv("FX_MOBILE_DB", str(ROOT / "data" / "forex_mobile.db")))
        disk = os.getenv("FX_MOBILE_STORAGE_DIR", "").strip()
        self.durable = bool(disk)
        if self.durable and not db_file.resolve().is_relative_to(Path(disk).resolve()):
            raise RuntimeError("PAPER database must be on the mounted permanent disk")
        self.db = PreservingMobileDatabase(str(db_file)) if self.durable else Database(str(db_file))
        if self.durable:
            # TradingEngine's legacy constructor clears open_positions on
            # every launch. Preserve the mobile ledger during boot instead.
            self.db._preserve_positions_during_boot = True
        self.feed = feed or (MobileLiveMarketFeed(self.cfg["symbols"], self.cfg)
                             if str(self.cfg.get("market_data_mode", "LIVE")).upper() == "LIVE"
                             else SyntheticFeed(self.cfg["symbols"]))
        self.engine = MobileTradingEngine(self.cfg, self.feed, self.db)
        self.checkpoint = PaperCheckpoint(self.db) if self.durable else None
        self.auto_resume_pending = False
        self.persistence_error = None
        if self.checkpoint:
            try:
                self.auto_resume_pending = self.checkpoint.restore(self.engine, self.cfg["symbols"])
                # Persist an initialized account only after successful recovery.
                self.checkpoint.save(self.engine, auto_resume=self.auto_resume_pending)
            except Exception as exc:
                self.persistence_error = f"Recovery requires review: {str(exc)[:150]}"
                self.engine.enabled = False
                logging.exception("PAPER durable account recovery failed; AI remains paused")
        # ONE-TIME opt-in launch for the new PAPER account the user asked to
        # restart. This marker is on the mounted disk, NOT in process memory:
        # later deliberate Pause/Stop survives every server restart. The prior
        # 126-trade ledger has a different filename and is never auto-started.
        if (self.checkpoint and self.persistence_error is None
                and db_file.name == "fx_mobile_fresh_multimarket_20261103.sqlite"):
            marker = Path(disk) / ".fx_mobile_fresh_multimarket_first_start_20261103"
            if not marker.exists():
                try:
                    if (not self.engine.positions
                            and int(self.db.lifetime_trade_summary()["trades"]) == 0):
                        self.auto_resume_pending = True
                        self.checkpoint.save(self.engine, auto_resume=True)
                        marker.write_text("one-time PAPER activation authorized 2026-11-03\n",
                                          encoding="utf-8")
                        logging.warning(
                            "FRESH_MULTIMARKET_PAPER_AUTO_START_QUEUED "
                            "awaiting verified LIVE market data; no IG"
                        )
                except Exception as exc:
                    self.auto_resume_pending = False
                    self.engine.enabled = False
                    try:
                        self.checkpoint.save(self.engine, auto_resume=False)
                    except Exception:
                        pass
                    self.persistence_error = "Fresh PAPER autostart could not be safely recorded"
                    logging.exception("Fresh PAPER activation safely paused: %s", exc)
        self.session_start_capital = self.engine.risk.session_start_balance
        self._cached_learning = {}
        self._learning_at = 0.0
        self.last_scan_at = None
        self.last_scan_error = None
        self.last_scan_completed_at = None
        self._last_feed_warning = 0.0
        # Volatile, observational telemetry only: never stored in checkpoint,
        # never read by selector, Champion, risk or execution.
        self._diag_last_log = 0.0
        self._diag_last_cycle = self.engine.decision_cycle_count
        self._diag_actions = Counter()
        self._diag_gates = Counter()
        self._diag_last_snapshot = {}
        self._diag_last_real_gate = "NONE"
        self._diag_last_gate_cycle = None
        self._diag_order_outcomes = Counter()
        self._diag_last_audit_cycle = self.engine.decision_cycle_count
        self.started_at = utcnow()
        self.worker = None
        self.watchdog = None
        self._last_watchdog_warning_at = 0.0
        # Complete immutable-ish JSON-compatible snapshot BEFORE the scan thread
        # starts. HTTP can always return this if the engine lock gets stuck.
        self._last_known_state = None
        self._scan_started_monotonic = None
        self._lock_warning_at = 0.0
        self._last_known_state = self.state()
        if start_worker:
            self.worker = threading.Thread(target=self._scan_loop, name="paper-engine", daemon=True)
            self.worker.start()
            self.watchdog = threading.Thread(target=self._watch_scanner, name="paper-scan-watchdog", daemon=True)
            self.watchdog.start()

    def _watch_scanner(self):
        """Log stalled-scan call sites without logging user data or secrets."""
        while not self.shutdown.wait(7.0):
            started = self._scan_started_monotonic
            now = time.monotonic()
            if started is None or now - started < 18 or now - self._last_watchdog_warning_at < 45:
                continue
            self._last_watchdog_warning_at = now
            frame = sys._current_frames().get(self.worker.ident if self.worker else None)
            stack = (traceback.extract_stack(frame)[-7:] if frame else [])
            locations = " > ".join(
                f"{Path(f.filename).name}:{f.name}:{f.lineno}" for f in stack
            )
            logging.warning("PAPER scanner stalled %.1fs; call stack: %s", now - started, locations)

    def _scan_loop(self):
        while not self.shutdown.is_set():
            begin = time.monotonic()
            self._scan_started_monotonic = begin
            try:
                with self.lock:
                    # Assess the last completed feed scan before each entry pass.
                    # Every market still refreshes; crypto-only PAPER cannot
                    # open a non-crypto position until full readiness returns.
                    ready = self.trading_readiness()
                    self.engine.entry_asset_scope = ready.get("scope", "BLOCKED")
                    self.engine.scan()
                    if self.auto_resume_pending and self.trading_readiness_after_scan():
                        self.engine.set_enabled(True, reset_on_start=False)
                        self.auto_resume_pending = False
                        logging.info("Previously active PAPER session safely resumed with fresh LIVE candles")
                    self._persist_checkpoint()
                    self.last_scan_at = utcnow()
                    self.last_scan_completed_at = time.monotonic()
                    self.last_scan_error = None
                    self._record_crypto_diagnostics()
                    if not self.engine.snapshots and time.monotonic() - self._last_feed_warning >= 60:
                        self._last_feed_warning = time.monotonic()
                        feed_errors = getattr(self.feed, '_last_error', {})
                        logging.warning('PAPER market data unavailable: provider failures=%d; examples=%r',
                                        len(feed_errors), list(feed_errors.items())[:4])
            except Exception as exc:
                logging.exception("Engine scan failed")
                self.last_scan_error = str(exc)[:240]
            finally:
                self._scan_started_monotonic = None
            delay = (self.cfg.get("scan_interval_seconds", 2) if self.engine.enabled
                     else self.cfg.get("paused_scan_interval_seconds", 4))
            self.shutdown.wait(max(0.2, float(delay) - (time.monotonic() - begin)))

    @staticmethod
    def _rejection_category(reason):
        # Stable categories without sensitive API credentials or high-cardinality
        # numeric values. Research observations do not bypass these gates.
        r = str(reason or "").upper()
        for token, category in (
            ("HIGH_VOL_RESEARCH_ONLY","HIGH_VOL_HISTORY"),
            ("HIGH VOL QUALITY GATE","HIGH_VOL_QUALITY"),
            ("ADAPTIVE RSI GATE","ADAPTIVE_RSI"),
            ("RSI/PRICE GATE","RSI_PRICE"),
            ("GOVERNOR RESEARCH-ONLY","LEARNING_GOVERNOR"),
            ("EVIDENCE QUALITY","EVIDENCE_QUALITY"),
            ("SESSION", "SESSION"),
            ("LATE_ENTRY","LATE_ENTRY"),
            ("POOR_LOCATION","POOR_LOCATION"),
            ("CONTEXT_HEALTH","CONTEXT_HEALTH"),
            ("CONTEXT_CIRCUIT","CONTEXT_CIRCUIT"),
            ("COST", "EXECUTION_COST"),
            ("RISK","RISK"),
        ):
            if token in r:
                return category
        return "OTHER"

    def _record_crypto_diagnostics(self):
        """Observe ONE actual closed-candle evaluation, never force a trade."""
        e = self.engine
        if e.enabled and e.decision_cycle_count != self._diag_last_cycle:
            for d in e.decisions.values():
                self._diag_actions[str(d.action)] += 1
            # Preserve the actual source and cycle of a real veto. 'last_status'
            # is not a reliable veto source and may mention a PAPER session.
            audit = getattr(e, "mobile_order_audit", {}) or {}
            if audit and audit.get("cycle") != self._diag_last_audit_cycle:
                self._diag_order_outcomes[audit.get("outcome", "UNKNOWN")] += 1
                self._diag_last_audit_cycle = audit["cycle"]
            for raw_reason, count in e.last_gate_reasons.items():
                category=self._rejection_category(raw_reason)
                self._diag_gates[category] += int(count)
                self._diag_last_real_gate = category
                self._diag_last_gate_cycle = e.decision_cycle_count
            self._diag_last_cycle = e.decision_cycle_count
        now = time.monotonic()
        if not self._diag_last_snapshot or now-self._diag_last_log >= 120:
            self._diag_last_log = now
            self._diag_last_snapshot = self._crypto_diagnostics()
            d = self._diag_last_snapshot
            logging.warning(
                "CRYPTO_PAPER_DIAG active=%s scope=%s fresh=%s "
                "candles=%s selection=%r actions=%r gates=%r "
                "shadow_rsi=%s high_vol_positive_only=%s last_gate=%s "
                "order_path=%r funnel=%r",
                d["active"],d["readiness_scope"],d["fresh_crypto"],
                d["decision_cycles"],d["selection"],
                d["actions_since_start"],d["order_gate_totals"],
                d["adaptive_rsi_mode"],
                d["high_vol_positive_edge_required"],
                d["last_block_category"],
                d["last_order_path"],d["order_path_counts"],
            )

    def _crypto_diagnostics(self):
        e=self.engine
        readiness=self.trading_readiness()
        actions=Counter(str(d.action) for d in e.decisions.values())
        feed_states=Counter(str(getattr(s,"feed_status","UNKNOWN"))
                            for s in e.snapshots.values())
        last_gates=Counter()
        for reason,count in e.last_gate_reasons.items():
            last_gates[self._rejection_category(reason)] += int(count)
        # Never include account identifiers, secrets or dollar amounts in
        # these diagnostics. They are a summary of PUBLIC market decisions.
        return {
            "active": bool(e.enabled),
            "readiness_scope": readiness.get("scope","BLOCKED"),
            "readiness_reason": readiness.get("reason",""),
            "fresh_crypto": int(readiness.get("fresh_crypto",0)),
            "fresh_non_crypto": int(readiness.get("fresh_non_crypto",0)),
            "paper_strategy_profile": "FRESH MULTI-MARKET · NO IG",
            "new_paper_ledger": True,
            "decision_cycles": int(e.decision_cycle_count),
            "selection": {key:int(e.selector.stats.get(key,0))
                          for key in ("scanned","eligible","selected","trade_ready")},
            "actions_current":dict(actions),
            "feed_status":dict(feed_states),
            "actions_since_start":dict(self._diag_actions),
            "order_gate_totals":dict(self._diag_gates),
            "last_order_gate":dict(last_gates),
            # Only a recorded REJECTION is a true blocker; a stale status
            # like 'AI started · new paper session' is NOT a session veto.
            "last_block_category":self._diag_last_real_gate,
            "last_real_gate_cycle":self._diag_last_gate_cycle,
            "last_order_path":getattr(e,"mobile_order_audit",{}) or {},
            "order_path_counts":dict(self._diag_order_outcomes),
            "adaptive_rsi_mode":("SHADOW ONLY" if
                self.cfg.get("adaptive_rsi_shadow_only",False) and
                not self.cfg.get("adaptive_rsi_execution_gate_enabled",False)
                else "EXECUTION GATE"),
            "high_vol_positive_edge_required":bool(
                self.cfg.get("high_vol_require_positive_edge",True)),
            "market_data_mode":str(self.cfg.get("market_data_mode","LIVE")),
        }

    def _persist_checkpoint(self):
        if self.checkpoint and self.persistence_error:
            raise RuntimeError("PAPER recovery/storage requires review before saving")
        if self.checkpoint:
            try:
                self.checkpoint.save(self.engine, auto_resume=self.auto_resume_pending)
            except Exception as exc:
                self.engine.enabled = False
                self.auto_resume_pending = False
                self.persistence_error = "PAPER storage write failed; AI paused"
                logging.exception("PAPER persistent storage write failed")
                raise RuntimeError("PAPER storage unavailable; AI paused") from exc

    def trading_readiness_after_scan(self):
        # The scan has completed its engine work but last_scan_completed_at
        # belongs to the previous cycle until the caller stamps it.
        if self.persistence_error:
            return False
        previous = self.last_scan_completed_at
        self.last_scan_completed_at = time.monotonic()
        try:
            if not self.trading_readiness()["ready"]:
                return False
            # Existing open positions require trustworthy LIVE quotes too,
            # not merely 3 unrelated healthy markets.
            for p in self.engine.positions:
                snap = self.engine.snapshots.get(p.symbol)
                if snap is None or getattr(snap, "feed_status", "") != "LIVE":
                    return False
                age = float(getattr(snap, "data_age_seconds", float("inf")))
                if not math.isfinite(age) or age > float(self.cfg.get("live_data_delayed_stale_seconds", 1200)):
                    return False
            return True
        finally:
            self.last_scan_completed_at = previous

    def trading_readiness(self):
        """Asset-aware PAPER readiness; preserved weekend forex quotes stay stale."""
        if self.persistence_error:
            return {"ready": False, "scope": "BLOCKED", "fresh": 0,
                    "fresh_crypto": 0, "required": 3, "reason": self.persistence_error}
        if str(self.cfg.get("market_data_mode", "LIVE")).upper() != "LIVE":
            return {"ready": True, "scope": "SYNTHETIC", "fresh": 0,
                    "fresh_crypto": 0, "required": 0,
                    "reason": "Synthetic test feed (not production LIVE pricing)"}
        elapsed = (time.monotonic() - self.last_scan_completed_at
                   if self.last_scan_completed_at is not None else None)
        if elapsed is None or elapsed > 45:
            return {"ready": False, "scope": "BLOCKED", "fresh": 0,
                    "fresh_crypto": 0, "required": 3,
                    "reason": "Market scanner has not completed recently",
                    "last_scan_age_seconds": None if elapsed is None else round(elapsed, 1)}
        fresh = 0
        fresh_crypto = 0
        fresh_non_crypto = 0
        crypto_symbols = []
        for symbol, snap in list(self.engine.snapshots.items()):
            if symbol not in self.cfg["symbols"]:
                continue
            if str(getattr(snap, "feed_status", "")) != "LIVE":
                continue
            try:
                age = float(getattr(snap, "data_age_seconds", float("inf")))
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(age) or age < 0:
                continue
            # The snapshot's original age does not increase while its feed is
            # down. Enforce current UTC freshness of the actual signal candle.
            stamp = getattr(snap, "timestamp", None)
            if stamp:
                try:
                    ts = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        continue
                    since = (datetime.now(timezone.utc) - ts).total_seconds()
                    if since < -90 or not math.isfinite(since):
                        continue
                    age = max(age, since)
                except (ValueError, TypeError, OverflowError):
                    continue
            klass = instrument_meta(symbol).get("asset_class")
            max_age = (
                float(self.cfg.get("live_data_stale_seconds", 180))
                if klass == "CRYPTO" else
                float(self.cfg.get("mobile_noncrypto_max_entry_age_seconds", 180))
            )
            if age <= max_age:
                fresh += 1
                if klass == "CRYPTO":
                    fresh_crypto += 1
                    crypto_symbols.append(symbol)
                else:
                    fresh_non_crypto += 1
        # Multiple crypto quotes do not make FX/metals/indices tradeable.
        # Real non-crypto market presence AND weekend opening are mandatory.
        full = fresh >= 3 and fresh_non_crypto >= 1 and _non_crypto_session_open()
        crypto_only = not full and fresh_crypto >= 1
        scope = "MULTI_MARKET" if full else ("CRYPTO_ONLY" if crypto_only else "BLOCKED")
        return {"ready": full or crypto_only, "scope": scope,
                "fresh": fresh, "fresh_crypto": fresh_crypto,
                "fresh_non_crypto": fresh_non_crypto,
                "crypto_symbols": crypto_symbols,
                "required": 1 if crypto_only else 3,
                "last_scan_age_seconds": round(elapsed, 1),
                "reason": (
                    "Ready for simulated multi-market trading" if full else
                    "CRYPTO 24/7 PAPER ready · non-crypto entries blocked"
                    if crypto_only else
                    "Waiting for 3 fresh LIVE markets or 1 fresh BTC/ETH crypto feed"
                )}

    def _learning(self):
        if time.monotonic() - self._learning_at > 15:
            self._cached_learning = clean_value(self.engine.learning_summary())
            self._learning_at = time.monotonic()
        return self._cached_learning

    def _market(self, sym):
        e = self.engine
        snap = e.snapshots.get(sym)
        decision = e.decisions.get(sym)
        out = {"symbol": sym, "meta": instrument_meta(sym), "rank": round(e.rankings.get(sym, 0), 2),
               "regime": e.regimes.get(sym, "—")}
        if snap:
            for attr in ("mid", "bid", "ask", "spread_pips", "rsi", "atr_pips", "session",
                         "feed_status", "feed_provider", "data_age_seconds", "quality", "timestamp"):
                out[attr] = getattr(snap, attr, None)
        if decision:
            out["decision"] = {k: getattr(decision, k) for k in
                               ("action", "score", "confidence", "reason", "stop_pips", "target_pips", "timestamp")}
        return clean_value(out)

    def _feed_probe(self):
        """Nonblocking live-feed telemetry, safe even during a stuck scan."""
        feed = self.feed
        errors = dict(getattr(feed, "_last_error", {}) or {})
        attempts = dict(getattr(feed, "_last_fetch", {}) or {})
        histories = getattr(feed, "history", {})
        inflight = getattr(feed, "_inflight", set())
        return {
            "fresh_markets": len(self.engine.snapshots),
            "failed_fetches": len(errors),
            "fetch_attempts": len(attempts),
            "inflight": len(inflight),
            "candle_histories": sum(len(values) >= 30 for values in histories.values()),
            "examples": [f"{symbol}: {reason}"[:180] for symbol, reason in list(errors.items())[:6]],
        }

    def state(self):
        # NEVER wait indefinitely for the scanner. The trading thread can be
        # stalled by public quote providers or slow strategy calculations.
        # Return the last fully built snapshot rather than freezing login.
        acquired = self.lock.acquire(timeout=0.3)
        if not acquired:
            snapshot = self._last_known_state
            if snapshot is None:
                raise RuntimeError("Trading engine initializing; retry shortly")
            stale = dict(snapshot)
            elapsed = (time.monotonic() - self._scan_started_monotonic
                       if self._scan_started_monotonic is not None else 0.0)
            stale["state_stale"] = True
            stale["scan_busy_seconds"] = round(max(0.0, elapsed), 1)
            stale["trading_readiness"] = {"ready": False, "fresh": 0, "required": 3,
                                           "last_scan_age_seconds": None,
                                           "reason": "Scanner busy: PAPER trading start is blocked"}
            # Cached login must still report ACTUAL provider failures, not
            # the outdated zero-error snapshot saved before downloads began.
            stale["feed_diagnostics"] = self._feed_probe()
            stale["status"] = "SCANNER BUSY · showing last known PAPER snapshot"
            stale["paper_storage"] = {"persistent": self.durable,
                                      "auto_resume_pending": self.auto_resume_pending,
                                      "error": self.persistence_error}
            if elapsed >= 10 and time.monotonic() - self._lock_warning_at >= 30:
                self._lock_warning_at = time.monotonic()
                # This runs on the HTTP thread even if the watchdog thread
                # itself has stalled. Never log stack source lines or secrets.
                thread_ident = self.worker.ident if self.worker else None
                frame = sys._current_frames().get(thread_ident) if thread_ident else None
                frames = traceback.extract_stack(frame)[-12:] if frame else []
                locations = " > ".join(
                    f"{Path(item.filename).name}:{item.name}:{item.lineno}"
                    for item in frames
                )
                logging.warning(
                    "Mobile scan lock busy %.1fs, scanner_alive=%s watchdog_alive=%s frame=%s",
                    elapsed, bool(self.worker and self.worker.is_alive()),
                    bool(self.watchdog and self.watchdog.is_alive()),
                    locations or "<no frame>")

            return stale
        try:
            e = self.engine
            top = e.top_markets(12)
            ranked = sorted(e.rankings, key=e.rankings.get, reverse=True)
            markets = [self._market(sym) for sym in ranked]
            session_trades = [dict(r) for r in self.db.trades_since(e.session_started_at)[:150]]
            equity_rows = [dict(r) for r in self.db.equity_history_since(e.session_started_at, 160)]
            result = clean_value({
                "version": __version__, "paper_only": True, "running": e.enabled,
                "state_stale": False, "scan_busy_seconds": 0,
                "paper_storage": {"persistent": self.durable,
                                  "auto_resume_pending": self.auto_resume_pending,
                                  "error": self.persistence_error},
                "trading_readiness": self.trading_readiness(),
                "staged_paper_test": self.staged_paper_test,
                "paper_strategy_profile": "FRESH MULTI-MARKET PAPER · NO IG",
                "full_symbol_count": self.full_symbol_count,
                "status": e.last_status, "started_at": self.started_at, "session_started_at": e.session_started_at,
                "last_scan_at": self.last_scan_at, "last_scan_error": self.last_scan_error,
                "market_data_mode": self.cfg.get("market_data_mode", "LIVE"),
                "feed_diagnostics": self._feed_probe(),
                "crypto_diagnostics": self._crypto_diagnostics(),
                "balance": e.balance, "equity": e.equity, "unrealized": e.unrealized_pnl(),
                "realized": e.balance - self.session_start_capital,
                "performance": e.performance(), "lifetime": self.db.lifetime_trade_summary(),
                "risk": self._learning(), "selection": e.selection_summary(),
                "exposure": e.exposure_summary(), "currency_strength": e.strength_summary(),
                "neural": e.neural.summary(), "adaptive_rsi": e.adaptive_rsi.summary(),
                "positions": e.positions, "trades": session_trades, "equity_history": equity_rows,
                "decisions": [dict(r) for r in self.db.recent_decisions_since(e.session_started_at, 70)],
                "top_markets": top, "markets": markets,
                "settings": {key: self.cfg.get(key) for key in EDITABLE},
                "selected_chart_symbol": self.cfg.get("selected_chart_symbol", "EURUSD"),
                "manual_selected_symbols": self.cfg.get("manual_selected_symbols", []),
                "symbols": self.cfg.get("symbols", []),
                "replays": [dict(r) for r in self.db.recent_trade_replays(75)],
            })
            self._last_known_state = result
            return result
        finally:
            self.lock.release()

    def candles(self, symbol, max_bars=150):
        with self.lock:
            if symbol not in self.cfg["symbols"]:
                raise ValueError("Unknown symbol")
            fun = getattr(self.feed, "candle_window", None)
            if not callable(fun):
                return {"symbol": symbol, "candles": [], "note": "OHLC unavailable for synthetic feed"}
            return {"symbol": symbol, "candles": fun(symbol, max_bars=max_bars)}

    def replay(self, trade_id):
        with self.lock:
            data = self.db.trade_replay(trade_id)
            if not data:
                raise KeyError("Replay not found")
            return data

    def command(self, name, payload):
        # Avoid hanging user controls while a public provider or scanner is busy.
        if not self.lock.acquire(timeout=1.0):
            raise ValueError("Scanner busy: try again when LIVE scan finishes")
        try:
            e = self.engine
            if name == "start":
                if self.persistence_error:
                    raise ValueError(self.persistence_error)
                if e.enabled:
                    return {"message": "AI already active", "running": True}
                readiness = self.trading_readiness()
                if not readiness["ready"]:
                    raise ValueError("PAPER start blocked: " + readiness["reason"])
                e.entry_asset_scope = readiness.get("scope", "MULTI_MARKET")
                if e.positions:
                    # Preserve any paused open PAPER positions; never reset them silently.
                    e.set_enabled(True, reset_on_start=False)
                else:
                    reset = payload.get("new_session", False)
                    if type(reset) is not bool: raise ValueError("new_session must be boolean")
                    e.set_enabled(True, reset_on_start=reset)
                    if reset: self.session_start_capital = e.balance
                self.auto_resume_pending = False
                self._persist_checkpoint()
                return {"message": e.last_status, "running": True}
            if name == "pause":
                self.auto_resume_pending = False
                e.set_enabled(False, reset_on_start=False)
                self._persist_checkpoint()
                return {"message": "AI paused; paper positions remain open"}
            if name == "stop":
                close = payload.get("close_positions", False)
                if type(close) is not bool:
                    raise ValueError("close_positions must be boolean")
                e.set_enabled(False, reset_on_start=False)
                self.auto_resume_pending = False
                count = e.close_all_positions("Mobile Stop · Close All") if close else 0
                self._persist_checkpoint()
                return {"message": "AI stopped", "positions_closed": count}
            if name == "close-all":
                if e.enabled:
                    raise ValueError("Pause AI before closing positions")
                n = e.close_all_positions("Mobile Manual Close All")
                self._persist_checkpoint()
                return {"message": f"Closed {n} PAPER positions", "count": n}
            if name == "new-session":
                if e.enabled or e.positions:
                    raise ValueError("Pause AI and close all open positions before resetting")
                e.reset_paper_session()
                self.session_start_capital = e.balance
                self._persist_checkpoint()
                return {"message": e.last_status}
            if name == "refresh":
                # Worker handles the full refresh; this API never forces network I/O.
                e.selector.force_reselect()
                return {"message": "Market re-selection queued for next scan"}
            if name == "select-chart":
                sym = payload.get("symbol")
                if sym not in self.cfg["symbols"]:
                    raise ValueError("Invalid market symbol")
                self.cfg["selected_chart_symbol"] = sym
                self._save_config()
                return {"message": "Chart locked", "symbol": sym}
            if name == "manual-symbols":
                values = payload.get("symbols")
                if not isinstance(values, list) or len(values) > 25 or not values or any(
                        type(v) is not str or v not in self.cfg["symbols"] for v in values):
                    raise ValueError("Choose 1–25 valid market symbols")
                self.cfg["manual_selected_symbols"] = list(dict.fromkeys(values))
                e.apply_config(self.cfg, force_reselect=True)
                self._save_config()
                return {"message": "Manual market watchlist saved"}
            if name == "settings":
                values = payload.get("values")
                if not isinstance(values, dict) or not values or set(values) - set(EDITABLE):
                    raise ValueError("Unknown settings or empty payload")
                changes = {}
                for key, value in values.items():
                    if not valid_setting(key, value):
                        raise ValueError(f"{key}: invalid type or outside allowed range")
                    changes[key] = EDITABLE[key][0](value)
                if "paper_trading_capital" in changes:
                    e.apply_paper_capital(changes.pop("paper_trading_capital"), start_new_session=False)
                self.cfg.update(changes)
                # Temporary rollback contract is hard CRYPTO ONLY / 24x7.
                # Ordinary risk/quality settings remain editable. A saved
                # forex-era manual or rollover setting cannot silently halt
                # crypto after Settings is saved.
                self.cfg.update(
                    selection_mode="AUTO TOP 10",market_scan_top_n=10,
                    enabled_asset_classes=["CRYPTO", "FOREX", "METALS", "ENERGY"],
                    context_crypto_24_7=True,
                    context_forex_new_york_enabled=True,
                    allow_asia_session=True,allow_london_session=True,
                    allow_new_york_session=True,allow_overlap_session=True,
                    allow_rollover_session=True,
                )
                self.cfg["risk_per_trade_pct"] = min(
                    float(self.cfg.get("risk_per_trade_pct", 0.1)), 0.10)
                self.cfg["max_total_risk_pct"] = min(
                    float(self.cfg.get("max_total_risk_pct", 0.5)), 0.50)
                self.cfg["max_open_positions"] = min(
                    int(self.cfg.get("max_open_positions", 3)), 3)
                e.apply_config(self.cfg, force_reselect=bool({"selection_mode", "market_scan_top_n"} & set(changes)))
                self._save_config()
                return {"message": "Settings saved. Capital applies on next new session."}
            raise KeyError("Unknown command")
        finally:
            self.lock.release()

    def _save_config(self):
        """Store mobile runtime overrides separately; do not overwrite PC config by accident."""
        path = self.settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        content = {key: self.cfg.get(key) for key in EDITABLE}
        content.update(selected_chart_symbol=self.cfg.get("selected_chart_symbol", "EURUSD"),
                       manual_selected_symbols=self.cfg.get("manual_selected_symbols", []))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(content, indent=2), encoding="utf-8")
        tmp.replace(path)

    def close(self):
        self.shutdown.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=3)
        with self.lock:
            close = getattr(self.feed, "close", None)
            if callable(close): close()
            if not self.persistence_error:
                self._persist_checkpoint()
            self.db.conn.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, runtime, token):
        super().__init__(addr, Handler)
        self.runtime = runtime
        self.token = token


class Handler(BaseHTTPRequestHandler):
    server_version = "FXAIMobile/2.9.2.6"

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.address_string(), fmt % args)

    def _json(self, obj, code=200):
        body = json.dumps(clean_value(obj), separators=(",", ":"), allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        expected = self.server.token
        supplied = self.headers.get("Authorization", "")
        if not expected:
            return self.client_address[0] in ("127.0.0.1", "::1")
        return hmac.compare_digest(supplied.encode(), ("Bearer " + expected).encode())

    def _serve(self, path):
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
                  "/icon.svg": ("icon.svg", "image/svg+xml")}
        if path not in assets:
            self._json({"error": "Not found"}, 404)
            return
        fname, mime = assets[path]
        body = (WEB / fname).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            # Liveness only. No positions or trading data without authentication.
            return self._json({"ok": True, "service": "fx-ai-mobile"})
        if not parsed.path.startswith("/api/"):
            return self._serve(parsed.path)
        if not self._authorized():
            return self._json({"error": "Access token required"}, 401)
        try:
            if parsed.path == "/api/state":
                return self._json(self.server.runtime.state())
            if parsed.path == "/api/candles":
                args = parse_qs(parsed.query)
                symbol = args.get("symbol", [""])[0]
                return self._json(self.server.runtime.candles(symbol))
            if parsed.path == "/api/replay":
                args = parse_qs(parsed.query)
                trade_id = args.get("trade_id", [""])[0]
                if len(trade_id) > 100: raise ValueError("Trade ID too long")
                return self._json(self.server.runtime.replay(trade_id))
            return self._json({"error": "Unknown endpoint"}, 404)
        except (KeyError, ValueError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception:
            logging.exception("Read error")
            return self._json({"error": "Internal server error"}, 500)

    def do_POST(self):
        if not self.path.startswith("/api/command/") or "?" in self.path:
            return self._json({"error": "Not found"}, 404)
        if not self._authorized():
            return self._json({"error": "Access token required"}, 401)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY:
                return self._json({"error": "Invalid request size"}, 413)
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json({"error": "JSON required"}, 415)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("JSON object expected")
            output = self.server.runtime.command(self.path.removeprefix("/api/command/"), body)
            return self._json(output)
        except (ValueError, KeyError, UnicodeError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception:
            logging.exception("Command error")
            return self._json({"error": "Internal server error"}, 500)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.chdir(ROOT)  # engine's existing relative learning paths still work
    host = os.getenv("FX_MOBILE_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", os.getenv("FX_MOBILE_PORT", "8000")))
    token = os.getenv("FX_MOBILE_TOKEN", "").strip()
    if host not in ("127.0.0.1", "::1", "localhost") and len(token) < 16:
        sys.exit("Set FX_MOBILE_TOKEN to a random secret of at least 16 characters before exposing server")
    if host not in ("127.0.0.1", "::1", "localhost") and os.getenv("FX_MOBILE_ALLOW_INSECURE_HTTP") == "1":
        logging.warning("Remote use requires an HTTPS reverse proxy (Render handles HTTPS externally)")
    runtime = MobileRuntime()
    srv = Server((host, port), runtime, token)
    logging.info("FX // AI mobile %s: http://%s:%s (PAPER-only, starts PAUSED)", __version__, host, port)
    try:
        srv.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        runtime.close()


if __name__ == "__main__":
    main()
