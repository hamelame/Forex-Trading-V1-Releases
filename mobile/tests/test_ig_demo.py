"""IG DEMO: cannot place live orders; credentials must never leave Render."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from mobile import ig_demo


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, _limit):
        return self.payload


class IGDemoReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.secrets = {
            "IG_DEMO_API_KEY": "top-secret-demo-key",
            "IG_DEMO_USERNAME": "fake_demo_user",
            "IG_DEMO_PASSWORD": "example-demo-password",
        }

    def test_status_safe_and_network_free(self):
        s = ig_demo.status(self.secrets)
        self.assertEqual(s["environment"], "DEMO")
        self.assertTrue(s["read_only"])
        self.assertTrue(s["credentials_configured"])
        self.assertFalse(s["broker_orders_enabled"])
        self.assertNotIn(self.secrets["IG_DEMO_API_KEY"], str(s))

    def test_credentials_must_be_complete(self):
        incomplete = dict(self.secrets)
        incomplete.pop("IG_DEMO_PASSWORD")
        self.assertFalse(ig_demo.status(incomplete)["credentials_configured"])
        with self.assertRaisesRegex(ig_demo.IGDemoError, "missing"):
            ig_demo.check_connection(incomplete)

    def test_explicit_check_only_logins_to_demo_and_reads_accounts(self):
        requests = []

        def opener(req, timeout):
            self.assertEqual(timeout, 10)
            requests.append(req)
            self.assertTrue(req.full_url.startswith(ig_demo.DEMO_BASE))
            self.assertNotIn("https://api.ig.com", req.full_url)
            if req.full_url.endswith("/session"):
                self.assertEqual(req.get_method(), "POST")
                body = json.loads(req.data)
                self.assertEqual(body["identifier"], self.secrets["IG_DEMO_USERNAME"])
                self.assertEqual(body["password"], self.secrets["IG_DEMO_PASSWORD"])
                return FakeResponse({"accountId": "AB12345"},
                                    headers={"CST": "secret-cst", "X-SECURITY-TOKEN": "secret-xst"})
            self.assertEqual(req.full_url, ig_demo.DEMO_BASE + "/accounts")
            self.assertEqual(req.get_method(), "GET")
            self.assertNotIn("positions", req.full_url)
            hdrs = {key.lower(): val for key, val in req.header_items()}
            self.assertEqual(hdrs["cst"], "secret-cst")
            self.assertEqual(hdrs["x-security-token"], "secret-xst")
            return FakeResponse({
                "accounts": [{
                    "accountId": "PRIVATE_ID", "accountType": "CFD",
                    "currency": "NOK", "balance": {"balance": 100000, "available": 99999}
                }]
            })

        result = ig_demo.check_connection(self.secrets, opener=opener)
        self.assertTrue(result["connected"])
        self.assertEqual(result["account_count"], 1)
        self.assertEqual(result["accounts"][0]["balance"], 100000.0)
        self.assertFalse(result["broker_orders_enabled"])
        self.assertEqual(len(requests), 2)
        for secret in (*self.secrets.values(), "secret-cst", "secret-xst", "PRIVATE_ID"):
            self.assertNotIn(secret, str(result))

    def test_failed_login_never_exposes_credentials_or_http_body(self):
        def reject(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 401, "auth rejected: TOP SECRET", {}, None
            )

        with self.assertRaises(ig_demo.IGDemoError) as cm:
            ig_demo.check_connection(self.secrets, opener=reject)
        self.assertNotIn("TOP SECRET", str(cm.exception))
        for secret in self.secrets.values():
            self.assertNotIn(secret, str(cm.exception))

    def test_known_ig_401_error_is_specific_without_leaking_raw_server_data(self):
        def reject(req, timeout):
            body = json.dumps({
                "errorCode": "error.security.invalid-details",
                "internalDebug": "PRIVATE: " + self.secrets["IG_DEMO_PASSWORD"]
            }).encode("utf-8")
            raise urllib.error.HTTPError(
                req.full_url, 401, "contains secret " + self.secrets["IG_DEMO_API_KEY"],
                {}, io.BytesIO(body)
            )
        with self.assertRaises(ig_demo.IGDemoError) as cm:
            ig_demo.check_connection(self.secrets, opener=reject)
        self.assertIn("username or password", str(cm.exception))
        for secret in self.secrets.values():
            self.assertNotIn(secret, str(cm.exception))
        self.assertNotIn("PRIVATE", str(cm.exception))

    def test_known_ig_403_api_key_error_is_specific_without_leaking_code(self):
        def reject(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 403, "Denied", {},
                io.BytesIO(b'{"errorCode":"error.security.api-key-invalid"}')
            )
        with self.assertRaises(ig_demo.IGDemoError) as cm:
            ig_demo.check_connection(self.secrets, opener=reject)
        self.assertEqual("IG DEMO says the API key is invalid.", str(cm.exception))

    def test_unrecognized_response_values_are_never_shown(self):
        def reject(req, timeout):
            body = json.dumps({
                "errorCode": "CUSTOM_" + self.secrets["IG_DEMO_API_KEY"],
                "message": self.secrets["IG_DEMO_PASSWORD"]
            }).encode()
            raise urllib.error.HTTPError(req.full_url, 403, "secret", {}, io.BytesIO(body))
        with self.assertRaises(ig_demo.IGDemoError) as cm:
            ig_demo.check_connection(self.secrets, opener=reject)
        self.assertIn("HTTP 403", str(cm.exception))
        for secret in self.secrets.values():
            self.assertNotIn(secret, str(cm.exception))

    def test_ig_pending_agreements_has_specific_action(self):
        def reject(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Denied", {},
                io.BytesIO(b'{"errorCode":"error.public-api.failure.pending.agreements.required"}')
            )
        with self.assertRaises(ig_demo.IGDemoError) as cm:
            ig_demo.check_connection(self.secrets, opener=reject)
        self.assertIn("accept agreements", str(cm.exception))

    def test_unsupported_endpoints_are_rejected_before_http(self):
        with self.assertRaisesRegex(ig_demo.IGDemoError, "read-only"):
            ig_demo._call("/positions/otc", method="POST", key="test",
                          body={"size": 1})


class IGDemoWSGITests(unittest.TestCase):
    def test_ig_demo_routes_are_auth_protected_and_engine_independent(self):
        from mobile_cloud import app as wsgi
        from unittest.mock import patch
        with patch.object(wsgi, "TOKEN", "test-mobile-token-123456"), \
             patch.object(wsgi, "get_runtime") as engine_factory, \
             patch.object(wsgi, "ig_demo_check", return_value={
                 "connected": True, "environment": "DEMO",
                 "read_only": True, "broker_orders_enabled": False, "accounts": []
             }) as check:
            client = wsgi.app.test_client()
            self.assertEqual(client.get("/api/ig-demo/status").status_code, 401)
            self.assertEqual(client.post("/api/ig-demo/check").status_code, 401)
            headers={"Authorization": "Bearer test-mobile-token-123456"}
            self.assertEqual(client.get("/api/ig-demo/status", headers=headers).status_code, 200)
            response=client.post("/api/ig-demo/check", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["broker_orders_enabled"])
            check.assert_called_once_with()
            engine_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
