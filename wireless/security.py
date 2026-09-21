"""Host-authoritative users, device credentials, and operation grants."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import secrets
import sqlite3
import threading
import time
from typing import Any, Callable, Iterator
import uuid

from .security_credentials import (
    ADMIN_ACTIONS,
    OWN_USER_ACTIONS,
    PIN_ITERATIONS,
    PIN_LOCKOUT_SECONDS,
    PIN_MAX_FAILURES,
    Principal,
    SecurityError,
    bounded_text,
    created_user_payload,
    derive_pin,
    new_pin_record,
    optional_target,
    pin_matches,
    principal_from_row,
    principal_row_active,
    token_hash,
    user_payload,
    validate_action,
    validate_pin,
    verify_pin,
)
from .security_face_policy import (
    apply_face_result,
    require_face_attempt_allowed,
    validate_face_result,
)
from .security_schema import install_security_schema
from .request_receipts import (
    canonical_request_id,
    load_receipt,
    request_digest,
    save_receipt,
)


PAIRING_INVITE_SECONDS = 5 * 60
OPERATION_GRANT_SECONDS = 30
LOCAL_DEVICE_SECONDS = 15 * 60
MAX_MOBILE_DEVICES_PER_USER = 10
MAX_ACTIVE_GRANTS_PER_DEVICE = 32
OWNER_PROTECTED_ACTIONS = frozenset(
    {"user.delete", "user.access", "enrollment.start", "user.pin", "device.revoke"}
)

class SecurityStore:
    def __init__(self, get_conn: Callable[[], sqlite3.Connection], pepper: bytes):
        if not isinstance(pepper, bytes) or len(pepper) != 32:
            raise ValueError("security pepper must contain exactly 32 bytes")
        self._get_conn = get_conn
        self._pepper = pepper
        self._pin_iterations = PIN_ITERATIONS
        self._lock = threading.RLock()
        self._dummy_pin_salt = hashlib.sha256(pepper + b"dummy-pin").digest()[:16]
        self._dummy_pin_verifier = derive_pin(
            "000000",
            self._dummy_pin_salt,
            self._pepper,
            self._pin_iterations,
        )

    @contextmanager
    def synchronized(self) -> Iterator[None]:
        """Serialize identity mutations with final runtime authorization."""

        with self._lock:
            yield

    def get_connection(self) -> sqlite3.Connection:
        """Open the configured database for a coordinated security transaction."""

        return self._get_conn()

    @property
    def pin_pepper(self) -> bytes:
        return self._pepper

    @property
    def pin_iterations(self) -> int:
        return self._pin_iterations

    def ensure_schema(self) -> None:
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            install_security_schema(conn)

    def configured(self) -> bool:
        with self._lock, self._get_conn() as conn:
            state = conn.execute(
                "SELECT ownership FROM commissioning_state WHERE id=1"
            ).fetchone()
            if state is not None:
                return state["ownership"] in {"legacy", "claimed"}
            row = conn.execute(
                "SELECT 1 FROM users AS u "
                "JOIN user_security AS s ON s.user_id=u.id "
                "WHERE u.active=1 AND s.is_admin=1 LIMIT 1"
            ).fetchone()
            return row is not None

    def initial_admin(self, name: str, pin: str) -> dict[str, Any]:
        name = bounded_text(name, "name", 100)
        validate_pin(pin)
        salt, verifier = new_pin_record(pin, self._pepper, self._pin_iterations)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            lifecycle = conn.execute(
                "SELECT ownership FROM commissioning_state WHERE id=1"
            ).fetchone()
            if lifecycle is not None:
                code = (
                    "claim_required"
                    if lifecycle["ownership"] == "unclaimed"
                    else "admin_already_configured"
                )
                raise SecurityError(
                    code,
                    409,
                    "initialize the product through its activation claim",
                )
            if self._active_admin_exists(conn):
                raise SecurityError(
                    "admin_already_configured",
                    409,
                    "an active administrator already exists",
                )
            users = conn.execute(
                "SELECT id, name, created_at FROM users WHERE active=1 "
                "AND lower(trim(name))=lower(trim(?))",
                (name,),
            ).fetchall()
            if len(users) > 1:
                raise SecurityError(
                    "ambiguous_user",
                    409,
                    "active user name is ambiguous",
                )
            if users:
                user_id = users[0]["id"]
                display_name = users[0]["name"]
                created_at = int(users[0]["created_at"])
            else:
                user_id = str(uuid.uuid4())
                display_name = name
                created_at = now
                conn.execute(
                    "INSERT INTO users "
                    "(id, name, face_encoding, active, face_access, created_at) "
                    "VALUES (?,?,NULL,1,1,?)",
                    (user_id, display_name, now),
                )
            existing = conn.execute(
                "SELECT auth_version FROM user_security WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE user_security SET pin_salt=?, pin_verifier=?, "
                    "is_admin=1, failed_attempts=0, lockout_until=0, "
                    "auth_version=auth_version+1, updated_at=? WHERE user_id=?",
                    (salt, verifier, now, user_id),
                )
                self._revoke_user_credentials(conn, user_id, now)
            else:
                conn.execute(
                    "INSERT INTO user_security "
                    "(user_id, pin_salt, pin_verifier, is_admin, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (user_id, salt, verifier, 1, now, now),
                )
            self._audit(
                conn,
                user_id,
                "security_initial_admin",
                "ok",
                f"Initialized administrator {user_id}",
            )
        return created_user_payload(user_id, display_name, True, created_at)

    def local_login(
        self, name: str | None, pin: str, *, user_id: str | None = None,
    ) -> dict[str, Any]:
        if user_id is not None:
            if name is not None:
                raise SecurityError(
                    "invalid_login_target", 400, "Select one account for sign-in.",
                )
            account = bounded_text(user_id, "user_id", 128)
            account_filter = "u.id=?"
        else:
            account = bounded_text(name, "name", 100)
            account_filter = "lower(trim(u.name))=lower(trim(?))"
        validate_pin(pin)
        now = self._now()
        pin_error: SecurityError | None = None
        result: dict[str, Any] | None = None
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            self._prune(conn, now)
            rows = conn.execute(
                "SELECT u.id, u.name, s.is_admin, s.pin_salt, s.pin_verifier, "
                "s.failed_attempts, s.lockout_until, s.auth_version "
                "FROM users AS u JOIN user_security AS s ON s.user_id=u.id "
                f"WHERE u.active=1 AND {account_filter}",
                (account,),
            ).fetchall()
            row = rows[0] if len(rows) == 1 else None
            if row is None:
                pin_matches(
                    pin,
                    self._dummy_pin_salt,
                    self._dummy_pin_verifier,
                    self._pepper,
                    self._pin_iterations,
                )
                pin_error = SecurityError(
                    "invalid_credentials",
                    401,
                    "invalid user name or PIN",
                )
            else:
                pin_error = verify_pin(
                    conn, row, pin, self._pepper, self._pin_iterations, now
                )
            if pin_error is not None:
                self._audit(
                    conn,
                    row["id"] if row else None,
                    "local_login",
                    "denied",
                    pin_error.code,
                )
            else:
                user_id = row["id"]
                stale_local_ids = [
                    item["id"]
                    for item in conn.execute(
                        "SELECT id FROM paired_devices "
                        "WHERE user_id=? AND kind='local'",
                        (user_id,),
                    )
                ]
                self._delete_device_rows(conn, stale_local_ids)
                token = secrets.token_hex(32)
                device_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO paired_devices "
                    "(id, user_id, name, token_hash, kind, active, created_at, "
                    "last_seen, expires_at) VALUES (?,?,?,?, 'local',1,?,?,?)",
                    (
                        device_id,
                        user_id,
                        "Local dashboard",
                        token_hash(token),
                        now,
                        now,
                        now + LOCAL_DEVICE_SECONDS,
                    ),
                )
                self._audit(
                    conn,
                    user_id,
                    "local_login",
                    "ok",
                    f"Local session {device_id}",
                )
                result = {
                    "device_token": token,
                    "device_id": device_id,
                    "expires_in": LOCAL_DEVICE_SECONDS,
                    "user": user_payload(
                        user_id,
                        row["name"],
                        bool(row["is_admin"]),
                        self._user_is_owner(conn, user_id),
                    ),
                }
        if pin_error is not None:
            raise pin_error
        assert result is not None
        return result

    def recover_admin(self, name: str, pin: str) -> dict[str, Any]:
        """Reset one existing user as admin from the stopped-service CLI."""

        name = bounded_text(name, "name", 100)
        validate_pin(pin)
        salt, verifier = new_pin_record(pin, self._pepper, self._pin_iterations)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            lifecycle = conn.execute(
                "SELECT ownership FROM commissioning_state WHERE id=1"
            ).fetchone()
            if lifecycle is not None and lifecycle["ownership"] != "legacy":
                raise SecurityError(
                    "recovery_flow_required",
                    409,
                    "use the product owner recovery flow",
                )
            users = conn.execute(
                "SELECT id, name, face_access, created_at FROM users "
                "WHERE active=1 AND lower(trim(name))=lower(trim(?))",
                (name,),
            ).fetchall()
            if not users:
                raise SecurityError("user_not_found", 404, "active user not found")
            if len(users) != 1:
                raise SecurityError(
                    "ambiguous_user",
                    409,
                    "active user name is ambiguous",
                )
            user = users[0]
            existing = conn.execute(
                "SELECT 1 FROM user_security WHERE user_id=?",
                (user["id"],),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE user_security SET pin_salt=?, pin_verifier=?, "
                    "is_admin=1, failed_attempts=0, lockout_until=0, "
                    "auth_version=auth_version+1, updated_at=? WHERE user_id=?",
                    (salt, verifier, now, user["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO user_security "
                    "(user_id, pin_salt, pin_verifier, is_admin, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (user["id"], salt, verifier, 1, now, now),
                )
            self._revoke_user_credentials(conn, user["id"], now)
            self._audit(
                conn,
                user["id"],
                "security_admin_recovered",
                "ok",
                "Offline local recovery; credentials revoked",
            )
        return {
            **created_user_payload(
                user["id"],
                user["name"],
                True,
                int(user["created_at"]),
            ),
            "faceAccess": bool(user["face_access"]),
        }

    def revoke_local(self, token: str) -> bool:
        try:
            hashed_token = token_hash(token)
        except SecurityError:
            return False
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            row = conn.execute(
                "SELECT id, user_id FROM paired_devices "
                "WHERE token_hash=? AND kind='local' AND active=1",
                (hashed_token,),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE paired_devices SET active=0, revoked_at=? WHERE id=?",
                (now, row["id"]),
            )
            conn.execute(
                "DELETE FROM operation_grants WHERE device_id=?",
                (row["id"],),
            )
            conn.execute(
                "DELETE FROM pairing_invites WHERE issued_by_device_id=?",
                (row["id"],),
            )
            self._audit(
                conn,
                row["user_id"],
                "local_logout",
                "ok",
                f"Local session {row['id']}",
            )
            return True

    def create_user(
        self,
        admin: Principal,
        name: str,
        pin: str,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        name = bounded_text(name, "name", 100)
        validate_pin(pin)
        if type(is_admin) is not bool:
            raise SecurityError("invalid_role", 400, "is_admin must be a boolean")
        salt, verifier = new_pin_record(pin, self._pepper, self._pin_iterations)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            current = principal_from_row(self._require_admin(conn, admin, now))
            if is_admin and self._owner_user_id(conn) is not None and not current.is_owner:
                raise SecurityError(
                    "owner_required",
                    403,
                    "only the product owner can create an administrator",
                )
            duplicate = conn.execute(
                "SELECT 1 FROM users WHERE active=1 "
                "AND lower(trim(name))=lower(trim(?)) LIMIT 1",
                (name,),
            ).fetchone()
            if duplicate:
                raise SecurityError("duplicate_user", 409, "active user already exists")
            user_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users "
                "(id, name, face_encoding, active, face_access, created_at) "
                "VALUES (?,?,NULL,1,1,?)",
                (user_id, name, now),
            )
            conn.execute(
                "INSERT INTO user_security "
                "(user_id, pin_salt, pin_verifier, is_admin, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, salt, verifier, int(is_admin), now, now),
            )
            self._audit(
                conn,
                user_id,
                "security_user_created",
                "ok",
                f"Created by {admin.user_id}; admin={int(is_admin)}",
            )
        return created_user_payload(user_id, name, is_admin, now)

    def issue_invite(
        self,
        admin: Principal,
        target_user_id: str,
    ) -> dict[str, Any]:
        target_user_id = bounded_text(target_user_id, "target_user_id", 128)
        now = self._now()
        token = secrets.token_hex(32)
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            self._require_admin(conn, admin, now)
            target = self._security_user(conn, target_user_id)
            if target is None:
                raise SecurityError("user_not_found", 404, "active user not found")
            self._prune(conn, now)
            conn.execute(
                "DELETE FROM pairing_invites WHERE target_user_id=?",
                (target_user_id,),
            )
            conn.execute(
                "INSERT INTO pairing_invites "
                "(token_hash, target_user_id, issued_by_user_id, "
                "issued_by_device_id, issuer_auth_version, expires_at, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    token_hash(token),
                    target_user_id,
                    admin.user_id,
                    admin.device_id,
                    admin.auth_version,
                    now + PAIRING_INVITE_SECONDS,
                    now,
                ),
            )
            self._audit(
                conn,
                target_user_id,
                "pairing_invite",
                "ok",
                f"Issued by {admin.user_id}",
            )
        return {"invite_token": token, "expires_in": PAIRING_INVITE_SECONDS}

    def pair(
        self,
        invite_token: str,
        pin: str,
        device_name: str,
        *,
        request_id: str | None = None,
        device_token: str | None = None,
    ) -> dict[str, Any]:
        invite_hash = token_hash(invite_token)
        validate_pin(pin)
        device_name = bounded_text(device_name, "device_name", 100)
        resumable = request_id is not None or device_token is not None
        if resumable:
            request_id = canonical_request_id(request_id)
            if device_token is None:
                raise SecurityError(
                    "invalid_device_token",
                    400,
                    "device_token is required with request_id",
                )
            token_hash(device_token)
            body_digest = request_digest(
                "pair",
                {
                    "invite_token": invite_token,
                    "pin": pin,
                    "device_name": device_name,
                    "request_id": request_id,
                    "device_token": device_token,
                },
            )
        else:
            body_digest = None
        now = self._now()
        pin_error: SecurityError | None = None
        result: dict[str, Any] | None = None
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            if resumable:
                receipt = load_receipt(conn, request_id, "pair", body_digest)
                if receipt is not None:
                    return receipt
            self._prune(conn, now)
            invite = conn.execute(
                "SELECT i.target_user_id, u.name, s.is_admin, s.pin_salt, "
                "s.pin_verifier, s.failed_attempts, s.lockout_until, s.auth_version "
                "FROM pairing_invites AS i "
                "JOIN users AS u ON u.id=i.target_user_id "
                "JOIN user_security AS s ON s.user_id=u.id "
                "JOIN paired_devices AS issuer_device "
                "ON issuer_device.id=i.issued_by_device_id "
                "AND issuer_device.user_id=i.issued_by_user_id "
                "JOIN users AS issuer_user ON issuer_user.id=i.issued_by_user_id "
                "JOIN user_security AS issuer_security "
                "ON issuer_security.user_id=issuer_user.id "
                "WHERE i.token_hash=? AND i.expires_at>? AND u.active=1 "
                "AND issuer_device.active=1 "
                "AND (issuer_device.expires_at IS NULL "
                "OR issuer_device.expires_at>?) "
                "AND issuer_user.active=1 AND issuer_security.is_admin=1 "
                "AND issuer_security.auth_version=i.issuer_auth_version",
                (invite_hash, now, now),
            ).fetchone()
            if invite is None:
                raise SecurityError(
                    "invalid_pairing_invite",
                    401,
                    "pairing invite is invalid or expired",
                )
            pin_row = {
                "id": invite["target_user_id"],
                "pin_salt": invite["pin_salt"],
                "pin_verifier": invite["pin_verifier"],
                "failed_attempts": invite["failed_attempts"],
                "lockout_until": invite["lockout_until"],
            }
            pin_error = verify_pin(
                conn, pin_row, pin, self._pepper, self._pin_iterations, now
            )
            if pin_error is not None:
                self._audit(
                    conn,
                    invite["target_user_id"],
                    "device_pairing",
                    "denied",
                    pin_error.code,
                )
            else:
                self._make_mobile_device_slot(conn, invite["target_user_id"])
                token = device_token or secrets.token_hex(32)
                device_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO paired_devices "
                    "(id, user_id, name, token_hash, kind, active, "
                    "created_at, last_seen) "
                    "VALUES (?,?,?,?, 'mobile',1,?,?)",
                    (
                        device_id,
                        invite["target_user_id"],
                        device_name,
                        token_hash(token),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "DELETE FROM pairing_invites WHERE token_hash=?",
                    (invite_hash,),
                )
                self._audit(
                    conn,
                    invite["target_user_id"],
                    "device_pairing",
                    "ok",
                    f"Paired device {device_id}",
                )
                result = {
                    "device_token": token,
                    "device_id": device_id,
                    "user": user_payload(
                        invite["target_user_id"],
                        invite["name"],
                        bool(invite["is_admin"]),
                        self._user_is_owner(conn, invite["target_user_id"]),
                    ),
                }
                if resumable:
                    result.pop("device_token")
                    result["request_id"] = request_id
                    save_receipt(
                        conn,
                        request_id,
                        "pair",
                        body_digest,
                        result,
                        now,
                    )
        if pin_error is not None:
            raise pin_error
        assert result is not None
        return result

    def principal(self, token: str) -> Principal:
        hashed_token = token_hash(token)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                self._principal_query() + " WHERE d.token_hash=?",
                (hashed_token,),
            ).fetchone()
            if row is None or not principal_row_active(row, now):
                raise SecurityError(
                    "invalid_device_credential",
                    401,
                    "device credential is invalid or revoked",
                )
            conn.execute(
                "UPDATE paired_devices SET last_seen=? WHERE id=?",
                (now, row["device_id"]),
            )
            return principal_from_row(row)

    def still_authorized(self, principal: Principal) -> bool:
        try:
            with self._lock, self._get_conn() as conn:
                self._require_principal(conn, principal, self._now())
            return True
        except (SecurityError, sqlite3.Error):
            return False

    def check_face_attempt(self, principal: Principal) -> None:
        now = self._now()
        with self._lock, self._get_conn() as conn:
            row = self._require_principal(conn, principal, now)
            require_face_attempt_allowed(row, now)

    def record_face_result(
        self,
        principal: Principal,
        matched: bool,
        limit: int,
    ) -> None:
        validate_face_result(matched, limit)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            row = self._require_principal(conn, principal, now)
            require_face_attempt_allowed(row, now)
            result, detail = apply_face_result(
                conn,
                principal.user_id,
                row,
                matched,
                limit,
                now,
            )
            self._audit(
                conn,
                principal.user_id,
                "face_verification",
                result,
                detail,
            )

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock, self._get_conn() as conn:
            row = self._security_user(conn, user_id)
            if row is None:
                return None
            return {
                **user_payload(
                    row["id"],
                    row["name"],
                    bool(row["is_admin"]),
                    bool(row["is_owner"]),
                ),
                "face_access": bool(row["face_access"]),
                "auth_version": int(row["auth_version"]),
            }

    def user_auth_version(self, user_id: str) -> int | None:
        user = self.get_user(user_id)
        return int(user["auth_version"]) if user else None

    def user_is_current(
        self,
        user_id: str,
        auth_version: int,
        *,
        require_face_access: bool = False,
    ) -> bool:
        with self._lock, self._get_conn() as conn:
            row = self._security_user(conn, user_id)
            return bool(
                row
                and int(row["auth_version"]) == auth_version
                and (not require_face_access or bool(row["face_access"]))
            )

    def set_pin(self, admin: Principal, target_user_id: str, pin: str) -> None:
        target_user_id = bounded_text(target_user_id, "target_user_id", 128)
        validate_pin(pin)
        salt, verifier = new_pin_record(pin, self._pepper, self._pin_iterations)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            current = principal_from_row(self._require_admin(conn, admin, now))
            self._require_owner_target_control(
                conn, current, "user.pin", target_user_id
            )
            target = conn.execute(
                "SELECT id FROM users WHERE id=? AND active=1",
                (target_user_id,),
            ).fetchone()
            if target is None:
                raise SecurityError("user_not_found", 404, "active user not found")
            existing = conn.execute(
                "SELECT 1 FROM user_security WHERE user_id=?",
                (target_user_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE user_security SET pin_salt=?, pin_verifier=?, "
                    "failed_attempts=0, lockout_until=0, "
                    "auth_version=auth_version+1, updated_at=? WHERE user_id=?",
                    (salt, verifier, now, target_user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO user_security "
                    "(user_id, pin_salt, pin_verifier, is_admin, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (target_user_id, salt, verifier, 0, now, now),
                )
            self._revoke_user_credentials(conn, target_user_id, now)
            self._audit(
                conn,
                target_user_id,
                "security_pin_changed",
                "ok",
                f"Changed by {admin.user_id}; credentials revoked",
            )

    def revoke_device(self, admin: Principal, device_id: str) -> None:
        device_id = bounded_text(device_id, "device_id", 128)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            current = principal_from_row(self._require_admin(conn, admin, now))
            device = conn.execute(
                "SELECT user_id FROM paired_devices WHERE id=? AND active=1",
                (device_id,),
            ).fetchone()
            if device is None:
                raise SecurityError("device_not_found", 404, "active device not found")
            owner_user_id = self._owner_user_id(conn)
            if device["user_id"] == owner_user_id and not current.is_owner:
                raise SecurityError(
                    "owner_required",
                    403,
                    "only the product owner can revoke an owner device",
                )
            conn.execute(
                "UPDATE paired_devices SET active=0, revoked_at=? WHERE id=?",
                (now, device_id),
            )
            conn.execute(
                "DELETE FROM operation_grants WHERE device_id=?",
                (device_id,),
            )
            conn.execute(
                "DELETE FROM pairing_invites WHERE issued_by_device_id=?",
                (device_id,),
            )
            self._audit(
                conn,
                device["user_id"],
                "device_revoked",
                "ok",
                f"Revoked {device_id} by {admin.user_id}",
            )

    def list_devices(self, admin: Principal) -> list[dict[str, Any]]:
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._require_admin(conn, admin, now)
            rows = conn.execute(
                "SELECT d.id, d.user_id, u.name AS user_name, d.name, d.active, "
                "d.created_at, d.last_seen, d.revoked_at, "
                "CASE WHEN c.ownership='claimed' AND c.owner_user_id=d.user_id "
                "THEN 1 ELSE 0 END AS user_is_owner "
                "FROM paired_devices AS d JOIN users AS u ON u.id=d.user_id "
                "LEFT JOIN commissioning_state AS c ON c.id=1 "
                "WHERE d.kind='mobile' ORDER BY d.created_at DESC"
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "user_name": row["user_name"],
                    "name": row["name"],
                    "active": bool(row["active"]),
                    "created_at": row["created_at"],
                    "last_seen": row["last_seen"],
                    "revoked_at": row["revoked_at"],
                    "user_is_owner": bool(row["user_is_owner"]),
                }
                for row in rows
            ]

    def confirm_session(self, principal: Principal, pin: str) -> Principal:
        """Verify the saved device's account without authorizing an operation."""
        validate_pin(pin)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            current = principal_from_row(
                self._require_principal(conn, principal, now)
            )
            user_row = conn.execute(
                "SELECT user_id AS id, pin_salt, pin_verifier, failed_attempts, "
                "lockout_until FROM user_security WHERE user_id=?",
                (current.user_id,),
            ).fetchone()
            pin_error = verify_pin(
                conn, user_row, pin, self._pepper, self._pin_iterations, now
            )
            self._audit(
                conn,
                current.user_id,
                "session_login",
                "denied" if pin_error else "ok",
                pin_error.code if pin_error else current.device_id,
            )
        if pin_error is not None:
            raise pin_error
        return current

    def authorize(
        self,
        principal: Principal,
        pin: str,
        action: str,
        target: str | None = None,
    ) -> dict[str, Any]:
        validate_pin(pin)
        action = validate_action(action)
        target = optional_target(target)
        now = self._now()
        pin_error: SecurityError | None = None
        result: dict[str, Any] | None = None
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            current_row = self._require_principal(conn, principal, now)
            current = principal_from_row(current_row)
            if action in ADMIN_ACTIONS and not current.is_admin:
                raise SecurityError(
                    "admin_required",
                    403,
                    "administrator access required",
                )
            self._require_owner_target_control(
                conn,
                current,
                action,
                target,
            )
            if action in OWN_USER_ACTIONS:
                target = target or current.user_id
                if target != current.user_id:
                    raise SecurityError(
                        "target_not_allowed",
                        403,
                        "operation must target the authenticated user",
                    )
                if action.startswith("scan.") and not current_row["face_access"]:
                    raise SecurityError(
                        "face_access_denied",
                        403,
                        "face access is disabled",
                    )
            user_row = conn.execute(
                "SELECT user_id AS id, pin_salt, pin_verifier, failed_attempts, "
                "lockout_until FROM user_security WHERE user_id=?",
                (current.user_id,),
            ).fetchone()
            pin_error = verify_pin(
                conn, user_row, pin, self._pepper, self._pin_iterations, now
            )
            if pin_error is not None:
                self._audit(
                    conn,
                    current.user_id,
                    "operation_authorization",
                    "denied",
                    f"{action}:{pin_error.code}",
                )
            else:
                self._prune(conn, now)
                conn.execute(
                    "DELETE FROM operation_grants "
                    "WHERE device_id=? AND action=? AND target IS ?",
                    (current.device_id, action, target),
                )
                active_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM operation_grants "
                    "WHERE device_id=? AND expires_at>?",
                    (current.device_id, now),
                ).fetchone()["count"]
                if active_count >= MAX_ACTIVE_GRANTS_PER_DEVICE:
                    raise SecurityError(
                        "grant_limit_reached",
                        429,
                        "too many active operation grants",
                    )
                token = secrets.token_hex(32)
                conn.execute(
                    "INSERT INTO operation_grants "
                    "(token_hash, device_id, user_id, action, target, "
                    "auth_version, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        token_hash(token),
                        current.device_id,
                        current.user_id,
                        action,
                        target,
                        current.auth_version,
                        now + OPERATION_GRANT_SECONDS,
                        now,
                    ),
                )
                self._audit(
                    conn,
                    current.user_id,
                    "operation_authorization",
                    "ok",
                    f"{action}:{target or '-'}",
                )
                result = {
                    "grant_token": token,
                    "expires_in": OPERATION_GRANT_SECONDS,
                }
        if pin_error is not None:
            raise pin_error
        assert result is not None
        return result

    def consume_grant(
        self,
        principal: Principal,
        token: str,
        action: str,
        target: str | None = None,
    ) -> None:
        try:
            hashed_token = token_hash(token)
        except SecurityError as error:
            if error.code != "invalid_token":
                raise
            raise SecurityError(
                "invalid_operation_grant",
                403,
                "operation grant is invalid or expired",
            ) from None
        action = validate_action(action)
        target = optional_target(target)
        now = self._now()
        with self._lock, self._get_conn() as conn:
            self._begin(conn)
            current = principal_from_row(
                self._require_principal(conn, principal, now)
            )
            self._require_owner_target_control(conn, current, action, target)
            grant = conn.execute(
                "SELECT 1 FROM operation_grants "
                "WHERE token_hash=? AND device_id=? AND user_id=? "
                "AND action=? AND target IS ? AND auth_version=? AND expires_at>?",
                (
                    hashed_token,
                    current.device_id,
                    current.user_id,
                    action,
                    target,
                    current.auth_version,
                    now,
                ),
            ).fetchone()
            if grant is None:
                raise SecurityError(
                    "invalid_operation_grant",
                    403,
                    "operation grant is invalid, expired, or already used",
                )
            conn.execute(
                "DELETE FROM operation_grants WHERE token_hash=?",
                (hashed_token,),
            )
            self._audit(
                conn,
                current.user_id,
                "operation_grant_consumed",
                "ok",
                f"{action}:{target or '-'}",
            )

    @staticmethod
    def _begin(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        user_id: str | None,
        stage: str,
        result: str,
        detail: str,
    ) -> None:
        conn.execute(
            "INSERT INTO auth_logs (id, user_id, stage, result, detail, ts) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, stage, result, detail, int(time.time())),
        )

    @staticmethod
    def _active_admin_exists(conn: sqlite3.Connection) -> bool:
        return conn.execute(
            "SELECT 1 FROM users AS u "
            "JOIN user_security AS s ON s.user_id=u.id "
            "WHERE u.active=1 AND s.is_admin=1 LIMIT 1"
        ).fetchone() is not None

    @staticmethod
    def _principal_query() -> str:
        return (
            "SELECT d.id AS device_id, d.user_id, d.active AS device_active, "
            "d.expires_at, u.name, u.active AS user_active, u.face_access, "
            "s.is_admin, s.auth_version, s.face_failed_attempts, "
            "s.face_lockout_until, CASE WHEN c.ownership='claimed' "
            "AND c.owner_user_id=u.id THEN 1 ELSE 0 END AS is_owner "
            "FROM paired_devices AS d "
            "JOIN users AS u ON u.id=d.user_id "
            "JOIN user_security AS s ON s.user_id=u.id "
            "LEFT JOIN commissioning_state AS c ON c.id=1"
        )

    def _require_principal(
        self,
        conn: sqlite3.Connection,
        principal: Principal,
        now: int,
    ) -> Any:
        row = conn.execute(
            self._principal_query() + " WHERE d.id=? AND d.user_id=?",
            (principal.device_id, principal.user_id),
        ).fetchone()
        if (
            row is None
            or not principal_row_active(row, now)
            or row["name"] != principal.name
            or bool(row["is_admin"]) != principal.is_admin
            or int(row["auth_version"]) != principal.auth_version
            or bool(row["is_owner"]) != principal.is_owner
        ):
            raise SecurityError(
                "stale_principal",
                401,
                "authenticated identity is no longer current",
            )
        return row

    def _require_admin(
        self,
        conn: sqlite3.Connection,
        principal: Principal,
        now: int,
    ) -> Any:
        row = self._require_principal(conn, principal, now)
        if not principal.is_admin:
            raise SecurityError("admin_required", 403, "administrator access required")
        return row

    def _require_owner_target_control(
        self,
        conn: sqlite3.Connection,
        principal: Principal,
        action: str,
        target: str | None,
    ) -> None:
        if action not in OWNER_PROTECTED_ACTIONS or target is None:
            return
        owner_user_id = self._owner_user_id(conn)
        if owner_user_id is None:
            return
        target_user_id = target
        if action == "device.revoke":
            device = conn.execute(
                "SELECT user_id FROM paired_devices WHERE id=?",
                (target,),
            ).fetchone()
            target_user_id = device["user_id"] if device else None
        if target_user_id == owner_user_id and not principal.is_owner:
            raise SecurityError(
                "owner_required",
                403,
                "only the product owner can modify the owner account",
            )

    @staticmethod
    def _owner_user_id(conn: sqlite3.Connection) -> str | None:
        row = conn.execute(
            "SELECT owner_user_id FROM commissioning_state "
            "WHERE id=1 AND ownership='claimed'"
        ).fetchone()
        return row["owner_user_id"] if row else None

    @classmethod
    def _user_is_owner(cls, conn: sqlite3.Connection, user_id: str) -> bool:
        return cls._owner_user_id(conn) == user_id

    @staticmethod
    def _security_user(conn: sqlite3.Connection, user_id: str) -> Any:
        return conn.execute(
            "SELECT u.id, u.name, u.face_access, s.is_admin, s.auth_version, "
            "CASE WHEN c.ownership='claimed' AND c.owner_user_id=u.id "
            "THEN 1 ELSE 0 END AS is_owner "
            "FROM users AS u JOIN user_security AS s ON s.user_id=u.id "
            "LEFT JOIN commissioning_state AS c ON c.id=1 "
            "WHERE u.id=? AND u.active=1",
            (user_id,),
        ).fetchone()

    @staticmethod
    def _revoke_user_credentials(
        conn: sqlite3.Connection,
        user_id: str,
        now: int,
    ) -> None:
        conn.execute(
            "UPDATE paired_devices SET active=0, revoked_at=? "
            "WHERE user_id=? AND active=1",
            (now, user_id),
        )
        conn.execute(
            "DELETE FROM operation_grants WHERE user_id=? OR target=?",
            (user_id, user_id),
        )
        conn.execute(
            "DELETE FROM pairing_invites "
            "WHERE issued_by_user_id=? OR target_user_id=?",
            (user_id, user_id),
        )

    @staticmethod
    def _delete_device_rows(conn: sqlite3.Connection, device_ids: list[str]) -> None:
        for device_id in device_ids:
            conn.execute(
                "DELETE FROM pairing_invites WHERE issued_by_device_id=?",
                (device_id,),
            )
            conn.execute(
                "DELETE FROM operation_grants WHERE device_id=?",
                (device_id,),
            )
            conn.execute("DELETE FROM paired_devices WHERE id=?", (device_id,))

    def _prune(self, conn: sqlite3.Connection, now: int) -> None:
        conn.execute("DELETE FROM pairing_invites WHERE expires_at<=?", (now,))
        conn.execute("DELETE FROM operation_grants WHERE expires_at<=?", (now,))
        expired_local_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM paired_devices "
                "WHERE kind='local' AND expires_at IS NOT NULL AND expires_at<=?",
                (now,),
            )
        ]
        self._delete_device_rows(conn, expired_local_ids)

    def _make_mobile_device_slot(
        self,
        conn: sqlite3.Connection,
        user_id: str,
    ) -> None:
        rows = conn.execute(
            "SELECT id, active FROM paired_devices "
            "WHERE user_id=? AND kind='mobile' ORDER BY created_at, id",
            (user_id,),
        ).fetchall()
        if len(rows) < MAX_MOBILE_DEVICES_PER_USER:
            return
        slots_needed = len(rows) - MAX_MOBILE_DEVICES_PER_USER + 1
        inactive = [row for row in rows if not row["active"]]
        if len(inactive) < slots_needed:
            raise SecurityError(
                "device_limit_reached",
                409,
                f"a user may have at most {MAX_MOBILE_DEVICES_PER_USER} devices",
            )
        self._delete_device_rows(
            conn,
            [row["id"] for row in inactive[:slots_needed]],
        )
