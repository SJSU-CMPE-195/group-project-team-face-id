"""Exercise host authorization through Flask with temporary SQLite and fake hardware."""

import base64
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pi_device_api import create_app
from security_fixtures import temporary_security, AuthorizedFakeRuntime
from wireless.api import secure_wireless_app
from wireless.config import load_or_create_device_config
from wireless.security import PAIRING_INVITE_SECONDS, SecurityError


class RouteRuntime(AuthorizedFakeRuntime):
    def cancel_device_sessions(self, device_id):
        self.calls.append(("cancel_device_sessions", device_id))

    def start_scan(self, purpose="unlock", expected_user=None, *, authorization=None):
        assert self.security.still_authorized(authorization)
        result = super().start_scan(purpose, expected_user)
        self.sessions[result["session_id"]]["actor"] = authorization.device_id
        return result

    def require_session_owner(self, session_id, principal):
        from car_face_auth.src.pi_runtime import RuntimeRequestError
        if self.sessions.get(session_id, {}).get("actor") != principal.device_id:
            raise RuntimeRequestError("session belongs to another device", 403)


class WirelessAuthorizationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fixture = temporary_security(self, root)
        self.security = self.fixture.security
        self.runtime = RouteRuntime(self.fixture.db)
        self.config = replace(
            load_or_create_device_config(root / "device.json"),
            tls_certificate_sha256="a" * 64,
        )
        self.app = secure_wireless_app(
            create_app(db_module=self.fixture.db, runtime=self.runtime),
            config=self.config, mode="pc", port=5057, dist_root=root,
            security=self.security,
        )
        self.client = self.app.test_client()
        self.origin = "http://localhost:5057"
        self.local_headers = {"Referer": self.origin + "/", "Origin": self.origin}

    def mutation(self, path, action, body=None, target=None, method="POST"):
        grant = self.fixture.grant(action, target)
        return self.client.open(path, method=method, json=body,
                                headers=self.fixture.headers(grant))

    def pair_user(self):
        user = self.security.create_user(self.fixture.principal(), "Regular", "654321")
        invite = self.security.issue_invite(self.fixture.principal(), user["id"])
        response = self.client.post("/api/pairings", json={
            "invite_token": invite["invite_token"], "pin": "654321", "device_name": "Phone",
        }, headers={"Authorization": f"Onboarding {self.config.pairing_key}"})
        self.assertEqual(response.status_code, 201, response.json)
        return user, response.json["device_token"], invite

    def test_old_shared_key_and_missing_auth_cannot_parse_or_write(self):
        for credential in (None, self.config.pairing_key):
            headers = {"Authorization": f"Bearer {credential}"} if credential else {}
            response = self.client.post("/api/users", data="invalid JSON",
                                        content_type="application/json", headers=headers)
            self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.fixture.db.get_all_users()), 1)

    def test_invite_is_required_single_use_and_does_not_grant_admin(self):
        user, token, invite = self.pair_user()
        response = self.client.post("/api/pairings", json={
            "invite_token": invite["invite_token"], "pin": "654321", "device_name": "Replay",
        }, headers={"Authorization": f"Onboarding {self.config.pairing_key}"})
        self.assertNotEqual(response.status_code, 201)
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/api/users", headers=headers).json[0]["id"], user["id"])
        self.assertEqual(len(self.client.get("/api/users", headers=headers).json), 1)
        self.assertEqual(self.client.get("/api/logs", headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/api/settings", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/enroll/start", json={"name": "Regular"},
                                         headers=headers).status_code, 403)
        self.assertEqual(self.runtime.calls, [])

    def test_pairing_invite_qr_preserves_authorization_and_secret_boundaries(self):
        user = self.security.create_user(
            self.fixture.principal(),
            "QR user",
            "654321",
        )
        body = {"user_id": user["id"]}

        denied = self.client.post(
            "/api/pairing-invites",
            json=body,
            headers=self.fixture.headers(),
            base_url="https://device.test:5056",
        )
        self.assertEqual(denied.status_code, 403, denied.json)

        grant = self.fixture.grant("pairing.invite", user["id"])
        response = self.client.post(
            "/api/pairing-invites",
            json=body,
            headers=self.fixture.headers(grant),
            base_url="https://device.test:5056",
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            response.json["qr_payload"],
            {
                **self.config.pairing_payload,
                "purpose": "pairing_invite",
                "invite_token": response.json["invite_token"],
            },
        )
        self.assertEqual(response.json["expires_in"], PAIRING_INVITE_SECONDS)
        payload_json = json.dumps(response.json["qr_payload"])
        for secret in (
            "pin",
            "activation_secret",
            "recovery_secret",
            "pairing_key",
            self.config.pairing_key,
        ):
            self.assertNotIn(secret, payload_json)
        image_prefix = "data:image/png;base64,"
        self.assertTrue(response.json["qr_image"].startswith(image_prefix))
        self.assertTrue(
            base64.b64decode(response.json["qr_image"][len(image_prefix):]).startswith(
                b"\x89PNG\r\n\x1a\n"
            )
        )
        paired = self.security.pair(
            response.json["invite_token"],
            "654321",
            "QR phone",
        )
        self.assertIn("device_token", paired)

        login = self.client.post(
            "/local/security/login",
            base_url=self.origin,
            json={"name": "Test Admin", "pin": self.fixture.pin},
            headers=self.local_headers,
        )
        authenticated = {
            **self.local_headers,
            "X-BASS-CSRF": login.json["csrf_token"],
        }
        with mock.patch("wireless.security.time.time", return_value=1_000):
            local_grant = self.client.post(
                "/local/wireless/api/operation-grants",
                base_url=self.origin,
                json={
                    "pin": self.fixture.pin,
                    "action": "pairing.invite",
                    "target_user_id": user["id"],
                },
                headers=authenticated,
            )
            local_response = self.client.post(
                "/local/wireless/api/pairing-invites",
                base_url=self.origin,
                json=body,
                headers={
                    **authenticated,
                    "X-BASS-Operation-Grant": local_grant.json["grant_token"],
                },
            )
        self.assertEqual(local_response.status_code, 201, local_response.json)
        self.assertEqual(local_response.headers["Cache-Control"], "no-store")
        self.assertEqual(local_response.json["qr_payload"]["version"], 3)
        with mock.patch(
            "wireless.security.time.time",
            return_value=1_000 + PAIRING_INVITE_SECONDS,
        ):
            with self.assertRaises(SecurityError) as expired:
                self.security.pair(
                    local_response.json["invite_token"],
                    "654321",
                    "Expired QR phone",
                )
        self.assertEqual(expired.exception.code, "invalid_pairing_invite")

    def test_targetless_grant_and_replay_are_checked_at_real_route(self):
        grant = self.fixture.grant("settings.update")
        headers = self.fixture.headers(grant)
        response = self.client.post("/api/settings", json={"autoRelockSeconds": 12}, headers=headers)
        self.assertEqual(response.status_code, 200, response.json)
        replay = self.client.post("/api/settings", json={"autoRelockSeconds": 13}, headers=headers)
        self.assertEqual(replay.status_code, 403, replay.json)
        self.assertEqual(self.fixture.db.get_settings_for_ui()["autoRelockSeconds"], 12)

    def test_user_list_keeps_roles_and_regular_user_scope(self):
        user, token, _ = self.pair_user()
        response = self.client.get("/api/users", headers=self.fixture.headers())
        self.assertEqual(response.status_code, 200)
        roles = {row["id"]: row["is_admin"] for row in response.json}
        self.assertTrue(roles[self.fixture.admin.user_id])
        self.assertFalse(roles[user["id"]])
        own = self.client.get(
            "/api/users", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(own.status_code, 200)
        self.assertEqual([row["id"] for row in own.json], [user["id"]])
        self.assertFalse(own.json[0]["is_admin"])

    def test_control_grant_accepts_empty_target_and_requires_pin(self):
        grant = self.client.post("/api/operation-grants", json={
            "pin": self.fixture.pin, "action": "device.lock", "target_user_id": "",
        }, headers=self.fixture.headers())
        self.assertEqual(grant.status_code, 200, grant.json)
        response = self.client.post("/api/lock", json={}, headers=self.fixture.headers(grant.json["grant_token"]))
        self.assertEqual(response.status_code, 200, response.json)

    def test_create_user_enrollment_grant_is_scoped_single_use_and_expires(self):
        def create(name):
            return self.mutation(
                "/api/users",
                "user.create",
                {"name": name, "pin": "654321", "enroll_face": True},
            )

        with mock.patch("wireless.security.time.time", return_value=1_000):
            created = create("Enrollment user")
            self.assertEqual(created.status_code, 201, created.json)
            token = created.json["enrollment_grant"]
            target = created.json["id"]

            with self.assertRaises(SecurityError) as wrong_target:
                self.security.consume_grant(
                    self.fixture.principal(),
                    token,
                    "enrollment.start",
                    self.fixture.admin.user_id,
                )
            self.assertEqual(wrong_target.exception.code, "invalid_operation_grant")

            self.security.consume_grant(
                self.fixture.principal(), token, "enrollment.start", target
            )
            with self.assertRaises(SecurityError) as reused:
                self.security.consume_grant(
                    self.fixture.principal(), token, "enrollment.start", target
                )
            self.assertEqual(reused.exception.code, "invalid_operation_grant")

            expiring = create("Expiring enrollment user")
            self.assertEqual(expiring.status_code, 201, expiring.json)

        with mock.patch("wireless.security.time.time", return_value=1_030):
            with self.assertRaises(SecurityError) as expired:
                self.security.consume_grant(
                    self.fixture.principal(),
                    expiring.json["enrollment_grant"],
                    "enrollment.start",
                    expiring.json["id"],
                )
            self.assertEqual(expired.exception.code, "invalid_operation_grant")

    def test_grant_wrong_target_cannot_delete_another_user(self):
        first = self.security.create_user(self.fixture.principal(), "One", "654321")
        second = self.security.create_user(self.fixture.principal(), "Two", "654321")
        response = self.mutation(f'/api/users/{second["id"]}', "user.delete", target=first["id"], method="DELETE")
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(self.fixture.db.get_user_by_id(second["id"]))

    def test_correct_pin_starts_own_host_scan_without_unlocking(self):
        response = self.client.post("/api/operation-grants", json={
            "pin": self.fixture.pin, "action": "scan.unlock",
            "target_user_id": self.fixture.admin.user_id,
        }, headers=self.fixture.headers())
        self.assertEqual(response.status_code, 200, response.json)
        started = self.client.post("/api/scan/start", json={"purpose": "unlock", "expected_user": "Impersonated"},
                                   headers=self.fixture.headers(response.json["grant_token"]))
        self.assertEqual(started.status_code, 200, started.json)
        session = self.runtime.sessions[started.json["session_id"]]
        self.assertEqual(session["expected_user"], self.fixture.admin.name)
        self.assertEqual(self.fixture.db.get_status()["lockState"], "locked")
        self.assertEqual(self.runtime.hardware_calls, [])

    def test_invalid_scan_purpose_is_rejected_before_session_creation(self):
        for purpose in ([], {}, 1, None):
            with self.subTest(purpose=purpose):
                response = self.client.post(
                    "/api/scan/start",
                    json={"purpose": purpose},
                    headers=self.fixture.headers(),
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.runtime.sessions, {})
        self.assertEqual(self.runtime.hardware_calls, [])

    def test_cannot_delete_last_admin_or_reset_it_without_login(self):
        response = self.mutation(f"/api/users/{self.fixture.admin.user_id}", "user.delete",
                                 target=self.fixture.admin.user_id, method="DELETE")
        self.assertEqual(response.status_code, 409, response.json)
        response = self.client.post("/local/security/initial-admin", base_url=self.origin,
                                   json={"name": "Other", "pin": "654321"}, headers=self.local_headers)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json["code"], "phone_claim_required")
        self.assertEqual(len(self.fixture.db.get_all_users()), 1)

    def test_local_login_csrf_logout_and_cross_origin_rate_isolation(self):
        for _ in range(21):
            rejected = self.client.post("/local/security/login", base_url=self.origin,
                json={"name": "Test Admin", "pin": self.fixture.pin},
                headers={"Origin": "https://outside.invalid"})
            self.assertEqual(rejected.status_code, 403)
        login = self.client.post("/local/security/login", base_url=self.origin,
            json={"name": "Test Admin", "pin": self.fixture.pin}, headers=self.local_headers)
        self.assertEqual(login.status_code, 200, login.json)
        self.assertIn("HttpOnly", login.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", login.headers["Set-Cookie"])
        grant_path = "/local/wireless/api/operation-grants"
        body = {"pin": self.fixture.pin, "action": "settings.update"}
        denied = self.client.post(grant_path, base_url=self.origin, json=body, headers=self.local_headers)
        self.assertEqual(denied.status_code, 403)
        authenticated = {**self.local_headers, "X-BASS-CSRF": login.json["csrf_token"]}
        allowed = self.client.post(grant_path, base_url=self.origin, json=body, headers=authenticated)
        self.assertEqual(allowed.status_code, 200, allowed.json)
        logout = self.client.post("/local/security/logout", base_url=self.origin, headers=authenticated)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/local/wireless/api/users", base_url=self.origin,
                                         headers=self.local_headers).status_code, 401)

    def test_remembered_local_login_requires_selected_id_and_pin(self):
        status = self.client.get("/local/security/status", base_url=self.origin,
                                 headers=self.local_headers)
        self.assertEqual(status.status_code, 200, status.json)
        self.assertEqual(status.json["device_id"], self.config.device_id)
        self.assertEqual(status.json["generation"], 0)
        selected = {"user_id": self.fixture.admin.user_id, "pin": self.fixture.pin}
        for body in ({"pin": self.fixture.pin}, {**selected, "name": "Test Admin"}):
            invalid = self.client.post("/local/security/login", base_url=self.origin,
                                       json=body, headers=self.local_headers)
            self.assertEqual(invalid.status_code, 400, invalid.json)
        wrong = self.client.post("/local/security/login", base_url=self.origin,
                                 json={**selected, "pin": "000000"}, headers=self.local_headers)
        self.assertEqual(wrong.status_code, 401, wrong.json)
        login = self.client.post("/local/security/login", base_url=self.origin,
                                 json=selected, headers=self.local_headers)
        self.assertEqual(login.status_code, 200, login.json)
        self.assertEqual(login.json["user"]["id"], selected["user_id"])
        self.assertIn("HttpOnly", login.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", login.headers["Set-Cookie"])
        without_csrf = self.client.post("/local/security/logout", base_url=self.origin,
                                       headers=self.local_headers)
        self.assertEqual(without_csrf.status_code, 403)
        logout = self.client.post("/local/security/logout", base_url=self.origin,
            headers={**self.local_headers, "X-BASS-CSRF": login.json["csrf_token"]})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/local/security/session", base_url=self.origin,
                                        headers=self.local_headers).status_code, 401)

    def test_revoked_browser_session_is_evicted_before_logout_csrf(self):
        selected = {"user_id": self.fixture.admin.user_id, "pin": self.fixture.pin}
        login = self.client.post(
            "/local/security/login",
            base_url=self.origin,
            json=selected,
            headers=self.local_headers,
        )
        self.assertEqual(login.status_code, 200, login.json)

        active_without_csrf = self.client.post(
            "/local/security/logout",
            base_url=self.origin,
            headers=self.local_headers,
        )
        self.assertEqual(active_without_csrf.status_code, 403)
        self.assertEqual(active_without_csrf.json["code"], "invalid_csrf")

        replacement = self.app.test_client().post(
            "/local/security/login",
            base_url=self.origin,
            json=selected,
            headers=self.local_headers,
        )
        self.assertEqual(replacement.status_code, 200, replacement.json)

        expired = self.client.get(
            "/local/security/session",
            base_url=self.origin,
            headers=self.local_headers,
        )
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.json["code"], "invalid_device_credential")

        logout = self.client.post(
            "/local/security/logout",
            base_url=self.origin,
            headers=self.local_headers,
        )
        self.assertEqual(logout.status_code, 401)
        self.assertEqual(logout.json["code"], "login_required")

    def test_phone_entry_is_bound_to_bearer_and_grants_no_operation(self):
        user, token, _ = self.pair_user()
        path = "/api/session-login"
        headers = {"Authorization": f"Bearer {token}"}
        no_device = self.client.post(path, data="invalid JSON", content_type="application/json")
        self.assertEqual(no_device.status_code, 401)
        wrong_account = self.client.post(path, json={"pin": self.fixture.pin}, headers=headers)
        self.assertEqual(wrong_account.status_code, 401, wrong_account.json)
        switch = self.client.post(path, json={"pin": "654321", "user_id": self.fixture.admin.user_id},
                                  headers=headers)
        self.assertEqual(switch.status_code, 400)
        login = self.client.post(path, json={"pin": "654321"}, headers=headers)
        self.assertEqual(login.status_code, 200, login.json)
        self.assertEqual(login.json, self.client.get("/api/me", headers=headers).json)
        self.assertEqual(login.json["user"]["id"], user["id"])
        self.assertFalse(login.json["user"]["is_admin"])
        self.assertEqual(login.headers["Cache-Control"], "no-store")
        denied = self.client.post("/api/scan/start", json={}, headers=headers)
        self.assertEqual(denied.status_code, 403, denied.json)
        self.assertEqual(self.runtime.calls, [])
        self.security.revoke_device(self.fixture.principal(), login.json["device_id"])
        revoked = self.client.post(path, json={"pin": "654321"}, headers=headers)
        self.assertEqual(revoked.status_code, 401, revoked.json)

    def test_legacy_account_pin_setup_and_device_revocation(self):
        legacy = self.fixture.db.add_user("Legacy")
        response = self.mutation(f'/api/users/{legacy["id"]}/pin', "user.pin",
                                 {"pin": "654321"}, target=legacy["id"])
        self.assertEqual(response.status_code, 200, response.json)
        user, token, _ = self.pair_user()
        principal = self.security.principal(token)
        response = self.mutation(f"/api/devices/{principal.device_id}/revoke", "device.revoke",
                                 target=principal.device_id)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
