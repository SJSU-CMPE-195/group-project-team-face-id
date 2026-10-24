"""Durable, device-owned face-enrollment request receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from typing import Any, Mapping
import uuid

from .security_credentials import (
    Principal,
    SecurityError,
    principal_from_row,
    principal_row_active,
)


_ACTION = "enrollment.start"
_API_VERSION = 4
_PENDING_STATES = frozenset({"accepted", "active"})
_FINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})


class EnrollmentRequestStore:
    """Bind one accepted enrollment to one product generation and device."""

    def __init__(self, security: Any):
        self.security = security

    def reserve(
        self,
        principal: Principal,
        body: Mapping[str, Any],
        grant_token: str,
    ) -> dict[str, Any]:
        now = self._now()
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            current = principal_from_row(
                self.security._require_principal(conn, principal, now)
            )
            values = self._request_values(body)
            self.security._require_admin_or_self_target(
                current,
                _ACTION,
                values["user_id"],
            )
            self.security._require_owner_target_control(
                conn,
                current,
                _ACTION,
                values["user_id"],
            )
            digest = self._request_digest(values)
            generation = self._generation(conn)
            existing = self._owned_row(
                conn,
                generation,
                current.device_id,
                values["request_id"],
            )
            if existing is not None:
                self._require_same_request(existing, digest)
                return self._reservation(existing, created=False)

            target = self.security._security_user(conn, values["user_id"])
            if target is None:
                raise SecurityError(
                    "invalid_user",
                    400,
                    "Select one existing user for enrollment.",
                )
            self.security._consume_grant_in_transaction(
                conn,
                current,
                grant_token,
                _ACTION,
                values["user_id"],
                now,
            )
            session_id = f"enroll_{uuid.uuid4()}"
            response = {
                "request_id": values["request_id"],
                "session_id": session_id,
                "status_url": (
                    f"/api/enroll/requests/{values['request_id']}"
                ),
            }
            conn.execute(
                "INSERT INTO enrollment_requests "
                "(generation,device_id,action,request_id,actor_user_id,"
                "actor_auth_version,target_user_id,target_auth_version,"
                "body_digest,session_id,response_json,state,result_json,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'accepted',NULL,?,?)",
                (
                    generation,
                    current.device_id,
                    _ACTION,
                    values["request_id"],
                    current.user_id,
                    current.auth_version,
                    values["user_id"],
                    int(target["auth_version"]),
                    digest,
                    session_id,
                    self._json(response),
                    now,
                    now,
                ),
            )
            row = self._require_session_row(conn, session_id)
            return self._reservation(row, created=True)

    def query(self, principal: Principal, request_id: Any) -> dict[str, Any]:
        now = self._now()
        with self.security.synchronized(), self.security.get_connection() as conn:
            current = principal_from_row(
                self.security._require_principal(conn, principal, now)
            )
            canonical_id = self._canonical_request_id(request_id)
            row = self._owned_row(
                conn,
                self._generation(conn),
                current.device_id,
                canonical_id,
            )
            if row is None:
                raise SecurityError(
                    "enrollment_request_not_found",
                    404,
                    "Enrollment request was not found.",
                )
            if row["actor_user_id"] != current.user_id:
                raise SecurityError(
                    "enrollment_request_not_found",
                    404,
                    "Enrollment request was not found.",
                )
            return self._projection(row)

    def activate(self, session_id: str) -> dict[str, Any]:
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            row = self._require_session_row(conn, session_id)
            activated = False
            if row["state"] == "accepted":
                now = self._now()
                self._require_completion_authorization(conn, row, now)
                updated = conn.execute(
                    "UPDATE enrollment_requests SET state='active',updated_at=? "
                    "WHERE session_id=? AND state='accepted'",
                    (now, session_id),
                )
                activated = updated.rowcount == 1
                row = self._require_session_row(conn, session_id)
            return {"activated": activated, **self._projection(row)}

    def finalize(
        self,
        session_id: str,
        state: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if state not in _FINAL_STATES - {"completed"}:
            raise ValueError("enrollment terminal state is invalid")
        result_json = self._result_json(result)
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            row = self._require_session_row(conn, session_id)
            if row["state"] in _PENDING_STATES:
                now = self._now()
                conn.execute(
                    "UPDATE enrollment_requests SET state=?,result_json=?,"
                    "updated_at=? WHERE session_id=? AND state IN ('accepted','active')",
                    (state, result_json, now, session_id),
                )
                row = self._require_session_row(conn, session_id)
            return self._projection(row)

    def complete(
        self,
        session_id: str,
        face_encoding: bytes,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(face_encoding, bytes) or not face_encoding:
            raise ValueError("face encoding must be non-empty bytes")
        result_json = self._result_json(result)
        now = self._now()
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            row = self._require_session_row(conn, session_id)
            if row["state"] in _FINAL_STATES:
                return self._projection(row)
            if row["state"] != "active":
                raise SecurityError(
                    "enrollment_not_active",
                    409,
                    "Enrollment request is not active.",
                )
            self._require_completion_authorization(conn, row, now)
            updated = conn.execute(
                "UPDATE users SET face_encoding=? WHERE id=? AND active=1",
                (face_encoding, row["target_user_id"]),
            )
            if updated.rowcount != 1:
                raise SecurityError(
                    "enrollment_authorization_expired",
                    403,
                    "Enrollment authorization is no longer valid.",
                )
            self.security._audit(
                conn,
                row["target_user_id"],
                "enroll_embedding",
                "ok",
                "Face template stored",
                actor_user_id=row["actor_user_id"],
            )
            receipt_updated = conn.execute(
                "UPDATE enrollment_requests SET state='completed',result_json=?,"
                "updated_at=? WHERE session_id=? AND state='active'",
                (result_json, now, session_id),
            )
            if receipt_updated.rowcount != 1:
                raise RuntimeError("enrollment completion receipt update failed")
            return self._projection(self._require_session_row(conn, session_id))

    def interrupt_pending(self) -> int:
        now = self._now()
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            rows = conn.execute(
                "SELECT session_id,request_id FROM enrollment_requests "
                "WHERE state IN ('accepted','active')"
            ).fetchall()
            for row in rows:
                result = {
                    "ok": False,
                    "request_id": row["request_id"],
                    "session_id": row["session_id"],
                    "state": "interrupted",
                    "message": "Enrollment was interrupted before completion.",
                }
                conn.execute(
                    "UPDATE enrollment_requests SET state='interrupted',"
                    "result_json=?,updated_at=? WHERE session_id=? "
                    "AND state IN ('accepted','active')",
                    (self._json(result), now, row["session_id"]),
                )
            return len(rows)

    def _require_completion_authorization(
        self,
        conn: sqlite3.Connection,
        receipt: sqlite3.Row,
        now: int,
    ) -> None:
        if self._generation(conn) != int(receipt["generation"]):
            self._authorization_expired()
        actor_row = conn.execute(
            self.security._principal_query() + " WHERE d.id=? AND d.user_id=?",
            (receipt["device_id"], receipt["actor_user_id"]),
        ).fetchone()
        if (
            actor_row is None
            or not principal_row_active(actor_row, now)
            or int(actor_row["auth_version"])
            != int(receipt["actor_auth_version"])
        ):
            self._authorization_expired()
        current = principal_from_row(actor_row)
        target = self.security._security_user(conn, receipt["target_user_id"])
        if (
            target is None
            or int(target["auth_version"])
            != int(receipt["target_auth_version"])
        ):
            self._authorization_expired()
        self.security._require_admin_or_self_target(
            current,
            _ACTION,
            receipt["target_user_id"],
        )
        self.security._require_owner_target_control(
            conn,
            current,
            _ACTION,
            receipt["target_user_id"],
        )

    @staticmethod
    def _authorization_expired() -> None:
        raise SecurityError(
            "enrollment_authorization_expired",
            403,
            "Enrollment authorization is no longer valid.",
        )

    @staticmethod
    def _request_values(body: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(body, Mapping) or set(body) != {
            "request_id",
            "user_id",
            "source",
        }:
            raise SecurityError(
                "invalid_request",
                400,
                "Only request_id, user_id, and source are allowed.",
            )
        request_id = EnrollmentRequestStore._canonical_request_id(
            body.get("request_id")
        )
        user_id = body.get("user_id")
        if (
            not isinstance(user_id, str)
            or not user_id
            or len(user_id) > 128
            or user_id != user_id.strip()
            or not user_id.isprintable()
        ):
            raise SecurityError(
                "invalid_user",
                400,
                "Select one existing user for enrollment.",
            )
        if body.get("source") != "device_camera":
            raise SecurityError(
                "invalid_source",
                400,
                "source must be device_camera",
            )
        return {
            "request_id": request_id,
            "user_id": user_id,
            "source": "device_camera",
        }

    @staticmethod
    def _canonical_request_id(value: Any) -> str:
        if not isinstance(value, str):
            raise SecurityError(
                "invalid_request_id",
                400,
                "request_id must be a canonical UUID v4",
            )
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise SecurityError(
                "invalid_request_id",
                400,
                "request_id must be a canonical UUID v4",
            ) from exc
        if parsed.version != 4 or str(parsed) != value:
            raise SecurityError(
                "invalid_request_id",
                400,
                "request_id must be a canonical UUID v4",
            )
        return value

    @staticmethod
    def _request_digest(values: Mapping[str, str]) -> bytes:
        payload = {
            "api_version": _API_VERSION,
            "source": values["source"],
            "user_id": values["user_id"],
        }
        return hashlib.sha256(
            EnrollmentRequestStore._json(payload).encode("utf-8")
        ).digest()

    @staticmethod
    def _generation(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT generation FROM commissioning_state WHERE id=1"
        ).fetchone()
        if row is None:
            raise SecurityError(
                "commissioning_unavailable",
                503,
                "commissioning state is unavailable",
            )
        return int(row["generation"])

    @staticmethod
    def _owned_row(
        conn: sqlite3.Connection,
        generation: int,
        device_id: str,
        request_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM enrollment_requests WHERE generation=? AND device_id=? "
            "AND action=? AND request_id=?",
            (generation, device_id, _ACTION, request_id),
        ).fetchone()

    @staticmethod
    def _session_row(
        conn: sqlite3.Connection,
        session_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM enrollment_requests WHERE session_id=?",
            (session_id,),
        ).fetchone()

    def _require_session_row(
        self,
        conn: sqlite3.Connection,
        session_id: str,
    ) -> sqlite3.Row:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("enrollment session ID is required")
        row = self._session_row(conn, session_id)
        if row is None:
            raise SecurityError(
                "enrollment_request_not_found",
                404,
                "Enrollment request was not found.",
            )
        return row

    @staticmethod
    def _require_same_request(row: sqlite3.Row, digest: bytes) -> None:
        if not hmac.compare_digest(bytes(row["body_digest"]), digest):
            raise SecurityError(
                "request_id_reused",
                422,
                "request_id was already used for different request data",
            )

    @staticmethod
    def _reservation(row: sqlite3.Row, *, created: bool) -> dict[str, Any]:
        return {
            "created": created,
            "request_id": row["request_id"],
            "session_id": row["session_id"],
            "actor_user_id": row["actor_user_id"],
            "actor_device_id": row["device_id"],
            "actor_auth_version": row["actor_auth_version"],
            "target_user_id": row["target_user_id"],
            "response": json.loads(row["response_json"]),
        }

    @staticmethod
    def _projection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "request_id": row["request_id"],
            "session_id": row["session_id"],
            "state": row["state"],
            "result": (
                json.loads(row["result_json"])
                if row["result_json"] is not None
                else None
            ),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    @staticmethod
    def _result_json(result: Mapping[str, Any]) -> str:
        if not isinstance(result, Mapping):
            raise ValueError("enrollment result must be an object")
        return EnrollmentRequestStore._json(dict(result))

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    def _now(self) -> int:
        return self.security._now()
