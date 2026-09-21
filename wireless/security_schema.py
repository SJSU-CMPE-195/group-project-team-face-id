"""Additive SQLite schema for host-authoritative wireless security."""

import sqlite3


COMMISSIONING_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS commissioning_state (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        ownership TEXT NOT NULL
            CHECK(ownership IN ('legacy', 'unclaimed', 'claimed')),
        owner_user_id TEXT,
        generation INTEGER NOT NULL CHECK(generation > 0),
        activation_secret TEXT,
        activation_hash BLOB CHECK(
            activation_hash IS NULL OR length(activation_hash) = 32
        ),
        recovery_hash BLOB CHECK(
            recovery_hash IS NULL OR length(recovery_hash) = 32
        ),
        updated_at INTEGER NOT NULL,
        CHECK(
            (ownership = 'claimed' AND owner_user_id IS NOT NULL
                AND activation_secret IS NULL AND activation_hash IS NULL
                AND recovery_hash IS NOT NULL)
            OR (ownership = 'unclaimed' AND owner_user_id IS NULL
                AND activation_secret IS NOT NULL
                AND activation_hash IS NOT NULL AND recovery_hash IS NULL)
            OR (ownership = 'legacy' AND owner_user_id IS NULL
                AND activation_secret IS NULL AND activation_hash IS NULL
                AND recovery_hash IS NULL)
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commissioning_attempts (
        request_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(
            kind IN ('claim', 'recover', 'transfer_start', 'transfer_accept', 'pair')
        ),
        body_digest BLOB NOT NULL CHECK(length(body_digest) = 32),
        response_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ownership_transfers (
        request_id TEXT PRIMARY KEY,
        owner_user_id TEXT NOT NULL,
        owner_device_id TEXT NOT NULL,
        owner_auth_version INTEGER NOT NULL CHECK(owner_auth_version > 0),
        secret_hash BLOB NOT NULL UNIQUE CHECK(length(secret_hash) = 32),
        generation INTEGER NOT NULL CHECK(generation > 0),
        expires_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
)

BASE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS user_security (
        user_id TEXT PRIMARY KEY REFERENCES users(id),
        pin_salt BLOB NOT NULL CHECK(length(pin_salt) = 16),
        pin_verifier BLOB NOT NULL CHECK(length(pin_verifier) = 32),
        is_admin INTEGER NOT NULL DEFAULT 0 CHECK(is_admin IN (0, 1)),
        failed_attempts INTEGER NOT NULL DEFAULT 0
            CHECK(failed_attempts BETWEEN 0 AND 4),
        lockout_until INTEGER NOT NULL DEFAULT 0,
        face_failed_attempts INTEGER NOT NULL DEFAULT 0
            CHECK(face_failed_attempts BETWEEN 0 AND 20),
        face_lockout_until INTEGER NOT NULL DEFAULT 0,
        auth_version INTEGER NOT NULL DEFAULT 1 CHECK(auth_version > 0),
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pairing_invites (
        token_hash BLOB PRIMARY KEY CHECK(length(token_hash) = 32),
        target_user_id TEXT NOT NULL REFERENCES users(id),
        issued_by_user_id TEXT NOT NULL REFERENCES users(id),
        issued_by_device_id TEXT NOT NULL REFERENCES paired_devices(id),
        issuer_auth_version INTEGER NOT NULL CHECK(issuer_auth_version > 0),
        expires_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paired_devices (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        token_hash BLOB NOT NULL UNIQUE CHECK(length(token_hash) = 32),
        kind TEXT NOT NULL DEFAULT 'mobile'
            CHECK(kind IN ('mobile', 'local')),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
        created_at INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        expires_at INTEGER,
        revoked_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operation_grants (
        token_hash BLOB PRIMARY KEY CHECK(length(token_hash) = 32),
        device_id TEXT NOT NULL REFERENCES paired_devices(id),
        user_id TEXT NOT NULL REFERENCES users(id),
        action TEXT NOT NULL,
        target TEXT,
        auth_version INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_security_devices_user
    ON paired_devices(user_id, kind, active)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_security_grants_device
    ON operation_grants(device_id, expires_at)
    """,
    *COMMISSIONING_SCHEMA_STATEMENTS,
)

USER_SECURITY_ADDITIVE_COLUMNS = (
    (
        "face_failed_attempts",
        "INTEGER NOT NULL DEFAULT 0 CHECK(face_failed_attempts BETWEEN 0 AND 20)",
    ),
    ("face_lockout_until", "INTEGER NOT NULL DEFAULT 0"),
)

PAIRING_INVITE_ADDITIVE_COLUMNS = (
    ("issued_by_device_id", "TEXT REFERENCES paired_devices(id)"),
    ("issuer_auth_version", "INTEGER"),
)

OWNERSHIP_TRANSFER_ADDITIVE_COLUMNS = (
    ("owner_device_id", "TEXT"),
    ("owner_auth_version", "INTEGER"),
)

USER_TRIGGER_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_user_identity_changed
    AFTER UPDATE OF active, face_access, face_encoding ON users
    WHEN OLD.active IS NOT NEW.active
      OR OLD.face_access IS NOT NEW.face_access
      OR OLD.face_encoding IS NOT NEW.face_encoding
    BEGIN
        UPDATE user_security
        SET auth_version = auth_version + 1,
            updated_at = CAST(strftime('%s', 'now') AS INTEGER)
        WHERE user_id = NEW.id;
        DELETE FROM operation_grants
        WHERE user_id = NEW.id OR target = NEW.id;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_last_admin_active
    BEFORE UPDATE OF active ON users
    WHEN OLD.active = 1 AND NEW.active = 0
      AND EXISTS (
          SELECT 1 FROM user_security
          WHERE user_id = OLD.id AND is_admin = 1
      )
      AND NOT EXISTS (
          SELECT 1
          FROM users AS other_user
          JOIN user_security AS other_security
            ON other_security.user_id = other_user.id
          WHERE other_user.active = 1
            AND other_security.is_admin = 1
            AND other_user.id != OLD.id
      )
    BEGIN
        SELECT RAISE(ABORT, 'cannot disable the last active admin');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_last_admin_user
    BEFORE DELETE ON users
    WHEN OLD.active = 1
      AND EXISTS (
          SELECT 1 FROM user_security
          WHERE user_id = OLD.id AND is_admin = 1
      )
      AND NOT EXISTS (
          SELECT 1
          FROM users AS other_user
          JOIN user_security AS other_security
            ON other_security.user_id = other_user.id
          WHERE other_user.active = 1
            AND other_security.is_admin = 1
            AND other_user.id != OLD.id
      )
    BEGIN
        SELECT RAISE(ABORT, 'cannot delete the last active admin');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_last_admin_role
    BEFORE UPDATE OF is_admin ON user_security
    WHEN OLD.is_admin = 1 AND NEW.is_admin = 0
      AND EXISTS (
          SELECT 1 FROM users WHERE id = OLD.user_id AND active = 1
      )
      AND NOT EXISTS (
          SELECT 1
          FROM users AS other_user
          JOIN user_security AS other_security
            ON other_security.user_id = other_user.id
          WHERE other_user.active = 1
            AND other_security.is_admin = 1
            AND other_user.id != OLD.user_id
      )
    BEGIN
        SELECT RAISE(ABORT, 'cannot demote the last active admin');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_last_admin_record
    BEFORE DELETE ON user_security
    WHEN OLD.is_admin = 1
      AND EXISTS (
          SELECT 1 FROM users WHERE id = OLD.user_id AND active = 1
      )
      AND NOT EXISTS (
          SELECT 1
          FROM users AS other_user
          JOIN user_security AS other_security
            ON other_security.user_id = other_user.id
          WHERE other_user.active = 1
            AND other_security.is_admin = 1
            AND other_user.id != OLD.user_id
      )
    BEGIN
        SELECT RAISE(ABORT, 'cannot remove the last active admin');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_owner_active
    BEFORE UPDATE OF active ON users
    WHEN OLD.active = 1 AND NEW.active = 0
      AND OLD.id = (
          SELECT owner_user_id FROM commissioning_state
          WHERE id = 1 AND ownership = 'claimed'
      )
    BEGIN
        SELECT RAISE(ABORT, 'cannot disable the product owner');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_owner_user
    BEFORE DELETE ON users
    WHEN OLD.id = (
        SELECT owner_user_id FROM commissioning_state
        WHERE id = 1 AND ownership = 'claimed'
    )
    BEGIN
        SELECT RAISE(ABORT, 'cannot delete the product owner');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_owner_role
    BEFORE UPDATE OF is_admin ON user_security
    WHEN OLD.is_admin = 1 AND NEW.is_admin = 0
      AND OLD.user_id = (
          SELECT owner_user_id FROM commissioning_state
          WHERE id = 1 AND ownership = 'claimed'
      )
    BEGIN
        SELECT RAISE(ABORT, 'cannot demote the product owner');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_security_keep_owner_record
    BEFORE DELETE ON user_security
    WHEN OLD.user_id = (
        SELECT owner_user_id FROM commissioning_state
        WHERE id = 1 AND ownership = 'claimed'
    )
    BEGIN
        SELECT RAISE(ABORT, 'cannot remove the product owner security record');
    END
    """,
)

USER_TRIGGER_NAMES = frozenset(
    {
        "trg_security_user_identity_changed",
        "trg_security_keep_last_admin_active",
        "trg_security_keep_last_admin_user",
        "trg_security_keep_last_admin_role",
        "trg_security_keep_last_admin_record",
        "trg_security_keep_owner_active",
        "trg_security_keep_owner_user",
        "trg_security_keep_owner_role",
        "trg_security_keep_owner_record",
    }
)

SCHEMA_STATEMENTS = (*BASE_SCHEMA_STATEMENTS, *USER_TRIGGER_STATEMENTS)


def install_security_schema(conn: sqlite3.Connection) -> None:
    """Install additive security schema inside the caller's transaction."""

    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    _add_missing_columns(conn, "user_security", USER_SECURITY_ADDITIVE_COLUMNS)
    _add_missing_columns(
        conn,
        "pairing_invites",
        PAIRING_INVITE_ADDITIVE_COLUMNS,
    )
    _add_missing_columns(
        conn,
        "ownership_transfers",
        OWNERSHIP_TRANSFER_ADDITIVE_COLUMNS,
    )
    conn.execute(
        "DELETE FROM pairing_invites "
        "WHERE issued_by_device_id IS NULL OR issuer_auth_version IS NULL"
    )
    conn.execute(
        "DELETE FROM ownership_transfers "
        "WHERE owner_device_id IS NULL OR owner_auth_version IS NULL"
    )


def drop_user_protection_triggers(conn: sqlite3.Connection) -> None:
    """Temporarily remove identity guards for an atomic product wipe."""

    for name in USER_TRIGGER_NAMES:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")


def install_user_protection_triggers(conn: sqlite3.Connection) -> None:
    for statement in USER_TRIGGER_STATEMENTS:
        conn.execute(statement)


def _add_missing_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
