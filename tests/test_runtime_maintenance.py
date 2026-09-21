"""Hardware-free checks for reversible runtime maintenance."""

from __future__ import annotations

import threading
import time
import unittest

from car_face_auth.src.pi_runtime import PiRuntime, RuntimeRequestError


class FakeDb:
    def __init__(self) -> None:
        self.settings = {
            "autoRelockSeconds": 60,
            "ignitionAutoStopSeconds": 60,
            "liveness": False,
            "failLockout": False,
            "lockoutAfter": 5,
        }
        self.users = [{"id": "u1", "name": "Ada", "face_access": 1}]
        self.lock_state = "unlocked"
        self.lock_writes = 0
        self.fail_lock = False
        self.settings_reads = 0
        self.worker_writes = 0
        self.logs: list[tuple[str, str, str, str | None]] = []

    def get_settings_for_ui(self):
        self.settings_reads += 1
        return dict(self.settings)

    def get_all_users(self):
        return [dict(user) for user in self.users]

    def get_status(self):
        return {"lockState": self.lock_state}

    def set_lock(self, reason):
        if self.fail_lock:
            raise RuntimeError("lock database failure")
        self.lock_state = "locked"
        self.lock_writes += 1

    def set_unlock(self, reason):
        self.lock_state = "unlocked"

    def log_event(self, stage, result, detail="", user_id=None):
        self.logs.append((stage, result, detail, user_id))

    def record_worker_write(self):
        self.worker_writes += 1


class FakeFaceEngine:
    def __init__(self) -> None:
        self.database_reads = 0

    def load_database(self):
        self.database_reads += 1
        return {"Ada": [object()]}


class MaintenanceRuntime(PiRuntime):
    actuator_control_available = True

    def __init__(self, db, face_engine):
        super().__init__(db, face_engine=face_engine)
        self.commands: list[str] = []
        self.worker_started = threading.Event()
        self.worker_stopped = threading.Event()

    def _send_command(self, command, connect=True):
        self.commands.append(command)
        return True

    def _run_scan(self, session_id):
        session = self._session(session_id)
        if not session:
            return
        self.worker_started.set()
        session["cancel_event"].wait()
        time.sleep(0.03)
        self._db.record_worker_write()
        self.worker_stopped.set()


class CameraFailureRuntime(MaintenanceRuntime):
    def _close_camera(self, owner_id=None):
        self._record_error("camera cleanup failed", "camera")
        return False


class RuntimeMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.face_engine = FakeFaceEngine()
        self.runtime = MaintenanceRuntime(self.db, self.face_engine)

    def test_quiesce_drains_worker_and_rejects_new_work(self):
        self.runtime.start_scan()
        self.assertTrue(self.runtime.worker_started.wait(1))

        result = self.runtime.quiesce("developer_reset", timeout=1)

        self.assertTrue(result["maintenance"])
        self.assertTrue(self.runtime.worker_stopped.is_set())
        writes_after_drain = self.db.worker_writes
        time.sleep(0.05)
        self.assertEqual(self.db.worker_writes, writes_after_drain)
        self.assertEqual(self.db.lock_state, "locked")
        with self.assertRaisesRegex(RuntimeRequestError, "maintenance"):
            self.runtime.start_scan()
        self.assertFalse(
            self.runtime.set_ignition(True, "test").get("ok")
        )

    def test_quiesce_cancels_actuation_timer_before_it_can_write(self):
        generation = self.runtime._actuation.snapshot().generation
        unlocked = self.runtime._actuation.unlock(
            "Ada",
            "u1",
            "test",
            auto_relock_seconds=1,
            expected_generation=generation,
        )
        self.assertTrue(unlocked["ok"])

        self.runtime.quiesce("simulated_power_off", timeout=1)
        writes_after_drain = self.db.lock_writes
        time.sleep(0.05)

        self.assertEqual(writes_after_drain, 1)
        self.assertEqual(self.db.lock_writes, writes_after_drain)
        actuation = self.runtime._actuation.snapshot()
        self.assertFalse(actuation.auto_relock_armed)
        self.assertIsNone(actuation.unlock_owner)

    def test_resume_reloads_database_state_and_clears_sessions(self):
        self.runtime.start_scan()
        self.assertTrue(self.runtime.worker_started.wait(1))
        self.runtime.quiesce(timeout=1)
        settings_reads = self.db.settings_reads

        self.runtime.resume_after_maintenance()

        self.assertGreater(self.db.settings_reads, settings_reads)
        self.assertEqual(self.face_engine.database_reads, 1)
        self.assertEqual(self.runtime._sessions, {})
        self.assertFalse(self.runtime.status()["maintenance"])

    def test_camera_cleanup_failure_stays_in_maintenance(self):
        runtime = CameraFailureRuntime(self.db, self.face_engine)

        with self.assertRaisesRegex(RuntimeError, "camera cleanup failed"):
            runtime.quiesce(timeout=1)

        self.assertTrue(runtime.status()["maintenance"])
        with self.assertRaisesRegex(RuntimeError, "has not drained"):
            runtime.resume_after_maintenance()
        with self.assertRaisesRegex(RuntimeRequestError, "maintenance"):
            runtime.start_scan()

    def test_lock_failure_still_drains_workers_and_stays_in_maintenance(self):
        self.runtime.start_scan()
        self.assertTrue(self.runtime.worker_started.wait(1))
        self.db.fail_lock = True

        with self.assertRaisesRegex(RuntimeError, "lock database failure"):
            self.runtime.quiesce(timeout=1)

        self.assertTrue(self.runtime.worker_stopped.is_set())
        self.assertTrue(self.runtime.status()["maintenance"])
        with self.assertRaisesRegex(RuntimeError, "has not drained"):
            self.runtime.resume_after_maintenance()


if __name__ == "__main__":
    unittest.main()
