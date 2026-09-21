"""Exercise request limits with real temporary credentials and SQLite state."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pi_device_api import create_app
from security_fixtures import temporary_security
from wireless.api import secure_wireless_app
from wireless.config import load_or_create_device_config


class WirelessRequestLimitTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = load_or_create_device_config(root / "device.json")
        self.fixture = temporary_security(self, root)
        self.client = secure_wireless_app(
            create_app(db_module=self.fixture.db, runtime=self.fixture.runtime),
            config=self.config,
            mode="pc",
            port=5057,
            dist_root=root,
            security=self.fixture.security,
        ).test_client()

    def settings_headers(self):
        return self.fixture.headers(self.fixture.grant("settings.update"))

    def test_management_burst_is_rejected_before_another_write_and_recovers(self):
        with patch("wireless.request_limits.time.monotonic", return_value=100):
            for index in range(30):
                result = self.client.post(
                    "/api/settings",
                    json={"autoRelockSeconds": index},
                    headers=self.settings_headers(),
                )
                self.assertEqual(result.status_code, 200)

            blocked = self.client.post(
                "/api/settings",
                json={"autoRelockSeconds": 99},
                headers=self.settings_headers(),
            )
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("Retry-After", blocked.headers)
            self.assertEqual(
                self.fixture.db.get_settings_for_ui()["autoRelockSeconds"], 29
            )

            lock_grant = self.fixture.grant(
                "device.lock", self.fixture.principal().user_id
            )
            lock = self.client.post(
                "/api/lock",
                json={},
                headers=self.fixture.headers(lock_grant),
            )
            self.assertEqual(lock.status_code, 200)

        with patch("wireless.request_limits.time.monotonic", return_value=161):
            allowed = self.client.post(
                "/api/settings",
                json={"autoRelockSeconds": 61},
                headers=self.settings_headers(),
            )
            self.assertEqual(allowed.status_code, 200)

    def test_unauthorized_body_is_not_parsed_and_does_not_consume_quota(self):
        for _ in range(31):
            response = self.client.post(
                "/api/settings",
                data="invalid JSON",
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/api/settings",
            json={"autoRelockSeconds": 17},
            headers=self.settings_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.fixture.db.get_settings_for_ui()["autoRelockSeconds"], 17
        )

    def test_phone_entry_pin_checks_share_the_credential_request_limit(self):
        with patch("wireless.request_limits.time.monotonic", return_value=100):
            for _ in range(20):
                result = self.client.post("/api/session-login", json={"pin": self.fixture.pin},
                                          headers=self.fixture.headers())
                self.assertEqual(result.status_code, 200, result.json)
            blocked = self.client.post("/api/operation-grants", json={
                "pin": self.fixture.pin, "action": "settings.update",
            }, headers=self.fixture.headers())
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("Retry-After", blocked.headers)
        with patch("wireless.request_limits.time.monotonic", return_value=161):
            allowed = self.client.post("/api/session-login", json={"pin": self.fixture.pin},
                                       headers=self.fixture.headers())
            self.assertEqual(allowed.status_code, 200, allowed.json)


if __name__ == "__main__":
    unittest.main()
