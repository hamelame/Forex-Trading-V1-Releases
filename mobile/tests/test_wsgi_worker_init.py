"""Regression: no scanner at module import; postfork health/HEAD starts one durable scanner."""
import importlib
import os
import unittest
from unittest.mock import patch, MagicMock

TOKEN = 'safe-test-token-only-1234567890'


class WSGIPostForkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, {'FX_MOBILE_TOKEN': TOKEN})
        cls.env.start()
        cls.wsgi = importlib.reload(importlib.import_module('mobile_cloud.app'))

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()

    def setUp(self):
        self.wsgi.runtime = None
        self.wsgi.runtime_pid = None

    def test_import_and_unauthorized_state_never_start_scanner(self):
        self.assertIsNone(self.wsgi.runtime)
        with patch.object(self.wsgi, 'MobileRuntime') as factory:
            with self.wsgi.app.test_client() as client:
                r = client.get('/api/state')
                self.assertEqual(r.status_code, 401)
                r = client.get('/')
                self.assertEqual(r.status_code, 200)
            factory.assert_not_called()

    def test_postfork_render_head_probe_and_health_start_scanner_once(self):
        factory = MagicMock()
        factory.return_value.worker.is_alive.return_value = True
        factory.return_value.watchdog.is_alive.return_value = True
        factory.return_value.state.return_value = {'paper_only':True,'running':False}
        with patch.object(self.wsgi, 'MobileRuntime',factory):
            with self.wsgi.app.test_client() as client:
                # Render's HEAD / probe triggers the postfork worker runtime.
                head=client.head('/')
                self.assertEqual(head.status_code,200)
                self.assertEqual(head.data,b'')
                health=client.get('/health')
                self.assertEqual(health.status_code,200)
                self.assertFalse(health.get_json()['crypto_only'])
                self.assertTrue(health.get_json()['multi_market_paper'])
                # A fresh login reuses that same runtime, NEVER restarting a
                # PAPER session or resetting balance/checkpoint.
                response=client.get('/api/state',
                    headers={'Authorization':'Bearer '+TOKEN})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.get_json()['paper_only'],True)
                factory.assert_called_once_with()
                self.assertEqual(self.wsgi.runtime_pid,os.getpid())

    def test_health_start_is_postfork_only_and_failure_is_reported(self):
        self.assertIsNone(self.wsgi.runtime)
        with patch.object(self.wsgi,'MobileRuntime',side_effect=RuntimeError('fixture')):
            with self.wsgi.app.test_client() as client:
                self.assertEqual(client.get('/health').status_code,503)
                self.assertEqual(client.head('/').status_code,503)
                self.assertEqual(client.get('/api/state').status_code,401)
            self.assertIsNone(self.wsgi.runtime)

    def test_first_authorized_request_starts_runtime_only_in_worker(self):
        factory = MagicMock()
        factory.return_value.state.return_value = {'paper_only': True, 'running': False}
        factory.return_value.worker.is_alive.return_value = True
        factory.return_value.watchdog.is_alive.return_value = True
        with patch.object(self.wsgi, 'MobileRuntime', factory):
            with self.wsgi.app.test_client() as client:
                headers = {'Authorization': 'Bearer ' + TOKEN}
                first = client.get('/api/state', headers=headers)
                second = client.get('/api/state', headers=headers)
                self.assertEqual(first.status_code, 200)
                self.assertTrue(first.get_json()['paper_only'])
                self.assertEqual(second.status_code, 200)
                factory.assert_called_once_with()
                self.assertEqual(self.wsgi.runtime_pid, os.getpid())

                # A simulated child process must not inherit its parent's
                # engine/thread state. It gets a new runtime instead.
                with patch.object(self.wsgi.os, 'getpid', return_value=os.getpid()+100):
                    third = client.get('/api/state', headers=headers)
                self.assertEqual(third.status_code, 200)
                self.assertEqual(factory.call_count, 2)


if __name__ == '__main__':
    unittest.main()
