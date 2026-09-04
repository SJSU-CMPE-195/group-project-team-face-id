"""Server-side validation for externally supplied input.

Every value arriving from a client passes through here before it reaches the
database, the runtime, or the hardware.  The rules are allowlists wherever a
fixed set of values exists, and explicit bounds everywhere else -- a client is
free to send anything, so nothing may be inferred from the fact that the UI
would not have sent it.

Raising ValidationError produces a 400 with a JSON body; the caller never sees
a stack trace or an HTML error page.
"""

from __future__ import annotations

from typing import Any, Iterable

# Generous enough for real names, small enough that nothing unbounded reaches
# SQLite. Audit detail is longer because it carries machine-written context.
MAX_NAME = 64
MAX_REASON = 120
MAX_DETAIL = 500
MAX_SESSION_ID = 64
MAX_CODE = 128


class ValidationError(Exception):
    """A client-supplied value that cannot be accepted."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _reject(field: str, why: str) -> None:
    raise ValidationError(f"{field} {why}")


def require_str(
    body: dict,
    key: str,
    *,
    max_len: int,
    min_len: int = 1,
    required: bool = True,
    default: str = "",
    aliases: Iterable[str] = (),
) -> str:
    """A trimmed string of bounded length.

    Rejects non-strings outright rather than coercing: ``{"name": 123}`` is a
    client bug, and silently turning it into "123" would create a user nobody
    asked for.
    """
    value = body.get(key)
    for alias in aliases:
        if value is None:
            value = body.get(alias)
    if value is None:
        if required:
            _reject(key, "is required")
        return default
    if not isinstance(value, str):
        _reject(key, "must be a string")
    value = value.strip()
    if len(value) < min_len:
        if required:
            _reject(key, "is required")
        return default
    if len(value) > max_len:
        _reject(key, f"must be at most {max_len} characters")
    if "\x00" in value:
        _reject(key, "may not contain null bytes")
    return value


def require_bool(body: dict, key: str) -> bool:
    if key not in body:
        _reject(key, "is required")
    value = body[key]
    # Deliberately strict: "false" and 0 are common client mistakes whose
    # wrong interpretation would silently grant access.
    if not isinstance(value, bool):
        _reject(key, "must be a boolean")
    return value


def require_int(
    body: dict,
    key: str,
    *,
    lo: int,
    hi: int,
    required: bool = True,
    default: int | None = None,
) -> int | None:
    if key not in body or body[key] is None:
        if required:
            _reject(key, "is required")
        return default
    value = body[key]
    # bool is an int subclass; accepting True as 1 here would hide a bug.
    if isinstance(value, bool) or not isinstance(value, int):
        _reject(key, "must be a whole number")
    if value < lo or value > hi:
        _reject(key, f"must be between {lo} and {hi}")
    return value


def one_of(value: Any, allowed: Iterable[str], field: str) -> str:
    allowed = tuple(allowed)
    if value not in allowed:
        _reject(field, f"must be one of: {', '.join(allowed)}")
    return value


def session_id(body_or_args: dict, *keys: str) -> str:
    """A session identifier from any of the accepted key spellings."""
    for key in keys:
        value = body_or_args.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            _reject(key, "must be a string")
        value = value.strip()
        if not value:
            continue
        if len(value) > MAX_SESSION_ID:
            _reject(key, f"must be at most {MAX_SESSION_ID} characters")
        return value
    _reject(keys[0], "is required")


# Settings are the one place a client can weaken the lock, so each key has an
# explicit type and range rather than being written through as a string.
SETTINGS_SCHEMA = {
    "autoRelockSeconds": ("int", 0, 3600),
    "ignitionAutoStopSeconds": ("int", 0, 3600),
    "promptAutoLockSeconds": ("int", 0, 3600),
    "lockoutAfter": ("int", 1, 100),
    "liveness": ("bool", None, None),
    "failLockout": ("bool", None, None),
}


def validate_settings(payload: dict) -> dict:
    """Keep only known keys, each within its declared type and range."""
    if not isinstance(payload, dict):
        raise ValidationError("settings must be a JSON object")
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in SETTINGS_SCHEMA:
            # Unknown keys were already dropped downstream; saying so is
            # friendlier than silently discarding a typo'd setting.
            _reject(key, "is not a known setting")
        kind, lo, hi = SETTINGS_SCHEMA[key]
        if kind == "bool":
            if not isinstance(value, bool):
                _reject(key, "must be a boolean")
            cleaned[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int):
                _reject(key, "must be a whole number")
            if value < lo or value > hi:
                _reject(key, f"must be between {lo} and {hi}")
            cleaned[key] = value
    return cleaned
