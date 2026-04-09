import uuid
import time
from db import get_conn

# ── Users ──────────────────────────────────────────────────────────────────────

def get_all_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM users WHERE active=1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

def add_user(name: str, face_encoding: bytes = None):
    user_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, name, face_encoding, created_at) VALUES (?,?,?,?)",
            (user_id, name, face_encoding, int(time.time()))
        )
    return {"id": user_id, "name": name}

def delete_user(user_id: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (user_id,))
    return {"ok": True}

def get_user_by_id(user_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()
        return dict(row) if row else None

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

# ── Settings ───────────────────────────────────────────────────────────────────

def get_settings():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

def save_settings(updates: dict):
    with get_conn() as conn:
        for key, value in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, str(value))
            )
    return {"ok": True}
