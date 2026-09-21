"""Reject corrupt persisted policy before starting a face operation."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import db
import db_api
from pi_device_api import create_app
from tests.test_runtime_safety import SafetyRuntime


class PersistedSafetySettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bass-settings-")
        self.addCleanup(temporary.cleanup)
        database_path = str(Path(temporary.name) / "isolated.db")
        self.patch = patch.object(db, "DB_PATH", database_path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        db.init_db()
        self.runtime = SafetyRuntime(db_api)
        self.client = create_app(db_module=db_api, runtime=self.runtime).test_client()

    def test_canonical_settings_round_trip_without_policy_fallback(self):
        settings = db_api.get_settings_for_ui()
        self.assertTrue(settings["liveness"])
        self.assertTrue(settings["failLockout"])
        self.assertEqual(settings["autoRelockSeconds"], 10)
        db_api.save_settings_from_ui({"liveness": False, "autoRelockSeconds": 600})
        self.assertFalse(db_api.get_settings_for_ui()["liveness"])
        self.assertEqual(db_api.get_settings_for_ui()["autoRelockSeconds"], 600)

    def test_corrupt_policy_blocks_reads_and_scan_before_side_effects(self):
        cases = (
            ("liveness_detection", "garbage"),
            ("fail_lockout", "0"),
            ("auto_relock_seconds", "bad"),
            ("ignition_auto_stop_seconds", "1801"),
            ("ignition_prompt_autolock_seconds", "-1"),
            ("lockout_after", "0"),
            ("auto_relock_seconds", "1.0"),
        )
        originals = db_api.get_settings()
        for key, value in cases:
            with self.subTest(key=key, value=value):
                db_api.save_settings({key: value})
                with self.assertRaises(ValueError):
                    db_api.get_settings_for_ui()
                self.assertEqual(self.client.get("/api/settings").status_code, 503)
                response = self.client.post("/api/scan/start", json={})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.runtime.camera_open_calls, 0)
                self.assertEqual(self.runtime.commands, [])
                db_api.save_settings({key: originals[key]})

    def test_missing_policy_is_not_replaced_by_default(self):
        with db.get_conn() as connection:
            connection.execute("DELETE FROM settings WHERE key='fail_lockout'")
        with self.assertRaisesRegex(ValueError, "failLockout"):
            db_api.get_settings_for_ui()
        self.assertEqual(self.client.post("/api/scan/start", json={}).status_code, 503)
        self.assertEqual(self.runtime.commands, [])


if __name__ == "__main__":
    unittest.main()
