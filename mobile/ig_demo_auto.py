"""Staged, explicit IG DEMO AI signal execution.

Only mirrors *new* EURUSD PAPER positions when the operator manually arms
this module. The PAPER engine is never changed and its lot size is NEVER used.

Risk rollout v1:
  - IG DEMO only, EUR/USD Mini CFD epic, fixed minimum 0.1 size
  - one broker position at a time, no more than 2 opening attempts per UTC day
  - broker-side 20-point stop and 40-point target included at creation
  - planned-risk guard <=0.5% of lower of IG DEMO balance/available funds
  - conservative 20x FX/execution buffer for a known NOK-based mini instrument
  - no entry unless PAPER actually opened a new position after arming
  - stop means stop new entries; manage/verify already opened positions
  - durable journal *before* sending any broker order, no duplicate retries
  - restart ALWAYS disarms automatic entries; old broker positions need review
  - ambiguous send/confirm/close disables entries and demands manual review.

Not a guaranteed maximum loss: normal stops can slip and carry extra costs.
Notional/currency assumptions are allowed only for verified IG NOK Mini CFD
contract with known size and pip value; other instruments are blocked.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from mobile.ig_demo import IGDemoError
from mobile.ig_demo_preflight import preview, _read_only_get
from mobile.ig_demo_risk import policy as broker_policy
from mobile.ig_demo_trial import (
    IGTrialError, MINI_EPIC, TRIAL_SIZE, TRIAL_STOP_POINTS,
    TRIAL_LIMIT_POINTS, MAX_PIP_VALUE, _validate_preflight, _login,
    _trial_positions, _request,
)

NAME = "IG_DEMO_AUTO_EURUSD_MINI_V1"
JOURNAL = "ig_demo_auto_state.json"
START_PHRASE = "START IG DEMO AUTO 0.5%"
STOP_PHRASE = "STOP IG DEMO AUTO"
MAX_TRADES_DAY = 2
MAX_BROKER_POSITIONS = 1
# Conservative NOK risk assumption; never treat it as guaranteed FX.
NOK_PER_PIP_CURRENCY_BUFFER = Decimal("20")
COST_BUFFER_NOK = Decimal("25")
MAX_ALLOWED_MARKET_SPREAD = Decimal("4")
_EPIC = MINI_EPIC
_DEAL_ID = re.compile(r"^[A-Za-z0-9._-]{1,30}$")
_REF = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
_PAPER_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _number(x):
    try:
        if isinstance(x, bool):
            return None
        out = Decimal(str(x))
        return out if out.is_finite() else None
    except (ValueError, TypeError, ArithmeticError):
        return None


def _storage(directory=None):
    root = Path(directory or os.getenv("FX_MOBILE_STORAGE_DIR", "")).resolve()
    if not directory and (not os.path.isabs(os.getenv("FX_MOBILE_STORAGE_DIR", ""))
                          or not root.is_dir() or not os.path.ismount(root)):
        raise IGTrialError("IG DEMO AUTO requires the persistent Render disk.")
    if not root.is_dir():
        raise IGTrialError("IG DEMO AUTO storage missing.")
    return root / JOURNAL


def _save(path, record):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex[:10] + ".tmp")
    raw = json.dumps(record, allow_nan=False, separators=(",", ":")).encode()
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _load(path):
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text("utf-8"))
        if not isinstance(obj, dict) or obj.get("mode") != NAME:
            raise ValueError("Unknown broker journal")
        return obj
    except (ValueError, UnicodeError, OSError):
        raise IGTrialError("IG DEMO AUTO journal invalid. Do not trade; inspect storage.") from None


def _initial():
    return {
        "mode": NAME, "environment": "DEMO", "armed": False,
        "stage": "STOPPED", "day": _today(), "attempts_today": 0,
        "max_attempts_today": MAX_TRADES_DAY, "paper_baseline_ids": [],
        "last_paper_id": None, "paper_id": None, "paper_direction": None,
        "deal_reference": None, "deal_id": None, "broker_stop_verified": False,
        "reconciled_close_by_api": False,
        "opening_risk_budget_nok": None, "estimated_risk_buffer_nok": None,
        "initial_balance_nok": None, "note": "IG DEMO AUTO is OFF; PAPER remains separate.",
        "updated_at": _now(),
    }


def _state_public(s):
    fields = (
        "mode", "environment", "armed", "stage", "day", "attempts_today",
        "max_attempts_today", "paper_direction", "broker_stop_verified",
        "reconciled_close_by_api", "opening_risk_budget_nok",
        "estimated_risk_buffer_nok", "note", "updated_at",
    )
    result = {k: s.get(k) for k in fields}
    result["risk_percent"] = 0.5
    result["allowed_symbol"] = "EURUSD"
    result["broker_instrument"] = "EUR/USD Mini"
    result["epic"] = MINI_EPIC
    result["broker_size"] = float(TRIAL_SIZE)
    result["max_open_broker_positions"] = MAX_BROKER_POSITIONS
    result["stop_distance"] = float(TRIAL_STOP_POINTS)
    result["take_profit_distance"] = float(TRIAL_LIMIT_POINTS)
    result["real_money_enabled"] = False
    result["no_auto_resume_after_restart"] = True
    return result


def _paper_ready(snapshot):
    if not isinstance(snapshot, dict):
        return False
    if (snapshot.get("engine_running") is not True
            or snapshot.get("paper_storage_ready") is not True
            or snapshot.get("market_data_mode") != "LIVE"
            or snapshot.get("scan_fresh") is not True):
        return False
    return True


def _candidate(snapshot, excluded):
    if not _paper_ready(snapshot):
        return None
    if snapshot.get("eurusd_feed_status") != "LIVE":
        return None
    seconds = _number(snapshot.get("eurusd_feed_age_seconds"))
    if seconds is None or seconds < 0 or seconds > 120:
        return None
    for p in snapshot.get("paper_positions", []):
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        if (not isinstance(pid, str) or not _PAPER_ID.fullmatch(pid)
                or pid in excluded or p.get("symbol") != "EURUSD"
                or p.get("side") not in ("BUY", "SELL")):
            continue
        opened = p.get("opened_at")
        if not isinstance(opened, str):
            continue
        try:
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(opened.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            continue
        if not 0 <= age <= 180:
            continue
        return {"id": pid, "direction": p["side"]}
    return None


def _position(rows, deal_id, side):
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = row.get("position")
        m = row.get("market")
        if not isinstance(p, dict) or not isinstance(m, dict):
            continue
        if p.get("dealId") != deal_id:
            continue
        if (m.get("epic") != MINI_EPIC or p.get("direction") != side
                or _number(p.get("size")) != Decimal("0.1")):
            raise IGTrialError("IG DEMO position identity changed. Auto blocked.")
        return p
    return None


def _stops_verified(p):
    return (
        isinstance(p, dict)
        and _number(p.get("stopLevel")) is not None
        and _number(p.get("limitLevel")) is not None
    )


def _safe_risk(preflight):
    """Fail closed on unknown NOK-vs-pip exposure or marginal equity.

    We deliberately take >=20 NOK per unit of quoted pip-value, *before*
    considering broker spread and a 25 NOK cost reserve. Only the EURUSD
    MINI known contract is supported; no other instrument is eligible.
    This is a planning envelope, not an FX guarantee or risk guarantee.
    """
    _, m = _validate_preflight(preflight)
    if preflight.get("account_currency") != "NOK":
        raise IGTrialError("IG DEMO AUTO only supports verified NOK CFD demo accounts.")
    if "NOK" not in m.get("currencies", []):
        raise IGTrialError("IG Mini NOK settlement currency unavailable.")
    if m.get("epic") != MINI_EPIC or str(m.get("contract_size")) not in ("10000", "10000.0"):
        raise IGTrialError("IG Mini instrument contract changed; AUTO blocked.")
    pip_value = _number(m.get("value_of_one_pip"))
    spread = _number(m.get("offer")) - _number(m.get("bid"))
    if pip_value is None or pip_value <= 0 or pip_value > Decimal("1.01"):
        raise IGTrialError("IG Mini pip value is not verified.")
    if spread <= 0 or spread > MAX_ALLOWED_MARKET_SPREAD:
        raise IGTrialError("IG Mini spread too wide for AUTO.")
    p = broker_policy(preflight.get("account_balance"),
                      preflight.get("account_available"), "NOK")
    budget = _number(p["indicative_risk_budget"])
    if budget is None:
        raise IGTrialError("IG DEMO equity is unknown; AUTO blocked.")
    planned_nok = (
        (Decimal(str(TRIAL_STOP_POINTS)) + spread) *
        Decimal(str(TRIAL_SIZE)) * pip_value *
        NOK_PER_PIP_CURRENCY_BUFFER + COST_BUFFER_NOK
    )
    if planned_nok > budget:
        raise IGTrialError("IG Mini minimum size exceeds the 0.5% planned risk budget.")
    # Defense-in-depth: no automatic execution on a near-empty account.
    if budget < Decimal("50"):
        raise IGTrialError("IG DEMO risk budget too small for first auto rollout.")
    return float(budget), float(planned_nok)


class IGDemoAuto:
    """Single-worker operator-armed engine; no activity without an API start."""

    def __init__(self, paper_snapshot, *, storage_dir=None, background=True):
        self.paper_snapshot = paper_snapshot
        self.path = _storage(storage_dir)
        self.lock = threading.RLock()
        self.wakeup = threading.Event()
        self.closed = False
        self.thread = None
        saved = _load(self.path)
        self.state = saved or _initial()
        if saved and saved.get("armed"):
            self.state["armed"] = False
            self.state["note"] = (
                "Render restarted. AUTO disarmed; check the IG DEMO account "
                "and any open broker positions before rearming."
            )
            self.state["updated_at"] = _now()
            _save(self.path, self.state)
        if background:
            self.thread = threading.Thread(
                target=self._loop, name="ig-demo-auto", daemon=True
            )
            self.thread.start()

    def _persist(self):
        self.state["updated_at"] = _now()
        _save(self.path, self.state)

    def status(self):
        with self.lock:
            return _state_public(self.state)

    def start(self, phrase):
        if phrase != START_PHRASE:
            raise IGTrialError("Explicit IG DEMO AUTO 0.5% start confirmation required.")
        with self.lock:
            st = self.state
            if st.get("armed"):
                return _state_public(st)
            if st.get("stage") not in ("STOPPED", "CLOSED", "WATCHING"):
                raise IGTrialError("An unfinished IG broker operation needs review first.")
            if st.get("day") != _today():
                st["day"] = _today()
                st["attempts_today"] = 0
            if st["attempts_today"] >= MAX_TRADES_DAY:
                raise IGTrialError("Daily IG DEMO AUTO rollout limit reached.")
            paper = self.paper_snapshot()
            if not _paper_ready(paper):
                raise IGTrialError("PAPER AI or live EURUSD feed not ready; AUTO cannot start.")
            p = preview()
            budget, planned = _safe_risk(p)
            if p.get("existing_ig_positions") != 0:
                raise IGTrialError("IG DEMO already has open positions. AUTO blocked.")
            # No new trades from PAPER positions which existed before arming.
            st["paper_baseline_ids"] = [
                v.get("id") for v in paper.get("paper_positions", [])
                if isinstance(v, dict) and isinstance(v.get("id"), str)
            ][:100]
            st["opening_risk_budget_nok"] = budget
            st["estimated_risk_buffer_nok"] = planned
            st["initial_balance_nok"] = p.get("account_balance")
            st["stage"] = "WATCHING"
            st["armed"] = True
            st["note"] = (
                "IG DEMO AUTO armed for NEW EURUSD PAPER entries only. "
                "0.1 Mini; stop 20 points; 0.5% risk gate. No real funds."
            )
            self._persist()
            self.wakeup.set()
            return _state_public(st)

    def stop(self, phrase):
        if phrase != STOP_PHRASE:
            raise IGTrialError("Explicit IG DEMO AUTO stop confirmation required.")
        with self.lock:
            self.state["armed"] = False
            if self.state["stage"] == "WATCHING":
                self.state["stage"] = "STOPPED"
            self.state["note"] = (
                "New IG DEMO entries stopped. Any existing broker position "
                "remains protected by its broker-side stop; monitor it in IG."
            )
            self._persist()
            self.wakeup.set()
            return _state_public(self.state)

    def _block(self, why, stage="BLOCKED"):
        self.state["armed"] = False
        self.state["stage"] = stage
        self.state["note"] = str(why)[:260]
        self._persist()

    def tick(self):
        """One bounded iteration. No new order unless explicit start persisted."""
        with self.lock:
            st = self.state
            if not st.get("armed") and st.get("stage") not in ("OPEN", "CLOSE_PENDING"):
                return _state_public(st)
            if st.get("stage") in ("OPEN", "CLOSE_PENDING"):
                self._manage_open()
                return _state_public(st)
            if not st.get("armed") or st.get("stage") != "WATCHING":
                return _state_public(st)
            paper = self.paper_snapshot()
            excluded = set(st.get("paper_baseline_ids", []))
            if st.get("last_paper_id"):
                excluded.add(st["last_paper_id"])
            signal = _candidate(paper, excluded)
            if signal is None:
                return _state_public(st)
            # Put this candidate into durable state BEFORE external requests.
            # If any broker checks fail, pause instead of risking repeated tries.
            st["last_paper_id"] = signal["id"]
            st["paper_id"] = signal["id"]
            st["paper_direction"] = signal["direction"]
            self._persist()
            try:
                self._place(signal)
            except (IGTrialError, IGDemoError, ValueError, TypeError) as exc:
                self._block(str(exc))
            except Exception:
                self._block("IG DEMO automatic entry encountered unexpected error. Check IG account.")
            return _state_public(st)

    def _place(self, signal):
        st = self.state
        if st["attempts_today"] >= MAX_TRADES_DAY:
            raise IGTrialError("Daily IG DEMO trade cap hit; AUTO stopped.")
        # Validate active PAPER trade remained eligible, not just a stored ID.
        still = self.paper_snapshot()
        live_signal = _candidate(still, set(st.get("paper_baseline_ids", [])))
        if not live_signal or live_signal != signal:
            raise IGTrialError("PAPER trade changed or data became stale before IG entry.")
        p = preview()
        budget, planned = _safe_risk(p)
        if p.get("existing_ig_positions") != 0:
            raise IGTrialError("Unexpected IG DEMO broker position. No new orders.")
        previous = _number(st.get("initial_balance_nok"))
        current = _number(p.get("account_balance"))
        if previous and current and current < previous * Decimal("0.99"):
            raise IGTrialError("IG DEMO daily account drawdown reached 1%. New entries stopped.")
        key, cst, xst, _ = _login()
        if _trial_positions(key, cst, xst):
            raise IGTrialError("IG DEMO position appeared before order. Entry cancelled.")
        # Re-evaluate broker quote, contract, spread, stops and settlement currency
        # immediately before committing the order intent.
        market = _read_only_get(
            "/markets/" + MINI_EPIC, key=key, cst=cst, xst=xst, version=3
        )
        ins, snap, rules = (market.get("instrument") or {},
                            market.get("snapshot") or {},
                            market.get("dealingRules") or {})
        currencies = ins.get("currencies") or []
        raw = {
            "epic": ins.get("epic"), "type": ins.get("type"),
            "unit": ins.get("unit"), "status": snap.get("marketStatus"),
            "stops_allowed": ins.get("stopsLimitsAllowed") is True,
            "expiry": ins.get("expiry"),
            "market_order_preference": rules.get("marketOrderPreference"),
            "delay_minutes": snap.get("delayTime"),
            "contract_size": ins.get("contractSize"),
            "value_of_one_pip": ins.get("valueOfOnePip"),
            "minimum_deal_size": rules.get("minDealSize"),
            "minimum_stop": rules.get("minNormalStopOrLimitDistance"),
            "bid": snap.get("bid"), "offer": snap.get("offer"),
            "currencies": [
                z.get("code") for z in currencies if isinstance(z, dict)
            ],
        }
        fresh = {**p, "market_candidates": [raw]}
        fresh_budget, fresh_planned = _safe_risk(fresh)
        if fresh_budget < planned or fresh_planned > budget:
            raise IGTrialError("IG DEMO risk changed since initial verification.")
        reference = "FXA-" + uuid.uuid4().hex[:24]
        st["deal_reference"] = reference
        st["deal_id"] = None
        st["stage"] = "ORDER_PENDING"
        st["broker_stop_verified"] = False
        st["opening_risk_budget_nok"] = fresh_budget
        st["estimated_risk_buffer_nok"] = fresh_planned
        st["attempts_today"] += 1
        st["note"] = "Order intent written before broker submission; no retries allowed."
        self._persist()
        order = {
            "dealReference": reference, "currencyCode": "NOK",
            "direction": signal["direction"], "epic": MINI_EPIC,
            "expiry": "-", "forceOpen": True, "guaranteedStop": False,
            "orderType": "MARKET", "size": float(TRIAL_SIZE),
            "stopDistance": float(TRIAL_STOP_POINTS),
            "limitDistance": float(TRIAL_LIMIT_POINTS),
        }
        try:
            acknowledgment = _request(
                "POST", "/positions/otc", key=key, cst=cst, xst=xst,
                payload=order
            )
            if acknowledgment.get("dealReference") != reference:
                raise IGTrialError("IG DEMO order reference mismatch.")
            result = _request(
                "GET", "/confirms/" + reference, key=key, cst=cst, xst=xst
            )
            if result.get("dealStatus") != "ACCEPTED":
                raise IGTrialError("IG DEMO order confirmation not accepted.")
            deal_id = result.get("dealId")
            if not isinstance(deal_id, str) or not _DEAL_ID.fullmatch(deal_id):
                raise IGTrialError("IG DEMO broker deal ID missing.")
            st["deal_id"] = deal_id
            rows = _trial_positions(key, cst, xst)
            pos = _position(rows, deal_id, signal["direction"])
            if not _stops_verified(pos):
                raise IGTrialError("IG DEMO broker-side stop AND target not verified.")
            st["broker_stop_verified"] = True
            st["stage"] = "OPEN"
            st["note"] = "IG DEMO Mini position opened with broker-side stop and target."
            self._persist()
        except Exception:
            self._block(
                "IG DEMO entry status uncertain or broker protection unverified. "
                "Check IG DEMO positions manually. Never repeat the order.",
                stage="REVIEW_REQUIRED"
            )

    def _manage_open(self):
        st = self.state
        deal_id, side = st.get("deal_id"), st.get("paper_direction")
        if not isinstance(deal_id, str) or not _DEAL_ID.fullmatch(deal_id):
            self._block("IG DEMO deal ID unavailable; manual broker review required.")
            return
        try:
            key, cst, xst, _ = _login()
            rows = _trial_positions(key, cst, xst)
            pos = _position(rows, deal_id, side)
            if pos is None:
                st["stage"] = "CLOSED"
                st["armed"] = False  # cautious rollout, one trade per explicit arm
                st["note"] = "IG no longer reports the position open; verify closure/history in IG."
                self._persist()
                return
            if not _stops_verified(pos):
                self._block("IG DEMO lost broker-side stop or target. Review IG immediately.")
                return
            st["broker_stop_verified"] = True
            if st["stage"] == "CLOSE_PENDING":
                # Never submit the same close instruction a second time.
                self._block("IG DEMO close not confirmed; check IG directly. No retry.",
                            stage="REVIEW_REQUIRED")
                return
            # User stopped new entries: keep safety checks and close when
            # PAPER strategy exits this exact trade; never close other trades.
            paper = self.paper_snapshot()
            ids = {
                q.get("id") for q in paper.get("paper_positions", [])
                if isinstance(q, dict)
            }
            if st.get("paper_id") in ids:
                return
            st["stage"] = "CLOSE_PENDING"
            st["note"] = "Automatic close intent recorded. No duplicate retries."
            self._persist()
            direction = "SELL" if side == "BUY" else "BUY"
            out = _request(
                "DELETE", "/positions/otc", key=key, cst=cst, xst=xst,
                payload={
                    "dealId": deal_id, "direction": direction,
                    "size": float(TRIAL_SIZE), "orderType": "MARKET"
                }
            )
            ref = out.get("dealReference")
            if not isinstance(ref, str) or not _REF.fullmatch(ref):
                raise IGTrialError("IG DEMO auto-close reference missing.")
            confirmation = _request(
                "GET", "/confirms/" + ref, key=key, cst=cst, xst=xst
            )
            if confirmation.get("dealStatus") != "ACCEPTED":
                raise IGTrialError("IG DEMO auto close not accepted.")
            remaining = _trial_positions(key, cst, xst)
            if _position(remaining, deal_id, side) is not None:
                raise IGTrialError("IG DEMO close confirmed but position still visible.")
            st["stage"] = "CLOSED"
            st["armed"] = False
            st["reconciled_close_by_api"] = True
            st["note"] = "IG DEMO automatic API close confirmed, position absent."
            self._persist()
        except (IGTrialError, IGDemoError, ValueError, TypeError):
            self._block(
                "IG DEMO position or close could not be confirmed. "
                "No automatic retry; check IG platform.",
                stage="REVIEW_REQUIRED"
            )
        except Exception:
            self._block(
                "IG DEMO broker reconciliation failed unexpectedly; check IG platform.",
                stage="REVIEW_REQUIRED"
            )

    def _loop(self):
        while not self.closed:
            self.wakeup.wait(12)
            self.wakeup.clear()
            if self.closed:
                return
            try:
                self.tick()
            except Exception:
                with self.lock:
                    self._block(
                        "Unexpected IG DEMO AUTO worker error. AUTO disarmed."
                    )

    def close(self):
        with self.lock:
            self.closed = True
            self.wakeup.set()
            self.state["armed"] = False
            self._persist()
