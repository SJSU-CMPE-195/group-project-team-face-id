"""Hardware-free safety policy tests for the face runtime."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time
import unittest
from unittest.mock import patch

from car_face_auth.src.pc_runtime import PcRuntime
from car_face_auth.src.pi_runtime import PiRuntime, RuntimeRequestError
from wireless.security import Principal
from wireless.security_credentials import SecurityError


class FakeDb:
    def __init__(self) -> None:
        self.settings = {
            "autoRelockSeconds": 0,
            "ignitionAutoStopSeconds": 0,
            "liveness": False,
            "failLockout": False,
            "lockoutAfter": 5,
        }
        self.settings_error: Exception | None = None
        self.settings_reads = 0
        self.users = [
            {"id": "u1", "name": "Ada", "face_access": 1},
            {"id": "u2", "name": "Bob", "face_access": 1},
        ]
        self.lock_state = "unlocked"
        self.lock_writes = 0
        self.unlock_writes = 0
        self.fail_unlock = False
        self.logs = []

    def get_settings_for_ui(self):
        self.settings_reads += 1
        if self.settings_error:
            raise self.settings_error
        return dict(self.settings)

    def get_all_users(self):
        return [dict(user) for user in self.users]

    def get_user_by_id(self, user_id):
        return next(
            (dict(user) for user in self.users if user["id"] == user_id),
            None,
        )

    def get_status(self):
        return {"lockState": self.lock_state}

    def set_lock(self, reason):
        self.lock_state = "locked"
        self.lock_writes += 1

    def set_unlock(self, reason):
        if self.fail_unlock:
            raise RuntimeError("unlock database failure")
        self.lock_state = "unlocked"
        self.unlock_writes += 1

    def log_event(self, stage, result, detail="", user_id=None):
        self.logs.append((stage, result, detail, user_id))


class FakeSecurityStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.locked = False
        self.record_error: Exception | None = None
        self.records: list[tuple[bool, int]] = []

    @contextmanager
    def synchronized(self):
        with self.lock:
            yield

    def still_authorized(self, principal):
        return principal.user_id == "u1" and principal.auth_version == 1

    def user_auth_version(self, user_id):
        return 1 if user_id in {"u1", "u2"} else None

    def user_is_current(
        self,
        user_id,
        auth_version,
        *,
        require_face_access=False,
    ):
        return user_id in {"u1", "u2"} and auth_version == 1

    def check_face_attempt(self, principal):
        if self.locked:
            raise SecurityError(
                "face_locked",
                423,
                "Face verification is temporarily locked",
            )

    def record_face_result(self, principal, matched, limit):
        if self.record_error:
            raise self.record_error
        self.records.append((matched, limit))
        self.locked = not matched and len(
            [result for result, _limit in self.records if not result]
        ) >= limit


class FakeCapture:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.active = True

    def failure(self):
        return None

    def request_stop(self):
        self.active = False


class SafetyRuntime(PiRuntime):
    actuator_control_available = True

    def __init__(self, db):
        super().__init__(db)
        self.commands = []
        self.command_results = {}
        self.camera_open_calls = 0
        self.camera_error = None

    def _spawn(self, session_id, target):
        return None

    def _send_command(self, command, connect=True):
        self.commands.append(command)
        return self.command_results.get(command, True)

    def _schedule_session_cleanup(self, session_id):
        return None

    def _close_camera(self, owner_id=None):
        return True

    def _get_face_engine(self):
        return object()

    def _ensure_model(self):
        return object()

    def _open_camera(self, owner_id=None):
        self.camera_open_calls += 1
        if self.camera_error:
            raise self.camera_error
        return object()


class WriteProbe:
    def __init__(self) -> None:
        self.writes = []
        self.closed = False

    def write(self, payload):
        self.writes.append(payload)

    def close(self):
        self.closed = True


class BlockedPhysicalRuntime(PiRuntime):
    def __init__(self, db):
        super().__init__(db)
        self.ensure_serial_calls = 0
        self.camera_open_calls = 0

    def _ensure_serial(self):
        self.ensure_serial_calls += 1
        raise AssertionError("blocked output must not open serial")

    def _spawn(self, session_id, target):
        raise AssertionError("blocked scan must not spawn a camera worker")

    def _open_camera(self, owner_id=None):
        self.camera_open_calls += 1
        raise AssertionError("blocked scan must not open a camera")

    def _close_camera(self, owner_id=None):
        return True


class FailingTimer:
    def __init__(self) -> None:
        self.daemon = False
        self.cancelled = False

    def start(self):
        raise RuntimeError("cannot start timer")

    def cancel(self):
        self.cancelled = True


class PassiveTimer(FailingTimer):
    def start(self):
        return None


def set_actuation_state(
    runtime,
    *,
    ignition_on=False,
    unlock_owner=None,
    unlock_owner_id=None,
):
    with runtime._actuation.synchronized():
        runtime._actuation._ignition_on = ignition_on
        runtime._actuation._unlock_owner = unlock_owner
        runtime._actuation._unlock_owner_id = unlock_owner_id


class RuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.store = FakeSecurityStore()
        self.runtime = SafetyRuntime(self.db)
        self.runtime.configure_authorization(self.store)
        self.ada = Principal("ada-phone", "u1", "Ada", False, 1)

    def start_scan(self):
        result = self.runtime.start_scan(
            expected_user="Ada",
            authorization=self.ada,
        )
        return self.runtime._session(result["session_id"])

    def grant(self, session, candidate="Ada"):
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

    def test_liveness_enabled_fails_before_camera_or_session(self):
        self.db.settings["liveness"] = True

        with self.assertRaisesRegex(
            RuntimeRequestError,
            "presentation-attack detection is unavailable",
        ) as raised:
            self.start_scan()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.runtime.camera_open_calls, 0)
        self.assertEqual(self.runtime._sessions, {})

    def test_liveness_enabled_blocks_host_and_client_enrollment(self):
        self.db.settings["liveness"] = True
        admin = Principal("admin-phone", "u1", "Ada", True, 1)

        for start in (
            self.runtime.start_enrollment,
            self.runtime.start_client_enrollment,
        ):
            with self.assertRaisesRegex(
                RuntimeRequestError,
                "presentation-attack detection is unavailable",
            ) as raised:
                start("Ada", authorization=admin)
            self.assertEqual(raised.exception.status_code, 503)

        self.assertEqual(self.runtime.camera_open_calls, 0)
        self.assertEqual(self.runtime._sessions, {})

    def test_settings_failure_blocks_start_and_reports_not_ready(self):
        self.db.settings_error = RuntimeError("database offline")
        set_actuation_state(self.runtime, unlock_owner="Ada")

        result = self.runtime.set_ignition(True)
        status = self.runtime.status()

        self.assertFalse(result["ok"])
        self.assertFalse(result["command_sent"])
        self.assertEqual(self.runtime.commands, [])
        self.assertFalse(status["settings_ready"])
        self.assertFalse(status["ready"])
        self.assertIn("database offline", status["error"])

    def test_force_lock_send_failure_preserves_reported_state(self):
        set_actuation_state(
            self.runtime,
            ignition_on=True,
            unlock_owner="Ada",
            unlock_owner_id="u1",
        )
        self.runtime.command_results = {"STOP": False, "LOCK": True}

        result = self.runtime.force_lock(reason="test")

        self.assertFalse(result["ok"])
        self.assertFalse(result["locked"])
        self.assertFalse(result["command_sent"])
        self.assertFalse(result["physical_state_confirmed"])
        self.assertEqual(self.db.lock_writes, 0)
        actuation = self.runtime._actuation.snapshot()
        self.assertTrue(actuation.ignition_on)
        self.assertEqual(actuation.unlock_owner_id, "u1")

    def test_force_lock_send_failure_preserves_safety_timers(self):
        auto_relock_timer = PassiveTimer()
        ignition_stop_timer = PassiveTimer()
        set_actuation_state(
            self.runtime,
            ignition_on=True,
            unlock_owner="Ada",
            unlock_owner_id="u1",
        )
        with self.runtime._actuation.synchronized():
            self.runtime._actuation._auto_relock_timer = auto_relock_timer
            self.runtime._actuation._ignition_stop_timer = ignition_stop_timer
        self.runtime.command_results = {"STOP": False, "LOCK": True}

        result = self.runtime.force_lock(reason="test")
        actuation = self.runtime._actuation.snapshot()

        self.assertFalse(result["ok"])
        self.assertTrue(actuation.auto_relock_armed)
        self.assertTrue(actuation.ignition_stop_armed)
        self.assertFalse(auto_relock_timer.cancelled)
        self.assertFalse(ignition_stop_timer.cancelled)

    def test_force_lock_success_reports_commands_without_physical_confirmation(self):
        set_actuation_state(
            self.runtime,
            ignition_on=True,
            unlock_owner="Ada",
            unlock_owner_id="u1",
        )

        result = self.runtime.force_lock(reason="test")

        self.assertTrue(result["ok"])
        self.assertTrue(result["locked"])
        self.assertTrue(result["command_sent"])
        self.assertFalse(result["physical_state_confirmed"])
        self.assertEqual(self.runtime.commands, ["STOP", "LOCK"])
        self.assertEqual(self.db.lock_writes, 1)
        actuation = self.runtime._actuation.snapshot()
        self.assertFalse(actuation.ignition_on)
        self.assertIsNone(actuation.unlock_owner_id)

    def test_final_settings_read_failure_blocks_unlock_command(self):
        session = self.start_scan()
        self.db.settings_error = RuntimeError("settings changed")

        result = self.grant(session)

        self.assertEqual(result["state"], "error")
        self.assertEqual(self.runtime.commands, [])
        self.assertEqual(self.db.unlock_writes, 0)
        self.assertIn("settings changed", result["message"])

    def test_validated_relock_timeout_is_reused_without_silent_reread(self):
        self.db.settings["autoRelockSeconds"] = 17
        session = self.start_scan()
        self.db.settings_reads = 0
        timer = PassiveTimer()

        with patch(
            "car_face_auth.src.runtime_actuation.threading.Timer",
            return_value=timer,
        ):
            result = self.grant(session)

        self.assertEqual(result["state"], "granted")
        self.assertTrue(self.runtime._actuation.snapshot().auto_relock_armed)
        self.assertEqual(self.db.settings_reads, 1)
        self.assertTrue(result["command_sent"])
        self.assertFalse(result["physical_state_confirmed"])

    def test_relock_timer_failure_blocks_unlock_before_command(self):
        runtime = SafetyRuntime(self.db)
        runtime.configure_authorization(self.store)
        self.db.settings["autoRelockSeconds"] = 17
        result = runtime.start_scan(
            expected_user="Ada",
            authorization=self.ada,
        )
        session = runtime._session(result["session_id"])
        capture = FakeCapture(session["id"])
        runtime._camera_capture = capture
        previous_timer = PassiveTimer()
        with runtime._actuation.synchronized():
            runtime._actuation._auto_relock_timer = previous_timer
        timer = FailingTimer()

        with patch(
            "car_face_auth.src.runtime_actuation.threading.Timer",
            return_value=timer,
        ):
            runtime._grant_scan(
                session["id"],
                "Ada",
                0.9,
                session["cancel_event"],
                capture,
                time.monotonic() + 5,
            )

        session = runtime._session(session["id"])
        self.assertEqual(session["state"], "error")
        self.assertIn("cannot start timer", session["message"])
        self.assertEqual(runtime.commands, [])
        self.assertEqual(self.db.unlock_writes, 0)
        self.assertTrue(runtime._actuation.snapshot().auto_relock_armed)
        self.assertFalse(previous_timer.cancelled)
        self.assertTrue(timer.cancelled)

    def test_ignition_timer_failure_blocks_start_before_command(self):
        runtime = SafetyRuntime(self.db)
        set_actuation_state(runtime, unlock_owner="Ada")
        self.db.settings["ignitionAutoStopSeconds"] = 17
        previous_timer = PassiveTimer()
        with runtime._actuation.synchronized():
            runtime._actuation._ignition_stop_timer = previous_timer
        timer = FailingTimer()

        with patch(
            "car_face_auth.src.runtime_actuation.threading.Timer",
            return_value=timer,
        ):
            result = runtime.set_ignition(True)

        self.assertFalse(result["ok"])
        self.assertFalse(result["command_sent"])
        self.assertIn("cannot start timer", result["error"])
        self.assertEqual(runtime.commands, [])
        actuation = runtime._actuation.snapshot()
        self.assertFalse(actuation.ignition_on)
        self.assertTrue(actuation.ignition_stop_armed)
        self.assertFalse(previous_timer.cancelled)
        self.assertTrue(timer.cancelled)

    def test_persistent_face_lockout_rejects_next_scan_with_423(self):
        self.db.settings.update(failLockout=True, lockoutAfter=1)
        session = self.start_scan()

        denied = self.grant(session, candidate="Bob")
        self.runtime._release_session(session["id"])

        self.assertEqual(denied["state"], "denied")
        self.assertEqual(self.store.records, [(False, 1)])
        with self.assertRaises(RuntimeRequestError) as raised:
            self.start_scan()
        self.assertEqual(raised.exception.status_code, 423)
        self.assertEqual(self.runtime.commands, [])

    def test_face_result_persistence_failure_blocks_actuation(self):
        self.db.settings["failLockout"] = True
        self.store.record_error = RuntimeError("audit storage unavailable")
        session = self.start_scan()

        result = self.grant(session)

        self.assertEqual(result["state"], "error")
        self.assertEqual(self.runtime.commands, [])
        self.assertEqual(self.db.unlock_writes, 0)
        self.assertIn("audit storage unavailable", result["message"])

    def test_revocation_preserves_running_ignition_state(self):
        set_actuation_state(
            self.runtime,
            ignition_on=True,
            unlock_owner="Ada",
            unlock_owner_id="u1",
        )
        before = self.runtime._actuation.snapshot()

        self.runtime._invalidate_authorization("Ada")
        after = self.runtime._actuation.snapshot()

        self.assertTrue(after.ignition_on)
        self.assertIsNone(after.unlock_owner)
        self.assertIsNone(after.unlock_owner_id)
        self.assertEqual(after.generation, before.generation + 1)
        self.assertEqual(self.runtime.commands, [])

    def test_unlock_db_failure_reports_sent_commands_and_clears_owner(self):
        set_actuation_state(
            self.runtime,
            ignition_on=True,
            unlock_owner="Ada",
            unlock_owner_id="u1",
        )
        self.db.fail_unlock = True
        generation = self.runtime._actuation.snapshot().generation

        result = self.runtime._actuation.unlock(
            "Ada",
            "u1",
            "test",
            auto_relock_seconds=0,
            expected_generation=generation,
        )
        after = self.runtime._actuation.snapshot()

        self.assertFalse(result["ok"])
        self.assertTrue(result["command_sent"])
        self.assertTrue(result["unlock_command_sent"])
        self.assertTrue(result["compensating_lock_command_sent"])
        self.assertEqual(self.runtime.commands, ["UNLOCK", "LOCK"])
        self.assertTrue(after.ignition_on)
        self.assertIsNone(after.unlock_owner)
        self.assertIsNone(after.unlock_owner_id)

    def test_unlock_db_and_compensation_failure_keeps_relock_armed(self):
        self.db.settings["autoRelockSeconds"] = 17
        self.db.fail_unlock = True
        self.runtime.command_results = {"LOCK": False}
        session = self.start_scan()
        timer = PassiveTimer()

        with patch(
            "car_face_auth.src.runtime_actuation.threading.Timer",
            return_value=timer,
        ):
            self.grant(session)
        result = self.runtime.scan_status(session["id"])
        actuation = self.runtime._actuation.snapshot()

        self.assertEqual(result["state"], "error")
        self.assertTrue(result["command_sent"])
        self.assertTrue(result["unlock_command_sent"])
        self.assertFalse(result["compensating_lock_command_sent"])
        self.assertTrue(result["auto_relock_armed"])
        self.assertFalse(result["physical_state_confirmed"])
        self.assertIn("physical state is unknown", result["message"])
        self.assertEqual(self.runtime.commands, ["UNLOCK", "LOCK"])
        self.assertTrue(actuation.auto_relock_armed)
        self.assertFalse(timer.cancelled)

    def test_lock_generation_blocks_late_scan_grant(self):
        session = self.start_scan()
        capture = FakeCapture(session["id"])
        self.runtime._camera_capture = capture

        lock_result = self.runtime._actuation.force_lock("auto_relock")
        self.assertTrue(lock_result["ok"])
        self.runtime.commands.clear()
        result = self.grant(session)

        self.assertEqual(result["state"], "denied")
        self.assertIn("Lock state changed", result["message"])
        self.assertEqual(self.runtime.commands, [])
        self.assertEqual(self.db.lock_state, "locked")

    def test_camera_failure_does_not_increment_face_failures(self):
        self.db.settings["failLockout"] = True
        session = self.start_scan()
        self.runtime.camera_error = RuntimeError("camera unavailable")

        self.runtime._run_scan(session["id"])
        result = self.runtime._session(session["id"])

        self.assertEqual(result["state"], "error")
        self.assertEqual(self.store.records, [])
        self.assertEqual(self.runtime.commands, [])

    def test_pc_runtime_identifies_simulated_actuator(self):
        runtime = PcRuntime(self.db)
        runtime._model = object()

        status = runtime.status()

        self.assertTrue(status["ready"])
        self.assertEqual(status["actuator_mode"], "simulated")
        self.assertEqual(status["actuator_feedback"], "simulated")
        self.assertTrue(status["simulated_actuators"])
        self.assertFalse(status["physical_state_confirmed"])

    def test_physical_pi_blocks_every_command_before_serial_or_camera(self):
        runtime = BlockedPhysicalRuntime(self.db)
        serial = WriteProbe()
        runtime._serial = serial
        set_actuation_state(runtime, unlock_owner="Ada")

        status = runtime.status()
        self.assertFalse(status["ready"])
        self.assertFalse(status["actuator_control_available"])
        self.assertFalse(status["simulated_actuators"])
        self.assertFalse(status["esp32_connected"])
        self.assertIn("disabled", status["error"])

        with self.assertRaises(RuntimeRequestError) as raised:
            runtime.start_scan(expected_user="Ada")
        self.assertEqual(raised.exception.status_code, 503)

        for command in ("UNLOCK", "START", "LOCK", "STOP"):
            self.assertFalse(runtime._send_command(command))
        self.assertFalse(runtime.set_ignition(True)["ok"])
        self.assertFalse(runtime.set_ignition(False)["ok"])
        self.assertFalse(runtime.force_lock(reason="blocked")["ok"])

        runtime._modules = {"probe": True}
        runtime.close()

        self.assertEqual(runtime.ensure_serial_calls, 0)
        self.assertEqual(runtime.camera_open_calls, 0)
        self.assertEqual(serial.writes, [])
        self.assertTrue(serial.closed)
        self.assertEqual(self.db.lock_writes, 0)


if __name__ == "__main__":
    unittest.main()
