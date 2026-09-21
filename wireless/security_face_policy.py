"""Persistent face-verification failure policy."""

from __future__ import annotations

import sqlite3
from typing import Any

from .security_credentials import SecurityError


FACE_LOCKOUT_SECONDS = 60
MIN_FACE_FAILURE_LIMIT = 1
MAX_FACE_FAILURE_LIMIT = 20


def validate_face_result(matched: bool, limit: int) -> None:
    if not isinstance(matched, bool):
        raise SecurityError(
            "invalid_face_result",
            400,
            "face result must be matched or not matched",
        )
    valid_limit = (
        not isinstance(limit, bool)
        and isinstance(limit, int)
        and MIN_FACE_FAILURE_LIMIT <= limit <= MAX_FACE_FAILURE_LIMIT
    )
    if not valid_limit:
        raise SecurityError(
            "invalid_face_failure_limit",
            400,
            "face failure limit must be between 1 and 20",
        )


def require_face_attempt_allowed(row: Any, now: int) -> None:
    if not row["face_access"]:
        raise SecurityError(
            "face_access_denied",
            403,
            "face access is disabled",
        )
    lockout_until = int(row["face_lockout_until"])
    if lockout_until > now:
        remaining = lockout_until - now
        raise SecurityError(
            "face_locked",
            423,
            f"face verification is locked for {remaining} more seconds",
        )


def apply_face_result(
    conn: sqlite3.Connection,
    user_id: str,
    row: Any,
    matched: bool,
    limit: int,
    now: int,
) -> tuple[str, str]:
    if matched:
        conn.execute(
            "UPDATE user_security SET face_failed_attempts=0, "
            "face_lockout_until=0, updated_at=? WHERE user_id=?",
            (now, user_id),
        )
        return "ok", "Face matched; failure counter reset"

    failures = int(row["face_failed_attempts"]) + 1
    if failures >= limit:
        conn.execute(
            "UPDATE user_security SET face_failed_attempts=0, "
            "face_lockout_until=?, updated_at=? WHERE user_id=?",
            (now + FACE_LOCKOUT_SECONDS, now, user_id),
        )
        return (
            "denied",
            f"Face mismatch; locked for {FACE_LOCKOUT_SECONDS} seconds",
        )

    conn.execute(
        "UPDATE user_security SET face_failed_attempts=?, "
        "face_lockout_until=0, updated_at=? WHERE user_id=?",
        (failures, now, user_id),
    )
    return "denied", f"Face mismatch; failure {failures} of {limit}"
