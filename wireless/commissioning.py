"""Durable product ownership, claim, recovery, and transfer state."""

from __future__ import annotations

import hmac
import secrets
import sqlite3
import time
from typing import Any, Mapping
import uuid

from .security_credentials import (
    Principal,
    SecurityError,
    bounded_text,
    new_pin_record,
    principal_from_row,
    token_hash,
    user_payload,
    validate_pin,
    verify_pin,
)
from .security_schema import (
    drop_user_protection_triggers,
    install_security_schema,
    install_user_protection_triggers,
)
from .request_receipts import (
    canonical_request_id,
    load_receipt,
    request_digest,
    save_receipt,
)


TRANSFER_SECONDS = 5 * 60
_DEFAULT_SETTINGS = (
    ("auto_relock_seconds", "10"),
    ("ignition_auto_stop_seconds", "20"),
    ("ignition_prompt_autolock_seconds", "0"),
    ("liveness_detection", "true"),
    ("fail_lockout", "true"),
    ("lockout_after", "5"),
)


class CommissioningStore:
    """Own the one authoritative lifecycle for a physical product."""

    def __init__(self, security, config, events):
        self.security = security
        self.config = config
        self.events = events

    def ensure_schema(self) -> None:
        """Create lifecycle storage and classify pre-existing installs once."""

        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            install_security_schema(conn)
            if self._state_row(conn) is not None:
                return
            now = self._now()
            has_product_data = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM users) "
                "OR EXISTS(SELECT 1 FROM user_security)"
            ).fetchone()[0]
            if has_product_data:
                conn.execute(
                    "INSERT INTO commissioning_state "
                    "(id, ownership, owner_user_id, generation, activation_secret, "
                    "activation_hash, recovery_hash, updated_at) "
                    "VALUES (1,'legacy',NULL,1,NULL,NULL,NULL,?)",
                    (now,),
                )
                return
            activation_secret = secrets.token_hex(32)
            conn.execute(
                "INSERT INTO commissioning_state "
                "(id, ownership, owner_user_id, generation, activation_secret, "
                "activation_hash, recovery_hash, updated_at) "
                "VALUES (1,'unclaimed',NULL,1,?,?,NULL,?)",
                (activation_secret, token_hash(activation_secret), now),
            )

    def status(self) -> dict[str, Any]:
        with self.security.synchronized(), self.security.get_connection() as conn:
            row = self._require_state(conn)
            return self._status_payload(row)

    def owner_user_id(self) -> str | None:
        return self.status()["owner_user_id"]

    def is_owner(self, user_id: str) -> bool:
        status = self.status()
        return status["ownership"] == "claimed" and status["owner_user_id"] == user_id

    def activation_material(self) -> str | None:
        """Return the one-time activation secret to an authorized dev caller."""

        with self.security.synchronized(), self.security.get_connection() as conn:
            row = self._require_state(conn)
            if row["ownership"] != "unclaimed":
                return None
            return row["activation_secret"]

    def ownership_request_digest(self, kind: str, body: Mapping[str, Any]) -> bytes:
        """Bind a draining transition to the same normalized persisted request."""
        if kind == "owner_recovery":
            return request_digest("recover", self._recovery_values(body))
        if kind == "ownership_transfer":
            values = self._claim_values(body, secret_field="transfer_secret")
            return request_digest("transfer_accept", values)
        raise ValueError("unknown ownership transition")

    def claim(self, body: Mapping[str, Any]) -> dict[str, Any]:
        values = self._claim_values(body, secret_field="activation_secret")
        digest = request_digest("claim", values)
        completed = False
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            receipt = load_receipt(conn, values["request_id"], "claim", digest)
            if receipt is not None:
                return receipt
            state = self._require_state(conn)
            if state["ownership"] != "unclaimed" or not self._hash_matches(
                values["activation_secret"], state["activation_hash"]
            ):
                raise SecurityError(
                    "invalid_activation",
                    401,
                    "activation credential is invalid or already used",
                )
            self.events.require_window("pairing")
            now = self._now()
            user_id = str(uuid.uuid4())
            device_id = str(uuid.uuid4())
            salt, verifier = new_pin_record(
                values["pin"], self.security.pin_pepper, self.security.pin_iterations
            )
            conn.execute(
                "INSERT INTO users "
                "(id,name,face_encoding,active,face_access,created_at) "
                "VALUES (?,?,NULL,1,1,?)",
                (user_id, values["name"], now),
            )
            conn.execute(
                "INSERT INTO user_security "
                "(user_id,pin_salt,pin_verifier,is_admin,created_at,updated_at) "
                "VALUES (?,?,?,1,?,?)",
                (user_id, salt, verifier, now, now),
            )
            self._insert_mobile_device(
                conn,
                device_id,
                user_id,
                values["device_name"],
                values["device_token"],
                now,
            )
            conn.execute(
                "UPDATE commissioning_state SET ownership='claimed', "
                "owner_user_id=?, activation_secret=NULL, activation_hash=NULL, "
                "recovery_hash=?, updated_at=? WHERE id=1",
                (user_id, token_hash(values["recovery_secret"]), now),
            )
            generation = int(state["generation"])
            result = self._commissioning_result(
                user_id,
                values["name"],
                device_id,
                values["request_id"],
                generation,
            )
            save_receipt(conn, values["request_id"], "claim", digest, result, now)
            self.security._audit(
                conn,
                user_id,
                "product_claim",
                "ok",
                f"Claimed generation {generation} with device {device_id}",
            )
            completed = True
        if completed:
            self.events.close_windows()
        return result

    def recover(
        self,
        body: Mapping[str, Any],
        *,
        window_verified: bool = False,
    ) -> dict[str, Any]:
        values = self._recovery_values(body)
        digest = request_digest("recover", values)
        completed = False
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            receipt = load_receipt(conn, values["request_id"], "recover", digest)
            if receipt is not None:
                return receipt
            state = self._require_state(conn)
            if state["ownership"] != "claimed" or not self._hash_matches(
                values["recovery_secret"], state["recovery_hash"]
            ):
                raise SecurityError(
                    "invalid_recovery",
                    401,
                    "recovery credential is invalid",
                )
            if window_verified:
                if not self.events.maintenance:
                    raise SecurityError(
                        "recovery_preflight_required",
                        409,
                        "recovery must be validated before maintenance",
                    )
            else:
                self.events.require_window("recovery")
            now = self._now()
            owner = self._current_owner(conn, state["owner_user_id"])
            if owner is None:
                raise SecurityError(
                    "ownership_inconsistent", 503, "product owner record is unavailable"
                )
            salt, verifier = new_pin_record(
                values["pin"], self.security.pin_pepper, self.security.pin_iterations
            )
            conn.execute("DELETE FROM operation_grants")
            conn.execute("DELETE FROM pairing_invites")
            conn.execute("DELETE FROM paired_devices")
            conn.execute(
                "UPDATE user_security SET pin_salt=?,pin_verifier=?,is_admin=1,"
                "failed_attempts=0,lockout_until=0,face_failed_attempts=0,"
                "face_lockout_until=0,auth_version=auth_version+1,updated_at=? "
                "WHERE user_id=?",
                (salt, verifier, now, owner["id"]),
            )
            device_id = str(uuid.uuid4())
            self._insert_mobile_device(
                conn,
                device_id,
                owner["id"],
                values["device_name"],
                values["device_token"],
                now,
            )
            conn.execute(
                "UPDATE commissioning_state SET recovery_hash=?,updated_at=? WHERE id=1",
                (token_hash(values["new_recovery_secret"]), now),
            )
            conn.execute("DELETE FROM ownership_transfers")
            generation = int(state["generation"])
            result = self._commissioning_result(
                owner["id"],
                owner["name"],
                device_id,
                values["request_id"],
                generation,
            )
            save_receipt(conn, values["request_id"], "recover", digest, result, now)
            self.security._audit(
                conn,
                owner["id"],
                "owner_recovery",
                "ok",
                f"Recovered generation {generation} with device {device_id}",
            )
            completed = True
        if completed:
            self.events.close_windows()
        return result

    def validate_recovery(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Prove recovery authority before the caller drains product work."""

        values = self._recovery_values(body)
        digest = request_digest("recover", values)
        with self.security.synchronized(), self.security.get_connection() as conn:
            receipt = load_receipt(conn, values["request_id"], "recover", digest)
            if receipt is not None:
                return {"completed": True, "response": receipt}
            state = self._require_state(conn)
            if state["ownership"] != "claimed" or not self._hash_matches(
                values["recovery_secret"], state["recovery_hash"]
            ):
                raise SecurityError(
                    "invalid_recovery",
                    401,
                    "recovery credential is invalid",
                )
            if self._current_owner(conn, state["owner_user_id"]) is None:
                raise SecurityError(
                    "ownership_inconsistent", 503, "product owner record is unavailable"
                )
            self.events.require_window("recovery")
            return {"completed": False, "response": None}

    def start_transfer(
        self, actor: Principal, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        values = {
            "request_id": canonical_request_id(body.get("request_id")),
            "pin": self._pin(body.get("pin")),
            "transfer_secret": self._secret(body.get("transfer_secret"), "transfer_secret"),
        }
        digest = request_digest("transfer_start", values)
        pin_error: SecurityError | None = None
        result: dict[str, Any] | None = None
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            receipt = load_receipt(
                conn, values["request_id"], "transfer_start", digest
            )
            if receipt is not None:
                return receipt
            now = self._now()
            current = principal_from_row(
                self.security._require_principal(conn, actor, now)
            )
            state = self._require_state(conn)
            if (
                state["ownership"] != "claimed"
                or state["owner_user_id"] != current.user_id
                or not current.is_owner
            ):
                raise SecurityError("owner_required", 403, "product owner access required")
            pin_row = conn.execute(
                "SELECT user_id AS id,pin_salt,pin_verifier,failed_attempts,"
                "lockout_until FROM user_security WHERE user_id=?",
                (current.user_id,),
            ).fetchone()
            pin_error = verify_pin(
                conn,
                pin_row,
                values["pin"],
                self.security.pin_pepper,
                self.security.pin_iterations,
                now,
            )
            if pin_error is not None:
                self.security._audit(
                    conn,
                    current.user_id,
                    "ownership_transfer",
                    "denied",
                    pin_error.code,
                )
            else:
                self.events.require_window("pairing")
                conn.execute("DELETE FROM ownership_transfers")
                conn.execute(
                    "INSERT INTO ownership_transfers "
                    "(request_id,owner_user_id,owner_device_id,owner_auth_version,"
                    "secret_hash,generation,expires_at,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        values["request_id"],
                        current.user_id,
                        current.device_id,
                        current.auth_version,
                        token_hash(values["transfer_secret"]),
                        state["generation"],
                        now + TRANSFER_SECONDS,
                        now,
                    ),
                )
                result = {
                    "ok": True,
                    "request_id": values["request_id"],
                    "generation": int(state["generation"]),
                    "expires_in": TRANSFER_SECONDS,
                }
                save_receipt(
                    conn, values["request_id"], "transfer_start", digest, result, now
                )
                self.security._audit(
                    conn,
                    current.user_id,
                    "ownership_transfer_started",
                    "ok",
                    f"Generation {state['generation']}",
                )
        if pin_error is not None:
            raise pin_error
        assert result is not None
        return result

    def accept_transfer(self, body: Mapping[str, Any]) -> dict[str, Any]:
        values = self._claim_values(body, secret_field="transfer_secret")
        digest = request_digest("transfer_accept", values)
        completed = False
        with self.security.synchronized(), self.security.get_connection() as conn:
            self.security._begin(conn)
            receipt = load_receipt(
                conn, values["request_id"], "transfer_accept", digest
            )
            if receipt is not None:
                return receipt
            now = self._now()
            state = self._require_state(conn)
            transfer = self._valid_transfer(conn, values["transfer_secret"], now)
            if (
                state["ownership"] != "claimed"
                or transfer is None
                or transfer["owner_user_id"] != state["owner_user_id"]
                or int(transfer["generation"]) != int(state["generation"])
            ):
                raise SecurityError(
                    "invalid_transfer", 401, "ownership transfer is invalid or expired"
                )
            next_generation = int(state["generation"]) + 1
            self._clear_product_identities(conn)
            user_id = str(uuid.uuid4())
            device_id = str(uuid.uuid4())
            salt, verifier = new_pin_record(
                values["pin"], self.security.pin_pepper, self.security.pin_iterations
            )
            conn.execute(
                "INSERT INTO users "
                "(id,name,face_encoding,active,face_access,created_at) "
                "VALUES (?,?,NULL,1,1,?)",
                (user_id, values["name"], now),
            )
            conn.execute(
                "INSERT INTO user_security "
                "(user_id,pin_salt,pin_verifier,is_admin,created_at,updated_at) "
                "VALUES (?,?,?,1,?,?)",
                (user_id, salt, verifier, now, now),
            )
            self._insert_mobile_device(
                conn,
                device_id,
                user_id,
                values["device_name"],
                values["device_token"],
                now,
            )
            conn.execute(
                "UPDATE commissioning_state SET ownership='claimed',owner_user_id=?,"
                "generation=?,activation_secret=NULL,activation_hash=NULL,"
                "recovery_hash=?,updated_at=? WHERE id=1",
                (
                    user_id,
                    next_generation,
                    token_hash(values["recovery_secret"]),
                    now,
                ),
            )
            result = self._commissioning_result(
                user_id,
                values["name"],
                device_id,
                values["request_id"],
                next_generation,
            )
            save_receipt(
                conn, values["request_id"], "transfer_accept", digest, result, now
            )
            self.security._audit(
                conn,
                user_id,
                "ownership_transfer_completed",
                "ok",
                f"Claimed generation {next_generation} with device {device_id}",
            )
            completed = True
        if completed:
            self.events.close_windows()
        return result

    def validate_transfer_accept(
        self, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Validate transfer proof before a caller quiesces product work.

        The transition still revalidates everything in ``accept_transfer``.
        """

        values = self._claim_values(body, secret_field="transfer_secret")
        digest = request_digest("transfer_accept", values)
        with self.security.synchronized(), self.security.get_connection() as conn:
            receipt = load_receipt(
                conn, values["request_id"], "transfer_accept", digest
            )
            if receipt is not None:
                return {"completed": True, "response": receipt}
            now = self._now()
            state = self._require_state(conn)
            transfer = self._valid_transfer(conn, values["transfer_secret"], now)
            if (
                state["ownership"] != "claimed"
                or transfer is None
                or transfer["owner_user_id"] != state["owner_user_id"]
                or int(transfer["generation"]) != int(state["generation"])
            ):
                raise SecurityError(
                    "invalid_transfer", 401, "ownership transfer is invalid or expired"
                )
            return {"completed": False, "response": None}

    @staticmethod
    def _valid_transfer(conn: sqlite3.Connection, secret: str, now: int):
        return conn.execute(
            "SELECT t.owner_user_id,t.generation FROM ownership_transfers AS t "
            "JOIN paired_devices AS d ON d.id=t.owner_device_id "
            "AND d.user_id=t.owner_user_id AND d.active=1 "
            "AND (d.expires_at IS NULL OR d.expires_at>?) "
            "JOIN users AS u ON u.id=t.owner_user_id AND u.active=1 "
            "JOIN user_security AS s ON s.user_id=t.owner_user_id "
            "AND s.is_admin=1 AND s.auth_version=t.owner_auth_version "
            "WHERE t.secret_hash=? AND t.expires_at>?",
            (now, token_hash(secret), now),
        ).fetchone()

    def reset_product(self, conn: sqlite3.Connection, activation_secret: str) -> dict[str, Any]:
        """Reset product data inside an already-open exclusive transaction."""

        activation_secret = self._secret(activation_secret, "activation_secret")
        state = self._require_state(conn)
        next_generation = int(state["generation"]) + 1
        self._clear_product_identities(conn)
        conn.execute("DELETE FROM commissioning_attempts")
        conn.execute("DELETE FROM settings")
        conn.executemany("INSERT INTO settings(key,value) VALUES (?,?)", _DEFAULT_SETTINGS)
        conn.execute(
            "UPDATE device_state SET device_name='FaceLock-Pi',lock_state='locked',"
            "battery=100,signal=5,last_seen=? WHERE id=1",
            (self._now() * 1000,),
        )
        conn.execute(
            "UPDATE commissioning_state SET ownership='unclaimed',owner_user_id=NULL,"
            "generation=?,activation_secret=?,activation_hash=?,recovery_hash=NULL,"
            "updated_at=? WHERE id=1",
            (
                next_generation,
                activation_secret,
                token_hash(activation_secret),
                self._now(),
            ),
        )
        return {
            "ownership": "unclaimed",
            "owner_user_id": None,
            "generation": next_generation,
        }

    def _clear_product_identities(self, conn: sqlite3.Connection) -> None:
        drop_user_protection_triggers(conn)
        conn.execute("DELETE FROM operation_grants")
        conn.execute("DELETE FROM pairing_invites")
        conn.execute("DELETE FROM paired_devices")
        conn.execute("DELETE FROM ownership_transfers")
        conn.execute("DELETE FROM user_security")
        conn.execute("DELETE FROM auth_logs")
        conn.execute("DELETE FROM users")
        install_user_protection_triggers(conn)

    @staticmethod
    def _insert_mobile_device(
        conn: sqlite3.Connection,
        device_id: str,
        user_id: str,
        device_name: str,
        device_token: str,
        now: int,
    ) -> None:
        conn.execute(
            "INSERT INTO paired_devices "
            "(id,user_id,name,token_hash,kind,active,created_at,last_seen) "
            "VALUES (?,?,?,?, 'mobile',1,?,?)",
            (device_id, user_id, device_name, token_hash(device_token), now, now),
        )

    @staticmethod
    def _commissioning_result(
        user_id: str,
        name: str,
        device_id: str,
        request_id: str,
        generation: int,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "user": user_payload(user_id, name, True, True),
            "device_id": device_id,
            "request_id": request_id,
            "generation": generation,
        }

    def _claim_values(
        self, body: Mapping[str, Any], *, secret_field: str
    ) -> dict[str, str]:
        if not isinstance(body, Mapping):
            raise SecurityError("invalid_body", 400, "request body must be an object")
        return {
            "request_id": canonical_request_id(body.get("request_id")),
            secret_field: self._secret(body.get(secret_field), secret_field),
            "name": bounded_text(body.get("name"), "name", 100),
            "pin": self._pin(body.get("pin")),
            "device_name": bounded_text(body.get("device_name"), "device_name", 100),
            "device_token": self._secret(body.get("device_token"), "device_token"),
            "recovery_secret": self._secret(
                body.get("recovery_secret"), "recovery_secret"
            ),
        }

    def _recovery_values(self, body: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(body, Mapping):
            raise SecurityError("invalid_body", 400, "request body must be an object")
        return {
            "request_id": canonical_request_id(body.get("request_id")),
            "recovery_secret": self._secret(
                body.get("recovery_secret"), "recovery_secret"
            ),
            "new_recovery_secret": self._secret(
                body.get("new_recovery_secret"), "new_recovery_secret"
            ),
            "pin": self._pin(body.get("pin")),
            "device_name": bounded_text(body.get("device_name"), "device_name", 100),
            "device_token": self._secret(body.get("device_token"), "device_token"),
        }

    @staticmethod
    def _pin(value: Any) -> str:
        validate_pin(value)
        return value

    @staticmethod
    def _secret(value: Any, field: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise SecurityError(
                f"invalid_{field}", 400, f"{field} must be 64 lowercase hex digits"
            )
        return value

    @staticmethod
    def _hash_matches(secret: str, expected: bytes | None) -> bool:
        return expected is not None and hmac.compare_digest(
            token_hash(secret), bytes(expected)
        )

    @staticmethod
    def _state_row(conn: sqlite3.Connection):
        return conn.execute(
            "SELECT ownership,owner_user_id,generation,activation_secret,"
            "activation_hash,recovery_hash FROM commissioning_state WHERE id=1"
        ).fetchone()

    @staticmethod
    def _current_owner(conn: sqlite3.Connection, owner_user_id: str | None):
        return conn.execute(
            "SELECT u.id,u.name,s.auth_version FROM users AS u "
            "JOIN user_security AS s ON s.user_id=u.id "
            "WHERE u.id=? AND u.active=1 AND s.is_admin=1",
            (owner_user_id,),
        ).fetchone()

    def _require_state(self, conn: sqlite3.Connection):
        row = self._state_row(conn)
        if row is None:
            raise SecurityError(
                "commissioning_unavailable", 503, "commissioning state is unavailable"
            )
        return row

    @staticmethod
    def _status_payload(row) -> dict[str, Any]:
        return {
            "ownership": row["ownership"],
            "owner_user_id": row["owner_user_id"],
            "generation": int(row["generation"]),
        }

    @staticmethod
    def _now() -> int:
        return int(time.time())
