"""Isolated recovery tests for host lifecycle orchestration."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import unittest

from wireless.host_lifecycle import HostLifecycle
from wireless.security import SecurityError
from wireless.request_receipts import request_digest


class FakeSecurity:
    @contextmanager
    def synchronized(self):
        yield


class FakeEvents:
    def __init__(self) -> None:
        self.lifecycle_lock = threading.RLock()
        self.maintenance = False

    def require_available(self) -> None:
        if self.maintenance:
            raise SecurityError("maintenance", 503, "maintenance")

    def set_maintenance(self, enabled: bool) -> None:
        self.maintenance = enabled


class FakeCommissioning:
    def __init__(self) -> None:
        self.receipt = None
        self.validate_calls = 0
        self.accept_calls = 0

    def ownership_request_digest(self, kind, body):
        return request_digest(kind, body)

    def validate_transfer_accept(self, _body):
        self.validate_calls += 1
        return {
            "completed": self.receipt is not None,
            "response": self.receipt,
        }

    def accept_transfer(self, body):
        self.accept_calls += 1
        if self.receipt is None:
            self.receipt = {
                "ok": True,
                "request_id": body["request_id"],
                "generation": 2,
            }
        return dict(self.receipt)


class FakeRuntime:
    def __init__(self) -> None:
        self.paused = False
        self.fail_quiesce = 0
        self.fail_initialize = 0
        self.calls: list[tuple[str, str | None]] = []

    def quiesce(self, reason):
        self.calls.append(("quiesce", reason))
        self.paused = True
        if self.fail_quiesce:
            self.fail_quiesce -= 1
            raise RuntimeError("drain failed")
        return {"ok": True, "maintenance": True}

    def initialize(self):
        self.calls.append(("initialize", None))
        if self.fail_initialize:
            self.fail_initialize -= 1
            raise RuntimeError("initialize failed")
        self.assert_paused_during_initialize()

    def assert_paused_during_initialize(self):
        if not self.paused:
            raise AssertionError("runtime resumed before PC initialization")

    def resume_after_maintenance(self):
        self.calls.append(("resume", None))
        self.paused = False


def lifecycle_with(runtime: FakeRuntime):
    lifecycle = HostLifecycle.__new__(HostLifecycle)
    lifecycle.security = FakeSecurity()
    lifecycle.runtime = runtime
    lifecycle.events = FakeEvents()
    lifecycle.commissioning = FakeCommissioning()
    lifecycle._pending_ownership_change = None
    return lifecycle


class HostLifecycleFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = {
            "request_id": "83670353-374d-425a-a42d-8afb737a476e",
            "transfer_secret": "a" * 64,
            "name": "Next Owner",
            "pin": "123456",
            "device_name": "New phone",
            "device_token": "b" * 64,
            "recovery_secret": "c" * 64,
        }

    def test_exact_retry_resumes_a_committed_transfer_after_reload_failure(self):
        runtime = FakeRuntime()
        runtime.fail_initialize = 1
        lifecycle = lifecycle_with(runtime)

        with self.assertRaisesRegex(SecurityError, "startup has not completed"):
            lifecycle.accept_transfer(dict(self.body))

        self.assertTrue(lifecycle.events.maintenance)
        self.assertEqual(lifecycle._pending_ownership_change,
                         request_digest("ownership_transfer", self.body))
        self.assertIsNotNone(lifecycle.commissioning.receipt)

        result = lifecycle.accept_transfer(dict(self.body))

        self.assertEqual(result, lifecycle.commissioning.receipt)
        self.assertFalse(lifecycle.events.maintenance)
        self.assertIsNone(lifecycle._pending_ownership_change)
        self.assertEqual(lifecycle.commissioning.validate_calls, 1)
        self.assertEqual(lifecycle.commissioning.accept_calls, 2)
        self.assertEqual(
            runtime.calls,
            [
                ("quiesce", "ownership_transfer"),
                ("initialize", None),
                ("quiesce", "ownership_transfer"),
                ("initialize", None),
                ("resume", None),
            ],
        )

    def test_exact_retry_repeats_a_failed_quiesce(self):
        runtime = FakeRuntime()
        runtime.fail_quiesce = 1
        lifecycle = lifecycle_with(runtime)

        with self.assertRaisesRegex(SecurityError, "has not stopped"):
            lifecycle.accept_transfer(dict(self.body))

        self.assertTrue(lifecycle.events.maintenance)
        self.assertEqual(lifecycle.commissioning.accept_calls, 0)

        result = lifecycle.accept_transfer(dict(self.body))

        self.assertTrue(result["ok"])
        self.assertFalse(lifecycle.events.maintenance)
        self.assertIsNone(lifecycle._pending_ownership_change)
        self.assertEqual(
            [call for call in runtime.calls if call[0] == "quiesce"],
            [
                ("quiesce", "ownership_transfer"),
                ("quiesce", "ownership_transfer"),
            ],
        )

    def test_different_retry_cannot_replace_a_pending_ownership_change(self):
        runtime = FakeRuntime()
        runtime.fail_quiesce = 1
        lifecycle = lifecycle_with(runtime)
        with self.assertRaises(SecurityError):
            lifecycle.accept_transfer(dict(self.body))
        different = {**self.body, "name": "Attacker"}

        with self.assertRaises(SecurityError) as rejected:
            lifecycle.accept_transfer(different)

        self.assertEqual(rejected.exception.code, "ownership_recovery_required")
        self.assertEqual(lifecycle._pending_ownership_change,
                         request_digest("ownership_transfer", self.body))
        self.assertTrue(lifecycle.events.maintenance)
        self.assertEqual(lifecycle.commissioning.accept_calls, 0)

    def test_pc_initializes_while_paused_before_runtime_resumes(self):
        runtime = FakeRuntime()
        runtime.paused = True
        lifecycle = lifecycle_with(runtime)

        lifecycle._resume_product()

        self.assertEqual(
            runtime.calls,
            [("initialize", None), ("resume", None)],
        )
        self.assertFalse(runtime.paused)


if __name__ == "__main__":
    unittest.main()
