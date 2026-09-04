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
    # Two processes write this file (the Flask API and the face engine), and
    # PiRuntime runs sessions on background threads, so a writer must not fail
    # the instant it finds the database busy.
    conn.execute("PRAGMA busy_timeout = 5000")
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

            -- Browser sessions.  "id" is the SHA-256 of the raw session token,
            -- never the token itself: a stolen database copy cannot be replayed
            -- as a login.  SHA-256 rather than bcrypt/argon2 is deliberate --
            -- these are 256-bit values from secrets.token_urlsafe(32), not
            -- human passwords, so there is no dictionary to slow down and a
            -- deliberately slow hash would cost real time on every request.
            CREATE TABLE IF NOT EXISTS sessions (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL REFERENCES users(id),
                created_at   INTEGER NOT NULL,
                expires_at   INTEGER NOT NULL,
                last_used_at INTEGER NOT NULL,
                revoked      INTEGER NOT NULL DEFAULT 0,
                user_agent   TEXT
            );

            -- One-time pairing codes.  Same hashing rule as sessions.
            -- "used_at" going non-NULL is what makes a code single-use.
            CREATE TABLE IF NOT EXISTS pairing_codes (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL REFERENCES users(id),
                created_by TEXT REFERENCES users(id),
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at    INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user
                ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires
                ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_pairing_expires
                ON pairing_codes(expires_at);
            -- get_logs only ever reads ORDER BY ts DESC.
            CREATE INDEX IF NOT EXISTS idx_auth_logs_ts
                ON auth_logs(ts DESC);

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
        # Everyone already in the database becomes an ordinary USER.  The first
        # ADMIN is seeded explicitly by install.sh, so an upgrade never silently
        # promotes an existing row to administrator.
        _ensure_column(conn, "users", "role", "TEXT NOT NULL DEFAULT 'USER'")
        _ensure_active_name_index(conn)

        # WAL lets the face engine read while the API writes instead of the two
        # blocking each other.  It is a persistent property of the file, so
        # setting it once here is enough.
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            # A database on a filesystem without shared-memory support (some
            # network mounts) keeps its existing journal mode rather than
            # failing the whole migration.
            pass

if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
