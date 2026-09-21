"""Bind issued credentials to the host TLS identity across backup and transfer."""

import hmac
import re
import uuid

from db_api import _insert_log


def bind_host_identity(get_conn, certificate_sha256: str, device_id: str, *, rotate: bool = False):
    if not re.fullmatch(r"[0-9a-f]{64}", certificate_sha256):
        raise ValueError("Invalid host certificate fingerprint.")
    if str(uuid.UUID(device_id)) != device_id:
        raise ValueError("Invalid host device identity.")
    with get_conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS host_identity ("
            "id INTEGER PRIMARY KEY CHECK(id=1), certificate_sha256 TEXT NOT NULL, "
            "device_id TEXT)"
        )
        columns = {item[1] for item in connection.execute("PRAGMA table_info(host_identity)")}
        if "device_id" not in columns:
            connection.execute("ALTER TABLE host_identity ADD COLUMN device_id TEXT")
        row = connection.execute(
            "SELECT certificate_sha256, device_id FROM host_identity WHERE id=1"
        ).fetchone()
        active_capabilities = connection.execute(
            "SELECT 1 FROM paired_devices WHERE active=1 "
            "UNION ALL SELECT 1 FROM pairing_invites "
            "UNION ALL SELECT 1 FROM operation_grants LIMIT 1"
        ).fetchone()
        matches = (row is not None and row[1] == device_id
                   and hmac.compare_digest(row[0], certificate_sha256))
        if not rotate and ((row and not matches) or (row is None and active_capabilities)):
            raise RuntimeError(
                "The database does not match this TLS identity. Restore the matching "
                "TLS/DB backup, or stop the host and explicitly run "
                "--rotate-tls-authorization-only to revoke all device credentials."
            )
        if rotate:
            connection.execute(
                "UPDATE paired_devices SET active=0, revoked_at=CAST(strftime('%s','now') AS INTEGER) "
                "WHERE active=1"
            )
            connection.execute("DELETE FROM operation_grants")
            connection.execute("DELETE FROM pairing_invites")
            _insert_log(connection, "tls_identity_rotation", "ok",
                        "Offline TLS rotation revoked every device, session, invite and grant")
        if not matches:
            connection.execute(
                "INSERT INTO host_identity(id, certificate_sha256, device_id) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET certificate_sha256=excluded.certificate_sha256, "
                "device_id=excluded.device_id",
                (certificate_sha256, device_id),
            )
