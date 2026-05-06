import uuid
import time
from db import get_conn

# ── Device status (face-ui /api/status) ─────────────────────────────────────────

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
    log_event("unlock", "ok", detail=reason)

def set_lock(reason: str = "auto_relock"):
    now_ms = int(time.time() * 1000)
    with get_conn() as conn:
        conn.execute(
            "UPDATE device_state SET lock_state = 'locked', last_seen = ? WHERE id = 1",
            (now_ms,),
        )
    log_event("lock", "ok", detail=reason)

# ── Users ──────────────────────────────────────────────────────────────────────

def get_all_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, face_access, created_at FROM users WHERE active=1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_users_for_ui():
    return [
        {
            "id": u["id"],
            "name": u["name"],
            "faceAccess": bool(u.get("face_access", 1)),
            "createdAt": int(u["created_at"]) * 1000,
        }
        for u in get_all_users()
    ]

def add_user(name: str, face_encoding: bytes = None):
    user_id = str(uuid.uuid4())
    ts = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, name, face_encoding, created_at) VALUES (?,?,?,?)",
            (user_id, name, face_encoding, ts)
        )
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
        conn.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
    log_event("delete_user", "ok", detail=f"Removed {display_name}", user_id=user_id)
    return {"ok": True}

def get_user_by_id(user_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        return dict(row) if row else None

def set_user_access(user_id: str, allowed: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET face_access=? WHERE id=? AND active=1",
            (1 if allowed else 0, user_id),
        )
    log_event("access_change", "ok", detail=f"{'granted' if allowed else 'revoked'} for {user_id}", user_id=user_id)
    return {"ok": True}

def set_user_embedding(user_id: str, blob: bytes):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET face_encoding=? WHERE id=? AND active=1",
            (blob, user_id),
        )
    log_event("enroll_embedding", "ok", detail=f"Embedding stored for {user_id}", user_id=user_id)
    return {"ok": True}

def get_all_face_encodings():
    """Returns all active users with their face encodings — used by ML partner."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, face_encoding FROM users WHERE active=1 AND face_encoding IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

# ── Logs ───────────────────────────────────────────────────────────────────────

def log_event(stage: str, result: str, detail: str = "", user_id: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO auth_logs (id, user_id, stage, result, detail, ts) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, stage, result, detail, int(time.time()))
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
        elif ui_key in ("autoRelockSeconds", "ignitionAutoStopSeconds", "lockoutAfter"):
            try:
                ui[ui_key] = int(val)
            except ValueError:
                if ui_key == "autoRelockSeconds":
                    ui[ui_key] = 10
                elif ui_key == "ignitionAutoStopSeconds":
                    ui[ui_key] = 20
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
