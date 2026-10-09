"""IG DEMO broker diagnostic, deliberately READ-ONLY.

This module cannot place/modify/close orders. It talks exclusively to IG's
official DEMO gateway. Credentials come from Render environment variables and
are never exposed in API responses, telemetry, disk files or GitHub.

It is intentionally separate from the running PAPER engine and does not
participate in market scans or automated broker execution.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request


# There is no LIVE endpoint in this module.
DEMO_BASE = "https://demo-api.ig.com/gateway/deal"
ENV_NAMES = ("IG_DEMO_API_KEY", "IG_DEMO_USERNAME", "IG_DEMO_PASSWORD")
MAX_BYTES = 1_000_000


class IGDemoError(Exception):
    """Safe, human-readable error that never includes credentials or tokens."""


def credentials_ready(environ=None):
    env = os.environ if environ is None else environ
    return all(bool(str(env.get(name, "")).strip()) for name in ENV_NAMES)


def status(environ=None):
    """Safe to call frequently: never opens a network connection."""
    return {
        "provider": "IG",
        "environment": "DEMO",
        "read_only": True,
        "credentials_configured": credentials_ready(environ),
        "broker_orders_enabled": False,
    }


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(float(value), 2)


def _call(path, *, method="GET", key, cst=None, xst=None, body=None, opener=None):
    if path not in ("/session", "/accounts"):
        raise IGDemoError("Unsupported IG DEMO read-only endpoint")
    url = DEMO_BASE + path
    headers = {
        "X-IG-API-KEY": key,
        "Accept": "application/json; charset=UTF-8",
        "Content-Type": "application/json",
        "Version": "2" if path == "/session" else "1",
    }
    if cst and xst:
        headers["CST"] = cst
        headers["X-SECURITY-TOKEN"] = xst
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=10) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise IGDemoError("IG DEMO response was too large")
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise IGDemoError("Invalid IG DEMO response")
            return parsed, response.headers
    except urllib.error.HTTPError as exc:
        # Never propagate API error bodies, which may contain account metadata.
        if exc.code in (401, 403):
            raise IGDemoError("IG DEMO authentication rejected. Check demo API username, password and key.") from None
        if exc.code == 429:
            raise IGDemoError("IG DEMO request limit reached; try again later.") from None
        raise IGDemoError("IG DEMO is unavailable (HTTP %s)." % exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise IGDemoError("IG DEMO could not be reached; try again later.") from None
    except (UnicodeError, json.JSONDecodeError):
        raise IGDemoError("IG DEMO returned an invalid response.") from None


def check_connection(environ=None, opener=None):
    """Explicit manual diagnostic only. Login + account GET; no broker orders.

    Return only minimum account health data. No username, password, key, CST,
    X-SECURITY-TOKEN, bearer, raw server data or client identifiers.
    """
    env = os.environ if environ is None else environ
    if not credentials_ready(env):
        raise IGDemoError(
            "IG demo credentials missing in Render Environment: "
            "IG_DEMO_API_KEY, IG_DEMO_USERNAME, IG_DEMO_PASSWORD"
        )
    key = str(env["IG_DEMO_API_KEY"]).strip()
    ident = str(env["IG_DEMO_USERNAME"]).strip()
    password = str(env["IG_DEMO_PASSWORD"]).strip()
    info, headers = _call(
        "/session", method="POST", key=key,
        body={"identifier": ident, "password": password}, opener=opener
    )
    cst = headers.get("CST")
    xst = headers.get("X-SECURITY-TOKEN")
    if not cst or not xst:
        raise IGDemoError("IG DEMO login did not return expected security tokens.")
    accounts, _ = _call("/accounts", key=key, cst=cst, xst=xst, opener=opener)
    rows = accounts.get("accounts")
    if not isinstance(rows, list):
        raise IGDemoError("IG DEMO did not return an account list.")
    summary = []
    for account in rows[:15]:
        if not isinstance(account, dict):
            continue
        balances = account.get("balance")
        if not isinstance(balances, dict):
            balances = {}
        summary.append({
            "type": str(account.get("accountType", "UNKNOWN"))[:24],
            "currency": str(account.get("currency", "UNKNOWN"))[:6],
            "balance": _number(balances.get("balance")),
            "available": _number(balances.get("available")),
        })
    if not summary:
        raise IGDemoError("IG DEMO account list was empty.")
    return {
        "connected": True,
        "environment": "DEMO",
        "read_only": True,
        "broker_orders_enabled": False,
        "account_count": len(summary),
        "accounts": summary,
    }
