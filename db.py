import sqlite3
import uuid
import time

DB_PATH = "/home/pi/faceid/faceid.db"

def get_conn():
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

            INSERT OR IGNORE INTO settings VALUES ('auto_relock_seconds', '10');
            INSERT OR IGNORE INTO settings VALUES ('liveness_detection',  'true');
            INSERT OR IGNORE INTO settings VALUES ('fail_lockout',        'true');
            INSERT OR IGNORE INTO settings VALUES ('lockout_after',       '5');
        """)

if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
