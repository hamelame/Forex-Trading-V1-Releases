"""Tests for the first one-shot IG DEMO Mini broker trial.

No live/demonstration broker credentials or network access are required.
All broker responses are local deterministic mocks.
"""
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from mobile import ig_demo_trial as trial


class FakeResponse:
    def __init__(self, content, headers=None):
        self.data = json.dumps(content).encode()
        self.headers = headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        return False
    def read(self, _max_length):
        return self.data


class FirstIGDemoTradeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = {
            "IG_DEMO_API_KEY": "FAKE_KEY_NEVER_REAL",
            "IG_DEMO_USERNAME": "FAKE_USER_NEVER_REAL",
            "IG_DEMO_PASSWORD": "FAKE_PASSWORD_NEVER_REAL",
        }
        self.calls = []
        self.positions = []
        self.market = self.make_market()
        self.preflight = {
            "environment": "DEMO",
            "account_type": "CFD",
            "existing_ig_positions": 0,
            "broker_order_execution_enabled": False,
            "account_available": 100_000.0,
            "account_currency": "NOK",
            "market_candidates": [self.market],
        }
        self.session = ("FAKE_KEY_NEVER_REAL", "CST_MOCK", "XST_MOCK", "DEMO_ACCOUNT_ONLY")
        self.mock_preflight = patch.object(trial, "preview", side_effect=lambda *args, **kwargs: self.preflight)
        self.mock_login = patch.object(trial, "_login", side_effect=lambda *args, **kwargs: self.session)
        self.mock_get = patch.object(trial, "_read_only_get", side_effect=self.ig_get)
        self.mock_preflight.start()
        self.mock_login.start()
        self.mock_get.start()
        self.addCleanup(self.mock_get.stop)
        self.addCleanup(self.mock_login.stop)
        self.addCleanup(self.mock_preflight.stop)

    def make_market(self):
        return {
            "epic": trial.MINI_EPIC,
            "type": "CURRENCIES",
            "unit": "CONTRACTS",
            "status": "TRADEABLE",
            "stops_allowed": True,
            "expiry": "-",
            "market_order_preference": "AVAILABLE_DEFAULT_OFF",
            "delay_minutes": 0.0,
            "contract_size": "10000",
            "value_of_one_pip": "1.00",
            "minimum_deal_size": {"unit": "POINTS", "value": 0.1},
            "minimum_stop": {"unit": "POINTS", "value": 2.0},
            "bid": 11200.5,
            "offer": 11201.8,
            "currencies": ["USD", "NOK"],
        }

    def ig_get(self, path, *, key, cst, xst, version=1, opener=None):
        self.calls.append(("GET", path))
        self.assertEqual(key, "FAKE_KEY_NEVER_REAL")
        self.assertEqual(cst, "CST_MOCK")
        self.assertEqual(xst, "XST_MOCK")
        if path == "/positions":
            return {"positions": list(self.positions)}
        if path == "/markets/" + trial.MINI_EPIC:
            return {
                "instrument": {
                    "epic": trial.MINI_EPIC, "type": "CURRENCIES",
                    "unit": "CONTRACTS", "expiry": "-",
                    "contractSize": "10000", "valueOfOnePip": "1.00",
                    "stopsLimitsAllowed": True,
                    "currencies": [{"code": "USD"}, {"code": "NOK"}],
                },
                "snapshot": {
                    "marketStatus": "TRADEABLE", "delayTime": 0,
                    "bid": 11200.5, "offer": 11201.8
                },
                "dealingRules": {
                    "minDealSize": {"unit": "POINTS", "value": 0.1},
                    "minNormalStopOrLimitDistance": {"unit": "POINTS", "value": 2.0},
                    "marketOrderPreference": "AVAILABLE_DEFAULT_OFF"
                },
            }
        self.fail("Unexpected read-only broker route: " + path)

    def fake_broker(self, req, timeout):
        self.assertEqual(timeout, 10)
        self.assertEqual(req.full_url.split("/gateway")[0], "https://demo-api.ig.com")
        self.assertNotIn("https://api.ig.com", req.full_url)
        method = req.get_method()
        path = req.full_url.split("/gateway/deal", 1)[1]
        self.calls.append((method, path))
        if method == "POST" and path == "/positions/otc":
            h = {k.lower(): v for k, v in req.header_items()}
            self.assertEqual(h["version"], "2")
            data = json.loads(req.data)
            self.assertEqual(data, {
                "dealReference": data["dealReference"],
                "currencyCode": "NOK", "direction": "BUY", "epic": trial.MINI_EPIC,
                "expiry": "-", "forceOpen": True, "guaranteedStop": False,
                "orderType": "MARKET", "size": 0.1,
                "stopDistance": 20.0, "limitDistance": 40.0,
            })
            self.assertLessEqual(len(data["dealReference"]), 30)
            self.assertEqual(data["currencyCode"], "NOK")
            self.ref = data["dealReference"]
            self.positions = [{
                "position": {
                    "dealId": "DEMO_DEAL_A1",
                    "size": 0.1, "direction": "BUY", "stopLevel": 11181.8,
                },
                "market": {"epic": trial.MINI_EPIC}
            }]
            return FakeResponse({"dealReference": data["dealReference"]})
        if method == "GET" and path.startswith("/confirms/"):
            self.assertEqual(req.get_header("Version"), "1")
            expected_ref = ("CLOSE_REF_123" if path.endswith("CLOSE_REF_123")
                            else getattr(self, "ref", "MISSING_REF"))
            self.assertEqual(path.removeprefix("/confirms/"), expected_ref)
            if path.endswith("CLOSE_REF_123"):
                return FakeResponse({"dealStatus": "ACCEPTED", "dealId": "DEMO_DEAL_A1"})
            return FakeResponse({"dealStatus": "ACCEPTED", "dealId": "DEMO_DEAL_A1"})
        if method == "DELETE" and path == "/positions/otc":
            data = json.loads(req.data)
            self.assertEqual(data, {
                "dealId": "DEMO_DEAL_A1", "direction": "SELL",
                "size": 0.1, "orderType": "MARKET"
            })
            self.assertEqual(req.get_header("Version"), "1")
            self.positions = []
            return FakeResponse({"dealReference": "CLOSE_REF_123"})
        self.fail("Unexpected order operation: %s %s" % (method, path))

    def invoke(self, phrase="PLACE ONE IG DEMO MINI BUY 0.1", opener=None):
        return trial.create_first_demo_trade(
            phrase=phrase, environ=self.env, storage_dir=self.directory.name,
            opener=opener or self.fake_broker,
        )

    def journal(self):
        return Path(self.directory.name) / trial.JOURNAL_FILE

    def test_preview_controls_are_safely_bounded(self):
        currency, market = trial._validate_preflight(self.preflight)
        self.assertEqual(currency, "NOK")
        self.assertEqual(market["epic"], "CS.D.EURUSD.CEEM.IP")
        self.assertEqual(trial.TRIAL_SIZE, 0.1)
        self.assertEqual(trial.TRIAL_STOP_POINTS, 20)
        self.assertEqual(trial.TRIAL_LIMIT_POINTS, 40)
        self.assertEqual(trial.status(self.directory.name)["stage"], "NOT_STARTED")

    def test_one_buy_with_broker_sl_tp_and_verified_position(self):
        result = self.invoke()
        self.assertEqual(result["stage"], "OPEN")
        self.assertTrue(result["broker_stop_verified"])
        self.assertEqual(result["trades_allowed"], 0)
        self.assertFalse(result["ai_trading_enabled"])
        self.assertFalse(result["broker_orders_enabled"])
        self.assertTrue(self.journal().exists())
        self.assertEqual(os.stat(self.journal()).st_mode & 0o777, 0o600)
        for secret in (*self.env.values(), "CST_MOCK", "XST_MOCK", "DEMO_ACCOUNT_ONLY"):
            self.assertNotIn(secret, json.dumps(result))
        self.assertEqual([m for m, _ in self.calls].count("POST"), 1)

        with self.assertRaisesRegex(trial.IGTrialError, "No duplicate"):
            self.invoke()
        self.assertEqual([m for m, _ in self.calls].count("POST"), 1)

    def test_user_must_explicitly_confirm(self):
        with self.assertRaisesRegex(trial.IGTrialError, "confirmation"):
            self.invoke(phrase="yes")
        self.assertFalse(self.journal().exists())
        self.assertEqual(self.calls, [])

    def test_wrong_epic_or_bigger_size_or_wider_stop_is_blocked(self):
        bad = [
            ("epic", "CS.D.EURUSD.CEE.IP"),
            ("contract_size", "100000"),
            ("value_of_one_pip", "10.00"),
            ("status", "CLOSED"),
            ("stops_allowed", False),
            ("delay_minutes", 1),
            ("expiry", "DFB"),
            ("bid", 11205),
            ("offer", 11210),
            ("currencies", []),
            ("market_order_preference", "NOT_AVAILABLE"),
        ]
        for name, new_value in bad:
            with self.subTest(name=name):
                base = self.make_market()
                base[name] = new_value
                p = dict(self.preflight, market_candidates=[base])
                with self.assertRaises(trial.IGTrialError):
                    trial._validate_preflight(p)
        for field, value in [
            ("existing_ig_positions", 1),
            ("environment", "LIVE"),
            ("account_type", "SPREADBET"),
            ("account_available", 0.0),
            ("broker_order_execution_enabled", True),
        ]:
            with self.subTest(field=field):
                p = dict(self.preflight, **{field: value})
                with self.assertRaises(trial.IGTrialError):
                    trial._validate_preflight(p)

    def test_broker_changed_spread_blocks_before_journal_or_submit(self):
        original = self.ig_get
        def changed_quote(path, **kw):
            result = original(path, **kw)
            if path == "/markets/" + trial.MINI_EPIC:
                result["snapshot"]["offer"] = 11220.0
            return result
        with patch.object(trial, "_read_only_get", side_effect=changed_quote):
            with self.assertRaisesRegex(trial.IGTrialError, "spread"):
                self.invoke()
        self.assertFalse(self.journal().exists())
        self.assertEqual([m for m,_ in self.calls].count("POST"), 0)

    def test_broker_changed_stop_rules_blocks_before_submit(self):
        original = self.ig_get
        def changed_rules(path, **kw):
            result = original(path, **kw)
            if path == "/markets/" + trial.MINI_EPIC:
                result["dealingRules"]["minNormalStopOrLimitDistance"]["value"] = 30
            return result
        with patch.object(trial, "_read_only_get", side_effect=changed_rules):
            with self.assertRaisesRegex(trial.IGTrialError, "stop"):
                self.invoke()
        self.assertFalse(self.journal().exists())
        self.assertEqual([m for m,_ in self.calls].count("POST"), 0)

    def test_uncertain_post_response_is_journaled_and_never_retried(self):
        def connection_drop(req, timeout):
            self.calls.append((req.get_method(), req.full_url))
            raise urllib.error.URLError("connection dropped after potential submit")
        result = self.invoke(opener=connection_drop)
        self.assertEqual(result["stage"], "PENDING_RECONCILIATION")
        self.assertEqual(result["trades_allowed"], 0)
        self.assertIn("do not", result["last_note"].lower())
        with self.assertRaisesRegex(trial.IGTrialError, "No duplicate"):
            self.invoke(opener=connection_drop)
        self.assertEqual(len(self.calls), 3)  # two pre-trade GETs + one POST

    def test_close_only_our_known_demo_deal_and_verifies_zero_positions(self):
        initial = self.invoke()
        self.assertEqual(initial["stage"], "OPEN")
        closed = trial.close_first_demo_trade(
            phrase="CLOSE MY IG DEMO MINI TRIAL", environ=self.env,
            storage_dir=self.directory.name, opener=self.fake_broker,
        )
        self.assertEqual(closed["stage"], "CLOSE_ACKNOWLEDGED")
        self.assertEqual([method for method, _ in self.calls].count("DELETE"), 1)
        final = trial.check_first_demo_trade(
            environ=self.env, storage_dir=self.directory.name, opener=self.fake_broker
        )
        self.assertEqual(final["stage"], "CLOSED")
        self.assertEqual(final["trades_allowed"], 0)
        with self.assertRaisesRegex(trial.IGTrialError, "No confirmed"):
            trial.close_first_demo_trade(
                phrase="CLOSE MY IG DEMO MINI TRIAL", environ=self.env,
                storage_dir=self.directory.name, opener=self.fake_broker
            )

    def test_close_requires_explicit_confirmation_and_correct_identity(self):
        self.invoke()
        with self.assertRaisesRegex(trial.IGTrialError, "confirmation"):
            trial.close_first_demo_trade(
                phrase="yes", environ=self.env, storage_dir=self.directory.name,
                opener=self.fake_broker
            )
        self.assertEqual([method for method, _ in self.calls].count("DELETE"), 0)
        self.positions[0]["market"]["epic"] = "CS.D.EURUSD.CEE.IP"
        with self.assertRaisesRegex(trial.IGTrialError, "differs"):
            trial.close_first_demo_trade(
                phrase="CLOSE MY IG DEMO MINI TRIAL", environ=self.env,
                storage_dir=self.directory.name, opener=self.fake_broker
            )
        self.assertEqual([method for method, _ in self.calls].count("DELETE"), 0)

    def test_reject_post_exceptions_without_exposing_credentials(self):
        # Existing journal is never destroyed on HTTP error or failed confirm.
        def rejected(req, timeout):
            self.calls.append((req.get_method(), req.full_url))
            raise urllib.error.HTTPError(
                req.full_url, 400,
                "SECRET " + self.env["IG_DEMO_PASSWORD"], {}, None
            )
        result = self.invoke(opener=rejected)
        self.assertEqual(result["stage"], "PENDING_RECONCILIATION")
        self.assertNotIn(self.env["IG_DEMO_PASSWORD"], str(result))

    def test_mount_is_required_in_production(self):
        with patch.dict(os.environ, {"FX_MOBILE_STORAGE_DIR": ""}):
            with self.assertRaisesRegex(trial.IGTrialError, "persistent"):
                trial.status()
            with self.assertRaisesRegex(trial.IGTrialError, "persistent"):
                trial.create_first_demo_trade(
                    phrase="PLACE ONE IG DEMO MINI BUY 0.1", environ=self.env
                )


class IGTrialWSGITests(unittest.TestCase):
    def test_trial_routes_are_protected_and_never_touch_paper(self):
        from mobile_cloud import app as wsgi
        token = "test-mobile-bearer-safe-123456"
        with patch.object(wsgi, "TOKEN", token), \
             patch.object(wsgi, "get_runtime") as paper, \
             patch.object(wsgi, "ig_trial_status", return_value={"stage": "NOT_STARTED"}), \
             patch.object(wsgi, "create_first_demo_trade", return_value={"stage": "OPEN"}) as opening, \
             patch.object(wsgi, "close_first_demo_trade", return_value={"stage": "CLOSE_ACKNOWLEDGED"}) as closing:
            client = wsgi.app.test_client()
            self.assertEqual(client.get("/api/ig-demo/trial/status").status_code, 401)
            self.assertEqual(client.post("/api/ig-demo/trial/open").status_code, 401)
            self.assertEqual(client.post("/api/ig-demo/trial/close").status_code, 401)
            headers = {"Authorization": "Bearer " + token}
            self.assertEqual(client.get("/api/ig-demo/trial/status", headers=headers).status_code, 200)
            invalid = client.post("/api/ig-demo/trial/open",
                headers=headers, json={"confirm":"yes", "size":100000, "epic":"CS.D.EURUSD.CEE.IP"})
            self.assertEqual(invalid.status_code, 400)
            good = client.post("/api/ig-demo/trial/open", headers=headers,
                json={"confirm":"PLACE ONE IG DEMO MINI BUY 0.1"})
            self.assertEqual(good.status_code, 200)
            self.assertEqual(good.get_json()["stage"], "OPEN")
            opening.assert_called_once_with(phrase="PLACE ONE IG DEMO MINI BUY 0.1")
            done = client.post("/api/ig-demo/trial/close", headers=headers,
                json={"confirm":"CLOSE MY IG DEMO MINI TRIAL"})
            self.assertEqual(done.status_code, 200)
            closing.assert_called_once_with(phrase="CLOSE MY IG DEMO MINI TRIAL")
            paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
