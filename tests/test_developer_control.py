"""Loopback auto-session and CSRF boundary tests."""

import unittest

from flask import Flask, jsonify

from wireless.developer_control import DeveloperControl
from wireless.security import SecurityError


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class _Events:
    def __init__(self):
        self.powered = True
        self.calls = []

    def snapshot(self):
        return {
            "powered": self.powered,
            "failed_power_target": None,
            "maintenance": False,
            "ownership": "unclaimed",
            "led": "unclaimed" if self.powered else "off",
            "pairing_remaining_seconds": 0,
            "recovery_remaining_seconds": 0,
            "press_active": False,
            "generation": 1,
        }

    def power(self, powered):
        self.powered = powered
        return self.snapshot()

    def __getattr__(self, name):
        if name.startswith("press_"):

            def record(press_id):
                self.calls.append((name, press_id))
                return {"press_id": press_id, "action": "none"}

            return record
        raise AttributeError(name)


class _Reset:
    def __init__(self):
        self.pending = {
            "request_id": "e37b45b8-ac11-4f33-a479-8b3b399ebce1",
            "phase": "failed_precommit",
        }
        self.calls = []

    def execute(self, request_id, confirmation):
        self.calls.append((request_id, confirmation))
        return {"request_id": request_id, "confirmation": confirmation}

    def pending_request(self):
        return dict(self.pending)


class DeveloperControlTests(unittest.TestCase):
    def setUp(self):
        self.events = _Events()
        self.reset = _Reset()
        self.clock = _Clock()
        app = Flask(__name__)

        @app.errorhandler(SecurityError)
        def security_error(error):
            return jsonify({"code": error.code}), error.status

        DeveloperControl(
            events=self.events,
            reset=self.reset,
            card_provider=lambda: {
                "public_payload": {
                    "version": 3,
                    "purpose": "device",
                    "device_id": "test-device",
                    "tls_certificate_sha256": "c" * 64,
                },
                "activation_payload": {
                    "version": 3,
                    "purpose": "activation",
                    "activation_secret": "b" * 64,
                    "device_id": "test-device",
                    "tls_certificate_sha256": "c" * 64,
                },
            },
            port=80,
            clock=self.clock,
            session_seconds=10,
        ).register(app)
        self.app = app
        self.client = app.test_client()
        self.local_headers = {"Sec-Fetch-Site": "same-origin"}

    def status(self, client=None):
        return (client or self.client).get(
            "/local/hardware/status", headers=self.local_headers
        )

    def mutation_headers(self, csrf):
        return {
            **self.local_headers,
            "Origin": "http://localhost",
            "X-BASS-Dev-CSRF": csrf,
        }

    def test_trusted_status_creates_session_and_card_is_available(self):
        response = self.status()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["authenticated"])
        self.assertEqual(
            response.get_json()["pending_reset"], self.reset.pending
        )
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        card = self.client.get(
            "/local/hardware/card", headers=self.local_headers
        ).get_json()
        self.assertTrue(
            card["public_qr_image"].startswith("data:image/png;base64,")
        )
        self.assertTrue(
            card["activation_qr_image"].startswith("data:image/png;base64,")
        )

    def test_expired_session_refreshes_csrf_transparently(self):
        first = self.status().get_json()["csrf_token"]
        self.clock.value += 11
        refreshed = self.status()
        second = refreshed.get_json()["csrf_token"]
        self.assertNotEqual(first, second)
        self.assertIn("bass_dev_session=", refreshed.headers["Set-Cookie"])
        stale = self.client.post(
            "/local/hardware/power",
            json={"powered": False},
            headers=self.mutation_headers(first),
        )
        self.assertEqual(stale.status_code, 403)

    def test_cookie_and_csrf_are_both_required_for_mutations(self):
        csrf = self.status().get_json()["csrf_token"]
        no_cookie = self.app.test_client().post(
            "/local/hardware/power",
            json={"powered": False},
            headers=self.mutation_headers(csrf),
        )
        self.assertEqual(no_cookie.status_code, 401)
        no_csrf = self.client.post(
            "/local/hardware/power",
            json={"powered": False},
            headers={**self.local_headers, "Origin": "http://localhost"},
        )
        self.assertEqual(no_csrf.status_code, 403)
        accepted = self.client.post(
            "/local/hardware/power",
            json={"powered": False},
            headers=self.mutation_headers(csrf),
        )
        self.assertEqual(accepted.status_code, 200)

    def test_product_reset_does_not_end_developer_session(self):
        csrf = self.status().get_json()["csrf_token"]
        request_id = "0d60839f-41cb-4cd0-9321-c0188cae40d9"
        reset = self.client.post(
            "/local/hardware/reset",
            json={"request_id": request_id, "confirmation": "RESET"},
            headers=self.mutation_headers(csrf),
        )
        self.assertEqual(reset.status_code, 200)
        power = self.client.post(
            "/local/hardware/power",
            json={"powered": False},
            headers=self.mutation_headers(csrf),
        )
        self.assertEqual(power.status_code, 200)

    def test_lan_cross_site_and_removed_session_route_are_rejected(self):
        lan = self.client.get(
            "/local/hardware/status",
            headers=self.local_headers,
            environ_overrides={"REMOTE_ADDR": "192.168.1.20"},
        )
        self.assertEqual(lan.status_code, 403)
        cross_site = self.client.get(
            "/local/hardware/status",
            headers={
                "Origin": "http://evil.test",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        self.assertEqual(cross_site.status_code, 403)
        removed = self.client.post(
            "/local/hardware/session",
            json={},
            headers=self.mutation_headers("x"),
        )
        self.assertEqual(removed.status_code, 404)


if __name__ == "__main__":
    unittest.main()
