import os
import sqlite3
import time
from pathlib import Path

DB_PATH = os.environ.get("FACEID_DB_PATH", "/home/pi/faceid/faceid.db")


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection, then release the DB handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row  # lets you access columns by name
    return conn


def _ensure_column(conn, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_active_name_index(conn) -> None:
    """Protect new databases while leaving legacy duplicate rows untouched."""
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_active_name "
            "ON users(lower(trim(name))) WHERE active = 1"
        )
    except sqlite3.IntegrityError:
        # A legacy database may already contain active duplicate names.  Do not
        # silently disable either account; db_api applies the same check while
        # those rows are being cleaned up.
        pass

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
            INSERT OR IGNORE INTO settings VALUES ('ignition_prompt_autolock_seconds', '0');
            INSERT OR IGNORE INTO settings VALUES ('liveness_detection',  'true');
            INSERT OR IGNORE INTO settings VALUES ('fail_lockout',        'true');
            INSERT OR IGNORE INTO settings VALUES ('lockout_after',       '5');

            INSERT OR IGNORE INTO device_state (id, device_name, lock_state, battery, signal, last_seen)
            VALUES (1, 'FaceLock-Pi', 'locked', 100, 5, CAST(strftime('%s','now') AS INTEGER) * 1000);
        """)
        # Older installs may predate these columns.  Defaults preserve their
        # existing users and make the access flag explicit for new queries.
        _ensure_column(conn, "users", "active", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "users", "face_access", "INTEGER NOT NULL DEFAULT 1")
        _ensure_active_name_index(conn)

if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
