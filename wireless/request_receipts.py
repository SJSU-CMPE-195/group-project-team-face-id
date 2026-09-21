"""Exact request replay primitives shared by credential-creating flows."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from typing import Any, Mapping
import uuid

from .security_credentials import SecurityError


def canonical_request_id(value: Any) -> str:
    if not isinstance(value, str):
        raise SecurityError("invalid_request_id", 400, "request_id must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise SecurityError(
            "invalid_request_id", 400, "request_id must be a canonical UUID"
        ) from exc
    if str(parsed) != value:
        raise SecurityError(
            "invalid_request_id", 400, "request_id must be a canonical UUID"
        )
    return value


def request_digest(kind: str, values: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(
        {"kind": kind, **values},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def load_receipt(
    conn: sqlite3.Connection,
    request_id: str,
    kind: str,
    digest: bytes,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT kind,body_digest,response_json FROM commissioning_attempts "
        "WHERE request_id=?",
        (request_id,),
    ).fetchone()
    if row is None:
        return None
    if row["kind"] != kind or not hmac.compare_digest(
        bytes(row["body_digest"]), digest
    ):
        raise SecurityError(
            "request_id_reused",
            409,
            "request_id was already used for different request data",
        )
    return json.loads(row["response_json"])


def save_receipt(
    conn: sqlite3.Connection,
    request_id: str,
    kind: str,
    digest: bytes,
    response: Mapping[str, Any],
    now: int,
) -> None:
    conn.execute(
        "INSERT INTO commissioning_attempts "
        "(request_id,kind,body_digest,response_json,created_at) VALUES (?,?,?,?,?)",
        (
            request_id,
            kind,
            digest,
            json.dumps(response, sort_keys=True, separators=(",", ":")),
            now,
        ),
    )
