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

# IG errorCode values from its published /session API reference.
# Translate only these fixed codes. Never echo an arbitrary error response
# (including account identifiers, tokens, credentials or server-provided text).
IG_ERROR_HINTS = {
    "error.security.invalid-details": "IG DEMO rejected the demo API username or password.",
    "error.security.api-key-invalid": "IG DEMO says the API key is invalid.",
    "error.security.api-key-disabled": "IG DEMO says the API key is disabled.",
    "error.security.api-key-restricted": "IG DEMO says this API key is restricted for this account.",
    "error.security.api-key-revoked": "IG DEMO says the API key has been revoked.",
    "error.security.api-key-missing": "IG DEMO did not receive an API key.",
    "endpoint.unavailable.for.api-key": "IG DEMO says this API key cannot access the requested endpoint.",
    "error.security.too-many-failed-attempts": "Too many failed IG DEMO logins. Wait before testing again.",
    "error.public-api.failure.preferred.account.not.set": "Select a preferred IG DEMO account on the IG website.",
    "error.public-api.failure.preferred.account.disabled": "The preferred IG DEMO account is disabled.",
    "error.public-api.failure.pending.agreements.required": "IG DEMO requires you to accept agreements in the IG web platform.",
    "error.public-api.failure.kyc.required": "IG requires account verification on its web platform.",
    "error.security.account-not-yet-activated": "The IG account has not been activated yet.",
    "error.public-api.failure.missing.credentials": "IG DEMO reports missing login credentials.",
    "error.public-api.exceeded-api-key-allowance": "IG DEMO API request allowance exceeded. Try later.",
    "error.public-api.exceeded-account-allowance": "IG DEMO account request allowance exceeded. Try later.",
    "error.security.account-access-denied": "IG DEMO account access is denied. Check account permissions.",
    "authentication.failure.not-a-client-account": "IG DEMO says these credentials are not for a client account.",
}


def _safe_ig_http_error(exc):
    """Read a *bounded* IG JSON error body, return only fixed approved text.

    HTTP status 401 is usually account credentials/permissions while 403 is
    often API key/allowance. Keeping them distinct helps avoid unnecessary
    API key rotations. Never return raw errorCode or server message.
    """
    code = None
    try:
        payload = json.loads(exc.read(4096).decode("utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("errorCode"), str):
            code = payload["errorCode"]
    except (AttributeError, OSError, ValueError, UnicodeError, TypeError):
        pass
    if code in IG_ERROR_HINTS:
        return IG_ERROR_HINTS[code]
    if exc.code == 401:
        return "IG DEMO login denied (HTTP 401). Check demo API username, password and account permissions."
    if exc.code == 403:
        return "IG DEMO access denied (HTTP 403). Check API key and demo account permissions."
    if exc.code == 429:
        return "IG DEMO request limit reached. Try again later."
    return "IG DEMO request failed (HTTP %d)." % exc.code


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
        # The error response is inspected ONLY for a known fixed IG errorCode.
        # All response fields, account identifiers and auth headers remain private.
        raise IGDemoError(_safe_ig_http_error(exc)) from None
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
