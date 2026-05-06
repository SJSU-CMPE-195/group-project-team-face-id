import os
import sqlite3
import time
from pathlib import Path

DB_PATH = os.environ.get("FACEID_DB_PATH", "/home/pi/faceid/faceid.db")

def get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets you access columns by name
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                face_encoding BLOB,
                active        INTEGER DEFAULT 1,
                face_access   INTEGER DEFAULT 1,
                created_at    INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_logs (
                id       TEXT PRIMARY KEY,
                user_id  TEXT REFERENCES users(id),
                stage    TEXT NOT NULL,
                result   TEXT NOT NULL,
                detail   TEXT,
                ts       INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS device_state (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                device_name TEXT NOT NULL DEFAULT 'FaceLock-Pi',
                lock_state  TEXT NOT NULL DEFAULT 'locked'
                    CHECK (lock_state IN ('locked', 'unlocked')),
                battery     INTEGER NOT NULL DEFAULT 100,
                signal      INTEGER NOT NULL DEFAULT 5,
                last_seen   INTEGER NOT NULL
            );

            INSERT OR IGNORE INTO settings VALUES ('auto_relock_seconds', '10');
            INSERT OR IGNORE INTO settings VALUES ('ignition_auto_stop_seconds', '20');
            INSERT OR IGNORE INTO settings VALUES ('liveness_detection',  'true');
            INSERT OR IGNORE INTO settings VALUES ('fail_lockout',        'true');
            INSERT OR IGNORE INTO settings VALUES ('lockout_after',       '5');

            INSERT OR IGNORE INTO device_state (id, device_name, lock_state, battery, signal, last_seen)
            VALUES (1, 'FaceLock-Pi', 'locked', 100, 5, CAST(strftime('%s','now') AS INTEGER) * 1000);
        """)

if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
