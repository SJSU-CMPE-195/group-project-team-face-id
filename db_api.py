import hashlib
import secrets
import uuid
import time
import sqlite3
from db import get_conn

# ── Device status (face-ui /api/status) ─────────────────────────────────────────


def _insert_log(conn, stage: str, result: str, detail: str = "", user_id: str = None):
    conn.execute(
        "INSERT INTO auth_logs (id, user_id, stage, result, detail, ts) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, stage, result, detail, int(time.time())),
    )

def get_status():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM device_state WHERE id = 1").fetchone()
        if not row:
            return {
                "online": True,
                "lockState": "locked",
                "deviceName": "FaceLock-Pi",
                "battery": 100,
                "signal": 5,
                "lastSeen": int(time.time() * 1000),
            }
        r = dict(row)
        last = r["last_seen"]
        if last < 10_000_000_000:
            last = last * 1000
        return {
            "online": True,
            "lockState": r["lock_state"],
            "deviceName": r["device_name"],
            "battery": r["battery"],
            "signal": r["signal"],
            "lastSeen": last,
        }

def set_unlock(reason: str = "manual_ui"):
    now_ms = int(time.time() * 1000)
    with get_conn() as conn:
        conn.execute(
            "UPDATE device_state SET lock_state = 'unlocked', last_seen = ? WHERE id = 1",
            (now_ms,),
        )
        _insert_log(conn, "unlock", "ok", detail=reason)

def set_lock(reason: str = "auto_relock"):
    now_ms = int(time.time() * 1000)
    with get_conn() as conn:
        conn.execute(
            "UPDATE device_state SET lock_state = 'locked', last_seen = ? WHERE id = 1",
            (now_ms,),
        )
        _insert_log(conn, "lock", "ok", detail=reason)

# ── Users ──────────────────────────────────────────────────────────────────────

def get_all_users():
    # Explicit projection: face_encoding must never leave the database layer
    # by accident.  face_enrolled carries the only part of it a client needs.
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, role, face_access, created_at, "
            "       (face_encoding IS NOT NULL) AS face_enrolled "
            "FROM users WHERE active=1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_users_for_ui():
    return [
        {
            "id": u["id"],
            "name": u["name"],
            "role": u.get("role", "USER"),
            "faceAccess": bool(u.get("face_access", 1)),
            "faceEnrolled": bool(u.get("face_enrolled", 0)),
            "createdAt": int(u["created_at"]) * 1000,
        }
        for u in get_all_users()
    ]

def add_user(name: str, face_encoding: bytes = None):
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    user_id = str(uuid.uuid4())
    ts = int(time.time())
    with get_conn() as conn:
        # Serialize the check with the insert.  The partial unique index made
        # by init_db handles clean databases; the transaction also protects
        # legacy databases where that index cannot be created safely.
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT id FROM users WHERE active=1 "
            "AND lower(trim(name))=lower(trim(?)) LIMIT 1",
            (name,),
        ).fetchone()
        if duplicate:
            return {"ok": False, "error": "active user with that name already exists"}
        try:
            conn.execute(
                "INSERT INTO users (id, name, face_encoding, created_at) VALUES (?,?,?,?)",
                (user_id, name, face_encoding, ts),
            )
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "active user with that name already exists"}
    log_event("enroll", "ok", detail=f"Added {name}", user_id=user_id)
    return {"id": user_id, "name": name, "createdAt": ts * 1000}

def delete_user(user_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        if not row:
            return {"ok": False}
        display_name = row["name"] or user_id
        conn.execute(
            "UPDATE users SET active=0, face_access=0, face_encoding=NULL WHERE id=?",
            (user_id,),
        )
    log_event("delete_user", "ok", detail=f"Removed {display_name}", user_id=user_id)
    return {"ok": True}

def get_user_by_id(user_id: str):
    """A user's safe profile.  Never selects face_encoding.

    This used to be ``SELECT *``, which meant the raw biometric template rode
    along in the returned dict.  No caller serialized it, but one ``jsonify``
    away is not a margin worth keeping.  Enrollment state is reported as a
    boolean instead of the template itself.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, role, active, face_access, created_at, "
            "       (face_encoding IS NOT NULL) AS face_enrolled "
            "FROM users WHERE id=? AND active=1",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        user = dict(row)
        user["face_enrolled"] = bool(user["face_enrolled"])
        return user


def get_user_by_name(name: str):
    """Case-insensitive lookup, matching the uniqueness rule used everywhere else."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE active=1 "
            "AND lower(trim(name))=lower(trim(?)) LIMIT 1",
            (name or "",),
        ).fetchone()
        user_id = row["id"] if row else None
    # Resolved after the first connection closes: nesting them would hold a
    # read open across a second connect for no benefit.
    return get_user_by_id(user_id) if user_id else None


def set_user_role(user_id: str, role: str):
    if role not in ("ADMIN", "USER"):
        return {"ok": False, "error": "role must be ADMIN or USER"}
    with get_conn() as conn:
        # Never let the last administrator demote themselves out of existence:
        # there would be no way back in short of editing the database by hand.
        if role == "USER":
            remaining = conn.execute(
                "SELECT COUNT(*) FROM users WHERE active=1 AND role='ADMIN' AND id<>?",
                (user_id,),
            ).fetchone()[0]
            if remaining == 0:
                return {"ok": False, "error": "cannot demote the last administrator"}
        cur = conn.execute(
            "UPDATE users SET role=? WHERE id=? AND active=1", (role, user_id)
        )
        if cur.rowcount != 1:
            return {"ok": False, "error": "user not found"}
    log_event("role_changed", "ok", detail=f"role set to {role}", user_id=user_id)
    return {"ok": True, "role": role}


def count_admins() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE active=1 AND role='ADMIN'"
        ).fetchone()[0]

def set_user_access(user_id: str, allowed: bool):
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET face_access=? WHERE id=? AND active=1",
            (1 if allowed else 0, user_id),
        )
        if cur.rowcount != 1:
            return {"ok": False, "error": "user not found"}
    log_event("access_change", "ok", detail=f"{'granted' if allowed else 'revoked'} for {user_id}", user_id=user_id)
    return {"ok": True}

def set_user_embedding(user_id: str, blob: bytes):
    if not isinstance(blob, (bytes, bytearray, memoryview)) or not blob:
        return {"ok": False, "error": "embedding is required"}
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET face_encoding=? WHERE id=? AND active=1",
            (bytes(blob), user_id),
        )
        if cur.rowcount != 1:
            return {"ok": False, "error": "user not found"}
    log_event("enroll_embedding", "ok", detail=f"Embedding stored for {user_id}", user_id=user_id)
    return {"ok": True}

def clear_user_embedding(user_id: str):
    """Remove a user's face template, addressed by id rather than name.

    face_engine.delete_embedding_for_name matches on an exact name, which
    disagrees with the lower(trim(...)) rule used everywhere else and cannot
    express "this specific row".  Authorization decisions are made about ids,
    so the deletion is too.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "user not found"}
        conn.execute(
            "UPDATE users SET face_encoding=NULL WHERE id=? AND active=1", (user_id,)
        )
        _insert_log(conn, "face_deleted", "ok",
                    detail=f"face template removed for {row['name']}", user_id=user_id)
    return {"ok": True}


def get_all_face_encodings():
    """Returns all active users with their face encodings — used by ML partner."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.name, u.face_encoding FROM users AS u "
            "WHERE u.active=1 AND u.face_access=1 AND u.face_encoding IS NOT NULL "
            "AND (SELECT COUNT(*) FROM users AS same "
            "     WHERE same.active=1 "
            "       AND lower(trim(same.name))=lower(trim(u.name)))=1"
        ).fetchall()
        return [dict(r) for r in rows]

# ── Sessions and pairing ───────────────────────────────────────────────────────
#
# Both tables are keyed by the SHA-256 of a high-entropy random value; the
# plaintext is returned to the caller exactly once and never stored, so a stolen
# database copy cannot be replayed as a login.  SHA-256 rather than bcrypt or
# argon2 is deliberate: these are 256-bit values from secrets.token_urlsafe(32),
# not human-chosen passwords.  There is no dictionary to slow an attacker down,
# and a deliberately slow hash would cost real time on every single request on a
# Raspberry Pi.

SESSION_IDLE_SECONDS = 12 * 60 * 60      # re-auth after 12h of inactivity
SESSION_ABSOLUTE_SECONDS = 30 * 24 * 3600  # hard cap regardless of use
PAIRING_TTL_SECONDS = 5 * 60             # a pairing code is short-lived by design


def _hash_secret(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def create_session(user_id: str, user_agent: str = "", idle_seconds: int = None,
                   absolute_seconds: int = None):
    """Issue a new session and return its plaintext token exactly once."""
    raw = secrets.token_urlsafe(32)
    now = int(time.time())
    absolute = absolute_seconds if absolute_seconds is not None else SESSION_ABSOLUTE_SECONDS
    with get_conn() as conn:
        owner = conn.execute(
            "SELECT id FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        if not owner:
            return {"ok": False, "error": "user not found"}
        conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, expires_at, last_used_at, "
            "revoked, user_agent) VALUES (?,?,?,?,?,0,?)",
            (_hash_secret(raw), user_id, now, now + absolute, now,
             (user_agent or "")[:200]),
        )
        _insert_log(conn, "session_created", "ok", detail="session issued", user_id=user_id)
    return {"ok": True, "token": raw, "expires_at": now + absolute}


def get_session_user(raw_token: str, idle_seconds: int = None):
    """Validate a session token and return its owner, or None.

    Checks revocation, the absolute expiry, the idle timeout, and that the
    account is still active -- in one query so a disabled user loses access
    immediately rather than at their next login.
    """
    if not raw_token:
        return None
    idle = idle_seconds if idle_seconds is not None else SESSION_IDLE_SECONDS
    now = int(time.time())
    session_id = _hash_secret(raw_token)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT s.id AS session_id, s.user_id, s.expires_at, s.last_used_at, "
            "       u.name, u.role, u.face_access, u.created_at, "
            "       (u.face_encoding IS NOT NULL) AS face_enrolled "
            "FROM sessions AS s JOIN users AS u ON u.id = s.user_id "
            "WHERE s.id=? AND s.revoked=0 AND u.active=1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= now or (row["last_used_at"] + idle) <= now:
            return None
        # Sliding idle window.  Only written when it actually moves, to keep
        # polling endpoints from writing on every request.
        if row["last_used_at"] != now:
            conn.execute(
                "UPDATE sessions SET last_used_at=? WHERE id=?", (now, session_id)
            )
        return {
            "session_id": row["session_id"],
            "id": row["user_id"],
            "name": row["name"],
            "role": row["role"] or "USER",
            "face_access": bool(row["face_access"]),
            "face_enrolled": bool(row["face_enrolled"]),
            "created_at": row["created_at"],
        }


def revoke_session(session_id: str, user_id: str = None):
    """Revoke one session.  Passing user_id scopes the delete to its owner."""
    with get_conn() as conn:
        if user_id:
            cur = conn.execute(
                "UPDATE sessions SET revoked=1 WHERE id=? AND user_id=? AND revoked=0",
                (session_id, user_id),
            )
        else:
            cur = conn.execute(
                "UPDATE sessions SET revoked=1 WHERE id=? AND revoked=0", (session_id,)
            )
        if cur.rowcount != 1:
            return {"ok": False, "error": "session not found"}
        _insert_log(conn, "session_revoked", "ok", detail="session revoked", user_id=user_id)
    return {"ok": True}


def revoke_all_sessions_for_user(user_id: str):
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE sessions SET revoked=1 WHERE user_id=? AND revoked=0", (user_id,)
        )
        _insert_log(conn, "session_revoked", "ok",
                    detail=f"revoked {cur.rowcount} session(s)", user_id=user_id)
    return {"ok": True, "revoked": cur.rowcount}


def list_sessions_for_user(user_id: str):
    now = int(time.time())
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, expires_at, last_used_at, user_agent "
            "FROM sessions WHERE user_id=? AND revoked=0 AND expires_at>? "
            "ORDER BY last_used_at DESC",
            (user_id, now),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "createdAt": r["created_at"] * 1000,
            "expiresAt": r["expires_at"] * 1000,
            "lastUsedAt": r["last_used_at"] * 1000,
            "userAgent": r["user_agent"] or "",
        }
        for r in rows
    ]


def create_pairing_code(user_id: str, created_by: str = None, ttl_seconds: int = None):
    """Mint a one-time pairing code, returned in plaintext exactly once."""
    raw = secrets.token_urlsafe(24)
    now = int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else PAIRING_TTL_SECONDS
    with get_conn() as conn:
        owner = conn.execute(
            "SELECT id FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        if not owner:
            return {"ok": False, "error": "user not found"}
        # Only one live code per user: minting a new one invalidates any
        # outstanding code so a forgotten code cannot be redeemed later.
        conn.execute(
            "UPDATE pairing_codes SET used_at=? WHERE user_id=? AND used_at IS NULL",
            (now, user_id),
        )
        conn.execute(
            "INSERT INTO pairing_codes (id, user_id, created_by, created_at, "
            "expires_at, used_at) VALUES (?,?,?,?,?,NULL)",
            (_hash_secret(raw), user_id, created_by, now, now + ttl),
        )
        _insert_log(conn, "pair_created", "ok",
                    detail=f"pairing code issued (ttl {ttl}s)", user_id=user_id)
    return {"ok": True, "code": raw, "expires_at": now + ttl, "expires_in": ttl}


def redeem_pairing_code(raw_code: str):
    """Consume a pairing code and return its user, or an error.

    The consume is a single conditional UPDATE (``used_at IS NULL`` in the
    WHERE clause), so two browsers racing the same code cannot both win --
    exactly one gets rowcount 1.
    """
    if not raw_code:
        return {"ok": False, "error": "pairing code is required"}
    now = int(time.time())
    code_id = _hash_secret(raw_code)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at, used_at FROM pairing_codes WHERE id=?",
            (code_id,),
        ).fetchone()
        if not row:
            _insert_log(conn, "pair_failed", "fail", detail="unknown pairing code")
            return {"ok": False, "error": "invalid or expired pairing code"}
        if row["used_at"] is not None:
            _insert_log(conn, "pair_failed", "fail", detail="pairing code already used",
                        user_id=row["user_id"])
            return {"ok": False, "error": "invalid or expired pairing code"}
        if row["expires_at"] <= now:
            _insert_log(conn, "pair_failed", "fail", detail="pairing code expired",
                        user_id=row["user_id"])
            return {"ok": False, "error": "invalid or expired pairing code"}
        claimed = conn.execute(
            "UPDATE pairing_codes SET used_at=? WHERE id=? AND used_at IS NULL",
            (now, code_id),
        )
        if claimed.rowcount != 1:
            return {"ok": False, "error": "invalid or expired pairing code"}
        _insert_log(conn, "pair_redeemed", "ok", detail="pairing code redeemed",
                    user_id=row["user_id"])
        return {"ok": True, "user_id": row["user_id"]}


# The audit table grew without bound and was never pruned, which on a Pi's SD
# card is a slow leak.  Keeping the most recent rows preserves the forensic
# value of the log without letting it consume the card.
MAX_AUDIT_ROWS = 20_000


def prune_logs(keep: int = MAX_AUDIT_ROWS):
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM auth_logs").fetchone()[0]
        if total <= keep:
            return {"ok": True, "removed": 0, "remaining": total}
        removed = conn.execute(
            "DELETE FROM auth_logs WHERE id IN ("
            "  SELECT id FROM auth_logs ORDER BY ts DESC LIMIT -1 OFFSET ?"
            ")",
            (keep,),
        ).rowcount
    return {"ok": True, "removed": removed, "remaining": total - removed}


def purge_expired(now: int = None):
    """Drop dead sessions, spent pairing codes, and stale audit rows.

    Called at startup.  Safe to call on a schedule.
    """
    now = now if now is not None else int(time.time())
    with get_conn() as conn:
        sessions = conn.execute(
            "DELETE FROM sessions WHERE expires_at<=? OR revoked=1", (now,)
        ).rowcount
        codes = conn.execute(
            "DELETE FROM pairing_codes WHERE expires_at<=? OR used_at IS NOT NULL",
            (now,),
        ).rowcount
    logs = prune_logs()
    return {
        "ok": True,
        "sessions": sessions,
        "pairing_codes": codes,
        "logs": logs["removed"],
    }


# ── Logs ───────────────────────────────────────────────────────────────────────

def log_event(stage: str, result: str, detail: str = "", user_id: str = None):
    with get_conn() as conn:
        _insert_log(conn, stage, result, detail=detail, user_id=user_id)

def get_logs(limit: int = 80):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM auth_logs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def _result_ok(result: str) -> bool:
    if not result:
        return False
    return result.lower() in ("ok", "success", "true", "pass", "1")


def list_logs_for_ui(limit: int = 80):
    out = []
    for row in get_logs(limit):
        ts = row["ts"]
        if ts < 10_000_000_000:
            ts = ts * 1000
        out.append(
            {
                "id": row["id"],
                "ts": ts,
                "type": row["stage"],
                "ok": _result_ok(row["result"]),
                "detail": row["detail"] or "",
            }
        )
    return out

# ── Settings ───────────────────────────────────────────────────────────────────

_UI_TO_DB_KEYS = {
    "autoRelockSeconds": "auto_relock_seconds",
    "ignitionAutoStopSeconds": "ignition_auto_stop_seconds",
    "promptAutoLockSeconds": "ignition_prompt_autolock_seconds",
    "liveness": "liveness_detection",
    "failLockout": "fail_lockout",
    "lockoutAfter": "lockout_after",
}

_DB_TO_UI_KEYS = {v: k for k, v in _UI_TO_DB_KEYS.items()}

def get_settings():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

def get_settings_for_ui():
    raw = get_settings()
    ui = {}
    for db_key, ui_key in _DB_TO_UI_KEYS.items():
        if db_key not in raw:
            continue
        val = raw[db_key]
        if ui_key in ("liveness", "failLockout"):
            ui[ui_key] = val.lower() in ("true", "1", "yes")
        elif ui_key in ("autoRelockSeconds", "ignitionAutoStopSeconds", "promptAutoLockSeconds", "lockoutAfter"):
            try:
                ui[ui_key] = int(val)
            except ValueError:
                if ui_key == "autoRelockSeconds":
                    ui[ui_key] = 10
                elif ui_key == "ignitionAutoStopSeconds":
                    ui[ui_key] = 20
                elif ui_key == "promptAutoLockSeconds":
                    ui[ui_key] = 0
                else:
                    ui[ui_key] = 5
        else:
            ui[ui_key] = val
    return ui

def save_settings(updates: dict):
    with get_conn() as conn:
        for key, value in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, str(value))
            )
    return {"ok": True}


def save_settings_from_ui(payload: dict):
    mapped = {}
    for ui_key, db_key in _UI_TO_DB_KEYS.items():
        if ui_key not in payload:
            continue
        v = payload[ui_key]
        if isinstance(v, bool):
            mapped[db_key] = "true" if v else "false"
        else:
            mapped[db_key] = str(v)
    save_settings(mapped)
    log_event("settings", "ok", detail="Updated settings from UI")
    return {"ok": True}
