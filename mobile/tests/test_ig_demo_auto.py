"""Deterministic staged IG DEMO AUTO tests with fake broker calls only.

NEVER sends any requests to IG. Validates that the independent PAPER scanner
is not modified, broker DEMO safeguards, restart-disarm, and durable idempotency.
"""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mobile import ig_demo_auto as auto
from mobile.ig_demo_trial import IGTrialError


def fresh_paper(items=None, **changes):
    obj = {
        "engine_running": True, "paper_storage_ready": True,
        "market_data_mode": "LIVE", "scan_fresh": True,
        "eurusd_feed_status": "LIVE", "eurusd_feed_age_seconds": 15,
        "paper_positions": list(items or []),
    }
    obj.update(changes)
    return obj


def position(pid, side="BUY", age=None):
    return {
        "id": pid, "symbol": "EURUSD", "side": side,
        "opened_at": datetime.now(timezone.utc).isoformat() if age is None else age,
    }


def live_market():
    return {
        "epic": auto.MINI_EPIC, "type": "CURRENCIES", "unit": "CONTRACTS",
        "status": "TRADEABLE", "stops_allowed": True, "expiry": "-",
        "market_order_preference": "AVAILABLE_DEFAULT_OFF",
        "delay_minutes": 0.0, "contract_size": "10000",
        "value_of_one_pip": "1.00", "minimum_deal_size": {
            "unit": "POINTS", "value": 0.1,
        }, "minimum_stop": {"unit": "POINTS", "value": 2.0},
        "bid": 11200.5, "offer": 11201.8,
        "currencies": ["USD", "NOK"],
    }


def broker_preflight():
    return {
        "environment": "DEMO", "account_type": "CFD",
        "broker_order_execution_enabled": False,
        "account_balance": 100000, "account_available": 100000,
        "account_currency": "NOK", "existing_ig_positions": 0,
        "market_candidates": [live_market()],
    }


def raw_market():
    m = live_market()
    return {
        "instrument": {
            "epic": m["epic"], "type": m["type"], "unit": m["unit"],
            "stopsLimitsAllowed": m["stops_allowed"], "expiry": m["expiry"],
            "contractSize": m["contract_size"], "valueOfOnePip": m["value_of_one_pip"],
            "currencies": [{"code": "NOK"}, {"code": "USD"}],
        },
        "snapshot": {
            "marketStatus": m["status"], "delayTime": 0,
            "bid": m["bid"], "offer": m["offer"],
        },
        "dealingRules": {
            "marketOrderPreference": m["market_order_preference"],
            "minDealSize": m["minimum_deal_size"],
            "minNormalStopOrLimitDistance": m["minimum_stop"],
        },
    }


class IGDemoAutoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paper = fresh_paper()
        self.preflight = broker_preflight()
        self.positions = []
        self.actions = []
        self.mockpreview = patch.object(auto, "preview", side_effect=self.preview)
        self.mocklogin = patch.object(auto, "_login",
                                      return_value=("mock-key", "mock-cst", "mock-xst", "DEMO_ONLY"))
        self.mockget = patch.object(auto, "_read_only_get", side_effect=self.get)
        self.mockpos = patch.object(auto, "_trial_positions", side_effect=self.positions_read)
        self.mockrequest = patch.object(auto, "_request", side_effect=self.request)
        for obj in (self.mockpreview, self.mocklogin, self.mockget, self.mockpos, self.mockrequest):
            obj.start()
            self.addCleanup(obj.stop)
        self.engine = auto.IGDemoAuto(
            lambda: self.paper, storage_dir=self.temp.name, background=False
        )

    def preview(self):
        self.actions.append(("GET", "preflight"))
        data = dict(self.preflight)
        data["market_candidates"] = [dict(m) for m in self.preflight["market_candidates"]]
        data["existing_ig_positions"] = len(self.positions)
        return data

    def get(self, path, *, key, cst, xst, version=1):
        self.actions.append(("GET", path))
        self.assertEqual(path, "/markets/" + auto.MINI_EPIC)
        self.assertEqual(version, 3)
        return raw_market()

    def positions_read(self, key, cst, xst):
        self.actions.append(("GET", "/positions"))
        return list(self.positions)

    def request(self, method, path, *, key, cst, xst, payload=None):
        self.actions.append((method, path))
        self.assertEqual(path, "/positions/otc" if method in ("POST", "DELETE")
                         else "/confirms/" + self.ref)
        if method == "POST":
            self.assertEqual(payload["epic"], "CS.D.EURUSD.CEEM.IP")
            self.assertEqual(payload["direction"], "BUY")
            self.assertEqual(payload["size"], 0.1)
            self.assertEqual(payload["stopDistance"], 20)
            self.assertEqual(payload["limitDistance"], 40)
            self.assertEqual(payload["currencyCode"], "NOK")
            self.ref = payload["dealReference"]
            self.positions = [{
                "position": {"dealId": "D1", "size": 0.1,
                             "direction": "BUY", "stopLevel": 11181,
                             "limitLevel": 11241},
                "market": {"epic": auto.MINI_EPIC},
            }]
            return {"dealReference": self.ref}
        if method == "DELETE":
            self.assertEqual(payload, {
                "dealId": "D1", "direction": "SELL",
                "size": 0.1, "orderType": "MARKET",
            })
            self.positions = []
            self.ref = "CLOSE_REF"
            return {"dealReference": self.ref}
        if method == "GET":
            return {"dealStatus": "ACCEPTED", "dealId": "D1"}
        self.fail("Unexpected broker operation")

    def start(self):
        return self.engine.start(auto.START_PHRASE)

    def test_default_disarmed_and_explicit_start_required(self):
        self.assertFalse(self.engine.status()["armed"])
        with self.assertRaisesRegex(IGTrialError, "Explicit"):
            self.engine.start("yes")
        self.assertFalse(self.engine.status()["armed"])
        self.assertEqual(self.actions, [])

    def test_start_only_new_paper_buy_after_arming_and_broker_close(self):
        self.paper = fresh_paper([position("old-pos")])
        started = self.start()
        self.assertEqual(started["stage"], "WATCHING")
        self.assertTrue(started["armed"])
        self.engine.tick()
        self.assertEqual([x for x in self.actions if x[0] == "POST"], [])
        self.paper = fresh_paper([position("old-pos"), position("new-pos")])
        opening = self.engine.tick()
        self.assertEqual(opening["stage"], "OPEN")
        self.assertTrue(opening["broker_stop_verified"])
        self.assertTrue(opening["armed"])
        self.assertEqual(opening["attempts_today"], 1)
        self.assertLess(opening["estimated_risk_buffer_nok"], opening["opening_risk_budget_nok"])
        self.assertEqual(len([x for x in self.actions if x[0] == "POST"]), 1)
        self.assertEqual(opening["risk_percent"], 0.5)
        self.assertFalse(opening["real_money_enabled"])
        # Still open in PAPER, so IG DEMO position should remain open.
        self.engine.tick()
        self.assertEqual(len([x for x in self.actions if x[0] == "DELETE"]), 0)
        self.paper = fresh_paper()
        result = self.engine.tick()
        self.assertEqual(result["stage"], "CLOSED")
        self.assertFalse(result["armed"])
        self.assertTrue(result["reconciled_close_by_api"])
        self.assertEqual(len([x for x in self.actions if x[0] == "DELETE"]), 1)
        self.assertEqual(len(self.positions), 0)
        self.engine.tick()
        self.assertEqual(len([x for x in self.actions if x[0] == "DELETE"]), 1)
        self.assertEqual(self.engine.status()["attempts_today"], 1)

    def test_sell_paper_signals_are_not_mirrored_in_first_rollout(self):
        self.start()
        self.paper = fresh_paper([position("SELL-1", side="SELL")])
        st = self.engine.tick()
        self.assertEqual(st["stage"], "WATCHING")
        self.assertEqual(len([x for x in self.actions if x[0] == "POST"]), 0)

    def test_no_ig_entry_when_paper_or_feed_unavailable(self):
        for change in (
            {"engine_running": False},
            {"scan_fresh": False},
            {"eurusd_feed_status": "STALE"},
            {"eurusd_feed_age_seconds": 500},
            {"paper_storage_ready": False},
            {"market_data_mode": "SYNTHETIC"},
        ):
            with self.subTest(change=change):
                self.paper = fresh_paper(**change)
                with self.assertRaises(IGTrialError):
                    self.start()
                self.assertFalse(self.engine.status()["armed"])
        self.paper = fresh_paper()
        self.start()
        for change in (
            {"scan_fresh": False}, {"eurusd_feed_status": "STALE"},
            {"eurusd_feed_age_seconds": 800},
        ):
            with self.subTest(change=change):
                self.paper = fresh_paper([position("fresh-signal")], **change)
                self.engine.tick()
                self.assertEqual(len([x for x in self.actions if x[0] == "POST"]), 0)

    def test_minimum_size_exceeds_risk_budget_then_no_order(self):
        self.preflight["account_balance"] = 1000
        self.preflight["account_available"] = 1000
        with self.assertRaises(IGTrialError):
            self.start()
        self.assertFalse(self.engine.status()["armed"])
        self.assertEqual(len([x for x in self.actions if x[0] == "POST"]), 0)

    def test_unverified_contract_currency_or_existing_position_blocks(self):
        for change in (
            {"account_currency": "USD"},
            {"account_balance": None},
            {"existing_ig_positions": 1},
        ):
            with self.subTest(change=change):
                self.preflight = dict(broker_preflight(), **change)
                if "existing_ig_positions" in change:
                    self.positions = [{"position": {"dealId": "OLD"}, "market": {}}]
                else:
                    self.positions = []
                with self.assertRaises(IGTrialError):
                    self.start()
                self.assertFalse(self.engine.status()["armed"])
        self.preflight = broker_preflight()
        self.preflight["market_candidates"] = [
            dict(live_market(), contract_size="100000")
        ]
        with self.assertRaises(IGTrialError):
            self.start()

    def test_uncertain_order_disarms_and_does_not_retry(self):
        self.start()
        self.paper = fresh_paper([position("new-pos")])
        with patch.object(auto, "_request", side_effect=IGTrialError("IG timeout")):
            result = self.engine.tick()
        self.assertFalse(result["armed"])
        self.assertEqual(result["stage"], "REVIEW_REQUIRED")
        self.assertEqual(result["attempts_today"], 1)
        self.assertTrue((Path(self.temp.name)/auto.JOURNAL).is_file())
        self.engine.tick()
        self.assertEqual(result["attempts_today"], self.engine.status()["attempts_today"])
        with self.assertRaisesRegex(IGTrialError, "unfinished"):
            self.start()

    def test_ig_stop_disarms_new_entries_but_still_manages_existing(self):
        self.start()
        self.paper = fresh_paper([position("new-pos")])
        self.engine.tick()
        stopped = self.engine.stop(auto.STOP_PHRASE)
        self.assertFalse(stopped["armed"])
        self.assertEqual(stopped["stage"], "OPEN")
        self.paper = fresh_paper()
        closed = self.engine.tick()
        self.assertEqual(closed["stage"], "CLOSED")
        self.assertFalse(closed["armed"])

    def test_paper_scan_unavailable_is_not_exit_signal(self):
        self.start()
        self.paper = fresh_paper([position("one")])
        self.engine.tick()
        self.paper = fresh_paper([], scan_fresh=False)
        self.engine.tick()
        self.assertEqual(self.engine.status()["stage"], "OPEN")
        self.assertEqual(len([x for x in self.actions if x[0] == "DELETE"]), 0)

    def test_restart_always_disarms_and_does_not_resubmit(self):
        self.start()
        self.paper = fresh_paper([position("one")])
        self.engine.tick()
        before = len([x for x in self.actions if x[0] == "POST"])
        reloaded = auto.IGDemoAuto(
            lambda: self.paper, storage_dir=self.temp.name, background=False
        )
        self.assertFalse(reloaded.status()["armed"])
        self.assertIn("restarted", reloaded.status()["note"].lower())
        self.assertEqual(reloaded.status()["stage"], "OPEN")
        reloaded.tick()
        self.assertEqual(len([x for x in self.actions if x[0] == "POST"]), before)
        with self.assertRaisesRegex(IGTrialError, "unfinished"):
            reloaded.start(auto.START_PHRASE)

    def test_daily_max_attempts_prevents_extra_orders(self):
        self.start()
        self.paper = fresh_paper([position("p1")])
        self.engine.tick()
        self.paper = fresh_paper()
        self.engine.tick()
        self.start()
        self.paper = fresh_paper([position("p2")])
        self.engine.tick()
        self.paper = fresh_paper()
        self.engine.tick()
        self.assertEqual(self.engine.status()["attempts_today"], 2)
        with self.assertRaisesRegex(IGTrialError, "Daily"):
            self.start()

    def test_pending_close_is_not_retried(self):
        self.start()
        self.paper = fresh_paper([position("p1")])
        self.engine.tick()
        self.paper = fresh_paper()
        with patch.object(auto, "_request", side_effect=IGTrialError("IG timeout")):
            state = self.engine.tick()
        self.assertEqual(state["stage"], "REVIEW_REQUIRED")
        self.assertFalse(state["armed"])
        self.engine.tick()
        self.assertEqual(len([x for x in self.actions if x[0] == "DELETE"]), 0)

    def test_auto_web_routes_are_auth_protected_and_separate_from_paper(self):
        from mobile_cloud import app as wsgi
        engine = self.engine
        with patch.object(wsgi, "TOKEN", "fake-mobile-token-123456"), \
             patch.object(wsgi, "get_ig_demo_auto", return_value=engine), \
             patch.object(wsgi, "get_runtime") as paper:
            client = wsgi.app.test_client()
            self.assertEqual(client.get("/api/ig-demo/auto/status").status_code, 401)
            self.assertEqual(client.post("/api/ig-demo/auto/start").status_code, 401)
            self.assertEqual(client.post("/api/ig-demo/auto/stop").status_code, 401)
            headers = {"Authorization": "Bearer fake-mobile-token-123456"}
            status = client.get("/api/ig-demo/auto/status", headers=headers)
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.get_json()["risk_percent"], 0.5)
            self.assertFalse(status.get_json()["armed"])
            wrong = client.post("/api/ig-demo/auto/start", headers=headers,
                                json={"confirm":"yes"})
            self.assertEqual(wrong.status_code, 400)
            self.assertFalse(engine.status()["armed"])
            okay = client.post("/api/ig-demo/auto/start", headers=headers,
                               json={"confirm":auto.START_PHRASE})
            self.assertEqual(okay.status_code, 200)
            self.assertTrue(okay.get_json()["armed"])
            ended = client.post("/api/ig-demo/auto/stop", headers=headers,
                                json={"confirm":auto.STOP_PHRASE})
            self.assertEqual(ended.status_code, 200)
            self.assertFalse(ended.get_json()["armed"])
            paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
