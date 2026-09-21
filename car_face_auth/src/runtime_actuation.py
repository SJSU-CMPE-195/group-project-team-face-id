"""Atomic actuator commands, reported state, ownership, and safety timers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Iterator


CommandSender = Callable[..., bool]
EventLogger = Callable[[str, str, str, str | None], None]


@dataclass(frozen=True)
class ActuationSnapshot:
    ignition_on: bool
    unlock_owner: str | None
    unlock_owner_id: str | None
    generation: int
    auto_relock_armed: bool
    ignition_stop_armed: bool


class RuntimeActuation:
    """Serialize command dispatch with the database state it represents."""

    def __init__(
        self,
        db_api: Any,
        send_command: CommandSender,
        log_event: EventLogger,
    ) -> None:
        self._db = db_api
        self._send_command = send_command
        self._log = log_event
        self._lock = threading.RLock()
        self._ignition_on = False
        self._unlock_owner: str | None = None
        self._unlock_owner_id: str | None = None
        self._generation = 0
        self._auto_relock_timer: threading.Timer | None = None
        self._ignition_stop_timer: threading.Timer | None = None
        self._maintenance_requested = threading.Event()
        self._maintenance = False

    @contextmanager
    def synchronized(self) -> Iterator[None]:
        with self._lock:
            yield

    def snapshot(self) -> ActuationSnapshot:
        with self._lock:
            return ActuationSnapshot(
                ignition_on=self._ignition_on,
                unlock_owner=self._unlock_owner,
                unlock_owner_id=self._unlock_owner_id,
                generation=self._generation,
                auto_relock_armed=self._auto_relock_timer is not None,
                ignition_stop_armed=self._ignition_stop_timer is not None,
            )

    def set_ignition(
        self,
        running: bool,
        reason: str,
        *,
        ignition_stop_seconds: int,
        closed: bool,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        running = bool(running)
        with self._lock:
            if self._maintenance_requested.is_set() or self._maintenance:
                return self._error("Pi runtime is in maintenance")
            if closed:
                return self._error("Pi runtime is shutting down")
            if (
                expected_generation is not None
                and self._generation != expected_generation
            ):
                return self._error(
                    "Actuation authorization changed before ignition"
                )
            if running:
                if not self._unlock_owner:
                    return self._error(
                        "a face-verified unlock is required before ignition"
                    )
                try:
                    if self._db.get_status().get("lockState") == "locked":
                        return self._error("device is locked")
                except Exception as exc:
                    return self._error(f"could not read device state: {exc}")
                if self._ignition_on:
                    return {
                        "ok": True,
                        "ignitionOn": True,
                        "command_sent": False,
                        "physical_state_confirmed": False,
                    }
                try:
                    self._arm_ignition_stop(ignition_stop_seconds)
                except RuntimeError as exc:
                    return self._error(str(exc))

            command = "START" if running else "STOP"
            if not self._send_command(command):
                if running:
                    self._cancel_ignition_stop()
                return self._error(f"{command} command was not sent")

            self._ignition_on = running
            if not running:
                self._cancel_ignition_stop()
            action = "start" if running else "stop"
            self._log("ignition", "ok", f"{action}:{reason}", None)
            return {
                "ok": True,
                "ignitionOn": running,
                "command_sent": True,
                "physical_state_confirmed": False,
            }

    def unlock(
        self,
        user_name: str,
        user_id: str | None,
        reason: str,
        *,
        auto_relock_seconds: int,
        expected_generation: int,
    ) -> dict[str, Any]:
        with self._lock:
            if self._maintenance_requested.is_set() or self._maintenance:
                return self._error("Pi runtime is in maintenance")
            if self._generation != expected_generation:
                return self._error("Actuation authorization changed before unlock")
            try:
                self._arm_auto_relock(auto_relock_seconds)
            except RuntimeError as exc:
                return self._error(str(exc))
            if not self._send_command("UNLOCK"):
                self._cancel_auto_relock()
                return self._error("UNLOCK command was not sent")
            try:
                self._db.set_unlock(reason=reason)
            except Exception as exc:
                compensating_lock_sent = self._send_command(
                    "LOCK",
                    connect=False,
                )
                if compensating_lock_sent:
                    self._cancel_auto_relock()
                self._clear_owner()
                self._generation += 1
                auto_relock_armed = self._auto_relock_timer is not None
                error = f"unlock state could not be saved: {exc}"
                if not compensating_lock_sent:
                    error += (
                        "; compensating LOCK command was not sent; "
                        "physical state is unknown"
                    )
                return {
                    "ok": False,
                    "error": error,
                    "command_sent": True,
                    "unlock_command_sent": True,
                    "compensating_lock_command_sent": bool(
                        compensating_lock_sent
                    ),
                    "auto_relock_armed": auto_relock_armed,
                    "physical_state_confirmed": False,
                }

            self._unlock_owner = user_name
            self._unlock_owner_id = user_id
            self._generation += 1
            self._log("face_scan", "ok", f"Granted: {user_name}", user_id)
            return {
                "ok": True,
                "command_sent": True,
                "physical_state_confirmed": False,
            }

    def force_lock(self, reason: str) -> dict[str, Any]:
        with self._lock:
            if self._maintenance_requested.is_set() or self._maintenance:
                return self._error("Pi runtime is in maintenance")
            return self._force_lock(reason)

    def quiesce(self, reason: str, timeout: float) -> dict[str, Any]:
        """Stop safety timers and leave actuation locked in maintenance."""

        deadline = time.monotonic() + max(0.0, timeout)
        self._maintenance_requested.set()
        remaining = max(0.0, deadline - time.monotonic())
        if not self._lock.acquire(timeout=remaining):
            raise RuntimeError("actuation did not enter maintenance before timeout")
        timers: tuple[threading.Timer | None, threading.Timer | None]
        result: dict[str, Any] = {}
        dispatch_error: Exception | None = None
        try:
            self._maintenance = True
            timers = (self._auto_relock_timer, self._ignition_stop_timer)
            self._auto_relock_timer = None
            self._ignition_stop_timer = None
            for timer in timers:
                if timer:
                    timer.cancel()
            try:
                result = self._force_lock(reason)
            except Exception as exc:
                dispatch_error = exc
        finally:
            self._lock.release()

        timer_error = False
        for timer in timers:
            if not timer or timer is threading.current_thread():
                continue
            timer.join(max(0.0, deadline - time.monotonic()))
            if timer.is_alive():
                timer_error = True
        if timer_error:
            raise RuntimeError("actuation timer did not stop before timeout")
        if dispatch_error is not None:
            raise RuntimeError(
                f"actuation could not be locked: {dispatch_error}"
            ) from dispatch_error
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "actuation could not be locked")
        if time.monotonic() > deadline:
            raise RuntimeError("actuation did not enter maintenance before timeout")
        return result

    def resume_after_maintenance(self) -> None:
        with self._lock:
            self._cancel_auto_relock()
            self._cancel_ignition_stop()
            self._mark_locked()
            self._generation += 1
            self._maintenance = False
            self._maintenance_requested.clear()

    def shutdown(self, had_hardware: bool) -> None:
        self._maintenance_requested.set()
        with self._lock:
            self._maintenance = True
            self._cancel_auto_relock()
            self._cancel_ignition_stop()
            self._generation += 1
            if not had_hardware:
                return
            result = self._dispatch_lock("shutdown")
            if result.get("ok"):
                self._mark_locked()

    def invalidate_authorization(self, user_name: str | None = None) -> bool:
        with self._lock:
            if (
                user_name
                and self._unlock_owner
                and user_name.casefold() != self._unlock_owner.casefold()
            ):
                return False
            self._clear_owner()
            self._generation += 1
            return True

    def _force_lock(self, reason: str) -> dict[str, Any]:
        self._generation += 1
        result = self._dispatch_lock(reason)
        if result.get("ok"):
            self._cancel_auto_relock()
            self._cancel_ignition_stop()
            self._mark_locked()
        return result

    def _dispatch_lock(self, reason: str) -> dict[str, Any]:
        stop_ok = self._send_command("STOP")
        lock_ok = self._send_command("LOCK")
        result = {
            "command_sent": bool(stop_ok and lock_ok),
            "stop_command_sent": bool(stop_ok),
            "lock_command_sent": bool(lock_ok),
            "physical_state_confirmed": False,
        }
        if not stop_ok or not lock_ok:
            failed = [
                command
                for command, sent in (("STOP", stop_ok), ("LOCK", lock_ok))
                if not sent
            ]
            return {
                **result,
                "ok": False,
                "error": f"{', '.join(failed)} command was not sent",
                "locked": False,
            }
        try:
            self._db.set_lock(reason=reason)
        except Exception as exc:
            return {
                **result,
                "ok": False,
                "error": f"lock state could not be saved: {exc}",
                "locked": False,
            }
        return {**result, "ok": True, "locked": True}

    def _arm_auto_relock(self, seconds: int) -> None:
        if seconds <= 0:
            self._cancel_auto_relock()
            return
        previous_timer = self._auto_relock_timer
        timer = None

        def relock_if_current() -> None:
            with self._lock:
                if self._auto_relock_timer is not timer:
                    return
                self._auto_relock_timer = None
                if self._maintenance_requested.is_set() or self._maintenance:
                    return
                self._force_lock(f"auto_relock_{seconds}s")

        if self._maintenance_requested.is_set() or self._maintenance:
            raise RuntimeError("Pi runtime is in maintenance")
        timer = threading.Timer(seconds, relock_if_current)
        timer.daemon = True
        try:
            timer.start()
        except Exception as exc:
            timer.cancel()
            raise RuntimeError(
                f"auto-relock timer could not be started: {exc}"
            ) from exc
        self._auto_relock_timer = timer
        if previous_timer:
            previous_timer.cancel()

    def _arm_ignition_stop(self, seconds: int) -> None:
        if seconds <= 0:
            self._cancel_ignition_stop()
            return
        previous_timer = self._ignition_stop_timer
        timer = None

        def stop_if_current() -> None:
            with self._lock:
                if self._ignition_stop_timer is not timer:
                    return
                self._ignition_stop_timer = None
                if self._maintenance_requested.is_set() or self._maintenance:
                    return
                self.set_ignition(
                    False,
                    f"ignition_timeout_{seconds}s",
                    ignition_stop_seconds=0,
                    closed=False,
                )

        if self._maintenance_requested.is_set() or self._maintenance:
            raise RuntimeError("Pi runtime is in maintenance")
        timer = threading.Timer(seconds, stop_if_current)
        timer.daemon = True
        try:
            timer.start()
        except Exception as exc:
            timer.cancel()
            raise RuntimeError(
                f"ignition-stop timer could not be started: {exc}"
            ) from exc
        self._ignition_stop_timer = timer
        if previous_timer:
            previous_timer.cancel()

    def _cancel_auto_relock(self) -> None:
        timer, self._auto_relock_timer = self._auto_relock_timer, None
        if timer:
            timer.cancel()

    def _cancel_ignition_stop(self) -> None:
        timer, self._ignition_stop_timer = self._ignition_stop_timer, None
        if timer:
            timer.cancel()

    def _clear_owner(self) -> None:
        self._unlock_owner = None
        self._unlock_owner_id = None

    def _mark_locked(self) -> None:
        self._ignition_on = False
        self._clear_owner()

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": message,
            "command_sent": False,
            "physical_state_confirmed": False,
        }
