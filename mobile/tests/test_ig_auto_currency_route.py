"""IG Mini order currency and NOK loss preview never sends broker orders."""
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

os.environ.setdefault("FX_MOBILE_TOKEN", "dummy-local-test-token-987654321")

from mobile import ig_demo_auto as auto
from mobile.ig_demo_trial import IGTrialError
from mobile_cloud import app as wsgi
from mobile.tests.test_ig_demo_auto import broker_preflight, live_market


class CurrencyRiskPreviewTests(unittest.TestCase):
    def test_readonly_preview_uses_ig_broker_rate_for_usd_settlement(self):
        p = broker_preflight()
        p["market_candidates"][0]["currencies"] = ["USD"]
        with patch.object(auto, "preview", return_value=p) as preview, \
             patch.object(auto, "_login",
                          return_value=("sample-key", "sample-cst", "sample-xst", "DEMO")) as login, \
             patch.object(auto, "validated_usdnok_ask", return_value=Decimal("10.24")) as fx, \
             patch.object(auto, "_request") as broker_order:
            data = auto.currency_risk_preview()
        preview.assert_called_once()
        login.assert_called_once()
        fx.assert_called_once_with("sample-key", "sample-cst", "sample-xst")
        broker_order.assert_not_called()
        self.assertEqual(data["account_currency"], "NOK")
        self.assertEqual(data["order_settlement_currency"], "USD")
        self.assertEqual(data["broker_conversion_nok_per_usd"], 10.24)
        self.assertFalse(data["broker_orders_sent"])
        self.assertLess(data["estimated_planned_risk_nok"], data["risk_budget_nok"])

    def test_nok_settlement_does_not_require_extra_fx_login(self):
        p = broker_preflight()
        with patch.object(auto, "preview", return_value=p), \
             patch.object(auto, "_login") as login, \
             patch.object(auto, "validated_usdnok_ask") as fx, \
             patch.object(auto, "_request") as broker_order:
            data = auto.currency_risk_preview()
        self.assertEqual(data["order_settlement_currency"], "NOK")
        self.assertIsNone(data["broker_conversion_nok_per_usd"])
        broker_order.assert_not_called()
        login.assert_not_called()
        fx.assert_not_called()

    def test_instrument_unsupported_currency_is_blocked(self):
        p=broker_preflight()
        p["market_candidates"][0]["currencies"]=["JPY"]
        with patch.object(auto, "preview", return_value=p), \
             patch.object(auto, "_request") as broker_order:
            with self.assertRaisesRegex(IGTrialError, "settlement currency"):
                auto.currency_risk_preview()
        broker_order.assert_not_called()


class CurrencyRiskRouteTests(unittest.TestCase):
    def setUp(self):
        self.token="demo-local-only-test-123456789"
        self.auth={"Authorization":"Bearer "+self.token}
        self.client=wsgi.app.test_client()

    def test_route_requires_auth_and_disallows_get(self):
        self.assertEqual(self.client.post("/api/ig-demo/auto/currency-risk").status_code,401)
        self.assertEqual(self.client.get("/api/ig-demo/auto/currency-risk").status_code,405)

    def test_route_returns_sanitized_preview_without_touching_paper(self):
        payload={
            "environment":"DEMO", "account_currency":"NOK",
            "order_settlement_currency":"USD",
            "risk_budget_nok":500.0,"estimated_planned_risk_nok":72.4,
            "broker_orders_sent":False,
        }
        with patch.object(wsgi,"TOKEN",self.token), \
             patch.object(wsgi,"ig_auto_currency_risk_preview",return_value=payload) as preview, \
             patch.object(wsgi,"get_runtime") as paper:
            response=self.client.post("/api/ig-demo/auto/currency-risk",headers=self.auth)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json(),payload)
        paper.assert_not_called()
        preview.assert_called_once()

    def test_blocked_broker_fx_quote_returns_http_400_and_no_order(self):
        with patch.object(wsgi,"TOKEN",self.token), \
             patch.object(wsgi,"ig_auto_currency_risk_preview",
                          side_effect=IGTrialError("USD/NOK conversion unavailable")), \
             patch.object(wsgi,"get_runtime") as paper:
            response=self.client.post("/api/ig-demo/auto/currency-risk",headers=self.auth)
        self.assertEqual(response.status_code,400)
        self.assertIn("conversion unavailable",response.get_json()["error"])
        paper.assert_not_called()


if __name__=="__main__":
    unittest.main()
