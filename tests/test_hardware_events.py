"""Isolated semantic button and simulated power tests."""

from contextlib import contextmanager
import threading
import unittest

from wireless.hardware_events import HardwareEvents
from wireless.security import SecurityError


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class _Security:
    def __init__(self):
        self.lock = threading.RLock()

    @contextmanager
    def synchronized(self):
        with self.lock:
            yield


class HardwareEventsTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.ownership = {"ownership": "unclaimed", "generation": 2}
        self.power_calls = []
        self.events = HardwareEvents(
            _Security(),
            lambda: dict(self.ownership),
            power_callback=self.power_calls.append,
            clock=self.clock,
        )

    def test_release_triggers_exactly_one_window_from_server_time(self):
        pairing_id = "pairing_press_001"
        self.events.press_down(pairing_id)
        self.clock.advance(1.5)
        self.events.press_keepalive(pairing_id)
        self.clock.advance(1.5)
        self.events.press_keepalive(pairing_id)
        result = self.events.press_up(pairing_id)
        self.assertEqual(result["action"], "pairing")
        self.assertEqual(
            self.events.require_window("pairing")["remaining_seconds"], 120
        )
        self.assertEqual(self.events.press_up(pairing_id), result)

        recovery_id = "recovery_press_01"
        self.events.press_down(recovery_id)
        for _ in range(5):
            self.clock.advance(2)
            self.events.press_keepalive(recovery_id)
        result = self.events.press_up(recovery_id)
        self.assertEqual(result["action"], "recovery")
        with self.assertRaises(SecurityError) as pairing_closed:
            self.events.require_window("pairing")
        self.assertEqual(
            pairing_closed.exception.code, "pairing_window_required"
        )

    def test_lost_keepalive_cancels_without_opening_a_window(self):
        press_id = "expired_press_001"
        self.events.press_down(press_id)
        self.clock.advance(2.001)
        with self.assertRaises(SecurityError) as expired:
            self.events.press_up(press_id)
        self.assertEqual(expired.exception.code, "press_not_active")
        self.assertFalse(self.events.snapshot()["press_active"])
        self.assertEqual(self.events.snapshot()["pairing_remaining_seconds"], 0)

    def test_power_off_closes_windows_and_denies_product_operations(self):
        press_id = "poweroff_press_1"
        self.events.press_down(press_id)
        self.clock.advance(1.5)
        self.events.press_keepalive(press_id)
        self.clock.advance(1.5)
        self.events.press_keepalive(press_id)
        self.events.press_up(press_id)
        status = self.events.power(False)
        self.assertEqual(status["led"], "off")
        self.assertEqual(status["pairing_remaining_seconds"], 0)
        with self.assertRaises(SecurityError) as powered_off:
            self.events.require_available()
        self.assertEqual(powered_off.exception.code, "powered_off")
        self.events.power(True)
        self.assertEqual(self.power_calls, [False, True])
        self.assertEqual(self.events.snapshot()["led"], "unclaimed")

    def test_maintenance_is_derived_and_fail_closed(self):
        self.events.set_maintenance(True)
        self.assertEqual(self.events.snapshot()["led"], "recovery")
        with self.assertRaises(SecurityError) as maintenance:
            self.events.press_down("maintenance_press")
        self.assertEqual(maintenance.exception.code, "maintenance")

    def test_failed_power_off_remains_unavailable_and_retries_quiesce(self):
        calls = []

        def fail_once(powered):
            calls.append(powered)
            if len(calls) == 1:
                raise RuntimeError("worker did not drain")

        events = HardwareEvents(
            _Security(),
            lambda: dict(self.ownership),
            power_callback=fail_once,
            clock=self.clock,
        )
        with self.assertRaises(SecurityError) as failed:
            events.power(False)
        self.assertEqual(failed.exception.code, "power_transition_failed")
        self.assertIs(events.snapshot()["failed_power_target"], False)
        with self.assertRaises(SecurityError) as unavailable:
            events.require_available()
        self.assertEqual(unavailable.exception.code, "powered_off")
        with self.assertRaises(SecurityError) as wrong_direction:
            events.power(True)
        self.assertEqual(
            wrong_direction.exception.code, "power_recovery_required"
        )
        self.assertFalse(events.power(False)["powered"])
        self.assertIsNone(events.snapshot()["failed_power_target"])
        self.assertEqual(calls, [False, False])


if __name__ == "__main__":
    unittest.main()
