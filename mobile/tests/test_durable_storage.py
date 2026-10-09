"""Persistent storage must be opt-in and actually mounted."""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DurableStorageTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch.dict(os.environ, {"FX_MOBILE_TOKEN": "safe-test-token-123456789"},
                                  clear=False)
        self.patcher.start()
        import mobile_cloud.app as module
        self.module = module

    def tearDown(self):
        self.patcher.stop()

    def test_ephemeral_default_remains_unchanged(self):
        with patch.dict(os.environ, {"FX_MOBILE_STORAGE_DIR": ""}, clear=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FX_MOBILE_DB", None)
                os.environ.pop("FX_MOBILE_SETTINGS", None)
                self.assertFalse(self.module.configure_mobile_storage())
                self.assertEqual(os.environ["FX_MOBILE_DB"], "/tmp/fx_mobile_v2926.sqlite")
                self.assertEqual(os.environ["FX_MOBILE_SETTINGS"], "/tmp/fx_mobile_settings_v2926.json")

    def test_missing_disk_refused(self):
        with patch.dict(os.environ, {"FX_MOBILE_STORAGE_DIR": "/missing_render_disk_mount"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "mounted persistent disk"):
                self.module.configure_mobile_storage()

    def test_mounted_disk_uses_durable_paths(self):
        with tempfile.TemporaryDirectory() as d:
            overrides = {"FX_MOBILE_STORAGE_DIR": d}
            with patch.dict(os.environ, overrides, clear=False), patch("os.path.ismount", return_value=True):
                os.environ.pop("FX_MOBILE_DB", None)
                os.environ.pop("FX_MOBILE_SETTINGS", None)
                self.assertTrue(self.module.configure_mobile_storage())
                self.assertEqual(Path(os.environ["FX_MOBILE_DB"]), Path(d) / "fx_mobile.sqlite")
                self.assertEqual(Path(os.environ["FX_MOBILE_SETTINGS"]), Path(d) / "fx_mobile_settings.json")
                os.environ["FX_MOBILE_DB"] = "/tmp/outside.sqlite"
                with self.assertRaisesRegex(RuntimeError, "must be located inside"):
                    self.module.configure_mobile_storage()


if __name__ == "__main__":
    unittest.main()
