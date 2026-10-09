"""Focused trust-boundary checks for the canonical Device API."""

from __future__ import annotations

import io
import unittest
import uuid

import pi_device_api
from pi_device_api import (
    MAX_JSON_BYTES,
    MAX_NAME_CHARS,
    MAX_REQUEST_BYTES,
    create_app,
)
from tests.test_pi_device_api import FakeDb, FakeRuntime


class PiDeviceApiLimitTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.runtime = FakeRuntime(self.db)
        self.app = create_app(db_module=self.db, runtime=self.runtime)
        self.app.testing = True
        self.client = self.app.test_client()

    def test_factory_has_no_unauthenticated_module_app(self):
        self.assertFalse(hasattr(pi_device_api, "app"))

    def test_json_and_global_body_limits_reject_before_side_effects(self):
        oversized_json = '{"name":"' + ("A" * MAX_JSON_BYTES) + '"}'
        response = self.client.post(
            "/api/users",
            data=oversized_json,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.db.users, [])

        response = self.client.post(
            "/api/lock",
            content_type="application/octet-stream",
            environ_overrides={
                "CONTENT_LENGTH": str(MAX_REQUEST_BYTES + 1),
                "wsgi.input": io.BytesIO(),
            },
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.runtime.calls, [])

    def test_json_contract_and_name_are_validated(self):
        cases = (
            ({"name": 7, "pin": "123456"}, 400),
            ({"name": "A" * (MAX_NAME_CHARS + 1), "pin": "123456"}, 400),
            ({"name": "line\nbreak", "pin": "123456"}, 400),
        )
        for payload, expected_status in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/api/users", json=payload)
                self.assertEqual(response.status_code, expected_status)
        self.assertEqual(self.db.users, [])

        response = self.client.post(
            "/api/users",
            json={"name": " 王小明 ", "pin": "123456"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.db.users[0]["name"], "王小明")

        response = self.client.post(
            "/api/users",
            data="[]",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_settings_accept_current_ui_ranges_and_reject_invalid_values(self):
        settings = {
            "autoRelockSeconds": 0,
            "ignitionAutoStopSeconds": 1800,
            "promptAutoLockSeconds": 600,
            "liveness": True,
            "failLockout": False,
            "lockoutAfter": 20,
        }
        response = self.client.post("/api/settings", json=settings)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.settings, settings)

        for payload in (
            {},
            {"autoRelockSeconds": -1},
            {"ignitionAutoStopSeconds": 1801},
            {"lockoutAfter": True},
            {"liveness": "true"},
            {"unexpected": 1},
        ):
            with self.subTest(payload=payload):
                before = dict(self.db.settings)
                response = self.client.post("/api/settings", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.db.settings, before)

    def test_client_authored_audit_log_is_gone(self):
        response = self.client.post(
            "/api/verify-log",
            json={"result": "success", "detail": "forged"},
        )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(self.db.logs, [])

    def test_enrollment_requires_authenticated_reservation_before_runtime(self):
        response = self.client.post(
            "/api/enroll/start",
            json={
                "request_id": str(uuid.uuid4()),
                "user_id": "user-1",
                "source": "device_camera",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.runtime.calls, [])

    def test_client_camera_enrollment_routes_are_retired_and_still_bounded(self):
        for path in ("/api/enroll/sample", "/api/enroll/finish"):
            with self.subTest(path=path):
                response = self.client.post(path, json={"session_id": "retired"})
                self.assertEqual(response.status_code, 410)

        response = self.client.post(
            "/api/enroll/sample",
            content_type="application/octet-stream",
            environ_overrides={
                "CONTENT_LENGTH": str(MAX_REQUEST_BYTES + 1),
                "wsgi.input": io.BytesIO(),
            },
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.runtime.calls, [])


if __name__ == "__main__":
    unittest.main()
