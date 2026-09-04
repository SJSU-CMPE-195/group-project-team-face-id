"""Contract tests for the canonical Pi Device API.

These tests inject both the database module and runtime so route behavior can
be checked on a workstation without loading Pi camera, InsightFace, or ESP32
dependencies.  Flask's in-process test client does not open a network socket.
"""

from __future__ import annotations

import io
import unittest
from threading import Event
import tempfile
import time
from unittest.mock import patch

import auth
from pi_device_api import create_app
from support import admin_client, session_client, REMOTE
from car_face_auth.src.pi_runtime import PiRuntime, RuntimeBusyError, RuntimeRequestError
import db as db_module
import db_api as real_db_api


class FakeDb:
    """Small database facade containing only methods used by these routes."""

    def __init__(self):
        self.lock_state = "locked"
        self.users = []
        self.logs = []
        self.settings = {"autoRelockSeconds": 0, "ignitionAutoStopSeconds": 0}
        self.unlock_reasons = []
        self.lock_reasons = []
        self.saved_embeddings = []

    def get_status(self):
        return {
            "online": True,
            "lockState": self.lock_state,
            "deviceName": "Fake Pi",
            "battery": 100,
            "signal": 5,
            "lastSeen": 1,
        }

    def get_all_users(self):
        return list(self.users)

    def get_all_face_encodings(self):
        return [user for user in self.users if user.get("has_template")]

    def set_unlock(self, reason="manual_ui"):
        self.lock_state = "unlocked"
        self.unlock_reasons.append(reason)

    def set_lock(self, reason="auto_relock"):
        self.lock_state = "locked"
        self.lock_reasons.append(reason)

    def list_users_for_ui(self):
        return list(self.users)

    def add_user(self, name):
        user = {"id": f"u{len(self.users) + 1}", "name": name, "faceAccess": True}
        self.users.append(user)
        return user

    def delete_user(self, user_id):
        for user in self.users:
            if user["id"] == user_id:
                self.users.remove(user)
                return {"ok": True}
        return {"ok": False}

    def set_user_access(self, user_id, allowed):
        for user in self.users:
            if user["id"] == user_id:
                user["faceAccess"] = allowed
                return {"ok": True}
        return {"ok": False, "error": "user not found"}

    def set_user_embedding(self, user_id, _blob):
        if any(user["id"] == user_id for user in self.users):
            return {"ok": True}
        return {"ok": False, "error": "user not found"}

    def get_user_by_id(self, user_id):
        return next((user for user in self.users if user["id"] == user_id), None)

    def log_event(self, stage, result, detail="", user_id=None):
        self.logs.append({"stage": stage, "result": result, "detail": detail, "user_id": user_id})

    def list_logs_for_ui(self):
        return list(self.logs)

    def get_settings_for_ui(self):
        return dict(self.settings)

    def save_settings_from_ui(self, payload):
        self.settings.update(payload)


class FakeFaceEngine:
    """Face-engine calls used by PiRuntime workers, with no ML dependency."""

    def __init__(self, names):
        self.names = list(names)
        self.analyze_calls = 0
        self.persisted = None

    def analyze_frame(self, _model, _frame, _database):
        index = min(self.analyze_calls, len(self.names) - 1)
        self.analyze_calls += 1
        name = self.names[index]
        return {
            "matched": name is not None,
            "user": name,
            "score": 0.91,
            "face_count": 1,
        }

    def extract_single_face_embedding(self, _model, _frame):
        sample = object()
        return sample, 1

    @staticmethod
    def decode_image_bytes(data):
        return object() if data else None

    def save_user_embedding(self, name, embeddings):
        self.persisted = (name, list(embeddings))
        return {"ok": True}


class HardwareFreePiRuntime(PiRuntime):
    """PiRuntime with camera/model/serial seams replaced by deterministic fakes."""

    def __init__(self, db):
        super().__init__(db)
        self.commands = []
        self.fail_commands = set()
        self.capture_started = Event()
        self.capture_gate = None

    def _ensure_model(self):
        return object()

    def _open_camera(self, owner_id=None):
        self._camera = object()
        self._camera_owner_id = owner_id
        return self._camera

    def _capture_bgr(self, _camera):
        self.capture_started.set()
        if self.capture_gate is not None:
            self.capture_gate.wait(timeout=3)
        return object()

    def _load_authorized_database(self, _face_engine):
        return {user["name"]: [] for user in self._db.get_all_users() if user.get("face_access", 1)}

    def _send_command(self, command, connect=True):
        self.commands.append(command)
        if command in self.fail_commands:
            self._record_error(f"ESP32 command {command} failed", "serial")
            return False
        return True

    @staticmethod
    def _enroll_sample_interval():
        return 0.0


def wait_for_state(runtime, session_id, method, final_states, timeout=3):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = method(session_id)
        if latest["state"] in final_states:
            return latest
        time.sleep(0.01)
    raise AssertionError(f"session did not reach {final_states}: {latest}")


class FakeRuntime:
    """Deterministic runtime double for route-level behavior."""

    def __init__(self, db):
        self.db = db
        self.calls = []
        self.sessions = {}
        self.next_id = 1
        self.busy = False
        self.unlock_result = {"ok": True}
        self.force_lock_result = {"ok": True, "locked": True}
        self.ignition_error = None
        self.hardware_calls = []
        self.ignition_on = False

    def status(self):
        return {
            "ready": False,
            "hardware": "not_initialized",
            "dependencies_loaded": False,
            "model_loaded": False,
            "camera_open": False,
            "esp32_connected": False,
            "serial_port": None,
            "ignitionOn": self.ignition_on,
            "active_session": None,
            "error": None,
        }

    def unlock(self, reason="manual_ui"):
        self.calls.append(("unlock", reason))
        if self.unlock_result.get("ok"):
            self.db.lock_state = "unlocked"
        return dict(self.unlock_result)

    def force_lock(self, reason="manual_ui"):
        self.calls.append(("force_lock", reason))
        if self.force_lock_result.get("ok"):
            self.db.lock_state = "locked"
            self.ignition_on = False
        return dict(self.force_lock_result)

    def set_ignition(self, running, reason="manual_ui"):
        self.calls.append(("set_ignition", bool(running), reason))
        if running and self.db.lock_state == "locked":
            return {"ok": False, "error": "device is locked"}
        if self.ignition_error:
            return {"ok": False, "error": self.ignition_error}
        self.ignition_on = bool(running)
        return {"ok": True, "ignitionOn": self.ignition_on}

    def _new_session(self, kind, **values):
        if self.busy:
            raise RuntimeRequestError("Pi camera is busy with another session", 409)
        session_id = f"{kind}_{self.next_id}"
        self.next_id += 1
        session = {
            "ok": True,
            "session_id": session_id,
            "state": "capturing" if kind == "enroll" else "starting",
            **values,
        }
        self.sessions[session_id] = session
        self.busy = True
        return dict(session)

    def start_scan(self, purpose="unlock", expected_user=None):
        purpose = (purpose or "unlock").strip().lower()
        if purpose not in ("unlock", "ignition"):
            raise RuntimeRequestError("purpose must be unlock or ignition", 400)
        if purpose == "ignition" and not (expected_user or "").strip():
            raise RuntimeRequestError("expected_user is required for ignition scans", 400)
        return self._new_session(
            "scan",
            purpose=purpose,
            source="pi_camera",
            expected_user=expected_user,
            user=None,
            score=None,
            face_count=0,
            matches=0,
            window={"matches": 0, "needed": 6, "size": 10},
            message="Pi camera scan starting.",
        )

    def start_client_scan(self, purpose="unlock", expected_user=None):
        purpose = (purpose or "unlock").strip().lower()
        if purpose not in ("unlock", "ignition"):
            raise RuntimeRequestError("purpose must be unlock or ignition", 400)
        if purpose == "ignition" and not (expected_user or "").strip():
            raise RuntimeRequestError("expected_user is required for ignition scans", 400)
        return self._new_session(
            "scan",
            state="scanning",
            purpose=purpose,
            source="client_camera",
            expected_user=expected_user,
            user=None,
            score=None,
            face_count=0,
            matches=0,
            window={"matches": 0, "needed": 6, "size": 10},
            message="Client camera scan is ready for frames.",
        )

    def add_client_scan_sample(self, session_id, image_bytes):
        if not image_bytes:
            raise RuntimeRequestError("image is required", 400)
        session = self._session_status(session_id, "scan")
        if session.get("source") != "client_camera":
            raise RuntimeRequestError("session does not accept client camera frames", 409)
        session["face_count"] = 1
        session["matches"] = min(6, session.get("matches", 0) + 1)
        session["user"] = "Ada"
        session["window"] = {"matches": session["matches"], "needed": 6, "size": 10}
        if session["matches"] >= 6:
            session["state"] = "granted"
            self.busy = False
        self.sessions[session_id] = session
        return dict(session)

    def scan_status(self, session_id):
        return self._session_status(session_id, "scan")

    def cancel_scan(self, session_id):
        session = self._session_status(session_id, "scan")
        session["state"] = "cancelled"
        session["message"] = "Scan cancelled."
        self.sessions[session_id] = session
        self.busy = False
        return dict(session)

    def start_enrollment(self, name):
        name = (name or "").strip()
        if not name:
            raise RuntimeRequestError("name is required", 400)
        return self._new_session(
            "enroll",
            user=name,
            count=0,
            samples_needed=10,
            source="pi_camera",
            message="Pi camera enrollment starting.",
        )

    def start_client_enrollment(self, name):
        name = (name or "").strip()
        if not name:
            raise RuntimeRequestError("name is required", 400)
        return self._new_session(
            "enroll",
            user=name,
            count=0,
            samples_needed=10,
            source="client_camera",
            message="Client camera enrollment is ready for samples.",
        )

    def add_client_enrollment_sample(self, session_id, image_bytes):
        if not image_bytes:
            raise RuntimeRequestError("image is required", 400)
        session = self._session_status(session_id, "enroll")
        if session.get("source") != "client_camera":
            raise RuntimeRequestError("session does not accept client camera samples", 409)
        session["count"] = min(10, session.get("count", 0) + 1)
        session["face_count"] = 1
        self.sessions[session_id] = session
        return dict(session)

    def finish_client_enrollment(self, session_id):
        session = self._session_status(session_id, "enroll")
        if session.get("source") != "client_camera":
            raise RuntimeRequestError("session does not accept client camera finish", 409)
        if session.get("count", 0) < 10:
            raise RuntimeRequestError(f"Need 10 samples, have {session.get('count', 0)}", 400)
        session.update(state="completed", recognition_available=True)
        self.sessions[session_id] = session
        self.busy = False
        return dict(session)

    def enrollment_status(self, session_id):
        return self._session_status(session_id, "enroll")

    def cancel_enrollment(self, session_id):
        session = self._session_status(session_id, "enroll")
        session["state"] = "cancelled"
        session["message"] = "Enroll cancelled."
        self.sessions[session_id] = session
        self.busy = False
        return dict(session)

    def _session_status(self, session_id, kind):
        session = self.sessions.get(session_id)
        if not session or not session["session_id"].startswith(f"{kind}_"):
            raise RuntimeRequestError(f"unknown {kind} session", 404)
        return dict(session)


class PiDeviceApiTests(unittest.TestCase):
    ADMIN_TOKEN = "route-test-admin-token"

    def setUp(self):
        self.db = FakeDb()
        self.runtime = FakeRuntime(self.db)
        self.app = create_app(db_module=self.db, runtime=self.runtime,
                              api_token=self.ADMIN_TOKEN)
        self.app.testing = True
        # These tests cover route behavior, not authentication, so the client
        # holds an administrator credential throughout.  Authorization itself
        # is covered by AuthorizationTests below.
        self.client = admin_client(self.app, self.ADMIN_TOKEN)

    def json(self, response):
        self.assertIsNotNone(response.json, response.data)
        return response.get_json()

    def test_scan_start_status_and_cancel_lifecycle(self):
        started = self.client.post(
            "/api/scan/start",
            json={"purpose": "unlock", "expectedUser": "Ada"},
        )
        self.assertEqual(started.status_code, 200)
        session = self.json(started)
        self.assertEqual(session["purpose"], "unlock")
        self.assertEqual(session["state"], "starting")
        session_id = session["session_id"]

        status = self.client.get(f"/api/scan/status?session_id={session_id}")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(self.json(status)["session_id"], session_id)

        cancelled = self.client.post("/api/scan/cancel", json={"sessionId": session_id})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.json(cancelled)["state"], "cancelled")

    def test_client_camera_scan_upload_contract(self):
        started = self.client.post(
            "/api/scan/start",
            json={"purpose": "unlock", "source": "client_camera"},
        )
        self.assertEqual(started.status_code, 200)
        session = self.json(started)
        self.assertEqual(session["source"], "client_camera")
        self.assertEqual(session["state"], "scanning")

        for expected_matches in range(1, 7):
            sample = self.client.post(
                "/api/scan/sample",
                data={
                    "session_id": session["session_id"],
                    "image": (io.BytesIO(b"jpeg"), "frame.jpg", "image/jpeg"),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(sample.status_code, 200)
            payload = self.json(sample)
            self.assertEqual(payload["matches"], expected_matches)

        self.assertEqual(payload["state"], "granted")
        self.assertEqual(payload["user"], "Ada")

    def test_client_camera_scan_upload_validation(self):
        started = self.json(
            self.client.post("/api/scan/start", json={"purpose": "unlock", "source": "client_camera"})
        )
        session_id = started["session_id"]

        missing = self.client.post("/api/scan/sample", data={"session_id": session_id})
        self.assertEqual(missing.status_code, 400)
        wrong_type = self.client.post(
            "/api/scan/sample",
            data={"session_id": session_id, "image": (io.BytesIO(b"text"), "frame.txt", "text/plain")},
            content_type="multipart/form-data",
        )
        self.assertEqual(wrong_type.status_code, 415)

        bad_source = self.client.post("/api/scan/start", json={"purpose": "unlock", "source": "door_camera"})
        self.assertEqual(bad_source.status_code, 400)

    def test_scan_validation_busy_conflict_and_unknown_errors(self):
        bad_purpose = self.client.post("/api/scan/start", json={"purpose": "door"})
        self.assertEqual(bad_purpose.status_code, 400)
        self.assertFalse(self.json(bad_purpose)["ok"])

        missing_session = self.client.get("/api/scan/status")
        self.assertEqual(missing_session.status_code, 400)
        self.assertIn("session_id", self.json(missing_session)["error"])

        started = self.client.post("/api/scan/start", json={"purpose": "unlock"})
        self.assertEqual(started.status_code, 200)
        conflict = self.client.post("/api/enroll/start", json={"name": "Ada"})
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("busy", self.json(conflict)["error"])

        unknown = self.client.get("/api/scan/status?session_id=scan_missing")
        self.assertEqual(unknown.status_code, 404)
        self.assertFalse(self.json(unknown)["ok"])

    def test_enrollment_start_status_and_cancel_lifecycle(self):
        missing_name = self.client.post("/api/enroll/start", json={})
        self.assertEqual(missing_name.status_code, 400)

        started = self.client.post("/api/enroll/start", json={"name": " Ada "})
        self.assertEqual(started.status_code, 200)
        session = self.json(started)
        self.assertEqual(session["user"], "Ada")
        self.assertEqual(session["source"], "pi_camera")
        self.assertEqual(session["count"], 0)
        session_id = session["session_id"]

        status = self.client.get(f"/api/enroll/status?sessionId={session_id}")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(self.json(status)["state"], "capturing")

        cancelled = self.client.post("/api/enroll/cancel", json={"session_id": session_id})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.json(cancelled)["state"], "cancelled")

        unknown = self.client.post("/api/enroll/cancel", json={"session_id": "enroll_missing"})
        self.assertEqual(unknown.status_code, 404)

    def test_client_camera_enrollment_upload_and_finish_contract(self):
        started = self.client.post("/api/enroll/start", json={"name": "Ada", "source": "client_camera"})
        self.assertEqual(started.status_code, 200)
        session = self.json(started)
        self.assertEqual(session["source"], "client_camera")
        session_id = session["session_id"]

        for expected_count in range(1, 11):
            sample = self.client.post(
                "/api/enroll/sample",
                data={"session_id": session_id, "image": (io.BytesIO(b"jpeg"), "sample.jpg", "image/jpeg")},
                content_type="multipart/form-data",
            )
            self.assertEqual(sample.status_code, 200)
            self.assertEqual(self.json(sample)["count"], expected_count)

        finished = self.client.post("/api/enroll/finish", json={"session_id": session_id})
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(self.json(finished)["state"], "completed")

    def test_client_camera_upload_validation(self):
        started = self.json(self.client.post("/api/enroll/start", json={"name": "Ada", "source": "client_camera"}))
        session_id = started["session_id"]

        missing = self.client.post("/api/enroll/sample", data={"session_id": session_id})
        self.assertEqual(missing.status_code, 400)
        wrong_type = self.client.post(
            "/api/enroll/sample",
            data={"session_id": session_id, "image": (io.BytesIO(b"text"), "sample.txt", "text/plain")},
            content_type="multipart/form-data",
        )
        self.assertEqual(wrong_type.status_code, 415)

        incomplete = self.client.post("/api/enroll/finish", json={"session_id": session_id})
        self.assertEqual(incomplete.status_code, 400)

    def test_face_status_reports_only_users_with_templates(self):
        self.db.users = [
            {"id": "u1", "name": "Ada", "has_template": True},
            {"id": "u2", "name": "Bob", "has_template": False},
        ]
        response = self.client.get("/api/face-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.json(response), {"enrolled": ["Ada"], "count": 1})

    def test_ignition_scan_requires_expected_driver(self):
        missing_driver = self.client.post("/api/scan/start", json={"purpose": "ignition"})
        self.assertEqual(missing_driver.status_code, 400)
        self.assertEqual(self.runtime.calls, [])

    def test_unlock_and_ignition_errors_are_reported_without_false_success(self):
        self.runtime.unlock_result = {"ok": False, "error": "ESP32 is unavailable"}
        unlock = self.client.post("/api/unlock")
        self.assertEqual(unlock.status_code, 503)
        self.assertFalse(self.json(unlock)["ok"])

        malformed = self.client.post("/api/scan/start", json=["unlock"])
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(self.json(malformed)["error"], "JSON object required")

    def test_access_requires_boolean_and_raw_embeddings_are_not_exposed(self):
        user = self.json(self.client.post("/api/users", json={"name": "Ada"}))
        invalid = self.client.patch(f"/api/users/{user['id']}/access", json={"allowed": "false"})
        self.assertEqual(invalid.status_code, 400)
        self.assertTrue(self.db.users[0]["faceAccess"])

        raw_write = self.client.patch(f"/api/users/{user['id']}/embedding", json={"blob": "AA=="})
        raw_read = self.client.get(f"/api/users/{user['id']}/embedding")
        self.assertEqual(raw_write.status_code, 405)
        self.assertEqual(raw_read.status_code, 405)
        self.assertFalse(self.json(raw_write)["ok"])
        self.assertEqual(self.json(raw_read)["error"], "method not allowed")

    def test_status_and_health_report_runtime_without_hardware_initialization(self):
        status = self.client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")
        payload = self.json(status)
        self.assertFalse(payload["runtime"]["ready"])
        self.assertFalse(payload["runtime"]["camera_open"])
        self.assertFalse(payload["runtime"]["esp32_connected"])
        self.assertEqual(self.runtime.hardware_calls, [])

        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.headers["Cache-Control"], "no-store")
        health_payload = self.json(health)
        self.assertTrue(health_payload["ok"])
        self.assertEqual(health_payload["service"], "pi_device_api")
        self.assertFalse(health_payload["runtime_ready"])

        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.headers["Cache-Control"], "no-store")
        self.assertFalse(self.json(ready)["ok"])

    def test_lock_and_full_reset_use_force_lock_and_keep_safe_state(self):
        self.db.lock_state = "unlocked"
        self.runtime.ignition_on = True

        locked = self.client.post("/api/lock", json={"reason": "manual_test"})
        self.assertEqual(locked.status_code, 200)
        self.assertEqual(self.json(locked), {"ok": True, "locked": True})
        self.assertEqual(self.runtime.calls[-1], ("force_lock", "manual_test"))
        self.assertEqual(self.db.lock_state, "locked")
        self.assertFalse(self.runtime.ignition_on)

        reset = self.client.post("/api/full-reset")
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self.json(reset), {"ok": True, "locked": True})
        self.assertEqual(self.runtime.calls[-1], ("force_lock", "full_reset"))
        self.assertEqual(self.db.lock_state, "locked")

    def test_lock_and_reset_surface_actuator_failure(self):
        self.runtime.force_lock_result = {"ok": False, "error": "ESP32 is unavailable"}

        locked = self.client.post("/api/lock")
        self.assertEqual(locked.status_code, 503)
        self.assertEqual(self.json(locked)["error"], "ESP32 is unavailable")

        reset = self.client.post("/api/full-reset")
        self.assertEqual(reset.status_code, 503)
        self.assertFalse(self.json(reset)["ok"])


class PiRuntimeBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.db.users = [{"id": "u1", "name": "Ada", "face_access": 1}]
        self.runtime = HardwareFreePiRuntime(self.db)

    def tearDown(self):
        self.runtime.close()

    def run_scan(self, face_engine, purpose="unlock", expected_user=None):
        importer = lambda name: face_engine if name == "car_face_auth.src.face_engine" else None
        with patch("car_face_auth.src.pi_runtime.importlib.import_module", side_effect=importer):
            session = self.runtime.start_scan(purpose=purpose, expected_user=expected_user)
            return session, wait_for_state(
                self.runtime,
                session["session_id"],
                self.runtime.scan_status,
                {"granted", "denied", "error", "timeout", "cancelled"},
            )

    def test_six_of_ten_same_user_matches_grant_unlock(self):
        face_engine = FakeFaceEngine(["Ada"] * 6)
        session, result = self.run_scan(face_engine)

        self.assertEqual(session["purpose"], "unlock")
        self.assertEqual(result["state"], "granted")
        self.assertEqual(result["matches"], 6)
        self.assertEqual(self.runtime.commands[:1], ["UNLOCK"])
        self.assertEqual(self.db.unlock_reasons, [f"scan:{session['session_id']}"])

    def test_client_camera_unlock_and_same_driver_ignition_run_on_pi(self):
        unlock_engine = FakeFaceEngine(["Ada"] * 6)
        self.runtime._face_engine = unlock_engine
        unlock = self.runtime.start_client_scan("unlock")
        self.assertEqual(unlock["source"], "client_camera")

        for _ in range(6):
            unlock_result = self.runtime.add_client_scan_sample(unlock["session_id"], b"jpeg")

        self.assertEqual(unlock_result["state"], "granted")
        self.assertEqual(unlock_result["user"], "Ada")
        self.assertEqual(self.runtime.commands, ["UNLOCK"])
        self.assertIsNone(self.runtime._camera)
        self.assertIsNone(self.runtime.status()["active_session"])

        ignition_engine = FakeFaceEngine(["Ada"] * 6)
        self.runtime._face_engine = ignition_engine
        ignition = self.runtime.start_client_scan("ignition", expected_user="Ada")
        for _ in range(6):
            ignition_result = self.runtime.add_client_scan_sample(ignition["session_id"], b"jpeg")

        self.assertEqual(ignition_result["state"], "granted")
        self.assertEqual(ignition_result["user"], "Ada")
        self.assertEqual(self.runtime.commands[-1], "START")
        self.assertTrue(self.runtime.status()["ignitionOn"])
        self.assertIsNone(self.runtime._camera)

    def test_ignition_grants_only_for_expected_user(self):
        _unlock_session, unlock_result = self.run_scan(FakeFaceEngine(["Ada"] * 6))
        self.assertEqual(unlock_result["state"], "granted")
        deadline = time.monotonic() + 2
        while self.runtime.status()["active_session"] is not None and time.monotonic() < deadline:
            time.sleep(0.01)

        matching = FakeFaceEngine(["Ada"] * 6)
        session, result = self.run_scan(matching, purpose="ignition", expected_user="Ada")
        self.assertEqual(result["state"], "granted")
        self.assertIn("START", self.runtime.commands)
        self.assertTrue(self.runtime.status()["ignitionOn"])

        deadline = time.monotonic() + 2
        while self.runtime.status()["active_session"] is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(self.runtime.status()["active_session"])
        self.runtime.force_lock("between-tests")
        self.runtime.commands.clear()
        _unlock_session, unlock_result = self.run_scan(FakeFaceEngine(["Ada"] * 6))
        self.assertEqual(unlock_result["state"], "granted")
        deadline = time.monotonic() + 2
        while self.runtime.status()["active_session"] is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.runtime.commands.clear()
        mismatching = FakeFaceEngine(["Bob"] * 6)
        _session, denied = self.run_scan(mismatching, purpose="ignition", expected_user="Ada")
        self.assertEqual(denied["state"], "denied")
        self.assertNotIn("START", self.runtime.commands)

    def test_enrollment_captures_ten_samples_and_persists_them(self):
        face_engine = FakeFaceEngine([])
        importer = lambda name: face_engine if name == "car_face_auth.src.face_engine" else None
        with patch("car_face_auth.src.pi_runtime.importlib.import_module", side_effect=importer):
            session = self.runtime.start_enrollment("Ada")
            result = wait_for_state(
                self.runtime,
                session["session_id"],
                self.runtime.enrollment_status,
                {"completed", "error", "cancelled", "timeout"},
            )

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["count"], 10)
        self.assertIsNotNone(face_engine.persisted)
        self.assertEqual(face_engine.persisted[0], "Ada")
        self.assertEqual(len(face_engine.persisted[1]), 10)

    def test_client_camera_enrollment_persists_on_pi_without_opening_camera(self):
        face_engine = FakeFaceEngine([])
        self.runtime._face_engine = face_engine
        session = self.runtime.start_client_enrollment("Ada")
        self.assertEqual(session["source"], "client_camera")

        for expected_count in range(1, 11):
            status = self.runtime.add_client_enrollment_sample(session["session_id"], b"jpeg")
            self.assertEqual(status["count"], expected_count)

        result = self.runtime.finish_client_enrollment(session["session_id"])
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["recognition_available"])
        self.assertEqual(face_engine.persisted[0], "Ada")
        self.assertEqual(len(face_engine.persisted[1]), 10)
        self.assertIsNone(self.runtime._camera)
        self.assertIsNone(self.runtime.status()["active_session"])

    def test_camera_ownership_rejects_conflict_and_cancel_releases_it(self):
        self.runtime.capture_gate = Event()
        face_engine = FakeFaceEngine(["Ada"] * 6)
        importer = lambda name: face_engine if name == "car_face_auth.src.face_engine" else None
        with patch("car_face_auth.src.pi_runtime.importlib.import_module", side_effect=importer):
            scan = self.runtime.start_scan()
            self.assertTrue(self.runtime.capture_started.wait(timeout=1))
            with self.assertRaises(RuntimeBusyError):
                self.runtime.start_enrollment("Ada")

            cancelled = self.runtime.cancel_scan(scan["session_id"])
            self.assertEqual(cancelled["state"], "cancelled")
            self.runtime.capture_gate.set()
            deadline = time.monotonic() + 2
            while self.runtime.status()["active_session"] is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(self.runtime.status()["active_session"])

            self.runtime.capture_gate = None
            enrollment = self.runtime.start_enrollment("Ada")
            self.assertTrue(enrollment["session_id"].startswith("enroll_"))

    def test_esp_failure_finishes_scan_as_error_not_granted(self):
        self.runtime.fail_commands.add("UNLOCK")
        face_engine = FakeFaceEngine(["Ada"] * 6)
        _session, result = self.run_scan(face_engine)

        self.assertEqual(result["state"], "error")
        self.assertFalse(result["ok"])
        self.assertEqual(self.db.unlock_reasons, [])

    def test_force_lock_cannot_be_reversed_by_cancelled_scan(self):
        self.runtime.capture_gate = Event()
        face_engine = FakeFaceEngine(["Ada"] * 6)
        importer = lambda name: face_engine if name == "car_face_auth.src.face_engine" else None
        with patch("car_face_auth.src.pi_runtime.importlib.import_module", side_effect=importer):
            scan = self.runtime.start_scan()
            self.assertTrue(self.runtime.capture_started.wait(timeout=1))
            result = self.runtime.force_lock("race_test")
            self.assertTrue(result["ok"])
            self.runtime.capture_gate.set()
            final = wait_for_state(
                self.runtime,
                scan["session_id"],
                self.runtime.scan_status,
                {"cancelled", "error", "granted"},
            )
        self.assertEqual(final["state"], "cancelled")
        self.assertEqual(self.runtime.commands[-2:], ["STOP", "LOCK"])

    def test_force_lock_releases_client_camera_scan_immediately(self):
        scan = self.runtime.start_client_scan()
        result = self.runtime.force_lock("client_scan_reset")

        self.assertTrue(result["ok"])
        self.assertEqual(self.runtime.scan_status(scan["session_id"])["state"], "cancelled")
        self.assertIsNone(self.runtime.status()["active_session"])

        next_scan = self.runtime.start_client_scan()
        self.assertEqual(next_scan["state"], "scanning")
        self.runtime.cancel_scan(next_scan["session_id"])

    def test_force_lock_reports_camera_teardown_timeout(self):
        self.runtime._camera = object()
        self.runtime._camera_io_lock.acquire()
        try:
            with patch.object(self.runtime, "_camera_close_timeout", return_value=0.01):
                result = self.runtime.force_lock("camera_timeout")
        finally:
            self.runtime._camera_io_lock.release()

        self.assertFalse(result["ok"])
        self.assertTrue(result["locked"])
        self.assertIn("camera", result["error"])
        self.assertEqual(self.runtime.commands[-2:], ["STOP", "LOCK"])

    def test_explicit_stop_is_sent_even_when_runtime_state_starts_off(self):
        self.assertFalse(self.runtime.status()["ignitionOn"])
        result = self.runtime.set_ignition(False, "safety_test")
        self.assertTrue(result["ok"])
        self.assertEqual(self.runtime.commands[-1], "STOP")

    def test_closed_runtime_rejects_new_actuation(self):
        self.runtime.close()
        commands_after_close = list(self.runtime.commands)

        self.assertFalse(self.runtime.unlock("after_close")["ok"])
        self.assertFalse(self.runtime.set_ignition(True, "after_close")["ok"])
        self.assertEqual(self.runtime.commands, commands_after_close)
        self.assertFalse(self.runtime.status()["ready"])

    def test_ignition_scan_is_denied_when_unlock_authorization_changes(self):
        _unlock_session, unlock_result = self.run_scan(FakeFaceEngine(["Ada"] * 6))
        self.assertEqual(unlock_result["state"], "granted")
        deadline = time.monotonic() + 2
        while self.runtime.status()["active_session"] is not None and time.monotonic() < deadline:
            time.sleep(0.01)

        self.runtime.capture_started = Event()
        self.runtime.capture_gate = Event()
        self.runtime.commands.clear()
        face_engine = FakeFaceEngine(["Ada"] * 6)
        importer = lambda name: face_engine if name == "car_face_auth.src.face_engine" else None
        with patch("car_face_auth.src.pi_runtime.importlib.import_module", side_effect=importer):
            session = self.runtime.start_scan(purpose="ignition", expected_user="Ada")
            self.assertTrue(self.runtime.capture_started.wait(timeout=1))
            self.assertTrue(self.runtime.unlock("authorization_changed")["ok"])
            self.runtime.capture_gate.set()
            result = wait_for_state(
                self.runtime,
                session["session_id"],
                self.runtime.scan_status,
                {"granted", "denied", "error", "cancelled"},
            )

        self.assertEqual(result["state"], "denied")
        self.assertNotIn("START", self.runtime.commands)


class DatabaseContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = db_module.DB_PATH
        db_module.DB_PATH = f"{self.tempdir.name}/faceid.db"
        db_module.init_db()

    def tearDown(self):
        db_module.DB_PATH = self.original_path
        self.tempdir.cleanup()

    def test_duplicate_names_and_missing_updates_fail_closed(self):
        user = real_db_api.add_user(" Ada ")
        self.assertIn("id", user)
        duplicate = real_db_api.add_user("ada")
        self.assertFalse(duplicate["ok"])
        self.assertFalse(real_db_api.set_user_access("missing", True)["ok"])
        self.assertFalse(real_db_api.set_user_embedding("missing", b"blob")["ok"])

    def test_face_access_filters_recognition_rows(self):
        user = real_db_api.add_user("Ada")
        self.assertTrue(real_db_api.set_user_embedding(user["id"], b"placeholder")["ok"])
        self.assertEqual([row["name"] for row in real_db_api.get_all_face_encodings()], ["Ada"])
        self.assertTrue(real_db_api.set_user_access(user["id"], False)["ok"])
        self.assertEqual(real_db_api.get_all_face_encodings(), [])

    def test_lock_state_and_audit_are_committed_together(self):
        real_db_api.set_unlock("contract_test")
        self.assertEqual(real_db_api.get_status()["lockState"], "unlocked")
        logs = real_db_api.get_logs()
        self.assertEqual(logs[0]["stage"], "unlock")
        self.assertEqual(logs[0]["detail"], "contract_test")


class AuthorizationTests(unittest.TestCase):
    """Authentication, RBAC, pairing, sessions, and ownership.

    Runs against a real SQLite database so the session and pairing SQL is
    genuinely exercised rather than mocked, with a fake runtime so no camera or
    serial port is needed.  Every request comes from a non-loopback address:
    the gate has no loopback exemption, and the tests say so explicitly.
    """

    LEGACY_TOKEN = "legacy-admin-token"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = db_module.DB_PATH
        db_module.DB_PATH = f"{self.tempdir.name}/faceid.db"
        db_module.init_db()

        self.admin = real_db_api.add_user("Admin Ada")
        real_db_api.set_user_role(self.admin["id"], "ADMIN")
        self.driver = real_db_api.add_user("Driver Bob")
        self.other = real_db_api.add_user("Driver Cleo")

        self.runtime = FakeRuntime(real_db_api)
        self.app = create_app(db_module=real_db_api, runtime=self.runtime,
                              api_token=self.LEGACY_TOKEN)
        self.app.testing = True
        self.anon = self.app.test_client()

    def tearDown(self):
        db_module.DB_PATH = self.original_path
        self.tempdir.cleanup()

    # -- helpers ---------------------------------------------------------

    def as_user(self, user):
        return session_client(self.app, real_db_api, user["id"])

    def as_admin(self):
        return self.as_user(self.admin)

    def as_driver(self):
        return self.as_user(self.driver)

    def get(self, client, path, **kw):
        return client.get(path, environ_base=REMOTE, **kw)

    def post(self, client, path, **kw):
        return client.post(path, environ_base=REMOTE, **kw)

    # -- unauthenticated -------------------------------------------------

    def test_unauthenticated_cannot_reach_protected_routes(self):
        for method, path in [
            ("get", "/api/me"), ("get", "/api/status"), ("get", "/api/users"),
            ("get", "/api/logs"), ("get", "/api/settings"),
            ("post", "/api/unlock"), ("post", "/api/users"),
            ("post", "/api/enroll/start"), ("post", "/api/pair/create"),
            ("delete", "/api/users/anything"),
        ]:
            with self.subTest(path=f"{method.upper()} {path}"):
                res = getattr(self.anon, method)(path, environ_base=REMOTE)
                self.assertEqual(res.status_code, 401)

    def test_unauthenticated_cannot_enroll_or_list_users(self):
        self.assertEqual(self.post(self.anon, "/api/enroll/start",
                                   json={"name": "Mallory"}).status_code, 401)
        self.assertEqual(self.get(self.anon, "/api/users").status_code, 401)

    def test_health_and_ready_stay_public(self):
        self.assertEqual(self.get(self.anon, "/health").status_code, 200)
        self.assertIn(self.get(self.anon, "/ready").status_code, (200, 503))

    # -- USER role -------------------------------------------------------

    def test_user_can_read_own_profile(self):
        res = self.get(self.as_driver(), "/api/me")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["name"], "Driver Bob")
        self.assertEqual(body["role"], "USER")

    def test_user_can_operate_the_lock(self):
        client = self.as_driver()
        self.assertEqual(self.get(client, "/api/status").status_code, 200)
        self.assertEqual(self.post(client, "/api/unlock", json={}).status_code, 200)

    def test_user_cannot_reach_admin_routes(self):
        client = self.as_driver()
        for method, path in [
            ("get", "/api/users"), ("get", "/api/logs"), ("get", "/api/settings"),
            ("get", "/api/face-status"), ("post", "/api/users"),
            ("post", "/api/settings"), ("post", "/api/pair/create"),
            ("delete", f"/api/users/{self.other['id']}"),
        ]:
            with self.subTest(path=f"{method.upper()} {path}"):
                res = getattr(client, method)(path, environ_base=REMOTE, json={})
                self.assertEqual(res.status_code, 403)

    def test_user_cannot_change_roles(self):
        res = self.as_driver().patch(f"/api/users/{self.driver['id']}/role",
                                     json={"role": "ADMIN"}, environ_base=REMOTE)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(real_db_api.get_user_by_id(self.driver["id"])["role"], "USER")

    def test_user_cannot_read_another_users_record(self):
        # The IDOR case: a valid session, someone else's id in the URL.
        res = self.get(self.as_driver(), f"/api/users/{self.other['id']}")
        self.assertEqual(res.status_code, 403)

    def test_user_can_read_their_own_record(self):
        res = self.get(self.as_driver(), f"/api/users/{self.driver['id']}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["id"], self.driver["id"])

    def test_no_response_ever_carries_a_face_embedding(self):
        real_db_api.set_user_embedding(self.driver["id"], b"\xde\xad\xbe\xef")
        admin = self.as_admin()
        for path in ("/api/users", f"/api/users/{self.driver['id']}",
                     "/api/face-status", "/api/me"):
            with self.subTest(path=path):
                raw = self.get(admin, path).data.lower()
                self.assertNotIn(b"face_encoding", raw)
                self.assertNotIn(b"deadbeef", raw)
                self.assertNotIn(b"\xde\xad\xbe\xef", raw)

    def test_user_enrollment_cannot_target_another_account(self):
        # A non-admin's client-supplied name is ignored entirely: Bob asking to
        # enroll "Driver Cleo" enrolls Bob, not Cleo.
        res = self.post(self.as_driver(), "/api/enroll/start",
                        json={"name": "Driver Cleo"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["user"], "Driver Bob")

    def test_admin_enrollment_may_target_another_account(self):
        res = self.post(self.as_admin(), "/api/enroll/start",
                        json={"name": "Driver Cleo"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["user"], "Driver Cleo")

    def test_user_cannot_feed_another_users_enrollment_session(self):
        # Cleo starts enrolling; Bob must not be able to push frames into it.
        started = self.post(self.as_user(self.other), "/api/enroll/start", json={})
        session_id = started.get_json()["session_id"]
        hijack = self.get(self.as_driver(),
                          f"/api/enroll/status?session_id={session_id}")
        self.assertEqual(hijack.status_code, 403)

    # -- ADMIN role ------------------------------------------------------

    def test_admin_can_perform_administration(self):
        client = self.as_admin()
        self.assertEqual(self.get(client, "/api/users").status_code, 200)
        self.assertEqual(self.get(client, "/api/logs").status_code, 200)
        self.assertEqual(self.get(client, "/api/settings").status_code, 200)
        created = self.post(client, "/api/users", json={"name": "Dana"})
        self.assertEqual(created.status_code, 201)

    def test_admin_can_read_any_user(self):
        res = self.get(self.as_admin(), f"/api/users/{self.driver['id']}")
        self.assertEqual(res.status_code, 200)

    def test_legacy_token_still_authenticates_as_admin(self):
        res = self.anon.get("/api/users", environ_base=REMOTE,
                            headers={"Authorization": f"Bearer {self.LEGACY_TOKEN}"})
        self.assertEqual(res.status_code, 200)

    def test_wrong_legacy_token_is_rejected(self):
        res = self.anon.get("/api/users", environ_base=REMOTE,
                            headers={"Authorization": "Bearer nope"})
        self.assertEqual(res.status_code, 401)

    # -- pairing ---------------------------------------------------------

    def test_pairing_code_works_exactly_once(self):
        issued = self.post(self.as_admin(), "/api/pair/create",
                           json={"user_id": self.driver["id"]})
        self.assertEqual(issued.status_code, 201)
        code = issued.get_json()["code"]

        first = self.post(self.anon, "/api/pair/redeem", json={"code": code})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.get_json()["name"], "Driver Bob")

        second = self.app.test_client().post("/api/pair/redeem", json={"code": code},
                                             environ_base=REMOTE)
        self.assertEqual(second.status_code, 401)

    def test_redeeming_sets_an_httponly_session_cookie(self):
        code = self.post(self.as_admin(), "/api/pair/create",
                         json={"user_id": self.driver["id"]}).get_json()["code"]
        res = self.post(self.anon, "/api/pair/redeem", json={"code": code})
        cookie = res.headers.get("Set-Cookie", "")
        self.assertIn("faceid_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        # The redeemed session actually works.
        self.assertEqual(self.get(self.anon, "/api/me").status_code, 200)

    def test_invalid_and_expired_pairing_codes_fail(self):
        self.assertEqual(
            self.post(self.anon, "/api/pair/redeem", json={"code": "garbage"}).status_code, 401)
        expired = real_db_api.create_pairing_code(self.driver["id"], ttl_seconds=-1)
        self.assertEqual(
            self.post(self.anon, "/api/pair/redeem",
                      json={"code": expired["code"]}).status_code, 401)

    def test_pairing_is_rate_limited(self):
        codes = [self.post(self.anon, "/api/pair/redeem", json={"code": "bad"})
                 for _ in range(7)]
        self.assertEqual(codes[-1].status_code, 429)

    def test_only_admin_can_mint_pairing_codes(self):
        res = self.post(self.as_driver(), "/api/pair/create",
                        json={"user_id": self.driver["id"]})
        self.assertEqual(res.status_code, 403)

    # -- sessions --------------------------------------------------------

    def test_logout_invalidates_the_session(self):
        client = self.as_driver()
        self.assertEqual(self.get(client, "/api/me").status_code, 200)
        self.assertEqual(self.post(client, "/api/auth/logout").status_code, 200)
        self.assertEqual(self.get(client, "/api/me").status_code, 401)

    def test_revoked_session_is_rejected(self):
        issued = real_db_api.create_session(self.driver["id"])
        client = self.app.test_client()
        client.set_cookie("faceid_session", issued["token"], domain="localhost")
        self.assertEqual(self.get(client, "/api/me").status_code, 200)
        real_db_api.revoke_all_sessions_for_user(self.driver["id"])
        self.assertEqual(self.get(client, "/api/me").status_code, 401)

    def test_expired_session_is_rejected(self):
        issued = real_db_api.create_session(self.driver["id"], absolute_seconds=-1)
        client = self.app.test_client()
        client.set_cookie("faceid_session", issued["token"], domain="localhost")
        self.assertEqual(self.get(client, "/api/me").status_code, 401)

    def test_disabling_an_account_kills_its_sessions(self):
        client = self.as_driver()
        self.assertEqual(self.get(client, "/api/me").status_code, 200)
        real_db_api.delete_user(self.driver["id"])
        self.assertEqual(self.get(client, "/api/me").status_code, 401)

    def test_demoting_an_admin_revokes_their_sessions(self):
        second = real_db_api.add_user("Second Admin")
        real_db_api.set_user_role(second["id"], "ADMIN")
        victim = self.as_user(second)
        self.assertEqual(self.get(victim, "/api/users").status_code, 200)
        self.as_admin().patch(f"/api/users/{second['id']}/role",
                              json={"role": "USER"}, environ_base=REMOTE)
        self.assertEqual(self.get(victim, "/api/users").status_code, 401)

    def test_garbage_cookie_is_rejected(self):
        client = self.app.test_client()
        client.set_cookie("faceid_session", "not-a-real-token", domain="localhost")
        self.assertEqual(self.get(client, "/api/me").status_code, 401)

    # -- injection -------------------------------------------------------

    def test_sql_like_input_is_stored_as_data(self):
        payload = "Robert'); DROP TABLE users;--"
        res = self.post(self.as_admin(), "/api/users", json={"name": payload})
        self.assertEqual(res.status_code, 201)
        names = [u["name"] for u in real_db_api.list_users_for_ui()]
        self.assertIn(payload, names)
        # The table is still there and still holds everyone.
        self.assertGreaterEqual(len(names), 4)

    # -- face templates --------------------------------------------------

    def test_user_can_delete_only_their_own_face(self):
        real_db_api.set_user_embedding(self.driver["id"], b"\x01\x02")
        real_db_api.set_user_embedding(self.other["id"], b"\x03\x04")
        client = self.as_driver()

        stolen = client.delete(f"/api/users/{self.other['id']}/face", environ_base=REMOTE)
        self.assertEqual(stolen.status_code, 403)
        self.assertTrue(real_db_api.get_user_by_id(self.other["id"])["face_enrolled"])

        own = client.delete(f"/api/users/{self.driver['id']}/face", environ_base=REMOTE)
        self.assertEqual(own.status_code, 200)
        self.assertFalse(real_db_api.get_user_by_id(self.driver["id"])["face_enrolled"])

    def test_admin_can_delete_any_face(self):
        real_db_api.set_user_embedding(self.driver["id"], b"\x01\x02")
        res = self.as_admin().delete(f"/api/users/{self.driver['id']}/face",
                                     environ_base=REMOTE)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(real_db_api.get_user_by_id(self.driver["id"])["face_enrolled"])

    def test_unauthenticated_cannot_delete_a_face(self):
        real_db_api.set_user_embedding(self.driver["id"], b"\x01\x02")
        res = self.anon.delete(f"/api/users/{self.driver['id']}/face", environ_base=REMOTE)
        self.assertEqual(res.status_code, 401)
        self.assertTrue(real_db_api.get_user_by_id(self.driver["id"])["face_enrolled"])

    # -- static serving --------------------------------------------------

    def test_static_routes_refuse_path_traversal(self):
        client = self.as_admin()
        for path in (
            "/assets/../db.py",
            "/assets/../../etc/passwd",
            "/assets/%2e%2e/db.py",
            "/icons/../../db_api.py",
            "/assets/..%2fdb.py",
        ):
            with self.subTest(path=path):
                res = client.get(path, environ_base=REMOTE)
                self.assertEqual(res.status_code, 404)
                self.assertNotIn(b"import ", res.data[:200])

    def test_static_serving_does_not_shadow_the_api(self):
        # A GET on a POST-only endpoint must stay a 405 from the API, not turn
        # into the SPA shell served with 200.
        client = self.as_admin()
        self.assertEqual(client.get("/api/unlock", environ_base=REMOTE).status_code, 405)
        self.assertEqual(client.get("/api/nope", environ_base=REMOTE).status_code, 405)

    # -- input validation and error shape --------------------------------

    def test_malformed_input_returns_json_not_a_stack_trace(self):
        """Type-confused input must never produce an HTML 500."""
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        client = self.as_admin()
        cases = [
            ("post", "/api/users", {"name": 123}),
            ("post", "/api/users", {"name": "A" * 999}),
            ("post", "/api/users", {"name": "a\x00b"}),
            ("post", "/api/enroll/start", {"name": 5}),
            ("post", "/api/scan/start", {"purpose": 1}),
            ("post", "/api/scan/start", {"purpose": "not-a-purpose"}),
            ("post", "/api/scan/cancel", {"session_id": {"a": 1}}),
            ("post", "/api/verify-log", {"detail": {"a": 1}}),
            ("post", "/api/settings", {"autoRelockSeconds": "abc"}),
            ("post", "/api/settings", {"autoRelockSeconds": -5}),
            ("post", "/api/settings", {"liveness": "no"}),
        ]
        for method, path, payload in cases:
            with self.subTest(path=path, payload=payload):
                res = getattr(client, method)(path, json=payload, environ_base=REMOTE)
                self.assertEqual(res.status_code, 400)
                self.assertTrue(res.content_type.startswith("application/json"),
                                f"{path} answered {res.content_type}")
                self.assertFalse(res.get_json()["ok"])

    def test_settings_bounds_are_enforced(self):
        client = self.as_admin()
        self.assertEqual(
            client.post("/api/settings", json={"autoRelockSeconds": 99999},
                        environ_base=REMOTE).status_code, 400)
        self.assertEqual(
            client.post("/api/settings", json={"unknownKey": 1},
                        environ_base=REMOTE).status_code, 400)
        self.assertEqual(
            client.post("/api/settings", json={"autoRelockSeconds": 30},
                        environ_base=REMOTE).status_code, 200)

    def test_oversized_upload_is_refused_before_the_view_runs(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        # Running this under -W always reports a ResourceWarning for an unclosed
        # temp file: Werkzeug spools the oversized body to disk and abandons the
        # handle when it aborts the parse. It surfaces at collection time, so it
        # cannot be caught around the call, and it is inside Werkzeug rather
        # than this application.
        payload = io.BytesIO(b"\xff\xd8" + b"\0" * (9 * 1024 * 1024))
        res = self.as_admin().post(
            "/api/enroll/sample",
            data={"session_id": "s1", "image": (payload, "big.jpg", "image/jpeg")},
            content_type="multipart/form-data", environ_base=REMOTE,
        )
        self.assertEqual(res.status_code, 413)
        self.assertTrue(res.content_type.startswith("application/json"))

    def test_security_headers_are_present(self):
        res = self.get(self.as_admin(), "/api/status")
        self.assertIn("default-src 'self'", res.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", res.headers["Content-Security-Policy"])
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertEqual(res.headers["Referrer-Policy"], "no-referrer")
        # HSTS only once TLS is actually in front; otherwise it strands clients.
        self.assertNotIn("Strict-Transport-Security", res.headers)

    def test_audit_log_is_pruned(self):
        real_db_api.prune_logs(keep=5)
        for i in range(20):
            real_db_api.log_event("test_event", "ok", detail=f"entry {i}")
        result = real_db_api.prune_logs(keep=5)
        self.assertGreater(result["removed"], 0)
        self.assertLessEqual(result["remaining"], 5)

    # -- policy completeness ---------------------------------------------

    def test_every_route_has_an_explicit_policy(self):
        """A new route must not inherit access by accident."""
        missing = []
        for rule in self.app.url_map.iter_rules():
            endpoint = rule.endpoint
            if endpoint == "static" or endpoint.startswith("sim_"):
                continue
            if endpoint not in auth.ENDPOINT_POLICY:
                missing.append(f"{endpoint} ({rule.rule})")
        self.assertEqual(
            missing, [],
            "These routes have no ENDPOINT_POLICY entry, so they silently "
            "default to admin-only. Add them to auth.ENDPOINT_POLICY:\n  "
            + "\n  ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
