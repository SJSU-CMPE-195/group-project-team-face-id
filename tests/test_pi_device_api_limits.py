"""Focused trust-boundary checks for the canonical Device API."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from PIL import Image

import pi_device_api
from pi_device_api import (
    MAX_JSON_BYTES,
    MAX_NAME_CHARS,
    MAX_REQUEST_BYTES,
    create_app,
)
from tests.test_pi_device_api import FakeDb, FakeRuntime


def jpeg_bytes(width: int = 64, height: int = 64) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="JPEG")
    return output.getvalue()


def jpeg_with_reported_width(width: int) -> bytes:
    data = bytearray(jpeg_bytes())
    marker = data.find(b"\xff\xc0")
    if marker < 0:
        raise AssertionError("test JPEG has no baseline SOF marker")
    data[marker + 7 : marker + 9] = width.to_bytes(2, "big")
    return bytes(data)


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
            ({"name": 7}, 400),
            ({"name": "A" * (MAX_NAME_CHARS + 1)}, 400),
            ({"name": "line\nbreak"}, 400),
        )
        for payload, expected_status in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/api/users", json=payload)
                self.assertEqual(response.status_code, expected_status)
        self.assertEqual(self.db.users, [])

        response = self.client.post("/api/users", json={"name": " 王小明 "})
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

    def test_enrollment_accepts_bounded_jpeg_before_runtime(self):
        started = self.client.post(
            "/api/enroll/start",
            json={"name": "Ada", "source": "client_camera"},
        ).get_json()
        response = self.client.post(
            "/api/enroll/sample",
            data={
                "session_id": started["session_id"],
                "image": (io.BytesIO(jpeg_bytes()), "sample.jpg", "image/jpeg"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 1)
        response.request.close()

    def test_enrollment_rejects_invalid_or_oversized_images_before_runtime(self):
        started = self.client.post(
            "/api/enroll/start",
            json={"name": "Ada", "source": "client_camera"},
        ).get_json()
        session_id = started["session_id"]

        cases = (
            (b"not-jpeg", "image/jpeg", 400),
            (jpeg_bytes(), "image/png", 415),
            (jpeg_with_reported_width(4097), "image/jpeg", 413),
        )
        for content, mimetype, expected_status in cases:
            with self.subTest(mimetype=mimetype, expected_status=expected_status):
                response = self.client.post(
                    "/api/enroll/sample",
                    data={
                        "session_id": session_id,
                        "image": (io.BytesIO(content), "sample.jpg", mimetype),
                    },
                )
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(self.runtime.sessions[session_id]["count"], 0)
                response.request.close()
                response.close()

        with patch.object(pi_device_api, "MAX_CLIENT_IMAGE_BYTES", 128):
            response = self.client.post(
                "/api/enroll/sample",
                data={
                    "session_id": session_id,
                    "image": (io.BytesIO(jpeg_bytes()), "sample.jpg", "image/jpeg"),
                },
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.runtime.sessions[session_id]["count"], 0)
        response.request.close()
        response.close()


if __name__ == "__main__":
    unittest.main()
