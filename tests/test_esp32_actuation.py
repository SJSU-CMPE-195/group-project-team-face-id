"""Focused regressions for ESP32 actuation authorization and reconnects."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from car_face_auth.src.runtime_actuation import RuntimeActuation


class FakeDatabase:
    def __init__(self) -> None:
        self.lock_state = "locked"
        self.unlock_writes = 0
        self.lock_writes = 0

    def get_status(self):
        return {"lockState": self.lock_state}

    def set_unlock(self, **_kwargs):
        self.lock_state = "unlocked"
        self.unlock_writes += 1

    def set_lock(self, **_kwargs):
        self.lock_state = "locked"
        self.lock_writes += 1


class FakeTimer:
    def __init__(self, _interval, callback) -> None:
        self.callback = callback
        self.daemon = False
        self.cancelled = False
        self.started = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.callback()


class ActuationHarness:
    def __init__(self) -> None:
        self.db = FakeDatabase()
        self.commands: list[str] = []
        self.command_results: dict[str, bool] = {}
        self.logs: list[dict[str, object]] = []
        self.actuation = RuntimeActuation(
            self.db,
            self.send_command,
            self.log_event,
        )

    def send_command(self, command: str, **_kwargs) -> bool:
        self.commands.append(command)
        return self.command_results.get(command, True)

    def log_event(
        self,
        stage: str,
        result: str,
        detail: str,
        user_id: str | None,
        **kwargs,
    ) -> None:
        self.logs.append(
            {
                "stage": stage,
                "result": result,
                "detail": detail,
                "user_id": user_id,
                **kwargs,
            }
        )

    def unlock(self, *, auto_relock_seconds: int = 0) -> dict[str, object]:
        return self.actuation.unlock(
            "Ada",
            "ada-id",
            "test_unlock",
            auto_relock_seconds=auto_relock_seconds,
            expected_generation=self.actuation.snapshot().generation,
        )


class Esp32ActuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = ActuationHarness()

    def test_ignition_timeout_is_attributed_to_the_verified_driver(self):
        self.assertTrue(self.harness.unlock()["ok"])
        timers: list[FakeTimer] = []

        def make_timer(interval, callback) -> FakeTimer:
            timer = FakeTimer(interval, callback)
            timers.append(timer)
            return timer

        with patch(
            "car_face_auth.src.runtime_actuation.threading.Timer",
            side_effect=make_timer,
        ):
            result = self.harness.actuation.set_ignition(
                True,
                "bob_face_scan",
                ignition_stop_seconds=30,
                closed=False,
                user_id="bob-id",
            )

        self.assertTrue(result["ok"])
        timers[0].fire()
        timeout_log = next(
            row
            for row in self.harness.logs
            if row["detail"] == "stop:ignition_timeout_30s"
        )
        self.assertEqual(timeout_log["user_id"], "bob-id")

    def test_failed_authorization_compensation_clears_all_authority(self):
        timers = [
            FakeTimer(30, lambda: None),
            FakeTimer(30, lambda: None),
        ]
        with patch(
            "car_face_auth.src.runtime_actuation.threading.Timer",
            side_effect=timers,
        ):
            self.assertTrue(
                self.harness.unlock(auto_relock_seconds=30)["ok"]
            )
            self.assertTrue(
                self.harness.actuation.set_ignition(
                    True,
                    "test_ignition",
                    ignition_stop_seconds=30,
                    closed=False,
                    user_id="bob-id",
                )["ok"]
            )
        before_generation = self.harness.actuation.snapshot().generation
        self.harness.command_results["STOP"] = False

        result = self.harness.actuation.reset_connection(
            "esp32_authorization_unconfirmed"
        )
        state = self.harness.actuation.snapshot()

        self.assertFalse(result["ok"])
        self.assertEqual(self.harness.commands[-2:], ["STOP", "LOCK"])
        self.assertTrue(all(timer.cancelled for timer in timers))
        self.assertFalse(state.auto_relock_armed)
        self.assertFalse(state.ignition_stop_armed)
        self.assertFalse(state.ignition_on)
        self.assertIsNone(state.unlock_owner)
        self.assertIsNone(state.unlock_owner_id)
        self.assertGreater(state.generation, before_generation)
        self.assertEqual(self.harness.db.lock_writes, 0)

        retry = self.harness.actuation.set_ignition(
            True,
            "late_retry",
            ignition_stop_seconds=0,
            closed=False,
            user_id="bob-id",
        )
        self.assertFalse(retry["ok"])
        self.assertIn("face-verified unlock", retry["error"])

    def test_unacknowledged_unlock_does_not_change_persisted_state(self):
        result = self.harness.actuation.unlock(
            "Ada",
            "ada-id",
            "hardware_scan",
            auto_relock_seconds=0,
            expected_generation=self.harness.actuation.snapshot().generation,
            authorize=lambda: False,
        )

        self.assertFalse(result["ok"])
        self.assertIn("not acknowledged", result["error"])
        self.assertEqual(self.harness.commands, [])
        self.assertEqual(self.harness.db.unlock_writes, 0)
        self.assertIsNone(self.harness.actuation.snapshot().unlock_owner)

    def test_unacknowledged_ignition_does_not_start_or_arm_timer(self):
        self.assertTrue(self.harness.unlock()["ok"])
        self.harness.commands.clear()

        result = self.harness.actuation.set_ignition(
            True,
            "hardware_scan",
            ignition_stop_seconds=30,
            closed=False,
            user_id="ada-id",
            authorize=lambda: False,
        )

        self.assertFalse(result["ok"])
        self.assertIn("not acknowledged", result["error"])
        self.assertEqual(self.harness.commands, [])
        state = self.harness.actuation.snapshot()
        self.assertFalse(state.ignition_on)
        self.assertFalse(state.ignition_stop_armed)

    def test_authorized_start_rejects_an_already_running_ignition(self):
        self.assertTrue(self.harness.unlock()["ok"])
        self.assertTrue(
            self.harness.actuation.set_ignition(
                True,
                "first_scan",
                ignition_stop_seconds=0,
                closed=False,
                user_id="ada-id",
            )["ok"]
        )
        authorization_calls = 0

        def authorize() -> bool:
            nonlocal authorization_calls
            authorization_calls += 1
            return True

        result = self.harness.actuation.set_ignition(
            True,
            "second_scan",
            ignition_stop_seconds=0,
            closed=False,
            user_id="ada-id",
            authorize=authorize,
        )

        self.assertFalse(result["ok"])
        self.assertIn("already running", result["error"])
        self.assertEqual(authorization_calls, 0)


if __name__ == "__main__":
    unittest.main()
