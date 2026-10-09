"""IG DEMO virtual-funds broker preflight regression and secret redaction."""
import json
import unittest
import urllib.error
from unittest.mock import patch

from mobile import ig_demo_preflight as preflight


class FakeResponse:
    def __init__(self, body, headers=None):
        self.body = json.dumps(body).encode()
        self.headers = headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        return False
    def read(self, _limit):
        return self.body


class BrokerPreflightTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "IG_DEMO_API_KEY": "SECRET_DEMO_KEY",
            "IG_DEMO_USERNAME": "SECRET_DEMO_USER",
            "IG_DEMO_PASSWORD": "SECRET_DEMO_PASSWORD",
        }
        self.calls = []

    def fake_ig(self, req, timeout):
        self.assertEqual(timeout, 10)
        self.assertTrue(req.full_url.startswith(preflight.DEMO_BASE))
        self.assertNotIn("https://api.ig.com", req.full_url)
        path = req.full_url.removeprefix(preflight.DEMO_BASE)
        self.calls.append((req.get_method(), path))
        if path == "/session":
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.get_header("Version"), "2")
            self.assertEqual(json.loads(req.data)["identifier"], self.env["IG_DEMO_USERNAME"])
            return FakeResponse({
                "accountType": "CFD", "reroutingEnvironment": "DEMO",
                "dealingEnabled": True, "currentAccountId": "DEMO123",
            }, headers={"CST": "private-cst", "X-SECURITY-TOKEN": "private-xst"})
        self.assertEqual(req.get_method(), "GET")
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["cst"], "private-cst")
        self.assertEqual(headers["x-security-token"], "private-xst")
        if path == "/accounts":
            self.assertEqual(req.get_header("Version"), "1")
            return FakeResponse({"accounts": [{
                "accountId": "DEMO123", "accountType": "CFD", "currency": "NOK",
                "balance": {"balance": 100000, "available": 99000}
            }]})
        if path == "/positions":
            self.assertEqual(req.get_header("Version"), "2")
            return FakeResponse({"positions": []})
        if path == "/markets?searchTerm=EURUSD":
            self.assertEqual(req.get_header("Version"), "1")
            return FakeResponse({"markets": [
                {"epic": "CS.D.EURUSD.CFD.IP",
                 "instrumentType": "CURRENCIES", "instrumentName": "EUR/USD"},
                {"epic": "OTHER.BET.EURUSD.CFD.NONE",
                 "instrumentType": "BINARY", "instrumentName": "ignore"}
            ]})
        if path == "/markets/CS.D.EURUSD.CFD.IP":
            self.assertEqual(req.get_header("Version"), "3")
            return FakeResponse({
                "instrument": {
                    "name": "EUR/USD", "epic": "CS.D.EURUSD.CFD.IP",
                    "type": "CURRENCIES", "expiry": "-",
                    "contractSize": "100000", "lotSize": 1,
                    "valueOfOnePip": "10", "onePipMeans": "0.0001",
                    "unit": "CONTRACTS", "stopsLimitsAllowed": True,
                    "currencies": [{"code": "USD"}],
                },
                "snapshot": {
                    "marketStatus": "TRADEABLE", "bid": 1.08, "offer": 1.0801,
                    "delayTime": 0,
                },
                "dealingRules": {
                    "minDealSize": {"value": 0.1, "unit": "POINTS"},
                    "minNormalStopOrLimitDistance": {"value": 2, "unit": "POINTS"},
                    "marketOrderPreference": "AVAILABLE_DEFAULT_ON"
                }
            })
        self.fail("Unexpected URL: " + path)

    def test_preflight_retrieves_real_market_rules_but_does_not_trade(self):
        result = preflight.preview(self.env, opener=self.fake_ig)
        self.assertEqual(result["environment"], "DEMO")
        self.assertEqual(result["account_currency"], "NOK")
        self.assertEqual(result["existing_ig_positions"], 0)
        self.assertEqual(result["account_balance"], 100000.0)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["broker_order_execution_enabled"])
        market = result["market_candidates"][0]
        self.assertEqual(market["epic"], "CS.D.EURUSD.CFD.IP")
        self.assertEqual(market["minimum_deal_size"]["value"], 0.1)
        self.assertEqual(market["minimum_stop"]["value"], 2.0)
        self.assertEqual(market["status"], "TRADEABLE")
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.calls[0], ("POST", "/session"))
        self.assertTrue(all(method=="GET" for method, _ in self.calls[1:]))
        for secret in (*self.env.values(), "private-cst", "private-xst", "DEMO123"):
            self.assertNotIn(secret, str(result))

    def test_market_search_accepts_slash_pair_and_non_cfd_epic_after_detail_validation(self):
        """CFD account may use EUR/USD naming and a MINI instrument EPIC."""
        paths = []
        def open_ig(req, timeout):
            path = req.full_url.removeprefix(preflight.DEMO_BASE)
            paths.append((req.get_method(), path))
            if path in ("/session", "/accounts", "/positions"):
                return self.fake_ig(req, timeout)
            if path == "/markets?searchTerm=EURUSD":
                return FakeResponse({"markets": []})
            if path == "/markets?searchTerm=EUR%2FUSD":
                return FakeResponse({"markets": [{
                    "epic": "CS.D.EURUSD.MINI.IP",
                    "instrumentName": "EUR/USD",
                    # Some IG search entries omit instrumentType entirely.
                }]})
            if path == "/markets/CS.D.EURUSD.MINI.IP":
                return FakeResponse({
                    "instrument": {
                        "epic": "CS.D.EURUSD.MINI.IP",
                        "type": "CURRENCIES", "name": "EUR/USD Mini",
                        "expiry": "-", "contractSize": "10000",
                        "stopsLimitsAllowed": True, "valueOfOnePip": "1",
                    },
                    "snapshot": {"marketStatus": "TRADEABLE",
                                 "bid": 1.08, "offer": 1.081, "delayTime": 0},
                    "dealingRules": {
                        "minDealSize": {"value": 0.01, "unit": "POINTS"},
                        "minNormalStopOrLimitDistance": {"value": 4, "unit": "POINTS"},
                    }
                })
            self.fail("Unexpected path: " + path)

        result = preflight.preview(self.env, opener=open_ig)
        self.assertEqual(result["market_candidates"][0]["epic"], "CS.D.EURUSD.MINI.IP")
        self.assertEqual(result["market_candidates"][0]["contract_size"], "10000")
        self.assertEqual(result["market_candidates"][0]["source"], "market search")
        self.assertEqual(result["search_diagnostics"], [
            {"term": "EURUSD", "results": 0, "pair_matches": 0},
            {"term": "EUR/USD", "results": 1, "pair_matches": 1},
        ])
        self.assertTrue(result["instrument_verified"])
        self.assertFalse(result["broker_order_execution_enabled"])
        self.assertEqual(paths[-1], ("GET", "/markets/CS.D.EURUSD.MINI.IP"))
        self.assertTrue(all(method == "GET" for method, _ in paths[1:]))

    def test_known_epic_lookup_is_read_only_last_resort(self):
        """When IG's search catalogue is empty, verify public example EPIC."""
        def open_ig(req, timeout):
            path = req.full_url.removeprefix(preflight.DEMO_BASE)
            if path.startswith("/markets?"):
                self.assertEqual(req.get_method(), "GET")
                return FakeResponse({"markets": []})
            return self.fake_ig(req, timeout)
        result = preflight.preview(self.env, opener=open_ig)
        self.assertTrue(result["instrument_verified"])
        self.assertEqual(result["market_candidates"][0]["source"], "read-only EPIC lookup")
        self.assertEqual(len(result["search_diagnostics"]), 3)
        self.assertFalse(result["broker_order_execution_enabled"])

    def test_non_currency_derivative_never_passes_instrument_details(self):
        def open_ig(req, timeout):
            path = req.full_url.removeprefix(preflight.DEMO_BASE)
            if path == "/markets/CS.D.EURUSD.CFD.IP":
                return FakeResponse({
                    "instrument": {"epic":"CS.D.EURUSD.CFD.IP",
                                   "type":"KNOCKOUTS_CURRENCIES", "name":"EUR/USD"},
                    "snapshot": {"marketStatus": "TRADEABLE"},
                    "dealingRules": {}
                })
            return self.fake_ig(req, timeout)
        result = preflight.preview(self.env, opener=open_ig)
        self.assertFalse(result["instrument_verified"])
        self.assertEqual(result["market_candidates"], [])
        self.assertFalse(result["broker_order_execution_enabled"])

    def test_rejects_extra_search_terms_or_forged_epics(self):
        for term in ("XAUUSD", "EUR%26USD", "EUR%2FUSD&apiKey=secret"):
            with self.subTest(term=term):
                with self.assertRaises(preflight.IGDemoError):
                    preflight._read_only_get("/markets?searchTerm="+term,
                        key="some-key", cst="cst", xst="xst", version=1)
        self.assertEqual(preflight._search_path("EUR/USD"),
                         "/markets?searchTerm=EUR%2FUSD")

    def test_live_reroute_is_rejected_before_orders_or_market_reads(self):
        def live_login(req, timeout):
            return FakeResponse({
                "accountType": "CFD", "reroutingEnvironment": "LIVE",
                "dealingEnabled": True, "currentAccountId": "DEMO123",
            }, headers={"CST": "x", "X-SECURITY-TOKEN": "y"})
        with self.assertRaisesRegex(preflight.IGDemoError, "non-DEMO environment"):
            preflight.preview(self.env, opener=live_login)

    def test_null_rerouting_environment_is_valid_on_pinned_demo_host(self):
        """IG v2 may return reroutingEnvironment=null when no redirect is needed."""
        def demo_login_null(req, timeout):
            if req.full_url.endswith("/session"):
                self.assertTrue(req.full_url.startswith(preflight.DEMO_BASE))
                return FakeResponse({
                    "accountType": "CFD", "reroutingEnvironment": None,
                    "dealingEnabled": True, "currentAccountId": "DEMO123",
                }, headers={"CST": "private-cst", "X-SECURITY-TOKEN": "private-xst"})
            return self.fake_ig(req, timeout)

        result = preflight.preview(self.env, opener=demo_login_null)
        self.assertEqual(result["environment"], "DEMO")
        self.assertEqual(result["account_type"], "CFD")
        self.assertFalse(result["broker_order_execution_enabled"])
        self.assertEqual(result["market_candidates"][0]["symbol"], "EURUSD")
        self.assertTrue(all(method == "GET" for method, _ in self.calls))

    def test_absent_rerouting_environment_is_valid_on_pinned_demo_host(self):
        def demo_login_missing(req, timeout):
            if req.full_url.endswith("/session"):
                return FakeResponse({
                    "accountType": "CFD", "dealingEnabled": True,
                    "currentAccountId": "DEMO123",
                }, headers={"CST": "private-cst", "X-SECURITY-TOKEN": "private-xst"})
            return self.fake_ig(req, timeout)
        result = preflight.preview(self.env, opener=demo_login_missing)
        self.assertEqual(result["environment"], "DEMO")
        self.assertFalse(result["broker_order_execution_enabled"])

    def test_other_reroutes_are_still_rejected(self):
        for destination in ("LIVE", "UAT", "TEST", "", "live", 42):
            with self.subTest(destination=destination):
                def other_redirect(req, timeout):
                    return FakeResponse({
                        "accountType": "CFD", "reroutingEnvironment": destination,
                        "dealingEnabled": True, "currentAccountId": "DEMO123"
                    }, headers={"CST": "private-cst", "X-SECURITY-TOKEN": "private-xst"})
                with self.assertRaisesRegex(preflight.IGDemoError, "non-DEMO environment"):
                    preflight.preview(self.env, opener=other_redirect)

    def test_spreadbet_is_rejected(self):
        def spreadbet(req, timeout):
            return FakeResponse({
                "accountType": "SPREADBET", "reroutingEnvironment": "DEMO",
                "dealingEnabled": True, "currentAccountId": "DEMO123"
            }, headers={"CST": "x", "X-SECURITY-TOKEN": "y"})
        with self.assertRaisesRegex(preflight.IGDemoError, "not a CFD"):
            preflight.preview(self.env, opener=spreadbet)

    def test_disabled_dealing_is_rejected(self):
        def disabled(req, timeout):
            return FakeResponse({
                "accountType": "CFD", "reroutingEnvironment": "DEMO",
                "dealingEnabled": False, "currentAccountId": "DEMO123"
            }, headers={"CST": "x", "X-SECURITY-TOKEN": "y"})
        with self.assertRaisesRegex(preflight.IGDemoError, "not enabled"):
            preflight.preview(self.env, opener=disabled)

    def test_only_allowlisted_read_only_endpoints_work(self):
        for path in ("/positions/otc", "/workingorders", "/markets/invalid!/?a=3",
                     "/markets?searchTerm=XAUUSD", "/accounts"):
            with self.subTest(path=path):
                with self.assertRaises(preflight.IGDemoError):
                    preflight._read_only_get(path,key="test",cst="a",xst="b")
        with self.assertRaises(preflight.IGDemoError):
            preflight._read_only_get("/positions",key="test",cst="a",xst="b",version=1)

    def test_account_identifier_must_match_cfd_demo(self):
        def mismatch(req, timeout):
            if req.full_url.endswith("/session"):
                return self.fake_ig(req, timeout)
            if req.full_url.endswith("/accounts"):
                return FakeResponse({"accounts":[{
                    "accountId":"ANOTHER", "accountType":"CFD",
                    "balance":{"balance":1}, "currency":"USD"}]})
            self.fail("Must fail before market preflight")
        with self.assertRaisesRegex(preflight.IGDemoError, "could not be verified"):
            preflight.preview(self.env, opener=mismatch)

    def test_missing_credentials_do_not_leak(self):
        with self.assertRaisesRegex(preflight.IGDemoError, "not configured"):
            preflight.preview({})


class BrokerPreflightWSGITests(unittest.TestCase):
    def test_manual_broker_preflight_endpoint_has_token_and_no_paper_engine_dependency(self):
        from mobile_cloud import app as wsgi
        with patch.object(wsgi, "TOKEN", "demo-mobile-token-123456"), \
             patch.object(wsgi, "get_runtime") as paper, \
             patch.object(wsgi, "ig_demo_preflight", return_value={
                 "environment":"DEMO","broker_order_execution_enabled":False,
                 "market_candidates":[]}) as preflight_check:
            client = wsgi.app.test_client()
            self.assertEqual(client.post("/api/ig-demo/preflight").status_code, 401)
            ok = client.post("/api/ig-demo/preflight", headers={
                "Authorization": "Bearer demo-mobile-token-123456"})
            self.assertEqual(ok.status_code, 200)
            self.assertFalse(ok.get_json()["broker_order_execution_enabled"])
            preflight_check.assert_called_once()
            paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
