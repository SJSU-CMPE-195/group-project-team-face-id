"""Hardware-free authorization tests for Pi runtime sessions and actuation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import threading
import time
import unittest

from car_face_auth.src.pi_runtime import PiRuntime, RuntimeRequestError, SAMPLES_NEEDED
from wireless.security import Principal


class FakeSecurityStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.versions = {"admin": 3, "u1": 7, "u2": 11}
        self.face_access = {"admin": True, "u1": True, "u2": True}
        self.devices = {
            ("admin-phone", "admin"),
            ("ada-phone", "u1"),
            ("ada-tablet", "u1"),
            ("bob-phone", "u2"),
        }

    @contextmanager
    def synchronized(self):
        with self.lock:
            yield

    def still_authorized(self, principal: Principal) -> bool:
        with self.lock:
            return bool(
                (principal.device_id, principal.user_id) in self.devices
                and self.versions.get(principal.user_id) == principal.auth_version
                and self.face_access.get(principal.user_id, False)
            )

    def user_auth_version(self, user_id: str) -> int | None:
        with self.lock:
            return self.versions.get(user_id)

    def user_is_current(
        self,
        user_id: str,
        auth_version: int,
        *,
        require_face_access: bool = False,
    ) -> bool:
        with self.lock:
            return bool(
                self.versions.get(user_id) == auth_version
                and (
                    not require_face_access
                    or self.face_access.get(user_id, False)
                )
            )

    def revoke(self, user_id: str) -> None:
        with self.lock:
            self.versions[user_id] += 1


class FakeDb:
    def __init__(self) -> None:
        self.users = [
            {"id": "admin", "name": "Admin", "face_access": 1},
            {"id": "u1", "name": "Ada", "face_access": 1},
            {"id": "u2", "name": "Bob", "face_access": 1},
        ]
        self.lock_state = "locked"
        self.unlocks = []
        self.logs = []

    def get_all_users(self):
        return [dict(user) for user in self.users]

    def get_user_by_id(self, user_id):
        return next(
            (dict(user) for user in self.users if user["id"] == user_id),
            None,
        )

    def get_status(self):
        return {"lockState": self.lock_state}

    def get_settings_for_ui(self):
        return {
            "autoRelockSeconds": 0,
            "ignitionAutoStopSeconds": 0,
            "liveness": False,
            "failLockout": False,
            "lockoutAfter": 5,
        }

    def set_unlock(self, reason):
        self.lock_state = "unlocked"
        self.unlocks.append(reason)

    def log_event(self, stage, result, detail="", user_id=None):
        self.logs.append((stage, result, detail, user_id))


class FakeFaceEngine:
    def __init__(self) -> None:
        self.saved = []

    def save_user_embedding(self, name, embeddings):
        self.saved.append((name, list(embeddings)))
        return {"ok": True}


class FakeCapture:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.active = True
        self.frame_id = 0
        self.wait_calls = 0

    def failure(self):
        return None

    def request_stop(self):
        self.active = False

    def wait_for_newer(self, _frame_id, _timeout):
        self.wait_calls += 1
        return None


class AuthorizationRuntime(PiRuntime):
    actuator_control_available = True

    def __init__(self, db, face_engine=None):
        super().__init__(db, face_engine=face_engine)
        self.commands = []
        self.spawned_sessions = []

    def _spawn(self, session_id, _target):
        session = self._sessions[session_id]
        self.spawned_sessions.append(
            {
                key: session.get(key)
                for key in (
                    "actor_device_id",
                    "user_id",
                    "auth_version",
                    "expected_user_id",
                )
            }
        )

    def _send_command(self, command, connect=True):
        self.commands.append(command)
        return True

    def _schedule_session_cleanup(self, _session_id):
        return None


class RuntimeAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.store = FakeSecurityStore()
        self.face_engine = FakeFaceEngine()
        self.runtime = AuthorizationRuntime(self.db, self.face_engine)
        self.runtime.configure_authorization(self.store)
        self.ada = Principal("ada-phone", "u1", "Ada", False, 7)
        self.ada_tablet = Principal("ada-tablet", "u1", "Ada", False, 7)
        self.bob = Principal("bob-phone", "u2", "Bob", False, 11)
        self.admin = Principal("admin-phone", "admin", "Admin", True, 3)

    def start_scan(self, principal=None, expected_user=None):
        principal = principal or self.ada
        expected_user = expected_user or principal.name
        result = self.runtime.start_scan(
            expected_user=expected_user,
            authorization=principal,
        )
        return self.runtime._session(result["session_id"])

    def grant(self, session, candidate):
        capture = FakeCapture(session["id"])
        self.runtime._camera_capture = capture
        self.runtime._grant_scan(
            session["id"],
            candidate,
            0.9,
            session["cancel_event"],
            capture,
            time.monotonic() + 5,
        )
        return self.runtime._session(session["id"])

    def test_session_binds_identity_before_worker_spawn(self):
        session = self.start_scan()

        self.assertEqual(
            self.runtime.spawned_sessions,
            [
                {
                    "actor_device_id": "ada-phone",
                    "user_id": "u1",
                    "auth_version": 7,
                    "expected_user_id": "u1",
                }
            ],
        )
        self.assertEqual(session["expected_user"], "Ada")
        self.assertNotIn(
            "_authorization_principal",
            self.runtime._scan_view(session),
        )

    def test_configured_runtime_rejects_missing_authorization(self):
        with self.assertRaisesRegex(RuntimeRequestError, "authorization") as raised:
            self.runtime.start_scan(expected_user="Ada")

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self.runtime.spawned_sessions, [])
        self.assertIsNone(self.runtime.status()["active_session"])

    def test_face_match_for_another_user_never_unlocks(self):
        session = self.start_scan()

        result = self.grant(session, "Bob")

        self.assertEqual(result["state"], "denied")
        self.assertEqual(self.runtime.commands, [])
        self.assertEqual(self.db.unlocks, [])

    def test_revocation_before_final_grant_has_no_side_effect(self):
        session = self.start_scan()
        self.store.revoke("u1")

        result = self.grant(session, "Ada")

        self.assertEqual(result["state"], "denied")
        self.assertEqual(self.runtime.commands, [])
        self.assertEqual(self.db.unlocks, [])

    def test_session_owner_rejects_another_device_for_same_user(self):
        session = self.start_scan()

        with self.assertRaisesRegex(RuntimeRequestError, "another authorization") as raised:
            self.runtime.require_session_owner(
                session["id"],
                self.ada_tablet,
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.runtime.require_session_owner(session["id"], self.ada)
        self.assertEqual(self.runtime.commands, [])
        self.assertEqual(self.db.unlocks, [])

    def test_ignition_owner_is_bound_to_stable_user_id(self):
        unlock_session = self.start_scan()
        result = self.grant(unlock_session, "Ada")
        self.assertEqual(result["state"], "granted")
        self.assertEqual(
            self.runtime._actuation.snapshot().unlock_owner_id,
            "u1",
        )
        self.runtime._release_session(unlock_session["id"])

        with self.assertRaises(RuntimeRequestError):
            self.runtime.start_scan(
                purpose="ignition",
                expected_user="Bob",
                authorization=self.bob,
            )

        self.assertNotIn("START", self.runtime.commands)

    def test_revoked_stream_stops_before_waiting_for_another_frame(self):
        session = self.start_scan()
        capture = FakeCapture(session["id"])
        self.runtime._camera_capture = capture
        stream = self.runtime.camera_stream(
            session["id"],
            authorization=self.ada,
        )
        self.assertIsNotNone(stream)
        self.store.revoke("u1")

        with self.assertRaises(StopIteration):
            next(stream)

        self.assertEqual(capture.wait_calls, 0)

    def test_admin_camera_view_does_not_grant_session_control(self):
        session = self.start_scan()

        self.runtime.require_camera_viewer(session["id"], self.admin)
        self.assertIsNone(
            self.runtime.camera_frame(session["id"], authorization=self.admin)
        )
        with self.assertRaisesRegex(RuntimeRequestError, "another authorization"):
            self.runtime.require_session_owner(session["id"], self.admin)
        with self.assertRaisesRegex(RuntimeRequestError, "another authorization"):
            self.runtime.require_camera_viewer(session["id"], self.ada_tablet)

    def test_camera_status_selects_latest_session_visible_to_viewer(self):
        ada_session = self.start_scan()
        self.runtime._release_session(ada_session["id"])
        bob_session = self.start_scan(self.bob)

        self.assertEqual(
            self.runtime.camera_status(authorization=self.admin)["session"][
                "session_id"
            ],
            bob_session["id"],
        )
        self.assertEqual(
            self.runtime.camera_status(authorization=self.ada)["session"]["session_id"],
            ada_session["id"],
        )
        self.assertIsNone(
            self.runtime.camera_status(authorization=self.ada_tablet)["session"]
        )

        unsecured = AuthorizationRuntime(self.db, self.face_engine)
        unsecured_session = unsecured.start_scan(expected_user="Ada")
        self.assertEqual(
            unsecured.camera_status()["session"]["session_id"],
            unsecured_session["session_id"],
        )

    def test_revoked_admin_camera_stream_stops_before_waiting(self):
        session = self.start_scan()
        capture = FakeCapture(session["id"])
        self.runtime._camera_capture = capture
        stream = self.runtime.camera_stream(
            session["id"],
            authorization=self.admin,
        )
        self.assertIsNotNone(stream)
        self.store.revoke("admin")

        with self.assertRaises(StopIteration):
            next(stream)

        self.assertEqual(capture.wait_calls, 0)

    def test_non_admin_cannot_start_enrollment(self):
        with self.assertRaisesRegex(RuntimeRequestError, "administrator") as raised:
            self.runtime.start_client_enrollment(
                "Ada",
                authorization=self.ada,
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self.face_engine.saved, [])

    def test_target_version_change_blocks_enrollment_save(self):
        started = self.runtime.start_client_enrollment(
            "Ada",
            authorization=self.admin,
        )
        session = self.runtime._session(started["session_id"])
        session["embeddings"] = [object() for _ in range(SAMPLES_NEEDED)]
        session["count"] = SAMPLES_NEEDED
        self.store.revoke("u1")

        result = self.runtime.finish_client_enrollment(session["id"])

        self.assertEqual(result["state"], "error")
        self.assertIn("authorization expired", result["message"].lower())
        self.assertEqual(self.face_engine.saved, [])

    def test_device_revocation_cancels_and_releases_client_session(self):
        started = self.runtime.start_client_enrollment(
            "Ada",
            authorization=self.admin,
        )

        cancelled = self.runtime.cancel_device_sessions("admin-phone")

        self.assertEqual(cancelled, 1)
        self.assertEqual(
            self.runtime.enrollment_status(started["session_id"])["state"],
            "cancelled",
        )
        self.assertIsNone(self.runtime.status()["active_session"])
        self.assertEqual(self.runtime.commands, [])

    def test_completed_result_survives_same_device_version_refresh(self):
        started = self.runtime.start_client_enrollment(
            "Admin",
            authorization=self.admin,
        )
        session = self.runtime._session(started["session_id"])
        session["state"] = "completed"
        self.store.revoke("admin")
        refreshed = replace(self.admin, auth_version=4)

        self.runtime.require_session_owner(session["id"], refreshed)
        with self.assertRaises(RuntimeRequestError):
            self.runtime.require_session_owner(session["id"], self.ada)


if __name__ == "__main__":
    unittest.main()
