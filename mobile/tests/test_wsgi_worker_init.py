"""Regression: WSGI imports must NEVER start background trading threads."""
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

    def test_import_and_health_never_start_scanner(self):
        self.assertIsNone(self.wsgi.runtime)
        with patch.object(self.wsgi, 'MobileRuntime') as factory:
            with self.wsgi.app.test_client() as client:
                r = client.get('/health')
                self.assertEqual(r.status_code, 200)
                r = client.get('/api/state')
                self.assertEqual(r.status_code, 401)
            factory.assert_not_called()

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
