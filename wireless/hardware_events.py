"""Authoritative semantic events shared by simulated and physical controls."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass
import math
import re
import threading
import time
from typing import Any, Callable

from .security import SecurityError


PAIRING_HOLD_SECONDS = 3.0
RECOVERY_HOLD_SECONDS = 10.0
DEFAULT_WINDOW_SECONDS = 120.0
DEFAULT_PRESS_LEASE_SECONDS = 2.0
_PRESS_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass
class _Press:
    press_id: str
    started_at: float
    last_seen_at: float


class HardwareEvents:
    """Serialize button meaning without exposing target state to clients."""

    def __init__(
        self,
        security,
        ownership_provider: Callable[[], str | dict[str, Any]],
        *,
        power_callback: Callable[[bool], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        pairing_window_seconds: float = DEFAULT_WINDOW_SECONDS,
        recovery_window_seconds: float = DEFAULT_WINDOW_SECONDS,
        press_lease_seconds: float = DEFAULT_PRESS_LEASE_SECONDS,
    ) -> None:
        if min(
            pairing_window_seconds,
            recovery_window_seconds,
            press_lease_seconds,
        ) <= 0:
            raise ValueError("hardware event timeouts must be positive")
        self._security = security
        self._ownership_provider = ownership_provider
        self._power_callback = power_callback
        self._clock = clock
        self._pairing_window_seconds = float(pairing_window_seconds)
        self._recovery_window_seconds = float(recovery_window_seconds)
        self._press_lease_seconds = float(press_lease_seconds)
        self._lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self._powered = True
        self._maintenance = False
        self._transitioning = False
        self._failed_power_target: bool | None = None
        self._press: _Press | None = None
        self._pairing_until = 0.0
        self._recovery_until = 0.0
        self._finished: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def press_down(self, press_id: str) -> dict[str, Any]:
        press_id = self._valid_press_id(press_id)
        with self._writer_lock(), self._lock:
            self._require_operational_locked()
            finished = self._finished.get(press_id)
            if finished is not None:
                return dict(finished)
            now = self._clock()
            self._expire_press_locked(now)
            if self._press is not None:
                if self._press.press_id == press_id:
                    return self._press_result("pressed", press_id)
                raise SecurityError(
                    "button_busy",
                    409,
                    "another simulated button press is active",
                )
            self._press = _Press(press_id, now, now)
            return self._press_result("pressed", press_id)

    def press_keepalive(self, press_id: str) -> dict[str, Any]:
        press_id = self._valid_press_id(press_id)
        with self._writer_lock(), self._lock:
            self._require_operational_locked()
            now = self._clock()
            self._expire_press_locked(now)
            press = self._require_press_locked(press_id)
            press.last_seen_at = now
            return self._press_result("pressed", press_id)

    def press_up(self, press_id: str) -> dict[str, Any]:
        press_id = self._valid_press_id(press_id)
        with self._writer_lock(), self._lock:
            self._require_operational_locked()
            finished = self._finished.get(press_id)
            if finished is not None:
                return dict(finished)
            now = self._clock()
            self._expire_press_locked(now)
            press = self._require_press_locked(press_id)
            held_seconds = max(0.0, now - press.started_at)
            self._press = None
            action = "none"
            if held_seconds >= RECOVERY_HOLD_SECONDS:
                action = "recovery"
                self._recovery_until = now + self._recovery_window_seconds
                self._pairing_until = 0.0
            elif held_seconds >= PAIRING_HOLD_SECONDS:
                action = "pairing"
                self._pairing_until = now + self._pairing_window_seconds
                self._recovery_until = 0.0
            result = {
                "press_id": press_id,
                "press_state": "released",
                "action": action,
                "held_seconds": round(held_seconds, 3),
            }
            self._remember_finished_locked(press_id, result)
            return dict(result)

    def press_cancel(self, press_id: str) -> dict[str, Any]:
        press_id = self._valid_press_id(press_id)
        with self._writer_lock(), self._lock:
            finished = self._finished.get(press_id)
            if finished is not None:
                return dict(finished)
            if self._press is not None and self._press.press_id == press_id:
                self._press = None
            result = {
                "press_id": press_id,
                "press_state": "cancelled",
                "action": "none",
            }
            self._remember_finished_locked(press_id, result)
            return dict(result)

    def power(self, powered: bool) -> dict[str, Any]:
        with self.lifecycle_lock:
            return self._power_locked(powered)

    def _power_locked(self, powered: bool) -> dict[str, Any]:
        if not isinstance(powered, bool):
            raise SecurityError(
                "invalid_power", 400, "powered must be a boolean"
            )
        with self._writer_lock(), self._lock:
            if self._maintenance:
                raise SecurityError(
                    "maintenance", 503, "the product is in maintenance mode"
                )
            if self._transitioning:
                raise SecurityError(
                    "power_transition",
                    409,
                    "a power transition is already active",
                )
            if (
                self._failed_power_target is not None
                and powered != self._failed_power_target
            ):
                raise SecurityError(
                    "power_recovery_required",
                    503,
                    "retry the failed power transition before changing power",
                )
            retrying_failed_transition = self._failed_power_target == powered
            if powered == self._powered and not retrying_failed_transition:
                return self._snapshot_locked()
            self._transitioning = True
            if not powered:
                self._powered = False
                self._close_windows_locked()

        try:
            if self._power_callback is not None:
                self._power_callback(powered)
        except Exception as exc:
            with self._writer_lock(), self._lock:
                self._transitioning = False
                self._failed_power_target = powered
            raise SecurityError(
                "power_transition_failed",
                503,
                "the simulated power transition failed; retry the same state",
            ) from exc

        with self._writer_lock(), self._lock:
            if powered:
                self._powered = True
            self._transitioning = False
            self._failed_power_target = None
            return self._snapshot_locked()

    def require_window(self, kind: str) -> dict[str, Any]:
        if kind not in {"pairing", "recovery"}:
            raise ValueError("window kind must be pairing or recovery")
        with self._writer_lock(), self._lock:
            self._require_operational_locked()
            now = self._clock()
            if kind == "pairing":
                deadline = self._pairing_until
            else:
                deadline = self._recovery_until
            if deadline <= now:
                raise SecurityError(
                    f"{kind}_window_required",
                    409,
                    f"open the {kind} window with the device button",
                )
            return {
                "kind": kind,
                "remaining_seconds": self._remaining(deadline, now),
            }

    def require_operational(self) -> None:
        with self._writer_lock(), self._lock:
            self._require_operational_locked()

    def require_available(self) -> None:
        """Compatibility name for the product request availability guard."""

        self.require_operational()

    def close_windows(self) -> None:
        with self._writer_lock(), self._lock:
            self._close_windows_locked()

    def set_maintenance(self, enabled: bool) -> None:
        """Fail closed before quiescing without waiting on the writer lock."""

        if not isinstance(enabled, bool):
            raise TypeError("maintenance state must be a boolean")
        with self._lock:
            self._maintenance = enabled
            if enabled:
                self._close_windows_locked()

    @property
    def maintenance(self) -> bool:
        with self._lock:
            return self._maintenance

    def snapshot(self) -> dict[str, Any]:
        with self._writer_lock(), self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        now = self._clock()
        self._expire_press_locked(now)
        ownership = self._ownership_provider()
        generation = None
        if isinstance(ownership, dict):
            generation = ownership.get("generation")
            ownership = ownership.get("ownership", "unknown")
        if not isinstance(ownership, str):
            ownership = "unknown"
        pairing_remaining = self._remaining(self._pairing_until, now)
        recovery_remaining = self._remaining(self._recovery_until, now)
        if not self._powered:
            led = "off"
        elif self._maintenance or recovery_remaining:
            led = "recovery"
        elif pairing_remaining:
            led = "pairing"
        elif ownership == "unclaimed":
            led = "unclaimed"
        else:
            led = "claimed"
        return {
            "powered": self._powered,
            "failed_power_target": self._failed_power_target,
            "maintenance": self._maintenance,
            "ownership": ownership,
            "led": led,
            "pairing_remaining_seconds": pairing_remaining,
            "recovery_remaining_seconds": recovery_remaining,
            "press_active": self._press is not None,
            "generation": generation,
        }

    def _require_operational_locked(self) -> None:
        if self._maintenance:
            raise SecurityError(
                "maintenance", 503, "the product is in maintenance mode"
            )
        if self._transitioning:
            raise SecurityError(
                "power_transition", 503, "the product power state is changing"
            )
        if not self._powered:
            raise SecurityError(
                "powered_off", 503, "the simulated product is off"
            )

    def _require_press_locked(self, press_id: str) -> _Press:
        if self._press is None or self._press.press_id != press_id:
            raise SecurityError(
                "press_not_active",
                409,
                "the simulated button press is no longer active",
            )
        return self._press

    def _expire_press_locked(self, now: float) -> None:
        if (
            self._press is not None
            and now - self._press.last_seen_at > self._press_lease_seconds
        ):
            press_id = self._press.press_id
            self._press = None
            self._remember_finished_locked(
                press_id,
                {
                    "press_id": press_id,
                    "press_state": "cancelled",
                    "action": "none",
                },
            )

    def _close_windows_locked(self) -> None:
        self._press = None
        self._pairing_until = 0.0
        self._recovery_until = 0.0

    def _remember_finished_locked(
        self, press_id: str, result: dict[str, Any]
    ) -> None:
        self._finished[press_id] = dict(result)
        self._finished.move_to_end(press_id)
        while len(self._finished) > 32:
            self._finished.popitem(last=False)

    @staticmethod
    def _press_result(state: str, press_id: str) -> dict[str, Any]:
        return {"press_id": press_id, "press_state": state, "action": "none"}

    @staticmethod
    def _valid_press_id(press_id: str) -> str:
        if not isinstance(press_id, str) or not _PRESS_ID.fullmatch(press_id):
            raise SecurityError(
                "invalid_press_id",
                400,
                "press_id must contain 16 to 128 URL-safe characters",
            )
        return press_id

    @staticmethod
    def _remaining(deadline: float, now: float) -> int:
        return max(0, math.ceil(deadline - now))

    def _writer_lock(self):
        synchronized = getattr(self._security, "synchronized", None)
        return synchronized() if synchronized is not None else nullcontext()
