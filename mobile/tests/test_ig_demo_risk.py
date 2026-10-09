"""User-configured IG DEMO 0.5%-risk cap is read-only and fail-closed."""
import unittest
from decimal import Decimal
from unittest.mock import patch

from mobile.ig_demo_risk import policy


class IGDemoRiskPolicyTests(unittest.TestCase):
    def test_half_percent_on_100k_nok(self):
        result = policy(100000, 100000, "NOK")
        self.assertEqual(result["max_planned_risk_pct"], 0.5)
        self.assertEqual(result["max_planned_risk_fraction"], 0.005)
        self.assertEqual(result["indicative_risk_budget"], 500.0)
        self.assertEqual(result["budget_unit"], "NOK")
        self.assertEqual(result["max_open_ig_positions"], 1)
        self.assertFalse(result["automatic_orders_enabled"])
        self.assertFalse(result["risk_sizing_verified"])
        self.assertFalse(result["broker_live_trading_enabled"])
        self.assertFalse(result["risk_budget_is_loss_guarantee"])

    def test_uses_lower_funds_and_rounds_down(self):
        result = policy(100000, 80000, "NOK")
        self.assertEqual(result["indicative_risk_budget"], 400.0)
        result = policy(99999.71, 100000, "NOK")
        self.assertEqual(result["indicative_risk_budget"], 499.99)
        result = policy("120003.99", "100002.99", "NOK")
        self.assertEqual(result["indicative_risk_budget"], 500.01)

    def test_missing_invalid_amounts_or_currency_do_not_authorize_anything(self):
        samples = (
            (None, 100000, "NOK"),
            (100000, None, "NOK"),
            (100000, 100000, ""),
            (100000, 100000, "nOK"),
            (100000, 100000, "LONG"),
            (float("nan"), 100000, "NOK"),
            (100000, float("inf"), "NOK"),
            (100000, -1, "NOK"),
            (0, 100000, "NOK"),
            (True, 100000, "NOK"),
            (100000, False, "NOK"),
            ("secret", 100000, "NOK"),
        )
        for balance, available, currency in samples:
            with self.subTest(balance=balance, available=available, currency=currency):
                result = policy(balance, available, currency)
                self.assertIsNone(result["indicative_risk_budget"])
                self.assertFalse(result["automatic_orders_enabled"])
                self.assertFalse(result["risk_sizing_verified"])

    def test_larger_balance_never_auto_raises_percentage(self):
        for balance in (1000, 10000, 1_000_000):
            with self.subTest(balance=balance):
                status = policy(balance, balance, "NOK")
                self.assertEqual(status["indicative_risk_budget"], balance * 0.005)
                self.assertEqual(status["max_planned_risk_pct"], 0.5)
                self.assertFalse(status["automatic_orders_enabled"])

    def test_policy_api_is_auth_protected_and_independent_from_paper_engine(self):
        from mobile_cloud import app as wsgi
        with patch.object(wsgi, "TOKEN", "risk-demo-auth-test-123456"), \
             patch.object(wsgi, "get_runtime") as paper:
            client = wsgi.app.test_client()
            self.assertEqual(client.get("/api/ig-demo/risk-policy").status_code, 401)
            response = client.get("/api/ig-demo/risk-policy", headers={
                "Authorization": "Bearer risk-demo-auth-test-123456"
            })
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertFalse(data["automatic_orders_enabled"])
            self.assertEqual(data["max_planned_risk_pct"], 0.5)
            self.assertIsNone(data["indicative_risk_budget"])
            paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
