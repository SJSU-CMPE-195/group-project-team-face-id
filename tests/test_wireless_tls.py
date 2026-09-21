"""Isolated TLS sockets with real temporary credentials and SQLite state."""

from __future__ import annotations

from dataclasses import replace
from http.cookies import SimpleCookie
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

from pi_device_api import create_app
from security_fixtures import temporary_security
from wireless.api import secure_wireless_app
from wireless.config import load_or_create_device_config
from wireless.server import wireless_servers
from wireless.tls import _openssl, load_or_create_tls_identity


def unused_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


class WirelessTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="bass-tls-security-")
        cls.addClassCleanup(cls.folder.cleanup)
        cls.root = Path(cls.folder.name)
        cls.config_path = cls.root / "device.json"
        config = load_or_create_device_config(cls.config_path)
        cls.identity = load_or_create_tls_identity(cls.config_path, config.device_id)
        cls.config = replace(
            config, tls_certificate_sha256=cls.identity.certificate_sha256,
        )
        other_path = cls.root / "other.json"
        other_config = load_or_create_device_config(other_path)
        cls.other_identity = load_or_create_tls_identity(other_path, other_config.device_id)
        cls.other_key = other_config.pairing_key
        (cls.root / "index.html").write_text("<title>BASS test</title>")

    def setUp(self):
        security_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fixture = temporary_security(self, security_root)
        self.runtime = self.fixture.runtime
        self.port = unused_port()
        self.dashboard_port = unused_port()
        while self.dashboard_port == self.port:
            self.dashboard_port = unused_port()
        app = secure_wireless_app(
            create_app(db_module=self.fixture.db, runtime=self.runtime),
            config=self.config,
            mode="pc",
            port=self.dashboard_port,
            dist_root=self.root,
            security=self.fixture.security,
        )
        self.client = app.test_client()
        self.server = self.enterContext(wireless_servers(
            app, host="127.0.0.1", port=self.port,
            dashboard_port=self.dashboard_port,
            tls_identity_path=self.identity.path,
        ))
        self.thread = threading.Thread(target=self.server.serve, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_https)

    def stop_https(self):
        self.server.stop()
        self.thread.join(timeout=5)

    def https(
        self,
        path,
        *,
        key=None,
        body=None,
        headers=None,
        identity=None,
        method=None,
    ):
        # Trust exactly the selected temporary certificate. Hostnames are not
        # the identity in QR-pinned deployments with changing LAN IP addresses.
        context = ssl.create_default_context(cafile=str((identity or self.identity).path))
        context.check_hostname = False
        connection = http.client.HTTPSConnection("127.0.0.1", self.port,
                                                 context=context, timeout=5)
        request_headers = dict(headers or {})
        if key is not None:
            request_headers["Authorization"] = f"Bearer {key}"
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        try:
            connection.request(
                method or ("POST" if body is not None else "GET"),
                path,
                body=json.dumps(body) if body is not None else None,
                headers=request_headers,
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_authenticated_https_reaches_existing_api(self):
        status, raw = self.https("/api/device-info", key=self.fixture.token)
        self.assertEqual(status, 200)
        info = json.loads(raw)
        self.assertEqual(info["protocol_version"], 3)
        self.assertEqual(info["transport"], "https")
        self.assertEqual(info["capabilities"]["actuator_feedback"], "simulated")
        self.assertTrue(info["capabilities"]["actuator_control_available"])
        self.assertFalse(info["capabilities"]["physical_state_confirmed"])

        grant = self.fixture.grant("settings.update")
        status, _ = self.https(
            "/api/settings",
            key=self.fixture.token,
            body={"autoRelockSeconds": 17},
            headers={"X-BASS-Operation-Grant": grant},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.fixture.db.get_settings_for_ui()["autoRelockSeconds"], 17
        )

    def test_missing_wrong_and_other_host_keys_cannot_write(self):
        for key in (None, "invalid", self.config.pairing_key, self.other_key):
            with self.subTest(key_kind="missing" if key is None else "wrong"):
                status, _ = self.https(
                    "/api/settings",
                    key=key,
                    body={"autoRelockSeconds": 99},
                    headers={"X-BASS-Operation-Grant": "invalid"},
                )
                self.assertEqual(status, 401)
        self.assertEqual(
            self.fixture.db.get_settings_for_ui()["autoRelockSeconds"], 10
        )

    def test_untrusted_certificate_fails_before_request(self):
        grant = self.fixture.grant("settings.update")
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.https(
                "/api/settings",
                key=self.fixture.token,
                body={"autoRelockSeconds": 99},
                headers={"X-BASS-Operation-Grant": grant},
                identity=self.other_identity,
            )
        self.assertEqual(
            self.fixture.db.get_settings_for_ui()["autoRelockSeconds"], 10
        )

    def test_plain_http_on_lan_port_cannot_write(self):
        grant = self.fixture.grant("settings.update")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            try:
                connection.request(
                    "POST",
                    "/api/settings",
                    body='{"autoRelockSeconds":99}',
                    headers={
                        "Authorization": f"Bearer {self.fixture.token}",
                        "Content-Type": "application/json",
                        "X-BASS-Operation-Grant": grant,
                    },
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 400)
            except (OSError, http.client.HTTPException):
                pass
        finally:
            connection.close()
        self.assertEqual(
            self.fixture.db.get_settings_for_ui()["autoRelockSeconds"], 10
        )

    def test_tls_listener_cannot_expose_local_bridge_or_qr(self):
        origin = f"http://localhost:{self.dashboard_port}"
        headers = {"Host": f"localhost:{self.dashboard_port}", "Origin": origin,
                   "Referer": f"{origin}/", "Sec-Fetch-Site": "same-origin",
                   "X-Forwarded-For": "127.0.0.1"}
        for path in ("/local/pairing-qr", "/local/wireless/api/users", "/"):
            with self.subTest(path=path):
                status, _ = self.https(path, headers=headers)
                self.assertEqual(status, 403)

    def test_local_dashboard_bridge_and_cross_origin_boundary(self):
        origin = f"http://127.0.0.1:{self.dashboard_port}"
        trusted = {
            "Origin": origin,
            "Referer": f"{origin}/",
            "Sec-Fetch-Site": "same-origin",
        }
        connection = http.client.HTTPConnection("127.0.0.1", self.dashboard_port, timeout=5)
        try:
            connection.request("GET", "/", headers=trusted)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()

            for path in ("/local/pairing-qr", "/local/wireless/api/users"):
                connection.request("GET", path, headers=trusted)
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()

            login_headers = {**trusted, "Content-Type": "application/json"}
            connection.request(
                "POST",
                "/local/security/login",
                body=json.dumps({"name": "Test Admin", "pin": self.fixture.pin}),
                headers=login_headers,
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            login = json.loads(response.read())
            cookie = SimpleCookie()
            cookie.load(response.getheader("Set-Cookie"))
            session_cookie = cookie["bass_session"]
            self.assertTrue(session_cookie["httponly"])
            self.assertEqual(session_cookie["samesite"], "Strict")
            authenticated = {
                **trusted,
                "Cookie": f"bass_session={session_cookie.value}",
            }

            connection.request(
                "GET", "/local/wireless/api/users", headers=authenticated
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()

            grant_body = json.dumps(
                {"pin": self.fixture.pin, "action": "settings.update"}
            )
            connection.request(
                "POST",
                "/local/wireless/api/operation-grants",
                body=grant_body,
                headers={**authenticated, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()

            csrf_headers = {**authenticated, "X-BASS-CSRF": login["csrf_token"]}
            connection.request(
                "POST",
                "/local/wireless/api/operation-grants",
                body=grant_body,
                headers={**csrf_headers, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            grant = json.loads(response.read())["grant_token"]

            connection.request(
                "POST",
                "/local/wireless/api/settings",
                body=json.dumps({"autoRelockSeconds": 23}),
                headers={
                    **csrf_headers,
                    "Content-Type": "application/json",
                    "X-BASS-Operation-Grant": grant,
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.request("GET", "/local/pairing-qr",
                               headers={"Origin": "https://outside.invalid",
                                        "Referer": "https://outside.invalid/",
                                        "Sec-Fetch-Site": "cross-site"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

    def test_forged_loopback_headers_do_not_override_peer(self):
        origin = f"http://localhost:{self.dashboard_port}"
        response = self.client.get(
            "/local/pairing-qr", base_url=origin,
            headers={"Referer": f"{origin}/", "X-Forwarded-For": "127.0.0.1"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.55"},
        )
        self.assertEqual(response.status_code, 403)

    def test_config_and_qr_identity_stay_fixed(self):
        config_before = self.config_path.read_bytes()
        tls_before = self.identity.path.read_bytes()
        restored_config = load_or_create_device_config(self.config_path)
        restored_tls = load_or_create_tls_identity(self.config_path, restored_config.device_id)
        restored_config = replace(restored_config,
                                  tls_certificate_sha256=restored_tls.certificate_sha256)
        self.assertEqual(restored_config.pairing_payload, self.config.pairing_payload)
        self.assertEqual(restored_config.pairing_payload["version"], 3)
        self.assertEqual(restored_config.pairing_payload["purpose"], "device")
        self.assertNotIn("pairing_key", restored_config.pairing_payload)
        self.assertEqual(json.loads(config_before)["version"], 1)
        self.assertEqual(self.config_path.read_bytes(), config_before)
        self.assertEqual(self.identity.path.read_bytes(), tls_before)
        self.assertEqual(restored_tls.context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_corrupt_identity_fails_without_replacement(self):
        path = self.root / "broken.json"
        config = load_or_create_device_config(path)
        tls_path = path.with_suffix(".tls.pem")
        tls_path.write_bytes(b"broken identity")
        tls_path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "not regenerated"):
            load_or_create_tls_identity(path, config.device_id)
        self.assertEqual(tls_path.read_bytes(), b"broken identity")

    def test_expired_certificate_fails_without_replacement(self):
        path = self.root / "expired.json"
        config = load_or_create_device_config(path)
        expired_cert = self.root / "expired-cert.pem"
        subprocess.run(
            [_openssl(), "x509", "-in", str(self.identity.path),
             "-signkey", str(self.identity.path), "-days", "0",
             "-out", str(expired_cert)],
            check=True, capture_output=True, timeout=10,
        )
        pem = self.identity.path.read_bytes()
        key_only = pem[:pem.index(b"-----BEGIN CERTIFICATE-----")]
        expired_identity = key_only + expired_cert.read_bytes()
        tls_path = path.with_suffix(".tls.pem")
        tls_path.write_bytes(expired_identity)
        tls_path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, "invalid or expired"):
            load_or_create_tls_identity(path, config.device_id)
        self.assertEqual(tls_path.read_bytes(), expired_identity)


if __name__ == "__main__":
    unittest.main()
