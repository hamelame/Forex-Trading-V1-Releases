"""Safe multi-forex broker sizing unit tests; zero broker HTTP or orders."""
import unittest

from mobile.ig_forex_risk_preview import dry_run_order_plan, IGForexPlanError


def verified(symbol="EURUSD", *, scaling=1, one_pip="0.0001",
             bid=1.1199, offer=1.1201, min_stop=0.0002, pip_value=10.0,
             min_size=0.1):
    return {
        "symbol": symbol, "epic": "CS.D." + symbol + ".CFD.IP",
        "broker_rules_verified": True, "market_status": "TRADEABLE",
        "settlement_currencies": ["NOK"],
        "scaling_factor": scaling, "one_pip_means": one_pip,
        "pip_value": pip_value, "min_deal_size": min_size,
        "min_stop_points": min_stop, "bid": bid, "offer": offer,
    }


def preview(m=None, **changes):
    params = {
        "account_currency": "NOK", "balance": 100000,
        "available": 100000, "market": m or verified(),
        "paper_symbol": "EURUSD", "paper_side": "BUY",
        "paper_entry": 1.12, "paper_stop": 1.116, "paper_target": 1.128,
    }
    params.update(changes)
    return dry_run_order_plan(**params)


class RiskPreviewTests(unittest.TestCase):
    def test_account_broker_limit_is_half_percent_of_lower_nok_funds(self):
        result = preview(available=70000)
        self.assertEqual(result["max_risk_nok"], 350.0)
        self.assertLess(result["risk_with_buffer_nok"], 350.0)
        self.assertFalse(result["send_order_enabled"])
        self.assertTrue(result["requires_forward_broker_validation"])
        self.assertEqual(result["account"], "IG DEMO")

    def test_ig_mini_quoted_in_integer_deal_levels_has_correct_scaling(self):
        m = verified(scaling=10000, bid=11199, offer=11201, min_stop=20)
        result = preview(m)
        self.assertEqual(result["stop_distance_broker_points"], 40)
        self.assertEqual(result["target_distance_broker_points"], 80)
        self.assertLess(result["risk_with_buffer_nok"], 500)

    def test_jpy_pair_stops_are_not_assumed_to_be_0_0001(self):
        m = verified("USDJPY", one_pip="0.01", bid=149.99, offer=150.01,
                     min_stop=0.05)
        result = preview(
            m, paper_symbol="USDJPY", paper_entry=150,
            paper_stop=149.6, paper_target=150.8,
        )
        self.assertEqual(result["symbol"], "USDJPY")
        self.assertEqual(result["stop_distance_broker_points"], 0.4)

    def test_sell_signal_has_correct_stop_and_target_direction(self):
        m = verified()
        result = preview(
            m, paper_side="SELL", paper_stop=1.124, paper_target=1.112
        )
        self.assertEqual(result["direction"], "SELL")
        with self.assertRaises(IGForexPlanError):
            preview(m, paper_side="SELL", paper_stop=1.116, paper_target=1.128)

    def test_fails_closed_for_unsupported_nok_conversion(self):
        m = verified()
        m["settlement_currencies"] = ["USD"]
        with self.assertRaisesRegex(IGForexPlanError, "NOK settlement"):
            preview(m)
        with self.assertRaisesRegex(IGForexPlanError, "NOK"):
            preview(account_currency="EUR")

    def test_fails_closed_for_wrong_symbol_or_missing_broker_rules(self):
        m = verified("GBPUSD")
        with self.assertRaisesRegex(IGForexPlanError, "mismatch"):
            preview(m)
        m = verified()
        m["broker_rules_verified"] = False
        with self.assertRaisesRegex(IGForexPlanError, "unverified"):
            preview(m)

    def test_fails_closed_for_weekend_or_delayed_market(self):
        m = verified()
        m["market_status"] = "CLOSED"
        with self.assertRaisesRegex(IGForexPlanError, "closed"):
            preview(m)

    def test_fails_closed_for_unknown_quote_scaling(self):
        m = verified()
        m["scaling_factor"] = None
        with self.assertRaisesRegex(IGForexPlanError, "scaling"):
            preview(m)

    def test_fails_closed_for_divergent_ig_vs_paper_prices(self):
        m = verified(bid=1.2, offer=1.2002)
        with self.assertRaisesRegex(IGForexPlanError, "diverge"):
            preview(m)

    def test_minimum_size_above_nok_risk_budget_is_blocked(self):
        m = verified(pip_value=2000, min_size=1)
        with self.assertRaisesRegex(IGForexPlanError, "0.5%"):
            preview(m)

    def test_broker_spread_cannot_dominate_stop_distance(self):
        m = verified(bid=1.119, offer=1.1215)
        with self.assertRaisesRegex(IGForexPlanError, "Spread"):
            preview(m)

    def test_minimum_broker_stop_must_be_met(self):
        m = verified(min_stop=0.02)
        with self.assertRaisesRegex(IGForexPlanError, "stop/limit"):
            preview(m)


if __name__ == "__main__":
    unittest.main()
