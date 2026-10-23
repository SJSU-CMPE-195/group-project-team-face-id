"""Regression coverage for ordinary-user face self-enrollment."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import uuid

from car_face_auth.src.pi_runtime import RuntimeRequestError
import db as db_module
from pi_device_api import create_app
from security_fixtures import temporary_security
from test_runtime_authorization import AuthorizationRuntime, FakeFaceEngine
from wireless.api import secure_wireless_app
from wireless.config import load_or_create_device_config
from wireless.security import SecurityError


class SelfEnrollmentTests(unittest.TestCase):
    member_pin = "654321"

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fixture = temporary_security(self, self.root)
        self.security = self.fixture.security
        self.admin = self.fixture.principal()
        with db_module.get_conn() as conn:
            conn.execute(
                "INSERT INTO commissioning_state "
                "(id,ownership,owner_user_id,generation,activation_secret,"
                "activation_hash,recovery_hash,updated_at) "
                "VALUES (1,'claimed',?,1,NULL,NULL,?,1)",
                (self.admin.user_id, b"r" * 32),
            )
        self.admin = self.fixture.principal()
        self.member = self.security.create_user(
            self.admin,
            "Ada",
            self.member_pin,
        )
        self.member_token = self._pair_user(
            self.member["id"],
            self.member_pin,
            "Ada phone",
        )
        self.fixture.db.save_settings_from_ui({"liveness": False})
        self.runtime = AuthorizationRuntime(self.fixture.db, FakeFaceEngine())
        self.runtime.configure_authorization(self.security)
        self.addCleanup(self.runtime.close)

    def _pair_user(self, user_id: str, pin: str, device_name: str) -> str:
        invite = self.security.issue_invite(self.admin, user_id)
        paired = self.security.pair(invite["invite_token"], pin, device_name)
        return paired["device_token"]

    def _reserve(
        self,
        token: str,
        pin: str,
        target_user_id: str,
        *,
        request_id: str | None = None,
    ):
        principal = self.security.principal(token)
        grant = self.security.authorize(
            principal,
            pin,
            "enrollment.start",
            target_user_id,
        )
        body = {
            "request_id": request_id or str(uuid.uuid4()),
            "user_id": target_user_id,
            "source": "device_camera",
        }
        reservation = self.security.enrollment.reserve(
            principal,
            body,
            grant["grant_token"],
        )
        return principal, body, reservation

    def _start(self, principal, reservation, target_user_id: str):
        started = self.runtime.start_enrollment(
            target_user_id,
            authorization=principal,
            enrollment_reservation=reservation,
        )
        session = self.runtime._session(started["session_id"])
        self.assertIsNotNone(session, started)
        return session

    def _complete(self, session, template: bytes):
        activation = self.runtime._authorization.activate_enrollment(session)
        self.assertTrue(activation["activated"])
        result = self.runtime._enrollment_result(
            session,
            state="completed",
            count=10,
            recognition_available=True,
            message=f"Enrollment completed for {session['name']}.",
        )
        projection = self.runtime._authorization.complete_enrollment(
            session,
            template,
            result,
        )
        self.runtime._apply_enrollment_projection(session["id"], projection)
        self.runtime._release_session(session["id"])
        return result, projection

    def _face_state(self):
        with db_module.get_conn() as conn:
            row = conn.execute(
                "SELECT face_encoding,face_access FROM users WHERE id=?",
                (self.member["id"],),
            ).fetchone()
        return bytes(row["face_encoding"]) if row["face_encoding"] else None, bool(
            row["face_access"]
        )

    def _enrollment_audit_count(self) -> int:
        with db_module.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) AS count FROM auth_logs "
                "WHERE stage='enroll_embedding' AND user_id=? "
                "AND actor_user_id=?",
                (self.member["id"], self.member["id"]),
            ).fetchone()["count"]

    def _set_original_template(self) -> None:
        with db_module.get_conn() as conn:
            conn.execute(
                "UPDATE users SET face_encoding=? WHERE id=?",
                (b"original-template", self.member["id"]),
            )

    def test_paired_ordinary_api_can_start_and_lookup_own_receipt(self):
        config = replace(
            load_or_create_device_config(self.root / "device.json"),
            tls_certificate_sha256="a" * 64,
        )
        app = secure_wireless_app(
            create_app(db_module=self.fixture.db, runtime=self.runtime),
            config=config,
            mode="pc",
            port=5057,
            dist_root=self.root,
            security=self.security,
        )
        client = app.test_client()
        headers = {"Authorization": f"Bearer {self.member_token}"}
        grant = client.post(
            "/api/operation-grants",
            headers=headers,
            json={
                "pin": self.member_pin,
                "action": "enrollment.start",
                "target_user_id": self.member["id"],
            },
        )
        self.assertEqual(grant.status_code, 200, grant.json)
        request_id = str(uuid.uuid4())
        started = client.post(
            "/api/enroll/start",
            headers={
                **headers,
                "X-BASS-Operation-Grant": grant.json["grant_token"],
            },
            json={
                "request_id": request_id,
                "user_id": self.member["id"],
                "source": "device_camera",
            },
        )

        self.assertEqual(started.status_code, 202, started.json)
        self.assertEqual(started.json["request_id"], request_id)
        receipt = client.get(
            f"/api/enroll/requests/{request_id}",
            headers=headers,
        )
        self.assertEqual(receipt.status_code, 200, receipt.json)
        self.assertEqual(receipt.json["state"], "accepted")
        self.assertEqual(
            receipt.json["session"]["session_id"],
            started.json["session_id"],
        )
        for internal_field in (
            "actor_user_id",
            "actor_device_id",
            "actor_auth_version",
            "target_user_id",
        ):
            self.assertNotIn(internal_field, started.json)
            self.assertNotIn(internal_field, receipt.json)

    def test_ordinary_user_can_enroll_and_replace_own_template_with_fresh_pin(self):
        principal, _body, reservation = self._reserve(
            self.member_token,
            self.member_pin,
            self.member["id"],
        )
        self._complete(
            self._start(principal, reservation, self.member["id"]),
            b"first-template",
        )
        self.assertEqual(self._face_state(), (b"first-template", True))

        principal, _body, replacement = self._reserve(
            self.member_token,
            self.member_pin,
            self.member["id"],
        )
        self._complete(
            self._start(principal, replacement, self.member["id"]),
            b"replacement-template",
        )

        self.assertEqual(self._face_state(), (b"replacement-template", True))
        self.assertEqual(self._enrollment_audit_count(), 2)

    def test_ordinary_user_cannot_target_another_user_or_owner(self):
        other = self.security.create_user(self.admin, "Bob", "777777")
        principal = self.security.principal(self.member_token)

        for target_user_id in (other["id"], self.admin.user_id):
            with self.subTest(target_user_id=target_user_id):
                with self.assertRaises(SecurityError) as denied:
                    self.security.authorize(
                        principal,
                        self.member_pin,
                        "enrollment.start",
                        target_user_id,
                    )
                self.assertEqual(denied.exception.code, "target_not_allowed")

    def test_disabled_user_can_enroll_but_remains_disabled(self):
        self.assertTrue(
            self.fixture.db.set_user_access(self.member["id"], False)["ok"]
        )
        principal, _body, reservation = self._reserve(
            self.member_token,
            self.member_pin,
            self.member["id"],
        )
        self._complete(
            self._start(principal, reservation, self.member["id"]),
            b"disabled-user-template",
        )

        self.assertEqual(self._face_state(), (b"disabled-user-template", False))
        refreshed = self.security.principal(self.member_token)
        with self.assertRaises(SecurityError) as denied:
            self.security.authorize(
                refreshed,
                self.member_pin,
                "scan.unlock",
                self.member["id"],
            )
        self.assertEqual(denied.exception.code, "face_access_denied")

    def test_completed_receipt_retry_does_not_duplicate_template_or_audit(self):
        principal, body, reservation = self._reserve(
            self.member_token,
            self.member_pin,
            self.member["id"],
        )
        result, completed = self._complete(
            self._start(principal, reservation, self.member["id"]),
            b"committed-template",
        )

        retry = self.security.enrollment.reserve(
            self.security.principal(self.member_token),
            body,
            "",
        )
        replay = self.security.enrollment.complete(
            reservation["session_id"],
            b"must-not-replace",
            {**result, "message": "must not replace the committed result"},
        )

        self.assertFalse(retry["created"])
        self.assertEqual(retry["response"], reservation["response"])
        self.assertEqual(replay, completed)
        self.assertEqual(self._face_state(), (b"committed-template", True))
        self.assertEqual(self._enrollment_audit_count(), 1)

    def test_cancellation_and_device_revocation_preserve_original_template(self):
        self._set_original_template()
        principal, _body, reservation = self._reserve(
            self.member_token,
            self.member_pin,
            self.member["id"],
        )
        session = self._start(principal, reservation, self.member["id"])
        self.assertTrue(
            self.runtime._authorization.activate_enrollment(session)["activated"]
        )

        cancelled = self.runtime.cancel_enrollment(session["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(self._face_state(), (b"original-template", True))
        self.assertEqual(self._enrollment_audit_count(), 0)
        self.runtime._release_session(session["id"])

        principal, _body, reservation = self._reserve(
            self.member_token,
            self.member_pin,
            self.member["id"],
        )
        session = self._start(principal, reservation, self.member["id"])
        self.assertTrue(
            self.runtime._authorization.activate_enrollment(session)["activated"]
        )
        self.security.revoke_device(self.admin, principal.device_id)

        self.assertEqual(self.runtime.cancel_device_sessions(principal.device_id), 1)
        self.assertEqual(self._face_state(), (b"original-template", True))
        self.assertEqual(self._enrollment_audit_count(), 0)
        with db_module.get_conn() as conn:
            state = conn.execute(
                "SELECT state FROM enrollment_requests WHERE request_id=?",
                (reservation["request_id"],),
            ).fetchone()["state"]
        self.assertEqual(state, "cancelled")

    def test_reservation_is_bound_to_actor_device_and_target(self):
        tablet_token = self._pair_user(
            self.member["id"],
            self.member_pin,
            "Ada tablet",
        )
        other = self.security.create_user(self.admin, "Bob", "777777")
        other_token = self._pair_user(other["id"], "777777", "Bob phone")
        principal, _body, reservation = self._reserve(
            self.member_token,
            self.member_pin,
            self.member["id"],
        )

        for token in (tablet_token, other_token):
            with self.subTest(token=token):
                with self.assertRaisesRegex(
                    RuntimeRequestError,
                    "another authorization",
                ):
                    self.runtime.start_enrollment(
                        self.member["id"],
                        authorization=self.security.principal(token),
                        enrollment_reservation=reservation,
                    )
        with self.assertRaisesRegex(RuntimeRequestError, "target does not match"):
            self.runtime.start_enrollment(
                other["id"],
                authorization=principal,
                enrollment_reservation=reservation,
            )
        with self.assertRaises(SecurityError) as hidden:
            self.security.enrollment.query(
                self.security.principal(tablet_token),
                reservation["request_id"],
            )
        self.assertEqual(hidden.exception.code, "enrollment_request_not_found")
        self.assertEqual(
            self.security.enrollment.query(
                principal,
                reservation["request_id"],
            )["state"],
            "accepted",
        )
        self.assertEqual(self.runtime.spawned_sessions, [])


if __name__ == "__main__":
    unittest.main()
