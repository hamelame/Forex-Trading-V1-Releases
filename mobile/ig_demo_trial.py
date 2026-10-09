"""First IG DEMO virtual-money execution trial, manual and single-use ONLY.

CRITICAL:
* Only the hard-coded IG DEMO gateway is reachable by this module.
* One EUR/USD MINI CFD trade, fixed size 0.1, broker-side SL/TP attached in
  the opening request. No internal PAPER fills/lots/AI changes.
* No auto-trading hooks, no live endpoint, no strategy scheduling.
* A durable, write-ahead journal prevents retry/duplicate on restart,
  crash, HTTP timeout or simultaneous button presses.
* Never return or log IG credentials or session tokens.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from mobile.ig_demo import (
    DEMO_BASE, IGDemoError, _call, _safe_ig_http_error, credentials_ready,
)
from mobile.ig_demo_preflight import _read_only_get, preview

MINI_EPIC = "CS.D.EURUSD.CEEM.IP"
TRIAL_SIZE = 0.1
TRIAL_STOP_POINTS = 20.0
TRIAL_LIMIT_POINTS = 40.0
MAX_PIP_VALUE = 1.01
MAX_CONTRACT_SIZE = 10_000.0
MAX_SPREAD_POINTS = 4.0
JOURNAL_FILE = "ig_demo_first_trade.json"
_LOCK = threading.RLock()
_DEAL_ID = re.compile(r"^[A-Za-z0-9._-]{1,30}$")
_REF = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class IGTrialError(IGDemoError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _storage(storage_dir=None):
    directory = Path(storage_dir or os.environ.get("FX_MOBILE_STORAGE_DIR", "")).resolve()
    if not storage_dir and (not os.path.isabs(os.environ.get("FX_MOBILE_STORAGE_DIR", ""))
                            or not directory.is_dir()
                            or not os.path.ismount(directory)):
        raise IGTrialError("IG DEMO trial requires the mounted persistent Render disk.")
    if not directory.is_dir():
        raise IGTrialError("IG DEMO trial storage is missing.")
    return directory / JOURNAL_FILE


def _load(path):
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("environment") != "DEMO":
            raise ValueError("invalid state")
        return state
    except (ValueError, UnicodeError, OSError):
        raise IGTrialError("IG DEMO journal needs manual review; broker orders blocked.") from None


def _write_new(path, record):
    # O_EXCL is cross-process atomic. If another worker or a previous run has
    # placed a trial, never attempt to submit another deal.
    data = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _update(path, record):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
    data = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _safe_status(record):
    if record is None:
        return {
            "environment": "DEMO", "mode": "MANUAL_FIRST_TRADE",
            "stage": "NOT_STARTED", "trades_allowed": 1,
            "broker_orders_enabled": False, "ai_trading_enabled": False,
            "epic": MINI_EPIC, "size": TRIAL_SIZE,
            "stop_distance_points": TRIAL_STOP_POINTS,
            "limit_distance_points": TRIAL_LIMIT_POINTS,
        }
    return {
        "environment": "DEMO", "mode": "MANUAL_FIRST_TRADE",
        "stage": record.get("stage", "LOCKED"),
        "trades_allowed": 0, "broker_orders_enabled": False,
        "ai_trading_enabled": False,
        "epic": MINI_EPIC, "size": TRIAL_SIZE,
        "stop_distance_points": TRIAL_STOP_POINTS,
        "limit_distance_points": TRIAL_LIMIT_POINTS,
        "created_at": record.get("created_at"),
        "changed_at": record.get("changed_at"),
        "broker_stop_verified": record.get("broker_stop_verified", False),
        "last_note": record.get("last_note", ""),
    }


def status(storage_dir=None):
    with _LOCK:
        return _safe_status(_load(_storage(storage_dir)))


def _positive(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise IGTrialError(label + " was not available from IG DEMO.") from None
    if not math.isfinite(value) or value <= 0:
        raise IGTrialError(label + " was not valid on IG DEMO.")
    return value


def _pick_currency(codes, account_currency):
    allowed = [x for x in codes if isinstance(x, str) and _CURRENCY.fullmatch(x)]
    if account_currency in allowed:
        return account_currency
    if "USD" in allowed:
        return "USD"
    if allowed:
        return allowed[0]
    raise IGTrialError("IG DEMO did not list any supported settlement currency for EUR/USD Mini.")


def _validate_preflight(p):
    if (not isinstance(p, dict) or p.get("environment") != "DEMO"
            or p.get("account_type") != "CFD"
            or p.get("existing_ig_positions") != 0
            or p.get("broker_order_execution_enabled") is not False):
        raise IGTrialError("IG DEMO account risk preflight did not pass.")
    available = _positive(p.get("account_available"), "Available demo funds")
    if available < 1000:
        raise IGTrialError("Too little available IG DEMO margin for a trial.")
    matches = [x for x in p.get("market_candidates", [])
               if isinstance(x, dict) and x.get("epic") == MINI_EPIC]
    if len(matches) != 1:
        raise IGTrialError("Verified IG DEMO EUR/USD Mini EPIC is missing.")
    m = matches[0]
    if (m.get("type") != "CURRENCIES" or m.get("unit") != "CONTRACTS"
            or m.get("status") != "TRADEABLE"
            or m.get("stops_allowed") is not True
            or m.get("expiry") != "-"
            or m.get("market_order_preference") not in ("AVAILABLE_DEFAULT_OFF", "AVAILABLE_DEFAULT_ON")):
        raise IGTrialError("EUR/USD Mini is not currently eligible for safe DEMO orders.")
    if m.get("delay_minutes") != 0:
        raise IGTrialError("IG DEMO EUR/USD Mini price is delayed or not verified as live.")
    contract = _positive(m.get("contract_size"), "Mini contract size")
    if contract > MAX_CONTRACT_SIZE or contract < 1000:
        raise IGTrialError("Unexpected EUR/USD Mini contract size.")
    pip_value = _positive(m.get("value_of_one_pip"), "Mini pip value")
    if pip_value > MAX_PIP_VALUE:
        raise IGTrialError("Unexpected EUR/USD Mini pip value.")
    minimum_size = m.get("minimum_deal_size") or {}
    minimum_stop = m.get("minimum_stop") or {}
    if minimum_size.get("unit") != "POINTS" or minimum_stop.get("unit") != "POINTS":
        raise IGTrialError("IG DEMO trade-size or stop-distance units are unsupported.")
    if _positive(minimum_size.get("value"), "Mini minimum size") > TRIAL_SIZE:
        raise IGTrialError("IG DEMO minimum size exceeds our hard 0.1 Mini limit.")
    if _positive(minimum_stop.get("value"), "Mini minimum stop") > TRIAL_STOP_POINTS:
        raise IGTrialError("IG DEMO requires a stop wider than our trial limit.")
    bid = _positive(m.get("bid"), "IG DEMO bid")
    offer = _positive(m.get("offer"), "IG DEMO offer")
    if not (offer > bid and offer - bid <= MAX_SPREAD_POINTS):
        raise IGTrialError("IG DEMO Mini spread is too wide for a small trial.")
    if TRIAL_STOP_POINTS * TRIAL_SIZE * pip_value > 5.0:
        raise IGTrialError("Trial risk exceeded the broker-denominated hard limit.")
    return _pick_currency(m.get("currencies") or [], p.get("account_currency")), m


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise IGTrialError("IG DEMO returned a redirect; broker orders blocked.")


def _opener(req, timeout=10, open_func=None):
    # Test transports implement the same callable(req, timeout) contract.
    opener = open_func or urllib.request.build_opener(_RejectRedirect()).open
    with opener(req, timeout=timeout) as response:
        body = response.read(20_001)
        if len(body) > 20_000:
            raise IGTrialError("IG DEMO broker response was too large.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise IGTrialError("IG DEMO returned an invalid broker response.") from None
        if not isinstance(data, dict):
            raise IGTrialError("Invalid IG DEMO broker response.")
        return data


def _request(method, path, *, key, cst, xst, payload=None, opener=None):
    if (method, path.split("/", 2)[1]) not in (("POST", "positions"), ("DELETE", "positions"),
                                                  ("GET", "confirms")):
        raise IGTrialError("Unsupported IG DEMO broker action.")
    if method in ("POST", "DELETE") and path != "/positions/otc":
        raise IGTrialError("Unsupported IG DEMO broker order path.")
    if method == "GET" and (not path.startswith("/confirms/")
                           or not _REF.fullmatch(path[len("/confirms/"):])):
        raise IGTrialError("Unsupported deal confirmation request.")
    headers = {
        "X-IG-API-KEY": key, "CST": cst, "X-SECURITY-TOKEN": xst,
        "Accept": "application/json; charset=UTF-8",
        "Content-Type": "application/json",
        "Version": "2" if method == "POST" else "1",
    }
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode() if payload else None
    req = urllib.request.Request(DEMO_BASE + path, data=data, method=method, headers=headers)
    try:
        return _opener(req, timeout=10, open_func=opener)
    except urllib.error.HTTPError as exc:
        raise IGTrialError(_safe_ig_http_error(exc)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise IGTrialError("IG DEMO broker response uncertain. DO NOT retry before reconciliation.") from None


def _login(env, opener=None):
    if not credentials_ready(env):
        raise IGTrialError("IG DEMO credentials are not configured.")
    key = str(env["IG_DEMO_API_KEY"]).strip()
    info, heads = _call(
        "/session", method="POST", key=key,
        body={"identifier": str(env["IG_DEMO_USERNAME"]).strip(),
              "password": str(env["IG_DEMO_PASSWORD"]).strip()},
        opener=opener
    )
    cst = heads.get("CST")
    xst = heads.get("X-SECURITY-TOKEN")
    if not cst or not xst:
        raise IGTrialError("IG DEMO did not return security tokens.")
    if info.get("reroutingEnvironment") not in (None, "DEMO"):
        raise IGTrialError("Non-DEMO account redirect forbidden.")
    if info.get("accountType") != "CFD" or info.get("dealingEnabled") is not True:
        raise IGTrialError("Active IG DEMO CFD account is not enabled.")
    account_id = info.get("currentAccountId")
    if not isinstance(account_id, str) or not account_id:
        raise IGTrialError("IG DEMO did not confirm the active account.")
    accounts, _ = _call("/accounts", key=key, cst=cst, xst=xst, opener=opener)
    matches = [x for x in accounts.get("accounts", [])
               if isinstance(x, dict) and x.get("accountId") == account_id
               and x.get("accountType") == "CFD"]
    if len(matches) != 1:
        raise IGTrialError("IG DEMO CFD account could not be verified.")
    return key, cst, xst, account_id


def _trial_positions(key, cst, xst, opener=None):
    result = _read_only_get(
        "/positions", key=key, cst=cst, xst=xst, version=2, opener=opener
    )
    rows = result.get("positions")
    if not isinstance(rows, list):
        raise IGTrialError("IG DEMO did not return a valid position list.")
    return rows


def _get_trial_position(rows, deal_id):
    for row in rows:
        if isinstance(row, dict):
            p = row.get("position")
            m = row.get("market")
            if isinstance(p, dict) and isinstance(m, dict) and p.get("dealId") == deal_id:
                if (m.get("epic") != MINI_EPIC or
                        abs(float(p.get("size", -1)) - TRIAL_SIZE) > 1e-8 or
                        p.get("direction") != "BUY"):
                    raise IGTrialError("Broker trade identity differs from original DEMO trial.")
                return p
    return None


def _update_confirmation(record, key, cst, xst, *, opener=None):
    try:
        confirm = _request(
            "GET", "/confirms/" + record["deal_reference"],
            key=key, cst=cst, xst=xst, opener=opener
        )
    except IGTrialError:
        record["stage"] = "PENDING_RECONCILIATION"
        record["last_note"] = "Could not confirm broker outcome; do not place another order."
        return record
    if confirm.get("dealStatus") == "REJECTED":
        record["stage"] = "REJECTED"
        record["last_note"] = "IG rejected the trial. No automated retry permitted."
        return record
    if confirm.get("dealStatus") != "ACCEPTED":
        record["stage"] = "PENDING_RECONCILIATION"
        record["last_note"] = "Broker confirmation not final; do not place another order."
        return record
    deal_id = confirm.get("dealId")
    if not isinstance(deal_id, str) or not _DEAL_ID.fullmatch(deal_id):
        record["stage"] = "PENDING_RECONCILIATION"
        record["last_note"] = "Broker deal ID missing; manual review needed."
        return record
    record["deal_id"] = deal_id
    if record.get("stage") == "CLOSE_PENDING":
        record["stage"] = "CLOSE_PENDING"
        record["last_note"] = "Close confirmation pending; check IG account."
        return record
    try:
        rows = _trial_positions(key, cst, xst, opener=opener)
        p = _get_trial_position(rows, deal_id)
    except (IGTrialError, ValueError, TypeError):
        p = None
    if p is None:
        record["stage"] = "PENDING_RECONCILIATION"
        record["last_note"] = "IG acknowledged the order but position not yet verified."
        return record
    record["broker_stop_verified"] = type(p.get("stopLevel")) in (int, float) and math.isfinite(p["stopLevel"])
    record["stage"] = "OPEN" if record["broker_stop_verified"] else "STOP_UNVERIFIED"
    record["last_note"] = (
        "Virtual IG MINI position open with broker-side stop verified."
        if record["broker_stop_verified"]
        else "Stop could not be verified. Close position using IG platform if needed."
    )
    return record


def create_first_demo_trade(*, phrase, environ=None, storage_dir=None, opener=None):
    """This is the ONLY opening path and requires explicit deliberate action."""
    if phrase != "PLACE ONE IG DEMO MINI BUY 0.1":
        raise IGTrialError("Explicit IG DEMO trial confirmation required.")
    env = os.environ if environ is None else environ
    path = _storage(storage_dir)
    with _LOCK:
        if path.exists():
            raise IGTrialError("IG DEMO trial already attempted. No duplicate orders permitted.")
        # Read account and broker metadata before placing ANY durable intent.
        p = preview(env, opener=opener)
        currency, market = _validate_preflight(p)
        key, cst, xst, account_id = _login(env, opener=opener)
        existing = _trial_positions(key, cst, xst, opener=opener)
        if existing:
            raise IGTrialError("IG DEMO account has open positions; first trade blocked.")
        # Refresh mini detail directly at the broker immediately before dealing.
        raw = _read_only_get(
            "/markets/" + MINI_EPIC, key=key, cst=cst, xst=xst,
            version=3, opener=opener
        )
        instrument = raw.get("instrument", {})
        snap = raw.get("snapshot", {})
        rules = raw.get("dealingRules", {})
        if (not isinstance(instrument, dict) or instrument.get("epic") != MINI_EPIC
                or not isinstance(snap, dict) or not isinstance(rules, dict)):
            raise IGTrialError("IG DEMO MINI market details changed; blocked.")
        currencies = instrument.get("currencies")
        if not isinstance(currencies, list):
            raise IGTrialError("IG DEMO MINI currency data missing; blocked.")
        fresh = {
            "epic": MINI_EPIC,
            "type": instrument.get("type"),
            "unit": instrument.get("unit"),
            "status": snap.get("marketStatus"),
            "stops_allowed": instrument.get("stopsLimitsAllowed") is True,
            "expiry": instrument.get("expiry"),
            "market_order_preference": rules.get("marketOrderPreference"),
            "delay_minutes": snap.get("delayTime"),
            "contract_size": instrument.get("contractSize"),
            "value_of_one_pip": instrument.get("valueOfOnePip"),
            "minimum_deal_size": rules.get("minDealSize"),
            "minimum_stop": rules.get("minNormalStopOrLimitDistance"),
            "bid": snap.get("bid"),
            "offer": snap.get("offer"),
            "currencies": [
                str(row.get("code", "")) for row in currencies
                if isinstance(row, dict)
            ],
        }
        # Recompute the entire risk gate with FRESH broker data, not the
        # previous result: quotes, costs and stop rules may change at any time.
        fresh_currency, _ = _validate_preflight({
            **p, "market_candidates": [fresh]
        })
        if fresh_currency != currency:
            raise IGTrialError("IG DEMO Mini settlement currency changed; blocked.")
        new_ref = "FXD-" + uuid.uuid4().hex[:24]
        record = {
            "environment": "DEMO", "stage": "PENDING_SUBMISSION",
            "created_at": _now(), "changed_at": _now(),
            "account_id": account_id, "deal_reference": new_ref,
            "epic": MINI_EPIC, "direction": "BUY", "size": TRIAL_SIZE,
            "stop_distance_points": TRIAL_STOP_POINTS,
            "limit_distance_points": TRIAL_LIMIT_POINTS,
            "broker_stop_verified": False,
            "last_note": "Durably recorded before submit; retry disabled.",
        }
        try:
            _write_new(path, record)
        except FileExistsError:
            raise IGTrialError("Another IG DEMO order test is already in progress.") from None
        payload = {
            "dealReference": new_ref, "currencyCode": currency,
            "direction": "BUY", "epic": MINI_EPIC, "expiry": "-",
            "forceOpen": True, "guaranteedStop": False,
            "orderType": "MARKET", "size": TRIAL_SIZE,
            "stopDistance": TRIAL_STOP_POINTS,
            "limitDistance": TRIAL_LIMIT_POINTS,
        }
        try:
            acknowledgment = _request(
                "POST", "/positions/otc", key=key, cst=cst, xst=xst,
                payload=payload, opener=opener
            )
            if acknowledgment.get("dealReference") != new_ref:
                raise IGTrialError("Unexpected IG DEAL reference; verify the account manually.")
            record["stage"] = "AWAITING_CONFIRMATION"
            record["last_note"] = "IG acknowledged virtual-money order; confirmation pending."
            record = _update_confirmation(record, key, cst, xst, opener=opener)
        except (IGTrialError, ValueError, TypeError):
            record["stage"] = "PENDING_RECONCILIATION"
            record["last_note"] = ("Broker order response uncertain. Check IG DEMO "
                                   "platform. Do not retry or enable automatic trading.")
        record["changed_at"] = _now()
        _update(path, record)
        return _safe_status(record)


def check_first_demo_trade(*, environ=None, storage_dir=None, opener=None):
    """Refreshes an uncertain open confirmation; never submits a trade."""
    path = _storage(storage_dir)
    with _LOCK:
        record = _load(path)
        if record is None:
            return _safe_status(None)
        if record.get("stage") not in ("AWAITING_CONFIRMATION", "PENDING_RECONCILIATION",
                                      "STOP_UNVERIFIED", "OPEN", "CLOSE_PENDING",
                                      "CLOSE_ACKNOWLEDGED", "CLOSE_REJECTED"):
            return _safe_status(record)
        env = os.environ if environ is None else environ
        key, cst, xst, account_id = _login(env, opener=opener)
        if account_id != record.get("account_id"):
            raise IGTrialError("IG DEMO account changed; trial status cannot be reconciled.")
        if record["stage"] in ("AWAITING_CONFIRMATION", "PENDING_RECONCILIATION") or not record.get("deal_id"):
            record = _update_confirmation(record, key, cst, xst, opener=opener)
        else:
            rows = _trial_positions(key, cst, xst, opener=opener)
            p = _get_trial_position(rows, record["deal_id"])
            if record["stage"] in ("CLOSE_PENDING", "CLOSE_ACKNOWLEDGED", "CLOSE_REJECTED"):
                if p is None:
                    record["stage"] = "CLOSED"
                    record["last_note"] = "IG DEMO confirms the trial position is no longer open."
                else:
                    record["last_note"] = "IG trial still open; verify the close in IG DEMO."
            elif p is not None:
                stop = p.get("stopLevel")
                record["broker_stop_verified"] = type(stop) in (int, float) and math.isfinite(stop)
                record["stage"] = "OPEN" if record["broker_stop_verified"] else "STOP_UNVERIFIED"
                record["last_note"] = ("Position and broker-side stop verified."
                                       if record["broker_stop_verified"] else
                                       "Broker-side stop not confirmed; check IG platform.")
            else:
                record["stage"] = "NOT_OPEN_AT_BROKER"
                record["last_note"] = "IG no longer reports this trial as an open position."
        record["changed_at"] = _now()
        _update(path, record)
        return _safe_status(record)


def close_first_demo_trade(*, phrase, environ=None, storage_dir=None, opener=None):
    """Explicit one-time close of only our own dealId, never other IG trades."""
    if phrase != "CLOSE MY IG DEMO MINI TRIAL":
        raise IGTrialError("Explicit IG DEMO close confirmation required.")
    path = _storage(storage_dir)
    with _LOCK:
        record = _load(path)
        if not record or record.get("stage") not in ("OPEN", "STOP_UNVERIFIED"):
            raise IGTrialError("No confirmed IG DEMO trial available for safe closure.")
        deal_id = record.get("deal_id")
        if not isinstance(deal_id, str) or not _DEAL_ID.fullmatch(deal_id):
            raise IGTrialError("Broker deal ID invalid; use IG platform to close.")
        env = os.environ if environ is None else environ
        key, cst, xst, account_id = _login(env, opener=opener)
        if account_id != record.get("account_id"):
            raise IGTrialError("IG DEMO account changed; cannot close unrelated trades.")
        rows = _trial_positions(key, cst, xst, opener=opener)
        p = _get_trial_position(rows, deal_id)
        if p is None:
            raise IGTrialError("Trial position no longer exists; refresh status instead.")
        record["stage"] = "CLOSE_PENDING"
        record["last_note"] = "Close instruction journaled. Never retry automatically."
        record["changed_at"] = _now()
        _update(path, record)
        try:
            out = _request(
                "DELETE", "/positions/otc", key=key, cst=cst, xst=xst,
                payload={"dealId": deal_id, "direction": "SELL",
                         "size": TRIAL_SIZE, "orderType": "MARKET"},
                opener=opener,
            )
            close_ref = out.get("dealReference")
            if not isinstance(close_ref, str) or not _REF.fullmatch(close_ref):
                raise IGTrialError("IG DEMO close reference missing.")
            record["close_reference"] = close_ref
            result = _request("GET", "/confirms/" + close_ref,
                              key=key, cst=cst, xst=xst, opener=opener)
            if result.get("dealStatus") == "ACCEPTED":
                record["stage"] = "CLOSE_ACKNOWLEDGED"
                record["last_note"] = "IG acknowledged closing; refresh and verify position gone."
            elif result.get("dealStatus") == "REJECTED":
                record["stage"] = "CLOSE_REJECTED"
                record["last_note"] = "IG rejected the close. Check the IG DEMO platform."
        except IGTrialError:
            record["last_note"] = "IG close result uncertain; check IG DEMO platform."
        record["changed_at"] = _now()
        _update(path, record)
        return _safe_status(record)
