"""Hardware-free contract tests for the simulated Pi Device API.

The simulator exercises the production ``PiRuntime`` workers with deterministic
camera frames and serial faults.  No Flask socket, camera, serial device, or
Pi-only dependency is used by this test module.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import db as db_module
import db_api


FINAL_SCAN_STATES = {"granted", "denied", "error", "timeout", "cancelled"}
FINAL_ENROLL_STATES = {"completed", "error", "timeout", "cancelled"}


def _frame(identity=None, face_count=None, score=None):
    if face_count is None:
        face_count = 1 if identity else 0
    if score is None and identity and face_count == 1:
        score = 0.91
    return {"identity": identity, "face_count": face_count, "score": score}


class SimulatedPiApiTests(unittest.TestCase):
    """Run the simulated runtime against an isolated SQLite database."""

    @classmethod
    def setUpClass(cls):
        cls._original_db_path = db_module.DB_PATH
        cls._original_db_env = os.environ.get("FACEID_DB_PATH")
        cls._original_env = {
            key: os.environ.get(key)
            for key in (
                "PI_SCAN_TIMEOUT_SECONDS",
                "PI_ENROLL_TIMEOUT_SECONDS",
                "PI_ENROLL_SAMPLE_INTERVAL_SECONDS",
                "PI_CAMERA_CLOSE_TIMEOUT_SECONDS",
            )
        }
        cls._tempdir = tempfile.TemporaryDirectory(prefix="face-ui-sim-")
        cls._db_path = str(Path(cls._tempdir.name) / "simulated.db")
        os.environ["FACEID_DB_PATH"] = cls._db_path
        db_module.DB_PATH = cls._db_path
        os.environ["PI_SCAN_TIMEOUT_SECONDS"] = "1"
        os.environ["PI_ENROLL_TIMEOUT_SECONDS"] = "2"
        os.environ["PI_ENROLL_SAMPLE_INTERVAL_SECONDS"] = "0"
        os.environ["PI_CAMERA_CLOSE_TIMEOUT_SECONDS"] = "1"
        db_module.init_db()

        # Import only after the temporary DB path is active.  This also proves
        # the mock entrypoint is importable without touching hardware modules.
        cls.mock_module = importlib.import_module("mock_pi_device_api")

    @classmethod
    def tearDownClass(cls):
        db_module.DB_PATH = cls._original_db_path
        if cls._original_db_env is None:
            os.environ.pop("FACEID_DB_PATH", None)
        else:
            os.environ["FACEID_DB_PATH"] = cls._original_db_env
        for key, value in cls._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls._tempdir.cleanup()

    def setUp(self):
        # Keep each test independent while preserving the same isolated DB.
        with db_module.get_conn() as conn:
            conn.execute("DELETE FROM auth_logs")
            conn.execute("DELETE FROM users")
            conn.execute("UPDATE device_state SET lock_state='locked'")
        db_module.init_db()
        self.app = self.mock_module.create_mock_app(db_module=db_api)
        self.client = self.app.test_client()
        self.runtime = self.app.config["SIMULATED_RUNTIME"]

    def tearDown(self):
        try:
            self.runtime.close()
        except Exception:
            pass

    def _json(self, response):
        payload = response.get_json()
        self.assertIsInstance(payload, (dict, list), response.data)
        return payload

    def _configure(self, *, frames=None, camera_error=None, camera_stalled=False,
                   frame_delay_ms=0, serial_connected=True, fail_commands=()):
        scenario = {
            "frames": frames or [_frame()],
            "frame_delay_ms": frame_delay_ms,
            "camera_error": camera_error,
            "camera_stalled": camera_stalled,
        }
        response = self.client.put(
            "/sim/scenario",
            json={
                "scenario": scenario,
                "serial_connected": serial_connected,
                "fail_commands": list(fail_commands),
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return self._json(response)

    def _add_ada(self):
        result = db_api.add_user("Ada")
        self.assertNotIn("error", result, result)
        return result

    def _poll(self, path, final_states, timeout=2):
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.get_json())
            latest = self._json(response)
            if latest.get("state") in final_states:
                return latest
            time.sleep(0.01)
        self.fail(f"{path} did not reach {sorted(final_states)}: {latest}")

    def _start_scan(self, purpose="unlock", expected_user=None):
        payload = {"purpose": purpose}
        if expected_user is not None:
            payload["expected_user"] = expected_user
        response = self.client.post("/api/scan/start", json=payload)
        self.assertEqual(response.status_code, 200, response.get_json())
        result = self._json(response)
        self.assertIn("session_id", result)
        return result

    def _commands(self):
        response = self.client.get("/sim/commands")
        self.assertEqual(response.status_code, 200, response.get_json())
        result = self._json(response)
        self.assertIsInstance(result, list)
        return result

    def test_ready_and_factory_expose_simulator_in_app_config(self):
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200, response.get_json())
        payload = self._json(response)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["runtime"]["ready"])
        self.assertIs(self.app.config["RUNTIME"], self.runtime)
        self.assertTrue(self.runtime.status()["ready"])

    def test_scripted_no_face_multiple_face_then_six_ada_matches_unlock(self):
        self._add_ada()
        self._configure(
            frames=[
                _frame(face_count=0),
                _frame(face_count=2, score=None),
                *[_frame("Ada") for _ in range(6)],
            ]
        )
        session = self._start_scan()
        result = self._poll(f"/api/scan/status?session_id={session['session_id']}", FINAL_SCAN_STATES)
        self.assertEqual(result["state"], "granted", result)
        self.assertEqual(result["user"], "Ada")
        self.assertGreaterEqual(result["window"]["matches"], 6)
        self.assertEqual(db_api.get_status()["lockState"], "unlocked")
        self.assertEqual(self._commands()[0], "UNLOCK")
        self.assertEqual(self._commands()[-1], "UNLOCK")

    def test_same_user_ignition_scan_sends_start_after_unlock(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")])
        unlock = self._start_scan()
        unlock_result = self._poll(f"/api/scan/status?session_id={unlock['session_id']}", FINAL_SCAN_STATES)
        self.assertEqual(unlock_result["state"], "granted", unlock_result)

        ignition = self._start_scan("ignition", expected_user="Ada")
        ignition_result = self._poll(
            f"/api/scan/status?session_id={ignition['session_id']}", FINAL_SCAN_STATES
        )
        self.assertEqual(ignition_result["state"], "granted", ignition_result)
        self.assertEqual(ignition_result["user"], "Ada")
        self.assertEqual(self._commands()[:2], ["UNLOCK", "START"])
        self.assertTrue(self.runtime.status()["ignitionOn"])

    def test_enrollment_captures_ten_samples(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")])
        response = self.client.post("/api/enroll/start", json={"name": "Ada"})
        self.assertEqual(response.status_code, 200, response.get_json())
        session = self._json(response)
        result = self._poll(
            f"/api/enroll/status?session_id={session['session_id']}", FINAL_ENROLL_STATES
        )
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(result["count"], 10)
        self.assertEqual(result["samples_needed"], 10)
        self.assertTrue(result["recognition_available"])
        saved = self.runtime._simulated_face_engine._saved
        self.assertEqual(len(saved["Ada"]), 10)

    def test_camera_error_finishes_scan_as_error(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")], camera_error="lens unavailable")
        session = self._start_scan()
        result = self._poll(f"/api/scan/status?session_id={session['session_id']}", FINAL_SCAN_STATES)
        self.assertEqual(result["state"], "error", result)
        self.assertIn("lens unavailable", result["message"])
        self.assertEqual(db_api.get_status()["lockState"], "locked")
        self.assertEqual(self._commands(), [])

    def test_serial_disconnect_is_not_ready_and_never_unlocks(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")], serial_connected=False)
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 503, ready.get_json())
        self.assertFalse(self._json(ready)["ok"])
        session = self._start_scan()
        result = self._poll(f"/api/scan/status?session_id={session['session_id']}", FINAL_SCAN_STATES)
        self.assertEqual(result["state"], "error", result)
        self.assertEqual(db_api.get_status()["lockState"], "locked")
        command_log = self.runtime.get_command_log()
        self.assertEqual(command_log[-1]["command"], "UNLOCK")
        self.assertFalse(command_log[-1]["ok"])

    def test_disconnect_rejects_a_stale_in_flight_serial_connection(self):
        self._configure(serial_connected=True)
        captured = threading.Event()
        continue_write = threading.Event()
        outcome = {}

        def use_captured_connection():
            connection = self.runtime._ensure_serial()
            captured.set()
            continue_write.wait(1)
            try:
                connection.write(b"UNLOCK\n")
                connection.flush()
                outcome["ok"] = True
            except Exception as exc:
                outcome["error"] = str(exc)

        worker = threading.Thread(target=use_captured_connection, daemon=True)
        worker.start()
        self.assertTrue(captured.wait(1))
        self.runtime.configure({"serial_connected": False})
        continue_write.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("ok", outcome)
        self.assertIn("disconnected", outcome.get("error", ""))

    def test_unlock_fault_fails_scan_and_preserves_locked_state(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")], fail_commands=["UNLOCK"])
        session = self._start_scan()
        result = self._poll(f"/api/scan/status?session_id={session['session_id']}", FINAL_SCAN_STATES)
        self.assertEqual(result["state"], "error", result)
        self.assertEqual(db_api.get_status()["lockState"], "locked")
        self.assertFalse(self.runtime.get_command_log()[-1]["ok"])
        self.assertEqual(self.runtime.get_command_log()[-1]["command"], "UNLOCK")
        ready = self.client.get("/ready")
        self.assertEqual(ready.status_code, 503, ready.get_json())
        self.assertIn("UNLOCK", self._json(ready)["runtime"]["error"])
        self._configure(frames=[_frame("Ada")])
        self.assertEqual(self.client.get("/ready").status_code, 200)

    def test_start_fault_fails_same_user_ignition(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")])
        unlock = self._start_scan()
        self.assertEqual(
            self._poll(f"/api/scan/status?session_id={unlock['session_id']}", FINAL_SCAN_STATES)["state"],
            "granted",
        )
        self._configure(frames=[_frame("Ada")], fail_commands=["START"])
        ignition = self._start_scan("ignition", expected_user="Ada")
        result = self._poll(f"/api/scan/status?session_id={ignition['session_id']}", FINAL_SCAN_STATES)
        self.assertEqual(result["state"], "error", result)
        self.assertFalse(self.runtime.status()["ignitionOn"])
        self.assertEqual(self.runtime.get_command_log()[-1]["command"], "START")
        self.assertFalse(self.runtime.get_command_log()[-1]["ok"])

    def test_stop_fault_keeps_ignition_state_on(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")])
        unlock = self._start_scan()
        self.assertEqual(
            self._poll(f"/api/scan/status?session_id={unlock['session_id']}", FINAL_SCAN_STATES)["state"],
            "granted",
        )
        ignition = self._start_scan("ignition", expected_user="Ada")
        self.assertEqual(
            self._poll(f"/api/scan/status?session_id={ignition['session_id']}", FINAL_SCAN_STATES)["state"],
            "granted",
        )
        self._configure(frames=[_frame("Ada")], fail_commands=["STOP"])
        response = self.client.post("/api/ignition/stop", json={})
        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertFalse(self._json(response)["ok"])
        self.assertTrue(self.runtime.status()["ignitionOn"])
        self.assertEqual(self.runtime.get_command_log()[-1]["command"], "STOP")
        self.assertFalse(self.runtime.get_command_log()[-1]["ok"])

    def test_lock_fault_reports_failure_but_db_is_locked(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")])
        unlock = self._start_scan()
        self.assertEqual(
            self._poll(f"/api/scan/status?session_id={unlock['session_id']}", FINAL_SCAN_STATES)["state"],
            "granted",
        )
        self._configure(frames=[_frame("Ada")], fail_commands=["LOCK"])
        response = self.client.post("/api/full-reset", json={})
        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertFalse(self._json(response)["ok"])
        self.assertEqual(db_api.get_status()["lockState"], "locked")
        self.assertEqual(self.runtime.get_command_log()[-1]["command"], "LOCK")
        self.assertFalse(self.runtime.get_command_log()[-1]["ok"])

    def test_stalled_capture_cancel_releases_camera_for_next_scan(self):
        self._add_ada()
        self._configure(frames=[_frame("Ada")], camera_stalled=True)
        self.assertEqual(self.client.get("/ready").status_code, 503)
        session = self._start_scan()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and self.runtime.status()["active_session"] is None:
            time.sleep(0.01)
        self.assertIsNotNone(self.runtime.status()["active_session"])
        cancel = self.client.post("/api/scan/cancel", json={"session_id": session["session_id"]})
        self.assertEqual(cancel.status_code, 200, cancel.get_json())
        self.assertEqual(self._json(cancel)["state"], "cancelled")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and self.runtime.status()["active_session"] is not None:
            time.sleep(0.01)
        self.assertIsNone(self.runtime.status()["active_session"])

        self._configure(frames=[_frame("Ada")], camera_stalled=False)
        next_session = self._start_scan()
        result = self._poll(
            f"/api/scan/status?session_id={next_session['session_id']}", FINAL_SCAN_STATES
        )
        self.assertEqual(result["state"], "granted", result)

    def test_simulation_routes_validate_reset_and_report_commands(self):
        scenario = self.client.get("/sim/scenario")
        self.assertEqual(scenario.status_code, 200, scenario.get_json())
        self.assertEqual(scenario.headers["Cache-Control"], "no-store")
        initial = self._json(scenario)
        self.assertIn("scenario", initial)
        self.assertIn("frames", initial["scenario"])

        invalid = self.client.put("/sim/scenario", json={"serial_connected": 1})
        self.assertEqual(invalid.status_code, 400, invalid.get_json())
        self.assertFalse(self._json(invalid)["ok"])
        unknown = self.client.put("/sim/scenario", json={"unknown": True})
        self.assertEqual(unknown.status_code, 400, unknown.get_json())
        self.assertFalse(self._json(unknown)["ok"])
        malformed = self.client.put("/sim/scenario", json=["not", "an", "object"])
        self.assertEqual(malformed.status_code, 400, malformed.get_json())

        self._add_ada()
        self._configure(frames=[_frame("Ada")])
        scan = self._start_scan()
        self.assertEqual(
            self._poll(f"/api/scan/status?session_id={scan['session_id']}", FINAL_SCAN_STATES)["state"],
            "granted",
        )
        commands = self._commands()
        self.assertEqual(commands, ["UNLOCK"])

        reset = self.client.post("/sim/reset", json={})
        self.assertEqual(reset.status_code, 200, reset.get_json())
        reset_payload = self._json(reset)
        self.assertTrue(reset_payload["ok"])
        self.assertFalse(reset_payload["scenario"]["camera_stalled"])
        self.assertIsNone(reset_payload["scenario"]["camera_error"])
        self.assertEqual(reset_payload["commands"][-2:], ["STOP", "LOCK"])
        self.assertEqual(db_api.get_status()["lockState"], "locked")

        self._configure(frames=[_frame("Ada")], fail_commands=["LOCK"])
        failed_reset = self.client.post("/sim/reset", json={})
        self.assertEqual(failed_reset.status_code, 503, failed_reset.get_json())
        self.assertFalse(self._json(failed_reset)["ok"])

    def test_importing_mock_api_does_not_load_pi_only_modules(self):
        code = (
            "import sys; import mock_pi_device_api; "
            "bad={'picamera2','insightface','cv2','serial'} & set(sys.modules); "
            "assert not bad, bad"
        )
        env = os.environ.copy()
        env["FACEID_DB_PATH"] = str(Path(self._tempdir.name) / "import-check.db")
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
