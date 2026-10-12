"""Read-only diagnostic tests for the IG DEMO AUTO local PAPER gates.

No broker requests, no new PAPER sessions, no changes to persisted accounts.
"""
import unittest
from unittest.mock import Mock, patch

from mobile_cloud import app as wsgi
from mobile.ig_demo_auto import IGDemoAuto, IGTrialError, START_PHRASE


def ready_snapshot(**overrides):
    data = {
        "engine_running": True,
        "paper_storage_ready": True,
        "market_data_mode": "LIVE",
        "scan_fresh": True,
        "eurusd_feed_status": "LIVE",
        "eurusd_feed_age_seconds": 15,
        "paper_positions": [],
    }
    data.update(overrides)
    return data


class GateDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.token = "dummy-unit-test-token-long-enough"
        self.auth = {"Authorization": "Bearer " + self.token}
        self.client = wsgi.app.test_client()

    def test_requires_auth_and_rejects_writes(self):
        self.assertEqual(self.client.get("/api/ig-demo/auto/readiness").status_code, 401)
        self.assertEqual(self.client.post("/api/ig-demo/auto/readiness").status_code, 405)

    def test_all_checks_pass_without_any_broker_access(self):
        with patch.object(wsgi, "TOKEN", self.token), \
             patch.object(wsgi, "ig_auto_paper_snapshot", return_value=ready_snapshot()), \
             patch.object(wsgi, "get_ig_demo_auto") as broker_engine:
            response = self.client.get("/api/ig-demo/auto/readiness", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["local_checks_pass"])
        self.assertEqual(len(data["checks"]), 6)
        self.assertTrue(all(p["ok"] for p in data["checks"]))
        self.assertFalse(data["broker_checked"])
        self.assertFalse(data["orders_sent"])
        broker_engine.assert_not_called()

    def test_crypto_live_does_not_override_stale_forex(self):
        with patch.object(wsgi, "TOKEN", self.token), \
             patch.object(wsgi, "ig_auto_paper_snapshot", return_value=ready_snapshot(
                 eurusd_feed_status="STALE", eurusd_feed_age_seconds=12000)), \
             patch.object(wsgi, "get_ig_demo_auto") as broker_engine:
            data = self.client.get("/api/ig-demo/auto/readiness", headers=self.auth).get_json()
        self.assertFalse(data["local_checks_pass"])
        self.assertIn("EUR/USD PAPER feed LIVE", data["reason"])
        broker_engine.assert_not_called()

    def test_paper_persistence_failure_has_own_label(self):
        with patch.object(wsgi, "TOKEN", self.token), \
             patch.object(wsgi, "ig_auto_paper_snapshot",
                          return_value=ready_snapshot(paper_storage_ready=False)):
            data = self.client.get("/api/ig-demo/auto/readiness", headers=self.auth).get_json()
        self.assertFalse(data["local_checks_pass"])
        self.assertIn("Permanent PAPER disk", data["reason"])

    def test_busy_scanner_is_not_false_storage_failure(self):
        with patch.object(wsgi, "TOKEN", self.token), \
             patch.object(wsgi, "ig_auto_paper_snapshot",
                          return_value={"scan_fresh": False, "paper_positions": [],
                                        "snapshot_unavailable": "SCANNER_BUSY"}):
            data = self.client.get("/api/ig-demo/auto/readiness", headers=self.auth).get_json()
        self.assertFalse(data["local_checks_pass"])
        self.assertIn("scanner busy", data["reason"].lower())
        self.assertNotIn("storage", data["reason"].lower())

    def test_real_snapshot_busy_marker_does_not_request_broker_data(self):
        locked = Mock()
        locked.lock.acquire.return_value = False
        with patch.object(wsgi, "get_runtime", return_value=locked):
            data = wsgi.ig_auto_paper_snapshot()
        self.assertEqual(data["snapshot_unavailable"], "SCANNER_BUSY")
        self.assertFalse(data["scan_fresh"])
        locked.lock.release.assert_not_called()

    def test_busy_scanner_ig_start_fails_without_order(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            engine = IGDemoAuto(
                lambda: {"snapshot_unavailable": "SCANNER_BUSY"},
                storage_dir=directory, background=False)
            with patch("mobile.ig_demo_auto.preview") as broker_preflight:
                with self.assertRaisesRegex(IGTrialError, "scanner was busy"):
                    engine.start(START_PHRASE)
                broker_preflight.assert_not_called()
            self.assertFalse(engine.status()["armed"])
            self.assertEqual(engine.status()["attempts_today"], 0)


if __name__ == "__main__":
    unittest.main()
