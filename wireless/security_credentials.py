"""Credential types and cryptographic primitives for wireless security."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
import sqlite3
from typing import Any


PIN_ITERATIONS = 600_000
PIN_SALT_BYTES = 16
PIN_VERIFIER_BYTES = 32
PIN_MAX_FAILURES = 5
PIN_LOCKOUT_SECONDS = 60

ADMIN_ACTIONS = frozenset(
    {
        "user.create",
        "user.delete",
        "user.access",
        "enrollment.start",
        "settings.update",
        "device.revoke",
        "pairing.invite",
        "user.pin",
    }
)
OWN_USER_ACTIONS = frozenset(
    {
        "scan.unlock",
        "scan.ignition",
        "device.lock",
        "ignition.stop",
        "device.reset",
    }
)
ALLOWED_ACTIONS = ADMIN_ACTIONS | OWN_USER_ACTIONS


@dataclass(frozen=True)
class Principal:
    device_id: str
    user_id: str
    name: str
    is_admin: bool
    auth_version: int
    is_owner: bool = False


class SecurityError(RuntimeError):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code = code
        self.status = status
        self.status_code = status
        self.message = message


def bounded_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise SecurityError(f"invalid_{field}", 400, f"{field} must be a string")
    value = value.strip()
    if not value or len(value) > limit or not value.isprintable():
        raise SecurityError(
            f"invalid_{field}",
            400,
            f"{field} must contain 1 to {limit} printable characters",
        )
    return value


def validate_pin(pin: Any) -> None:
    valid = (
        isinstance(pin, str)
        and len(pin) == 6
        and pin.isascii()
        and pin.isdigit()
    )
    if not valid:
        raise SecurityError(
            "invalid_pin_format",
            400,
            "PIN must contain exactly six ASCII digits",
        )


def derive_pin(pin: str, salt: bytes, pepper: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("ascii") + pepper,
        salt,
        iterations,
        PIN_VERIFIER_BYTES,
    )


def new_pin_record(
    pin: str,
    pepper: bytes,
    iterations: int,
) -> tuple[bytes, bytes]:
    salt = secrets.token_bytes(PIN_SALT_BYTES)
    return salt, derive_pin(pin, salt, pepper, iterations)


def pin_matches(
    pin: str,
    salt: bytes,
    verifier: bytes,
    pepper: bytes,
    iterations: int,
) -> bool:
    candidate = derive_pin(pin, salt, pepper, iterations)
    return hmac.compare_digest(candidate, verifier)


def verify_pin(
    conn: sqlite3.Connection,
    row: Any,
    pin: str,
    pepper: bytes,
    iterations: int,
    now: int,
) -> SecurityError | None:
    if row is None:
        return SecurityError("invalid_credentials", 401, "invalid user name or PIN")
    if int(row["lockout_until"]) > now:
        remaining = int(row["lockout_until"]) - now
        return SecurityError(
            "pin_locked",
            423,
            f"PIN is locked for {remaining} more seconds",
        )
    if pin_matches(
        pin,
        bytes(row["pin_salt"]),
        bytes(row["pin_verifier"]),
        pepper,
        iterations,
    ):
        conn.execute(
            "UPDATE user_security SET failed_attempts=0, lockout_until=0, "
            "updated_at=? WHERE user_id=?",
            (now, row["id"]),
        )
        return None
    failures = int(row["failed_attempts"]) + 1
    if failures >= PIN_MAX_FAILURES:
        conn.execute(
            "UPDATE user_security SET failed_attempts=0, lockout_until=?, "
            "updated_at=? WHERE user_id=?",
            (now + PIN_LOCKOUT_SECONDS, now, row["id"]),
        )
        return SecurityError(
            "pin_locked",
            423,
            f"PIN is locked for {PIN_LOCKOUT_SECONDS} seconds",
        )
    conn.execute(
        "UPDATE user_security SET failed_attempts=?, updated_at=? WHERE user_id=?",
        (failures, now, row["id"]),
    )
    return SecurityError("invalid_credentials", 401, "invalid user name or PIN")


def token_hash(token: Any) -> bytes:
    if (
        not isinstance(token, str)
        or len(token) != 64
        or any(character not in "0123456789abcdef" for character in token)
    ):
        raise SecurityError("invalid_token", 401, "security token is invalid")
    return hashlib.sha256(bytes.fromhex(token)).digest()


def optional_target(target: Any) -> str | None:
    if target is None:
        return None
    if not isinstance(target, str):
        raise SecurityError("invalid_target", 400, "target must be a string")
    target = target.strip()
    if not target:
        return None
    if len(target) > 128 or not target.isprintable():
        raise SecurityError("invalid_target", 400, "target is invalid")
    return target


def validate_action(action: Any) -> str:
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        raise SecurityError("invalid_action", 400, "operation action is invalid")
    return action


def user_payload(
    user_id: str,
    name: str,
    is_admin: bool,
    is_owner: bool = False,
) -> dict[str, Any]:
    return {
        "id": user_id,
        "name": name,
        "is_admin": bool(is_admin),
        "is_owner": bool(is_owner),
    }


def created_user_payload(
    user_id: str,
    name: str,
    is_admin: bool,
    created_at: int,
    is_owner: bool = False,
) -> dict[str, Any]:
    return {
        **user_payload(user_id, name, is_admin, is_owner),
        "createdAt": created_at * 1000,
        "faceAccess": True,
    }


def principal_row_active(row: Any, now: int) -> bool:
    expires_at = row["expires_at"]
    return bool(
        row["device_active"]
        and row["user_active"]
        and (expires_at is None or int(expires_at) > now)
    )


def principal_from_row(row: Any) -> Principal:
    return Principal(
        device_id=row["device_id"],
        user_id=row["user_id"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
        auth_version=int(row["auth_version"]),
        is_owner=bool(row["is_owner"]),
    )
