"""Local IG DEMO full-FX discovery regressions; NO real IG network calls."""
import unittest
from unittest.mock import patch

from forex_app.instruments import FX_SYMBOLS
from mobile.ig_demo import IGDemoError
from mobile.ig_demo_preflight import _read_only_get
from mobile.ig_forex_catalog import (
    FOREX_SYMBOLS, MAX_PAGE_SIZE, _pair_identity, assess_market,
    preview_forex_page,
)


def market(symbol, epic=None, *, status="CLOSED", instrument_type="CURRENCIES"):
    epic = epic or "CS.D." + symbol + ".CFD.IP"
    return {
        "instrument": {
            "epic": epic, "name": symbol[:3] + "/" + symbol[3:],
            "type": instrument_type, "expiry": "-",
            "unit": "CONTRACTS", "stopsLimitsAllowed": True,
            "contractSize": "10000", "valueOfOnePip": "1",
            "onePipMeans": "0.0001",
            "currencies": [{"code": "NOK"}, {"code": "USD"}],
        },
        "snapshot": {"marketStatus": status, "delayTime": 0,
                     "bid": 1.12, "offer": 1.13},
        "dealingRules": {
            "minDealSize": {"unit": "POINTS", "value": 0.1},
            "minNormalStopOrLimitDistance": {"unit": "POINTS", "value": 20.0},
            "marketOrderPreference": "AVAILABLE_DEFAULT_ON",
        },
    }


class ForexCatalogueTests(unittest.TestCase):
    def test_catalogue_exactly_matches_pc_forex_set_without_mutating_any_config(self):
        self.assertEqual(set(FOREX_SYMBOLS), set(FX_SYMBOLS))
        self.assertIn("USDNOK", FOREX_SYMBOLS)
        self.assertIn("EURNOK", FOREX_SYMBOLS)
        self.assertIn("EURUSD", FOREX_SYMBOLS)
        self.assertIn("USDJPY", FOREX_SYMBOLS)
        self.assertNotIn("BTCUSD", FOREX_SYMBOLS)
        self.assertNotIn("XAUUSD", FOREX_SYMBOLS)
        self.assertGreater(len(FOREX_SYMBOLS), 65)

    def test_identity_rejects_similar_but_wrong_instruments(self):
        self.assertTrue(_pair_identity("USDNOK", "CS.D.USDNOK.CFD.IP"))
        self.assertTrue(_pair_identity("USDNOK", "USD/NOK"))
        self.assertFalse(_pair_identity("USDNOK", "CS.D.USDNOKK.CFD.IP"))
        self.assertFalse(_pair_identity("USDNOK", "USDSEK"))
        self.assertFalse(_pair_identity("USDNOK", "XAUUSD"))

    def test_unknown_binary_instrument_cannot_be_reported_verified(self):
        symbol = "USDNOK"
        epic = "CS.D.USDNOK.CFD.IP"
        raw = market(symbol, epic, status="TRADEABLE", instrument_type="KNOCKOUTS_CURRENCIES")
        result = assess_market(symbol, epic, raw)
        self.assertFalse(result["broker_rules_verified"])
        self.assertFalse(result["identity_verified"])
        self.assertFalse(result["auto_execution_enabled"])

    def test_verified_rules_are_still_readonly_and_never_order_authority(self):
        for pair in ("EURUSD", "USDNOK", "USDJPY"):
            with self.subTest(pair=pair):
                m = assess_market(pair, "CS.D." + pair + ".CFD.IP",
                                  market(pair, status="TRADEABLE"))
                self.assertTrue(m["broker_rules_verified"])
                self.assertFalse(m["auto_execution_enabled"])
                self.assertFalse(m["broker_position_allowed"])
                self.assertTrue(m["read_only"])
                self.assertEqual(m["min_deal_size"], 0.1)

    def test_missing_stop_or_pip_scale_or_delayed_quotes_are_blocked(self):
        epic = "CS.D.GBPUSD.CFD.IP"
        for field in ("stop", "pip", "delay"):
            with self.subTest(field=field):
                m = market("GBPUSD", epic, status="TRADEABLE")
                if field == "stop":
                    m["dealingRules"]["minNormalStopOrLimitDistance"]["unit"] = "PERCENTAGE"
                elif field == "pip":
                    m["instrument"]["onePipMeans"] = "??"
                else:
                    m["snapshot"]["delayTime"] = 1
                result = assess_market("GBPUSD", epic, m)
                self.assertFalse(result["broker_rules_verified"])

    def test_readonly_paging_bounded_and_keeps_credentials_secret(self):
        requested = []
        env = {
            "IG_DEMO_USERNAME": "secret_username",
            "IG_DEMO_API_KEY": "secret_api_key",
            "IG_DEMO_PASSWORD": "secret_password",
        }
        def fake_call(path, **kwargs):
            self.assertEqual(path, "/accounts")
            self.assertEqual(kwargs["key"], "dummy-api-key")
            return {"accounts": [{
                "accountId": "secret-account", "accountType": "CFD",
                "currency": "NOK",
            }]}, {}
        def fake_get(path, **kwargs):
            requested.append((path, kwargs.get("allowed_fx_symbol")))
            if path.startswith("/markets?"):
                symbol = path.removeprefix("/markets?searchTerm=")
                self.assertEqual(kwargs.get("allowed_fx_symbol"), symbol)
                return {"markets": [
                    {"epic": "CS.D." + symbol + ".CFD.IP",
                     "instrumentName": symbol[:3] + "/" + symbol[3:],
                     "instrumentType": "CURRENCIES"},
                    {"epic": "CS.D." + symbol + ".BINARY",
                     "instrumentType": "BINARY"},
                ]}
            self.assertIsNone(kwargs.get("allowed_fx_symbol"))
            epic = path.removeprefix("/markets/")
            symbol = epic.split(".")[2]
            return market(symbol, epic)
        with patch("mobile.ig_forex_catalog._login",
                   return_value=("dummy-api-key", "private-cst", "private-xst", "secret-account")), \
             patch("mobile.ig_forex_catalog.demo_call", side_effect=fake_call), \
             patch("mobile.ig_forex_catalog._read_only_get", side_effect=fake_get):
            result = preview_forex_page(
                offset=FOREX_SYMBOLS.index("USDNOK"), limit=2, environ=env
            )
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["symbol"], "USDNOK")
        self.assertEqual(result["items"][0]["candidates"][0]["epic"], "CS.D.USDNOK.CFD.IP")
        self.assertEqual(len(requested), 4)  # 2 searches + 2 market detail GETs
        self.assertEqual(result["risk_limit_pct"], 0.5)
        self.assertFalse(result["multi_forex_auto_execution_enabled"])
        self.assertTrue(result["existing_eurusd_mini_auto_unchanged"])
        for secret in (*env.values(), "private-cst", "private-xst", "secret-account"):
            self.assertNotIn(secret, str(result))

    def test_bad_page_requests_never_login(self):
        env = {k: "dummy" for k in ("IG_DEMO_USERNAME", "IG_DEMO_PASSWORD", "IG_DEMO_API_KEY")}
        with patch("mobile.ig_forex_catalog._login") as login:
            for offset, limit in ((0, 0), (0, MAX_PAGE_SIZE + 1), (-1, 1),
                                  (len(FOREX_SYMBOLS), 1), (True, 1), (0, 1.5)):
                with self.subTest(offset=offset, limit=limit):
                    with self.assertRaises(ValueError):
                        preview_forex_page(offset=offset, limit=limit, environ=env)
            login.assert_not_called()

    def test_legacy_readonly_allowlist_not_weakened_by_fx_catalogue(self):
        with self.assertRaises(IGDemoError):
            _read_only_get("/markets?searchTerm=USDNOK",
                           key="dummy", cst="dummy", xst="dummy")
        with self.assertRaises(IGDemoError):
            _read_only_get("/markets?searchTerm=XAUUSD",
                           key="dummy", cst="dummy", xst="dummy",
                           allowed_fx_symbol="XAUUSD")
        with self.assertRaises(IGDemoError):
            _read_only_get("/positions/otc", key="dummy", cst="dummy", xst="dummy",
                           allowed_fx_symbol="EURUSD")
        with self.assertRaises(IGDemoError):
            _read_only_get("/markets?searchTerm=USDNOK%26foo%3Dbar",
                           key="dummy", cst="dummy", xst="dummy",
                           allowed_fx_symbol="USDNOK")


if __name__ == "__main__":
    unittest.main()
