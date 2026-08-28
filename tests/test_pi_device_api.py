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

from pi_device_api import create_app
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
    def setUp(self):
        self.db = FakeDb()
        self.runtime = FakeRuntime(self.db)
        self.app = create_app(db_module=self.db, runtime=self.runtime)
        self.app.testing = True
        self.client = self.app.test_client()

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


if __name__ == "__main__":
    unittest.main()
