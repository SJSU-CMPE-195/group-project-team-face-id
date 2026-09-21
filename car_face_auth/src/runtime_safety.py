"""Pure validation for persisted runtime safety settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from safety_policy import INTEGER_SETTING_RANGES


@dataclass(frozen=True)
class RuntimeSafetySettings:
    auto_relock_seconds: int
    ignition_stop_seconds: int
    liveness: bool
    fail_lockout: bool
    lockout_after: int


def validate_runtime_safety_settings(
    settings: Any,
    *,
    require_complete: bool,
    liveness_available: bool,
) -> RuntimeSafetySettings:
    if not isinstance(settings, dict):
        raise ValueError("settings response is invalid")

    defaults = {} if require_complete else {
        "autoRelockSeconds": 0,
        "ignitionAutoStopSeconds": 0,
        "liveness": False,
        "failLockout": False,
        "lockoutAfter": 5,
    }

    def setting(name: str) -> Any:
        if name in settings:
            return settings[name]
        if name in defaults:
            return defaults[name]
        raise ValueError(f"{name} setting is missing")

    liveness = setting("liveness")
    if not isinstance(liveness, bool):
        raise ValueError("liveness setting is invalid")
    fail_lockout = setting("failLockout")
    if not isinstance(fail_lockout, bool):
        raise ValueError("failLockout setting is invalid")

    auto_relock = _validated_integer(
        setting("autoRelockSeconds"),
        "autoRelockSeconds",
    )
    ignition_stop = _validated_integer(
        setting("ignitionAutoStopSeconds"),
        "ignitionAutoStopSeconds",
    )
    lockout_after = _validated_integer(
        setting("lockoutAfter"),
        "lockoutAfter",
    )
    if liveness and not liveness_available:
        raise ValueError(
            "liveness verification is enabled but presentation-attack "
            "detection is unavailable"
        )

    return RuntimeSafetySettings(
        auto_relock_seconds=auto_relock,
        ignition_stop_seconds=ignition_stop,
        liveness=liveness,
        fail_lockout=fail_lockout,
        lockout_after=lockout_after,
    )


def _validated_integer(
    value: Any,
    name: str,
) -> int:
    minimum, maximum = INTEGER_SETTING_RANGES[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} setting is invalid")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} setting is outside the supported range")
    return value
