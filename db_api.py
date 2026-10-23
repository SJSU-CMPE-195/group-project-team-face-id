import uuid
import time
import sqlite3
from db import get_conn
from event_log import format_event
from safety_policy import BOOLEAN_SETTINGS, INTEGER_SETTING_RANGES

# ── Device status (face-ui /api/status) ─────────────────────────────────────────


def _insert_log(
    conn,
    stage: str,
    result: str,
    detail: str = "",
    user_id: str = None,
    *,
    actor_user_id: str = None,
    actor_kind: str = "legacy",
):
    conn.execute(
        "INSERT INTO auth_logs "
        "(id, user_id, actor_user_id, actor_kind, stage, result, detail, ts) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            user_id,
            actor_user_id,
            actor_kind,
            stage,
            result,
            detail,
            int(time.time()),
        ),
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

def set_unlock(
    reason: str = "manual_ui",
    *,
    user_id: str = None,
    actor_user_id: str = None,
    actor_kind: str = "legacy",
):
    now_ms = int(time.time() * 1000)
    with get_conn() as conn:
        conn.execute(
            "UPDATE device_state SET lock_state = 'unlocked', last_seen = ? WHERE id = 1",
            (now_ms,),
        )
        _insert_log(
            conn,
            "unlock",
            "ok",
            detail=reason,
            user_id=user_id,
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
        )

def set_lock(
    reason: str = "auto_relock",
    *,
    user_id: str = None,
    actor_user_id: str = None,
    actor_kind: str = "legacy",
):
    now_ms = int(time.time() * 1000)
    with get_conn() as conn:
        conn.execute(
            "UPDATE device_state SET lock_state = 'locked', last_seen = ? WHERE id = 1",
            (now_ms,),
        )
        _insert_log(
            conn,
            "lock",
            "ok",
            detail=reason,
            user_id=user_id,
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
        )

# ── Users ──────────────────────────────────────────────────────────────────────

def get_all_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, face_access, face_encoding IS NOT NULL AS face_enrolled, "
            "created_at FROM users WHERE active=1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_users_for_ui():
    return [
        {
            "id": u["id"],
            "name": u["name"],
            "faceAccess": bool(u.get("face_access", 1)),
            "faceEnrolled": bool(u.get("face_enrolled")),
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
        _insert_log(conn, "enroll", "ok", detail=f"Added {name}", user_id=user_id)
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
        _insert_log(
            conn,
            "delete_user",
            "ok",
            detail=f"Removed {display_name}",
            user_id=user_id,
        )
    return {"ok": True}

def get_user_by_id(user_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        return dict(row) if row else None

def set_user_access(user_id: str, allowed: bool):
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET face_access=? WHERE id=? AND active=1",
            (1 if allowed else 0, user_id),
        )
        if cur.rowcount != 1:
            return {"ok": False, "error": "user not found"}
        _insert_log(
            conn,
            "access_change",
            "ok",
            detail=f"{'granted' if allowed else 'revoked'} for {user_id}",
            user_id=user_id,
        )
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
        _insert_log(
            conn,
            "enroll_embedding",
            "ok",
            detail=f"Embedding stored for {user_id}",
            user_id=user_id,
        )
    return {"ok": True}

def get_all_face_encodings():
    """Return unambiguous templates for recognizing active accounts."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.name, u.face_encoding FROM users AS u "
            "WHERE u.active=1 AND u.face_encoding IS NOT NULL "
            "AND (SELECT COUNT(*) FROM users AS same "
            "     WHERE same.active=1 "
            "       AND lower(trim(same.name))=lower(trim(u.name)))=1"
        ).fetchall()
        return [dict(r) for r in rows]

# ── Logs ───────────────────────────────────────────────────────────────────────

def log_event(
    stage: str,
    result: str,
    detail: str = "",
    user_id: str = None,
    *,
    actor_user_id: str = None,
    actor_kind: str = "legacy",
):
    with get_conn() as conn:
        _insert_log(
            conn,
            stage,
            result,
            detail=detail,
            user_id=user_id,
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
        )

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


def list_logs_for_ui(limit: int = 80, *, principal=None):
    where = ""
    params = []
    with get_conn() as conn:
        if principal is not None and not getattr(principal, "is_owner", False):
            user_id = getattr(principal, "user_id", None)
            if not user_id:
                return []
            if getattr(principal, "is_admin", False):
                owner = conn.execute(
                    "SELECT owner_user_id FROM commissioning_state "
                    "WHERE id=1 AND ownership='claimed'"
                ).fetchone()
                owner_id = owner["owner_user_id"] if owner else None
                where = (
                    "WHERE actor_kind != 'legacy' "
                    "AND (? IS NULL OR actor_user_id IS NULL OR actor_user_id != ?) "
                    "AND (? IS NULL OR user_id IS NULL OR user_id != ?) "
                )
                params.extend((owner_id, owner_id, owner_id, owner_id))
            else:
                where = (
                    "WHERE actor_kind != 'legacy' "
                    "AND (actor_user_id = ? OR user_id = ?) "
                )
                params.extend((user_id, user_id))

        rows = conn.execute(
            f"SELECT * FROM auth_logs {where}ORDER BY ts DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        referenced_user_ids = {
            user_id
            for row in rows
            for user_id in (row["actor_user_id"], row["user_id"])
            if user_id
        }
        user_names = {}
        if referenced_user_ids:
            placeholders = ",".join("?" for _ in referenced_user_ids)
            user_rows = conn.execute(
                f"SELECT id,name FROM users WHERE id IN ({placeholders})",
                tuple(referenced_user_ids),
            ).fetchall()
            user_names = {row["id"]: row["name"] for row in user_rows}

    out = []
    for row in rows:
        ts = row["ts"]
        if ts < 10_000_000_000:
            ts = ts * 1000
        detail = row["detail"] or ""
        if principal is not None and not getattr(principal, "is_owner", False):
            detail = f"{row['stage']}: {row['result']}"
        display = format_event(
            row["stage"],
            row["result"],
            row["detail"] or "",
            actor_name=user_names.get(row["actor_user_id"]),
            subject_name=user_names.get(row["user_id"]),
        )
        out.append(
            {
                "id": row["id"],
                "ts": ts,
                "type": row["stage"],
                "ok": _result_ok(row["result"]),
                "detail": detail,
                **display,
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
        val = raw.get(db_key)
        if not isinstance(val, str):
            raise ValueError(f"{ui_key} setting is missing or invalid")
        if ui_key in BOOLEAN_SETTINGS:
            if val not in ("true", "false"):
                raise ValueError(f"{ui_key} setting is invalid")
            ui[ui_key] = val == "true"
        else:
            if len(val) > 4 or not val.isascii() or not val.isdecimal():
                raise ValueError(f"{ui_key} setting is invalid")
            number = int(val)
            minimum, maximum = INTEGER_SETTING_RANGES[ui_key]
            if not minimum <= number <= maximum:
                raise ValueError(f"{ui_key} setting is outside the supported range")
            ui[ui_key] = number
    return ui

def save_settings(updates: dict):
    with get_conn() as conn:
        _save_settings(conn, updates)
    return {"ok": True}


def _save_settings(conn, updates: dict):
    for key, value in updates.items():
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            (key, str(value)),
        )


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
    with get_conn() as conn:
        _save_settings(conn, mapped)
        _insert_log(conn, "settings", "ok", detail="Updated settings from UI")
    return {"ok": True}
